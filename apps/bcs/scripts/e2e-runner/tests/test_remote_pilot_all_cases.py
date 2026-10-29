from __future__ import annotations

import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.cli import main
from bcs_e2e.evidence import ASSERTIONS, _make_plan
from bcs_e2e.pilot import run_case
from bcs_e2e.profile import load


ROOT = Path(__file__).resolve().parents[1]


class RemoteShapedSemanticProvider:
    """Fake remote semantic provider: same contract, no local-surrogate source."""
    def __init__(self, adapter):
        self.adapter = adapter
        self.database = "agentclawdb"
        self.environment = "pre"

    def admit(self, assertion_ids):
        return {"status": "READY", "source": "alisddbquery", "database": self.database,
                "environment": self.environment, "assertion_ids": list(assertion_ids)}

    def observe(self, assertion_id, resource_id):
        plan = _make_plan(assertion_id, resource_id, self.database, self.environment)
        table, fields, key = ASSERTIONS[assertion_id]
        if assertion_id == "group.persisted":
            value = self.adapter.groups.get(resource_id)
            row = {"group_id": resource_id, "env": "pre",
                   "driver_bot": value["driver_bot_uuid"], "originator": value["originator_actor_id"],
                   "status": value["status"]}
        elif assertion_id == "session.persisted":
            value = self.adapter.sessions.get(resource_id)
            row = {"session_id": resource_id, "env": "pre",
                   "group_id": value["group_id"], "status": value["status"],
                   "session_title": value["title"]}
        elif assertion_id == "file.persisted":
            value = self.adapter.files.get(resource_id)
            row = {"file_id": resource_id, "env": "pre",
                   "session_id": value["session_id"], "status": value["status"],
                   "size": value["size"], "sha256": value["sha256"],
                   "storage_backend": "baas"}
        else:
            raise AssertionError(f"unsupported assertion {assertion_id}")
        return {"status": "OBSERVED", "source": "alisddbquery", "plan": plan.public(),
                "rows": [{field: row[field] for field in fields}]}


class RemoteShapedAdapter(LocalMockAdapter):
    """Fake Gateway/WS/BaaS boundary used only to exercise shared Stories."""
    def preflight(self):
        return {"schema_version": 1, "status": "DISCOVERY_PASSED", "ready_to_execute": False,
                "checks": [], "requests": [], "not_verified": ["real deployment"]}

    def semantic_observer(self):
        return RemoteShapedSemanticProvider(self)

    def _record(self, operation, method, path, status):
        record = super()._record(operation, method, path, status)
        record["source"] = "remote-shaped-fake"
        record["path"] = path.replace("/mock", "/api/v1/collaboration")
        return record

    def file_prepare(self, session_id, body):
        result, record = super().file_prepare(session_id, body)
        self.files[result["file_id"]]["storage_backend"] = "baas"
        return result, record


class RemotePilotAllCaseTests(unittest.TestCase):
    def profile(self):
        value = load(ROOT / "profiles/preprod.example.json")
        value["environment"].update(id="pre-shaped", expected_bcs_env="pre")
        value["human"]["user_id"] = "owner-1"
        for role, bot in (("driver", "bot-a"), ("member", "bot-b")):
            value["actors"][role].update(bot_id=bot, owner_id="owner-1")
        value["observer"].update(server="alisddbquery", database="agentclawdb",
                                  environment_binding_confirmed=True)
        value["policy"].update(fixture_pool="remote-shaped-all-cases", exclusive_use_confirmed=True)
        return value

    def test_all_three_stories_share_the_remote_composition_contract(self):
        profile = self.profile()
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            tmp = Path(tmp)
            profile_path = tmp / "profile.json"
            profile_path.write_text(json.dumps(profile))
            run_id = "remote-shaped-all"
            state = tmp / "state"
            with patch("bcs_e2e.pool.pool_root", return_value=tmp / "pools"):
                with contextlib.redirect_stdout(__import__("io").StringIO()):
                    self.assertEqual(main(["prepare", "--profile", str(profile_path),
                                           "--run-id", run_id, "--state-dir", str(state)]), 0)
                injected = []
                for case_id in ("PRE-01", "PRE-02", "PRE-03"):
                    def factory(current, *, state_dir, case_id, observer_client, physical_binding_probe):
                        adapter = RemoteShapedAdapter(current)
                        injected.append((case_id, observer_client, physical_binding_probe))
                        return adapter
                    result = run_case(profile, state, run_id, case_id,
                                      observer_client=object(),
                                      physical_binding_probe=lambda db, env: {
                                          "status": "BOUND", "database": db, "environment": env},
                                      adapter_factory=factory)
                    self.assertEqual(result["status"], "PASS")
                    self.assertEqual(result["evidence_status"], "PASS")
                    self.assertEqual(result["cleanup"], "PASS")
        self.assertEqual([item[0] for item in injected], ["PRE-01", "PRE-02", "PRE-03"])
        self.assertTrue(all(item[1] is not None for item in injected))
        self.assertTrue(all(item[2] is not None for item in injected))


if __name__ == "__main__":
    unittest.main()
