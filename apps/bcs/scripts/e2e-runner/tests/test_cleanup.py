from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cleanup import execute_cleanup
from bcs_e2e.ledger import Ledger
from bcs_e2e.execution_ledger import ExecutionLedger


class FakeAdapter:
    def __init__(self, *, already_deleted: set[tuple[str, str]] | None = None):
        self.calls: list[tuple] = []
        self.already_deleted = already_deleted or set()

    def verify_cleanup_scope(self, run_id, records):
        # Explicit verifier fake for executor control-flow tests only.
        return {"status": "VERIFIED"}

    def _delete(self, kind: str, resource_id: str):
        self.calls.append((f"{kind}.delete", resource_id))
        if (kind, resource_id) in self.already_deleted:
            raise RuntimeError("simulated lost delete response")
        return {"deleted": True}, {}

    def _verify(self, kind: str, resource_id: str):
        self.calls.append((f"{kind}.verify_deleted", resource_id))
        return None, {}

    def group_delete(self, resource_id):
        return self._delete("group", resource_id)

    def group_verify_deleted(self, resource_id):
        return self._verify("group", resource_id)

    def session_delete(self, resource_id):
        return self._delete("session", resource_id)

    def session_verify_deleted(self, resource_id):
        return self._verify("session", resource_id)

    def file_delete(self, session_id, resource_id):
        self.calls.append(("file.delete", session_id, resource_id))
        if ("file", resource_id) in self.already_deleted:
            raise RuntimeError("simulated lost delete response")
        return {"deleted": True}, {}

    def file_verify_deleted(self, session_id, resource_id):
        self.calls.append(("file.verify_deleted", session_id, resource_id))
        return None, {}


def build_ledger(path: Path, *, include_file: bool = True) -> Ledger:
    ledger = Ledger(path, "cleanup-run", "a" * 64, create=True)
    group = ledger.intent("PRE-03", 1, "group", "req-group")
    ledger.observed(group, "group-1", origin="created")
    session = ledger.intent("PRE-03", 1, "session", "req-session", group)
    ledger.observed(session, "session-1", origin="created")
    if include_file:
        file = ledger.intent("PRE-03", 1, "file", "req-file", session)
        ledger.observed(file, "file-1", origin="created")
    return ledger


