"""A ledger alone cannot authorize deletion or erase unaccounted mock resources."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cleanup import execute_cleanup
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.mock_state import MockStateError
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.pool import PoolBusyError, PoolLease
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import read


class CleanupInventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "run"
        self.profile_path = ROOT / "profiles/local.mock.json"
        self.profile = load(self.profile_path)
        root_patch = patch("bcs_e2e.pool.pool_root", return_value=self.root / "pools")
        root_patch.start()
        self.addCleanup(root_patch.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["prepare", "--profile", str(self.profile_path), "--run-id", "scope-run",
                                   "--state-dir", str(self.state)]), 0)
        self.adapter = PersistentLocalMockAdapter(self.profile, self.state, case_id="PRE-03")
        self.ledger = Ledger(self.state / "run.sqlite", "scope-run", fingerprint(self.profile))
        self.addCleanup(self.ledger.close)
        self.initial_sessions = {}
        self.group_handle, self.group = self.create_group("group")
        self.session_handle, self.session = self.create_session("session", self.group_handle, self.group)
        self.file_handle = self.ledger.intent("PRE-03", 1, "file", "create-file", self.session_handle)
        file, _ = self.adapter.file_prepare(self.session, {"file_name": "small.txt", "size": 3, "mime_type": "text/plain"})
        self.file = file["file_id"]
        self.ledger.observed(self.file_handle, self.file, origin="created")
        self.adapter.file_upload(self.session, self.file, b"abc")
        self.adapter.file_complete(self.session, self.file)
        self.adapter.file_share(self.session, self.file)

    def create_group(self, label):
        handle, initial = self.ledger.group_creation_intent("PRE-03", 1, "create-" + label)
        value, _ = self.adapter.group_create({"driver_bot_uuid": "bot_local_driver"})
        self.ledger.observed(handle, value["group_id"], origin="created")
        self.ledger.observed(initial, value["initial_session_id"], origin="created")
        self.initial_sessions[initial] = value["initial_session_id"]
        return handle, value["group_id"]

    def create_session(self, label, parent, group_id):
        handle = self.ledger.intent("PRE-03", 1, "session", "create-" + label, parent)
        value, _ = self.adapter.session_create(group_id, {"title": "fixture"})
        self.ledger.observed(handle, value["session_id"], origin="created")
        return handle, value["session_id"]

    def sql(self, statement, args=()):
        with self.ledger.db:
            self.ledger.db.execute(statement, args)

    def assert_blocked_without_deletion(self):
        fresh = PersistentLocalMockAdapter(self.profile, self.state)
        before = fresh.store.path.read_bytes()
        with patch.object(fresh, "group_delete") as group, patch.object(fresh, "session_delete") as session, \
             patch.object(fresh, "file_delete") as file:
            result = execute_cleanup(self.ledger, fresh, dry_run=False)
        self.assertEqual(result["status"], "BLOCKED")
        group.assert_not_called()
        session.assert_not_called()
        file.assert_not_called()
        self.assertEqual(fresh.store.path.read_bytes(), before)
        lock = Path(read(self.state / "run.json")["lock_path"])
        with self.assertRaises(PoolBusyError):
            PoolLease.acquire(lock, "another-run", fingerprint(self.profile))
        return result

    def test_cross_group_session_parent_blocks_before_first_child_delete(self):
        other, _ = self.create_group("other-group")
        self.sql("UPDATE resource SET parent=? WHERE handle=?", (other, self.session_handle))
        self.assert_blocked_without_deletion()

    def test_cross_session_file_parent_blocks_live_and_tombstoned_file(self):
        other_handle, other_id = self.create_session("other-session", self.group_handle, self.group)
        self.sql("UPDATE resource SET parent=? WHERE handle=?", (other_handle, self.file_handle))
        self.assert_blocked_without_deletion()
        self.adapter.file_delete(self.session, self.file)
        self.assert_blocked_without_deletion()
        fresh = PersistentLocalMockAdapter(self.profile, self.state)
        for action in (fresh.file_delete, fresh.file_verify_deleted):
            with self.assertRaisesRegex(MockStateError, "different session"):
                action(other_id, self.file)

    def test_omitted_leaf_resource_is_not_silently_adopted_or_deleted(self):
        self.sql("DELETE FROM resource WHERE handle=?", (self.file_handle,))
        self.assert_blocked_without_deletion()

    def test_forged_ledger_deletion_and_case_attempt_are_rejected(self):
        for field, invalid in (("state", "deleted"), ("case_id", "OTHER"), ("attempt", 2), ("kind", "unexpected")):
            with self.subTest(field=field):
                previous = self.ledger.db.execute(f"SELECT {field} FROM resource WHERE handle=?", (self.file_handle,)).fetchone()[0]
                self.sql(f"UPDATE resource SET {field}=? WHERE handle=?", (invalid, self.file_handle))
                self.assert_blocked_without_deletion()
                self.sql(f"UPDATE resource SET {field}=? WHERE handle=?", (previous, self.file_handle))

    def test_empty_ledger_does_not_hide_live_inventory_from_cleanup_or_release(self):
        for handle in (self.file_handle, self.session_handle, *self.initial_sessions, self.group_handle):
            self.sql("DELETE FROM resource WHERE handle=?", (handle,))
        self.assert_blocked_without_deletion()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(["release", "--state-dir", str(self.state)])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")

    def test_borrowed_and_reused_roots_remain_visible_and_quarantine_pool(self):
        for origin in ("borrowed", "reused"):
            with self.subTest(origin=origin):
                handle = self.ledger.intent("PRE-03", 1, "group", "borrow-" + origin)
                self.ledger.observed(handle, "retained-" + origin, origin=origin)
                result = self.assert_blocked_without_deletion()
                self.assertIn(handle, result["retained_handles"])
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = main(["cleanup", "--profile", str(self.profile_path), "--state-dir", str(self.state),
                               "--allow-write", "--confirm-run-id", "scope-run"])
                self.assertEqual(rc, 2)
                self.assertIn(handle, json.loads(output.getvalue())["retained_handles"])
                self.assertTrue(Path(read(self.state / "run.json")["lock_path"]).exists())

    def test_borrowed_child_blocks_all_deletions(self):
        self.sql("UPDATE resource SET origin='borrowed' WHERE handle=?", (self.file_handle,))
        self.assert_blocked_without_deletion()

    def test_created_ledger_flag_does_not_override_independent_borrowed_origin(self):
        snapshot = read(self.adapter.store.path)
        snapshot["provenance"]["files"][self.file]["origin"] = "borrowed"
        snapshot["generation"] += 1
        self.adapter.store.save(snapshot, self.adapter.generation)
        self.assert_blocked_without_deletion()

    def test_adapter_deletion_ahead_of_ledger_converges_with_fresh_adapter(self):
        self.adapter.file_delete(self.session, self.file)
        fresh = PersistentLocalMockAdapter(self.profile, self.state)
        self.assertEqual(execute_cleanup(self.ledger, fresh, dry_run=False)["status"], "PASS")
        snapshot = fresh.store.load()
        self.assertEqual(snapshot["deleted_files"], {self.file})
        self.assertEqual(snapshot["deleted_sessions"], {self.session, *self.initial_sessions.values()})
        self.assertEqual(snapshot["deleted"], {self.group})
        before = fresh.store.path.read_bytes()
        self.assertEqual(execute_cleanup(self.ledger, fresh, dry_run=False)["status"], "PASS")
        self.assertEqual(fresh.store.path.read_bytes(), before)

    def test_direct_parent_deletion_and_unknown_ids_fail_independent_of_ledger(self):
        for action in (lambda: self.adapter.group_delete(self.group),
                       lambda: self.adapter.session_delete(self.session),
                       lambda: self.adapter.file_delete(self.session, "unknown-file")):
            with self.assertRaises(MockStateError):
                action()

    def test_unsupported_adapter_is_not_granted_delete_authority(self):
        result = execute_cleanup(self.ledger, object(), dry_run=False)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["phase"], "SCOPE_VERIFICATION")

    def test_unclosed_mock_subscription_blocks_cleanup_without_deleting_resources(self):
        token = self.adapter.session_token(self.session)[0]["token"]
        self.adapter.session_ws_connect(self.group, self.session, token)
        self.assert_blocked_without_deletion()

    def test_remote_cleanup_is_blocked_before_adapter_construction(self):
        # Separate prepared remote Run, with syntactically resolved public fixture values.
        remote = load(ROOT / "profiles/preprod.example.json")
        remote["environment"]["expected_bcs_env"] = "pre"
        remote["human"]["user_id"] = "owner-test"
        for role in ("driver", "member"):
            remote["actors"][role].update(bot_id="bot-" + role, owner_id="owner-test")
        remote["observer"].update(server="test-observer", database="test-database")
        remote["policy"]["exclusive_use_confirmed"] = True
        path = self.root / "remote.json"
        path.write_text(json.dumps(remote))
        state = self.root / "remote-run"
        args = ["--profile", str(path), "--state-dir", str(state)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["prepare", *args, "--run-id", "remote-run"]), 0)
        output = io.StringIO()
        with patch("bcs_e2e.cli.adapter_for") as factory, contextlib.redirect_stdout(output):
            rc = main(["cleanup", *args, "--allow-write", "--confirm-run-id", "remote-run"])
        self.assertEqual(rc, 2)
        self.assertIn("independent reviewed scope verifier",
                       json.loads(output.getvalue())["reason"])
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
