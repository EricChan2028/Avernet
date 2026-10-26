from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.alisddbquery import AlisdDbQueryError, AlisdDbQueryObserver, AlisdDbQueryTimeout
from bcs_e2e.evidence import ASSERTIONS
from bcs_e2e.inventory import current_manifest
from bcs_e2e.observation_gate import (AdmittedObserver, ObserverAdmissionError,
                                      observer_admission_diagnostic)
from bcs_e2e.pool import pool_path, pool_scope
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import ConfigError, load
from bcs_e2e.retained import SUITE, run_retained_case


class Client:
    def db_list(self):
        return {"success": True, "data": ["safe_db"]}

    def table_list(self, _database):
        return {"success": True, "data": sorted({value[0] for value in ASSERTIONS.values()})}

    def table_describe(self, _database, table):
        fields = next(value[1] for value in ASSERTIONS.values() if value[0] == table)
        return {"success": True, "columns": list(fields)}

    def db_query(self, _plan):
        raise AssertionError("business queries are outside admission")


class Adapter:
    def __init__(self, provider):
        self.provider = provider

    def semantic_observer(self):
        return self.provider


class ForgedError(RuntimeError):
    observer_operation = "db_list"
    category = "tool_failure"


class ObserverAdmissionDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.profile = load(ROOT / "profiles/preprod.example.json")
        self.profile["observer"]["database"] = "safe_db"
        self.profile["environment"]["expected_bcs_env"] = "pre"
        self.binding = lambda database, environment: {
            "status": "BOUND", "database": database, "environment": environment,
        }

    def observer(self, client=None, binding=None):
        return AlisdDbQueryObserver(
            client=client or Client(), database="safe_db", environment="pre",
            environment_binding_confirmed=True,
            physical_binding_probe=self.binding if binding is None else binding,
        )

    def admission_error(self, provider):
        with self.assertRaises(ObserverAdmissionError) as raised:
            AdmittedObserver(Adapter(provider), self.profile, "PRE-03", resource_scope=lambda *_: ())
        return raised.exception

    def assert_sealed(self, error, operation, category):
        self.assertEqual(error.diagnostic, {
            "stage": "observer_admission", "operation": operation, "category": category,
        })
        encoded = json.dumps(error.diagnostic)
        for secret in ("safe_db", "bcs_session_files", "private-secret", "ForgedError"):
            self.assertNotIn(secret, encoded)

    def test_metadata_timeout_and_tool_failure_report_only_closed_enums(self):
        for method, failure, operation, category in (
                ("db_list", AlisdDbQueryTimeout("private-secret"), "unknown", "timeout"),
                ("table_list", AlisdDbQueryError("private-secret"), "unknown", "tool_failure"),
                ("table_describe", AlisdDbQueryError("private-secret"),
                 "unknown", "tool_failure")):
            with self.subTest(method=method):
                client = Client()
                setattr(client, method, lambda *_, failure=failure: (_ for _ in ()).throw(failure))
                self.assert_sealed(self.admission_error(self.observer(client)), operation, category)

    def test_arbitrary_metadata_failure_keeps_stage_operation_but_not_forged_category(self):
        client = Client()
        client.db_list = lambda: (_ for _ in ()).throw(
            ForgedError("private-secret safe_db bcs_session_files"))
        self.assert_sealed(self.admission_error(self.observer(client)), "unknown", "unknown")

    def test_malformed_metadata_and_binding_rejection_are_bounded(self):
        for method, response, operation in (
                ("db_list", {"success": True, "data": "safe_db"}, "unknown"),
                ("table_list", {"success": True, "data": {}}, "unknown"),
                ("table_describe", {"success": True, "columns": "private-secret"},
                 "unknown")):
            with self.subTest(method=method):
                client = Client()
                setattr(client, method, lambda *_, response=response: response)
                self.assert_sealed(
                    self.admission_error(self.observer(client)), operation, "metadata_contract")
        rejected = lambda database, environment: {
            "status": "REJECTED", "database": database, "environment": environment,
            "reason": "private-secret",
        }
        self.assert_sealed(
            self.admission_error(self.observer(binding=rejected)),
            "unknown", "metadata_contract")

    def test_physical_binding_config_rejection_is_review_rejected_but_unknown_stays_unknown(self):
        def rejected(_database, _environment):
            raise ConfigError("private-secret expired mapping")

        def unknown(_database, _environment):
            raise ForgedError("private-secret changed mapping")

        self.assert_sealed(
            self.admission_error(self.observer(binding=rejected)),
            "unknown", "metadata_contract")
        self.assert_sealed(
            self.admission_error(self.observer(binding=unknown)),
            "unknown", "metadata_contract")

    def test_unhashable_forged_diagnostic_is_rejected_without_raising(self):
        for diagnostic in (
                {"stage": "observer_admission", "operation": [], "category": "unknown"},
                {"stage": "observer_admission", "operation": "unknown", "category": {}},
        ):
            with self.subTest(diagnostic=diagnostic):
                error = ObserverAdmissionError("private-secret", diagnostic)
                self.assertIsNone(observer_admission_diagnostic(error))

    def test_arbitrary_forged_exception_cannot_choose_diagnostic(self):
        class Provider:
            def admit(self, _assertions):
                raise ForgedError("private-secret safe_db bcs_session_files")

        self.assert_sealed(self.admission_error(Provider()), "unknown", "unknown")

    def test_local_mock_admission_and_cancellation_are_unchanged(self):
        local = load(ROOT / "profiles/local.mock.json")
        admitted = AdmittedObserver(
            LocalMockAdapter(local), local, "PRE-03", resource_scope=lambda *_: ())
        self.assertEqual(admitted.source, "local-semantic")

        class CancelledProvider:
            def admit(self, _assertions):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            AdmittedObserver(
                Adapter(CancelledProvider()), self.profile, "PRE-03", resource_scope=lambda *_: ())

        client = Client()
        client.db_list = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            AdmittedObserver(
                Adapter(self.observer(client)), self.profile, "PRE-03", resource_scope=lambda *_: ())

    def test_retained_failure_includes_diagnostic_only_for_observer_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "run"
            local = load(ROOT / "profiles/local.mock.json")
            local["policy"]["fixture_pool"] = "diagnostic-pool"
            run_id = "diagnostic-run"
            with patch("bcs_e2e.pool.pool_root", return_value=root / "registry"):
                prepare_run(local, state, run_id, manifest=current_manifest(SUITE))
                error = ObserverAdmissionError("sealed", {
                    "stage": "observer_admission", "operation": "table_describe",
                    "category": "metadata_contract",
                })
                with patch("bcs_e2e.retained.AdmittedObserver", side_effect=error):
                    report = run_retained_case(local, state, run_id, "RETAIN-GROUP-01")
                self.assertEqual(report["functional"], "NOT_RUN")
                self.assertEqual(report["failure"], {
                    "phase": "pre-effect-admission",
                    "error_type": "ObserverAdmissionError",
                    "diagnostic": error.diagnostic,
                })
                # The pre-effect failure receipt is a scheduling handoff record,
                # not a Case report, so no assertion/evidence arrays are forged.
                self.assertNotIn("assertions", report)
                self.assertNotIn("evidence", report)
                self.assertTrue(pool_path(pool_scope(local)).exists())


if __name__ == "__main__":
    unittest.main()
