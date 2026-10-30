from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.inventory import freeze_manifest, executor_digest
from bcs_e2e.ledger import Ledger
from bcs_e2e.profile import fingerprint
from bcs_e2e.reporting import build_report, render_junit, render_markdown, write_reports


ROOT = Path(__file__).resolve().parents[1]


class ReportingTests(unittest.TestCase):
    def profile(self):
        return json.loads((ROOT / "profiles/local.mock.json").read_text())

    def write_run(self, state: Path):
        state.mkdir(mode=0o700)
        manifest_digest = freeze_manifest(state, self.manifest())
        (state / "run.json").write_text(json.dumps({
            "schema_version": 1, "status": "COMPLETED", "run_id": "report-run",
            "profile_digest": fingerprint(self.profile()), "ready_to_execute": False,
            "manifest_digest": manifest_digest, "executor_digest": executor_digest(),
        }))

    def write_case(self, state: Path, case_id: str, status: str = "PASS"):
        (state / f"report-{case_id}.json").write_text(json.dumps({
            "schema_version": 1, "status": status, "case_id": case_id,
            "run_id": "report-run", "profile_digest": fingerprint(self.profile()),
            "manifest_digest": fingerprint(self.manifest()), "executor_digest": executor_digest(),
            "case_version": next(c["version"] for c in self.manifest()["cases"] if c["id"] == case_id),
            "functional": "PASS" if status == "PASS" else "FAIL",
            "cleanup": "PASS" if status == "PASS" else "BLOCKED",
            "evidence_status": "LOCAL_SURROGATE" if status == "PASS" else "BLOCKED",
            "evidence_required": next(c["required_evidence"] for c in self.manifest()["cases"] if c["id"] == case_id),
            "semantic_evidence": [{"status": "OBSERVED", "source": "local-semantic"}],
            "assertions": [{"id": assertion_id, "status": status}
                           for assertion_id in next(case["assertions"] for case in self.manifest()["cases"] if case["id"] == case_id)],
            "evidence": [{"operation": "test.read", "method": "GET", "path": "/test",
                          "elapsed_ms": 3.5, "token": "must-not-appear"}],
        }))

    def manifest(self):
        return json.loads((ROOT / "manifest.json").read_text())

    def test_pilot_success_is_not_full_release_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            for case in ("PRE-01", "PRE-02", "PRE-03"):
                self.write_case(state, case)
            summary = build_report(self.profile(), state, self.manifest())
            self.assertEqual(summary["status"], "PILOT_PASS")
            self.assertEqual(summary["scope"]["full_release_status"], "NOT_QUALIFIED")
            self.assertEqual(summary["scope"]["legacy_inventory_count"], 41)
            self.assertEqual(summary["mock_boundary"]["local_mock"], True)
            self.assertNotIn("must-not-appear", json.dumps(summary))
            self.assertIn("not a performance benchmark", summary["timing"]["interpretation"])

    def test_pass_case_with_nonpassing_required_or_extra_assertion_is_blocked_in_every_format(self):
        for status in ('FAIL','BLOCKED','SKIPPED','ERROR','CANCELLED'):
            for extra in (False, True):
                with self.subTest(status=status,extra=extra),tempfile.TemporaryDirectory() as tmp:
                    state=Path(tmp)/'state';self.write_run(state)
                    for case in ('PRE-01','PRE-02','PRE-03'):self.write_case(state,case)
                    path=state/'report-PRE-01.json';report=json.loads(path.read_text())
                    if extra:report['assertions'].append({'id':'PRE-01.additional-business-check','status':status})
                    else:report['assertions'][0]['status']=status
                    path.write_text(json.dumps(report));original=path.read_bytes()
                    summary=build_report(self.profile(),state)
                    self.assertEqual(summary['status'],'BLOCKED')
                    self.assertEqual(summary['integrity']['status'],'BLOCKED')
                    self.assertEqual(summary['cases'][0]['status'],'BLOCKED')
                    self.assertTrue(any('nonpassing assertion' in issue for issue in summary['integrity']['issues']))
                    self.assertIsNotNone(ET.fromstring(render_junit(summary)).find(".//testcase[@name='PRE-01']/skipped"))
                    self.assertIn('| PRE-01 | '+str(self.manifest()['cases'][0]['version'])+' | BLOCKED |',render_markdown(summary))
                    outputs=write_reports(summary,state/'reports',{'all'})
                    self.assertEqual(json.loads(Path(outputs['outputs']['json']).read_text())['status'],'BLOCKED')
                    self.assertEqual(path.read_bytes(),original)

    def test_nonpass_case_remains_its_actual_status_not_rewritten_as_an_integrity_error(self):
        for status in ('FAIL','BLOCKED','SKIPPED','ERROR','CANCELLED'):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as tmp:
                state=Path(tmp)/'state';self.write_run(state)
                for case in ('PRE-01','PRE-02','PRE-03'):self.write_case(state,case,status if case=='PRE-01' else 'PASS')
                summary=build_report(self.profile(),state)
                self.assertEqual(summary['cases'][0]['status'],status)
                self.assertFalse(any('nonpassing assertion' in issue for issue in summary['integrity']['issues']))
                junit=ET.fromstring(render_junit(summary))
                self.assertEqual(junit.get('errors'),'1' if status=='ERROR' else '0')
                node='error' if status=='ERROR' else 'failure' if status=='FAIL' else 'skipped'
                self.assertIsNotNone(junit.find(".//testcase[@name='PRE-01']/"+node))


    def test_report_cli_exits_nonzero_when_pass_case_hides_failed_assertion(self):
        import contextlib
        import io
        from bcs_e2e.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state';self.write_run(state)
            for case in ('PRE-01','PRE-02','PRE-03'):self.write_case(state,case)
            path=state/'report-PRE-01.json';report=json.loads(path.read_text());report['assertions'][0]['status']='FAIL'
            path.write_text(json.dumps(report))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc=main(['report','--profile',str(ROOT/'profiles/local.mock.json'),'--state-dir',str(state),'--format','all'])
            self.assertEqual(rc,2)
            result=json.loads(out.getvalue());self.assertEqual(result['status'],'BLOCKED')
            self.assertEqual(set(result['outputs']),{'json','junit','markdown'})

    def test_cleanup_pass_never_claims_history_or_physical_storage_erasure(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            for case in ("PRE-01", "PRE-02", "PRE-03"):
                self.write_case(state, case)
            summary = build_report(self.profile(), state, self.manifest())
            self.assertEqual(summary['status'], 'PILOT_PASS')
            self.assertEqual(summary['cleanup']['verification_scope'], 'REGISTERED_RESOURCE_API_CONVERGENCE')
            self.assertEqual(summary['cleanup']['verification_mode'], 'LOCAL_SURROGATE')
            self.assertEqual(summary['cleanup']['physical_data_erasure'], 'NOT_VERIFIED')
            self.assertEqual(summary['cleanup']['associated_history_purge'], 'NOT_VERIFIED')
            self.assertEqual(summary['cleanup']['retention_disposition'], 'ENVIRONMENT_OWNER_REVIEW_REQUIRED')
            markdown = render_markdown(summary)
            self.assertIn('not complete physical erasure', markdown)
            self.assertIn('Associated history purge: NOT_VERIFIED', markdown)
            junit = ET.fromstring(render_junit(summary))
            properties = junit.findall('.//property[@name="physical_data_erasure"]')
            self.assertEqual(len(properties), 3)
            self.assertTrue(all(prop.get('value') == 'NOT_VERIFIED' for prop in properties))
            self.assertEqual(summary['reports']['declared_case_count'], 3)

    def test_missing_case_is_blocked_and_all_formats_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            self.write_case(state, "PRE-01")
            summary = build_report(self.profile(), state, self.manifest())
            self.assertEqual(summary["status"], "BLOCKED")
            junit = render_junit(summary)
            self.assertIn("skipped", junit)
            self.assertIn('name="runner_status" value="BLOCKED"', junit)
            self.assertIn("NOT_QUALIFIED", render_markdown(summary))
            result = write_reports(summary, state / "reports", {"all"})
            self.assertEqual(set(result["outputs"]), {"json", "junit", "markdown"})
            self.assertTrue((state / "reports/summary.json").is_file())

    def test_old_case_pass_with_missing_evidence_is_not_success_in_junit(self):
        for evidence in ("BLOCKED", "INCOMPLETE", "NOT_RUN"):
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / "state"
                self.write_run(state)
                for case in ("PRE-01", "PRE-02", "PRE-03"):
                    self.write_case(state, case)
                path = state / "report-PRE-01.json"
                report = json.loads(path.read_text())
                report["evidence_status"] = evidence
                path.write_text(json.dumps(report))
                summary = build_report(self.profile(), state, self.manifest())
                self.assertEqual(summary["status"], "BLOCKED")
                self.assertEqual(summary["cases"][0]["status"], "BLOCKED")
                junit = ET.fromstring(render_junit(summary))
                case = junit.find(".//testcase[@name='PRE-01']")
                self.assertIsNotNone(case.find("skipped"))

    def test_report_integrity_rejects_foreign_unknown_and_mismatched_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            for case in ("PRE-01", "PRE-02", "PRE-03"):
                self.write_case(state, case)
            (state / "report-UNKNOWN.json").write_text(json.dumps({"case_id": "UNKNOWN"}))
            foreign = json.loads((state / "report-PRE-01.json").read_text())
            foreign["run_id"] = "other-run"
            (state / "report-PRE-01.json").write_text(json.dumps(foreign))
            summary = build_report(self.profile(), state, self.manifest())
            self.assertEqual(summary["status"], "BLOCKED")
            self.assertEqual(summary["integrity"]["status"], "BLOCKED")
            self.assertTrue(any("unknown report" in item for item in summary["integrity"]["issues"]))
            self.assertTrue(any("run mismatch" in item for item in summary["integrity"]["issues"]))

    def test_report_integrity_rejects_duplicate_and_missing_pass_assertions(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            for case in ("PRE-01", "PRE-02", "PRE-03"):
                self.write_case(state, case)
            value = json.loads((state / "report-PRE-01.json").read_text())
            value["assertions"] = [{"id": "PRE-01.one", "status": "PASS"},
                                    {"id": "PRE-01.one", "status": "PASS"}]
            (state / "report-PRE-01.json").write_text(json.dumps(value))
            summary = build_report(self.profile(), state, self.manifest())
            self.assertEqual(summary["status"], "BLOCKED")
            self.assertTrue(any("duplicate assertion" in item for item in summary["integrity"]["issues"]))
            self.assertEqual(summary["reports"]["expected_assertion_count"],
                             sum(len(case["assertions"]) for case in self.manifest()["cases"]))

    def test_summary_exposes_safe_ledger_correlations(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            ledger_path = state / "run.sqlite"
            (state / "run.json").write_text(json.dumps({
                "schema_version": 1, "status": "COMPLETED", "run_id": "report-run",
                "profile_digest": fingerprint(self.profile()), "ready_to_execute": False,
                "ledger_path": str(ledger_path),
                "manifest_digest": fingerprint(self.manifest()), "executor_digest": executor_digest(),
            }))
            with Ledger(ledger_path, "report-run", fingerprint(self.profile()), create=True) as ledger:
                ledger.record_correlation("PRE-01", 1, {
                    "operation": "group.create", "method": "POST", "path": "/groups",
                    "submitted_request_id": "wire-1", "response_request_id": "server-1",
                    "body": "must-not-be-stored",
                })
                ledger.record_diagnostic("PRE-01", 1, {
                    "status": "INCOMPLETE", "source": "logs",
                    "query": {"environment": "local", "request_id": "wire-1",
                              "start_epoch": 1, "end_epoch": 2},
                    "record_count": 100, "truncated": True,
                })
            for case in ("PRE-01", "PRE-02", "PRE-03"):
                self.write_case(state, case)
            summary = build_report(self.profile(), state, self.manifest())
            self.assertEqual(summary["status"], "PILOT_PASS")
            self.assertEqual(summary["correlations"][0]["request_id"], "wire-1")
            self.assertEqual(summary["diagnostics"][0]["status"], "INCOMPLETE")
            self.assertEqual(summary["cases"][0]["diagnostic_status"], "INCOMPLETE")
            self.assertNotIn("must-not-be-stored", json.dumps(summary))

    def test_generated_case_and_cleanup_reports_carry_run_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            for case in ("PRE-01", "PRE-02", "PRE-03"):
                self.write_case(state, case)
            self.assertEqual(build_report(self.profile(), state, self.manifest())["integrity"]["status"], "PASS")


    def test_evidence_declaration_mismatch_never_stays_green_in_any_report(self):
        invalid = (None, [], "api", {"api": True}, ["api"], ["api", "api"],
                   ["api", "ob", "trace"], ["api", None], ["api", ["ob"]],
                   ["api", "sqlite"])
        for declared in invalid:
            with self.subTest(declared=declared), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / "state"
                self.write_run(state)
                for case_id in ("PRE-01", "PRE-02", "PRE-03"):
                    self.write_case(state, case_id)
                path = state / "report-PRE-01.json"
                actual = json.loads(path.read_text())
                if declared is None:
                    del actual["evidence_required"]
                else:
                    actual["evidence_required"] = declared
                path.write_text(json.dumps(actual))
                before = path.read_bytes()
                summary = build_report(self.profile(), state)
                case = summary["cases"][0]
                self.assertEqual(summary["status"], "BLOCKED")
                self.assertEqual(case["status"], "BLOCKED")
                self.assertEqual(case["functional"], "PASS")
                self.assertEqual(case["evidence_status"], "LOCAL_SURROGATE")
                self.assertTrue(any("required evidence declaration" in item
                                    for item in summary["integrity"]["issues"]))
                junit = ET.fromstring(render_junit(summary))
                self.assertIsNotNone(junit.find(".//testcase[@name='PRE-01']/skipped"))
                self.assertIn("| PRE-01 | 3 | BLOCKED | PASS |", render_markdown(summary))
                self.assertEqual(path.read_bytes(), before)

    def test_evidence_declaration_error_preserves_original_case_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            self.write_case(state, "PRE-01", "FAIL")
            path = state / "report-PRE-01.json"
            actual = json.loads(path.read_text())
            del actual["evidence_required"]
            path.write_text(json.dumps(actual))
            summary = build_report(self.profile(), state)
            self.assertEqual(summary["cases"][0]["status"], "FAIL")
            self.assertEqual(summary["cases"][0]["functional"], "FAIL")
            self.assertIsNotNone(ET.fromstring(render_junit(summary)).find(
                ".//testcase[@name='PRE-01']/failure"))

    def test_evidence_comparison_uses_frozen_inventory_not_current_execution_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.write_run(state)
            for case_id in ("PRE-01", "PRE-02", "PRE-03"):
                self.write_case(state, case_id)
            # Simulate an already-frozen historical contract. Never admit this
            # altered contract as a new Run through today's finite executors.
            path = state / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["cases"][0]["required_evidence"].append("historical-trace")
            path.write_text(json.dumps(manifest))
            digest = fingerprint(manifest)
            for name in ("run.json", "report-PRE-01.json", "report-PRE-02.json", "report-PRE-03.json"):
                file = state / name
                value = json.loads(file.read_text()); value["manifest_digest"] = digest
                file.write_text(json.dumps(value))
            with patch("bcs_e2e.inventory.current_manifest", side_effect=AssertionError("offline only")), \
                 patch("bcs_e2e.inventory.validate_evidence_policy", side_effect=AssertionError("not admission")):
                summary = build_report(self.profile(), state)
                self.assertEqual(summary["cases"][0]["status"], "BLOCKED")
                actual_path = state / "report-PRE-01.json"
                actual = json.loads(actual_path.read_text())
                actual["evidence_required"] = list(reversed(manifest["cases"][0]["required_evidence"]))
                actual_path.write_text(json.dumps(actual))
                before = {p.name: p.read_bytes() for p in state.iterdir()}
                summary = build_report(self.profile(), state)
                # Equality accepts a recorded historical contract, not today's
                # ability to execute it or independent proof of collection.
                self.assertEqual(summary["status"], "PILOT_PASS")
                self.assertEqual(summary["scope"]["full_release_status"], "NOT_QUALIFIED")
                self.assertEqual({p.name: p.read_bytes() for p in state.iterdir()}, before)

    def test_sqlite_substitution_is_limited_to_owned_local_adapter(self):
        for kind, observer, expected in (("singlebox", "local-sqlite", "sqlite"),
                                         ("mock", "local-semantic", "ob"),
                                         ("singlebox", "alisddbquery", "ob")):
            with self.subTest(kind=kind, observer=observer), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / "state"
                self.write_run(state)
                profile = self.profile()
                profile["entrypoint"]["kind"] = kind
                profile["observer"]["kind"] = observer
                for case_id in ("PRE-01", "PRE-02", "PRE-03"):
                    self.write_case(state, case_id)
                for name in ("run.json", "report-PRE-01.json", "report-PRE-02.json", "report-PRE-03.json"):
                    path = state / name
                    actual = json.loads(path.read_text())
                    actual["profile_digest"] = fingerprint(profile)
                    if name.startswith("report-"):
                        actual["evidence_status"] = "PASS"
                        actual["evidence_required"] = [expected if x == "ob" else x
                                                       for x in actual["evidence_required"]]
                    path.write_text(json.dumps(actual))
                summary = build_report(profile, state)
                self.assertEqual(summary["status"], "PILOT_PASS")
                self.assertIn(expected, summary["cases"][0]["required_evidence"])
                path = state / "report-PRE-01.json"
                actual = json.loads(path.read_text())
                actual["evidence_required"] = ["api", "ob" if expected == "sqlite" else "sqlite"]
                path.write_text(json.dumps(actual))
                self.assertEqual(build_report(profile, state)["cases"][0]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()

class ReportOutputSafetyTests(unittest.TestCase):
    def profile(self):
        return json.loads((ROOT / "profiles/local.mock.json").read_text())

    def summary(self):
        return {"status": "BLOCKED", "run_id": "run", "target": {"tier": "local", "environment_id": "mock"},
                "scope": {"full_release_status": "NOT_QUALIFIED", "legacy_inventory_count": 41},
                "cases": [], "routes": [], "timing": {"interpretation": "not a benchmark"},
                "mock_boundary": {"not_proven": []}}

    def test_string_urls_and_bearer_values_are_redacted(self):
        from bcs_e2e.reporting import redact
        value = "GET https://storage.example.test/x?X-Amz-Signature=secret&token=issued Bearer abc.def.ghi"
        result = redact({"message": value})["message"]
        self.assertNotIn("secret", result)
        self.assertNotIn("issued", result)
        self.assertNotIn("abc.def.ghi", result)
        self.assertIn("[REDACTED]", result)

    def test_report_outputs_are_private_atomic_and_reject_symlinks(self):
        from bcs_e2e.reporting import write_reports
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "reports"
            result = write_reports(self.summary(), directory, {"all"})
            self.assertEqual(set(result["outputs"]), {"json", "junit", "markdown"})
            for path in directory.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            target = Path(tmp) / "outside.txt"
            target.write_text("must remain")
            (directory / "summary.json").unlink()
            (directory / "summary.json").symlink_to(target)
            with self.assertRaises(OSError):
                write_reports(self.summary(), directory, {"json"})
            self.assertEqual(target.read_text(), "must remain")
            self.assertEqual(list(directory.glob(".*.tmp-*")), [])

    def test_invalid_timing_values_are_not_counted(self):
        from bcs_e2e.reporting import _timing
        result = _timing({"case": {"evidence": [{"elapsed_ms": -1}, {"elapsed_ms": float("nan")},
                                                  {"elapsed_ms": 2.5}]}})
        self.assertEqual(result["steps"], 1)
        self.assertEqual(result["total_elapsed_ms"], 2.5)
