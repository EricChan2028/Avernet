from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter, RemotePilotAdapter
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.observation_gate import (AdmittedObserver, ObserverAdmissionError, aggregate_diagnostic,
                                      aggregate_evidence)
from bcs_e2e.pilot import run_pre01
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import read


class ObservationGateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        registry = patch("bcs_e2e.pool.pool_root", return_value=self.root / "pools")
        registry.start()
        self.addCleanup(registry.stop)
        self.count = 0

    def prepare(self):
        self.count += 1
        self.profile = load(ROOT / "profiles/local.mock.json")
        self.profile["policy"]["fixture_pool"] = f"pool-{self.count}"
        self.run_id = f"gate-{self.count}"
        self.state = self.root / self.run_id
        self.path = self.root / f"{self.run_id}-profile.json"
        self.path.write_text(json.dumps(self.profile))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["prepare", "--profile", str(self.path), "--run-id", self.run_id,
                                   "--state-dir", str(self.state)]), 0)
        return LocalMockAdapter(self.profile)

    def execute(self, case, adapter):
        output = io.StringIO()
        with patch("bcs_e2e.pilot.adapter_for", return_value=adapter), contextlib.redirect_stdout(output):
            rc = main(["run", "--profile", str(self.path), "--state-dir", str(self.state),
                       "--run-id", self.run_id, "--case", case,
                       "--allow-write", "--confirm-run-id", self.run_id])
        return rc, json.loads(output.getvalue())

    def test_all_cases_require_observer_before_any_business_intent(self):
        for case in ("PRE-01", "PRE-02", "PRE-03"):
            for failure in ("missing", "factory", "admission"):
                with self.subTest(case=case, failure=failure):
                    adapter = self.prepare()
                    provider = adapter.semantic_observer()
                    if failure == "missing":
                        resolver = Mock(return_value=None)
                    elif failure == "factory":
                        resolver = Mock(side_effect=RuntimeError("private-auth-secret"))
                    else:
                        provider.admit = Mock(side_effect=RuntimeError("private-auth-secret"))
                        resolver = Mock(return_value=provider)
                    with patch.object(adapter, "semantic_observer", resolver), \
                         patch.object(adapter, "group_create") as create:
                        rc, result = self.execute(case, adapter)
                    self.assertEqual(rc, 2)
                    self.assertEqual(result["status"], "BLOCKED")
                    self.assertEqual(result["functional"], "NOT_RUN")
                    self.assertEqual(result["cleanup"], "NOT_ATTEMPTED")
                    self.assertEqual(result["evidence_status"], "BLOCKED")
                    self.assertNotIn("private-auth-secret", json.dumps(result))
                    create.assert_not_called()
                    resolver.assert_called_once()
                    with Ledger(self.state / "run.sqlite", self.run_id, fingerprint(self.profile)) as ledger:
                        self.assertEqual(ledger.db.execute("SELECT count(*) FROM resource").fetchone()[0], 0)
                    self.assertEqual(read(self.state / f"report-{case}.json")["status"], "BLOCKED")

    def test_actual_remote_adapter_without_binding_is_blocked_without_http(self):
        self.prepare()
        # Construct its real dependency graph with fake transports; none may send.
        with patch("bcs_e2e.adapters.ReadClient") as read_client, \
             patch("bcs_e2e.adapters.WriteClient") as write_client, \
             patch("bcs_e2e.adapters.SessionWebSocketClient"), \
             patch("bcs_e2e.adapters.RemoteFileClient") as files:
            adapter = RemotePilotAdapter(self.profile)
            rc, result = self.execute("PRE-01", adapter)
        self.assertEqual(rc, 2)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(read_client.return_value.mock_calls, [])
        self.assertEqual(write_client.return_value.mock_calls, [])
        self.assertEqual(files.return_value.mock_calls, [])

    def test_invalid_observation_after_mutation_preserves_functional_result_and_cleanup(self):
        for case in ("PRE-01", "PRE-02", "PRE-03"):
            for invalid in (None, {}, {"status": "BLOCKED"}, {"status": "OBSERVED", "source": "untrusted"}):
                with self.subTest(case=case, invalid=invalid):
                    adapter = self.prepare()
                    provider = adapter.semantic_observer()
                    with patch.object(adapter, "semantic_observer", return_value=provider), \
                         patch.object(provider, "observe", return_value=invalid):
                        rc, report = self.execute(case, adapter)
                    self.assertEqual(rc, 2)
                    self.assertEqual(report["status"], "BLOCKED")
                    self.assertEqual(report["functional"], "PASS")
                    self.assertEqual(report["cleanup"], "PASS")
                    self.assertEqual(report["evidence_status"], "BLOCKED")
                    with Ledger(self.state / "run.sqlite", self.run_id, fingerprint(self.profile)) as ledger:
                        self.assertEqual(ledger.cleanup_plan()["resources"], [])
                    self.assertNotEqual(read(self.state / "run.json")["status"], "CASE_COMPLETED")

    def test_one_failed_pre02_observation_is_not_masked_by_local_success_in_either_order(self):
        for failing in ("group.persisted", "session.persisted"):
            with self.subTest(failing=failing):
                adapter = self.prepare()
                provider = adapter.semantic_observer()
                original = provider.observe
                def observe(assertion_id, resource_id):
                    return None if assertion_id == failing else original(assertion_id, resource_id)
                with patch.object(adapter, "semantic_observer", return_value=provider), \
                     patch.object(provider, "observe", side_effect=observe):
                    rc, report = self.execute("PRE-02", adapter)
                self.assertEqual(rc, 2)
                self.assertEqual(report["evidence_status"], "BLOCKED")
                self.assertEqual(report["cleanup"], "PASS")

    def test_programmatic_composition_can_inject_observer_client_without_cli_binding(self):
        adapter = self.prepare()
        sentinel = object()
        captured = {}
        def factory(profile, *, state_dir, case_id, observer_client, physical_binding_probe):
            captured["client"] = observer_client
            captured["case_id"] = case_id
            return adapter
        report = run_pre01(self.profile, self.state, self.run_id,
                           observer_client=sentinel, adapter_factory=factory)
        self.assertEqual(report["status"], "PASS")
        self.assertIs(captured["client"], sentinel)
        self.assertEqual(captured["case_id"], "PRE-01")

    def test_diagnostic_observations_are_visible_but_non_gating(self):
        self.assertEqual(aggregate_diagnostic(()), "NOT_RUN")
        self.assertEqual(aggregate_diagnostic(("PASS",)), "PASS")
        self.assertEqual(aggregate_diagnostic(("INCOMPLETE",)), "INCOMPLETE")
        self.assertEqual(aggregate_diagnostic(("BLOCKED",)), "INCOMPLETE")

    def test_remote_profile_cannot_admit_a_local_surrogate(self):
        adapter = self.prepare()
        remote = load(ROOT / "profiles/preprod.example.json")
        scope = Mock()
        with self.assertRaises(ObserverAdmissionError):
            AdmittedObserver(adapter, remote, "PRE-01", resource_scope=scope)
        scope.assert_not_called()

    def test_empty_or_incomplete_evidence_never_passes(self):
        self.assertEqual(aggregate_evidence(()), "INCOMPLETE")
        for pair in (("LOCAL_SURROGATE", "BLOCKED"), ("BLOCKED", "LOCAL_SURROGATE")):
            self.assertEqual(aggregate_evidence(pair), "BLOCKED")
        for pair in (("LOCAL_SURROGATE", "INCOMPLETE"), ("INCOMPLETE", "LOCAL_SURROGATE")):
            self.assertEqual(aggregate_evidence(pair), "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