class CleanupTests(unittest.TestCase):
    def test_dry_run_never_calls_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(Path(tmp) / "run.sqlite")
            adapter = FakeAdapter()
            try:
                result = execute_cleanup(ledger, adapter, dry_run=True)
            finally:
                ledger.close()
            self.assertEqual(result["status"], "DRY_RUN")
            self.assertEqual(adapter.calls, [])

    def test_exact_child_first_cleanup_and_idempotent_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(Path(tmp) / "run.sqlite")
            adapter = FakeAdapter()
            try:
                result = execute_cleanup(ledger, adapter, dry_run=False)
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(
                    adapter.calls,
                    [
                        ("file.delete", "session-1", "file-1"),
                        ("file.verify_deleted", "session-1", "file-1"),
                        ("session.delete", "session-1"),
                        ("session.verify_deleted", "session-1"),
                        ("group.delete", "group-1"),
                        ("group.verify_deleted", "group-1"),
                    ],
                )
                self.assertEqual(ledger.cleanup_plan()["status"], "PLANNED")
                self.assertEqual(ledger.cleanup_plan()["resources"], [])
                calls = list(adapter.calls)
                self.assertEqual(execute_cleanup(ledger, adapter, dry_run=False)["status"], "PASS")
                self.assertEqual(adapter.calls, calls)
            finally:
                ledger.close()

    def test_new_execution_contract_requires_persisted_termination_before_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(Path(tmp) / "run.sqlite", include_file=False)
            effects = ExecutionLedger(ledger)
            handle = effects.intent("PRE-03", 1, "context", "effect-request", session_id="session-1")
            effects.observe(handle, "effect-1")
            effects.transition(handle, "QUEUED", reason="queued")
            effects.transition(handle, "COMPLETED", reason="controlled completion")
            adapter = FakeAdapter()
            try:
                result = execute_cleanup(ledger, adapter, dry_run=False)
            finally:
                ledger.close()
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["execution_safety"]["status"], "BLOCKED")
            self.assertEqual(adapter.calls, [])

    def test_unknown_resource_blocks_without_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(Path(tmp) / "run.sqlite", include_file=False)
            group = ledger.intent("PRE-03", 1, "group", "req-unknown")
            ledger.unknown(group)
            adapter = FakeAdapter()
            try:
                result = execute_cleanup(ledger, adapter, dry_run=False)
            finally:
                ledger.close()
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(adapter.calls, [])

    def test_delete_errors_never_converge_via_a_followup_not_found(self):
        for error in (PermissionError("HTTP 403"), RuntimeError("HTTP 401"),
                      TimeoutError("response lost"), RuntimeError("unclassified HTTP 404")):
            with self.subTest(error=str(error)), tempfile.TemporaryDirectory() as tmp:
                ledger = build_ledger(Path(tmp) / "run.sqlite")
                adapter = FakeAdapter()
                try:
                    with patch.object(adapter, "file_delete", side_effect=error), \
                         patch.object(adapter, "file_verify_deleted", return_value=(None, {})) as verify:
                        result = execute_cleanup(ledger, adapter, dry_run=False)
                    self.assertEqual(result["status"], "BLOCKED")
                    self.assertEqual(result["failed"]["resource_id"], "file-1")
                    self.assertEqual(len(result["remaining"]), 3)
                    verify.assert_not_called()
                    self.assertEqual(adapter.calls, [])
                finally:
                    ledger.close()

    def test_explicit_unsuccessful_delete_blocks_even_if_verify_would_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(Path(tmp) / "run.sqlite")
            adapter = FakeAdapter()
            try:
                with patch.object(adapter, "file_delete", return_value=({"deleted": False}, {})), \
                     patch.object(adapter, "file_verify_deleted") as verify:
                    result = execute_cleanup(ledger, adapter, dry_run=False)
                self.assertEqual(result["status"], "BLOCKED")
                verify.assert_not_called()
                self.assertEqual(adapter.calls, [])
            finally:
                ledger.close()

    def test_partial_failure_reports_current_ledger_and_does_not_delete_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(Path(tmp) / "run.sqlite")
            adapter = FakeAdapter()
            try:
                with patch.object(adapter, "session_delete", side_effect=PermissionError("denied")):
                    result = execute_cleanup(ledger, adapter, dry_run=False)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual([item["resource_id"] for item in result["resources"]], ["file-1"])
                self.assertEqual({item["resource_id"] for item in result["remaining"]},
                                 {"group-1", "session-1"})
                self.assertEqual(adapter.calls, [("file.delete", "session-1", "file-1"),
                                                ("file.verify_deleted", "session-1", "file-1")])
                retry = execute_cleanup(ledger, adapter, dry_run=False)
                self.assertEqual(retry["status"], "PASS")
                self.assertEqual([item["resource_id"] for item in retry["resources"]],
                                 ["session-1", "group-1"])
            finally:
                ledger.close()

    def test_verify_failure_retains_child_and_stops_parent_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(Path(tmp) / "run.sqlite")
            adapter = FakeAdapter()
            try:
                with patch.object(adapter, "file_verify_deleted", side_effect=TimeoutError):
                    result = execute_cleanup(ledger, adapter, dry_run=False)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(len(result["remaining"]), 3)
                self.assertEqual(adapter.calls, [("file.delete", "session-1", "file-1")])
            finally:
                ledger.close()

    def test_final_scope_failure_prevents_success_even_after_all_known_deletions(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_ledger(Path(tmp) / "run.sqlite")
            adapter = FakeAdapter()
            try:
                with patch.object(adapter, "verify_cleanup_scope", side_effect=[
                    {"status": "VERIFIED"}, RuntimeError("inventory changed")
                ]):
                    result = execute_cleanup(ledger, adapter, dry_run=False)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(result["phase"], "FINAL_VERIFICATION")
                self.assertEqual(len(result["resources"]), 3)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
