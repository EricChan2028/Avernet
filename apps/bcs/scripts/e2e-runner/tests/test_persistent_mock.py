"""Persisted surrogates survive independent processes without remote credentials."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.mock_state import MockStateError, MockStateStore
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.profile import load
from bcs_e2e.run_state import read, write


class PersistentMockTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "run"
        self.registry = self.root / "pools"
        registry = patch("bcs_e2e.pool.pool_root", return_value=self.registry)
        registry.start()
        self.addCleanup(registry.stop)
        self.profile_path = ROOT / "profiles/local.mock.json"
        self.profile = load(self.profile_path)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["prepare", "--profile", str(self.profile_path), "--run-id", "persist-run",
                                   "--state-dir", str(self.state)]), 0)
        self.store = MockStateStore(self.state, "persist-run", self.profile)

    def adapter(self, *, create=False):
        return PersistentLocalMockAdapter(self.profile, self.state, case_id="PRE-03" if create else None)

    def seed(self, adapter):
        group, _ = adapter.group_create({"driver_bot_uuid": "bot_local_driver"})
        session, _ = adapter.session_create(group["group_id"], {"title": "test session"})
        file, _ = adapter.file_prepare(session["session_id"], {"file_name": "fixture.txt", "size": 7,
                                                             "mime_type": "text/plain"})
        adapter.file_upload(session["session_id"], file["file_id"], b"payload")
        adapter.file_complete(session["session_id"], file["file_id"])
        share, _ = adapter.file_share(session["session_id"], file["file_id"])
        return group["group_id"], session["session_id"], file["file_id"], share["share_token"]

    def test_fresh_adapter_reads_file_bytes_shares_and_creation_provenance(self):
        with patch("socket.socket", side_effect=AssertionError("must stay offline")):
            group, session, file, token = self.seed(self.adapter(create=True))
            fresh = self.adapter()
            self.assertEqual(fresh.group_read(group)[0]["group_id"], group)
            self.assertEqual(fresh.file_download(session, file)[0], b"payload")
            self.assertEqual(fresh.shared_file_download(token)[0], b"payload")
        snapshot = self.store.load()
        self.assertEqual(snapshot["provenance"]["files"][file], {
            "case_id": "PRE-03", "attempt": 1, "origin": "created", "parent_id": session})
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("LOCAL_MOCK_UNUSED", self.store.path.read_text())

    def test_read_only_calls_do_not_rewrite_snapshot(self):
        group, session, file, token = self.seed(self.adapter(create=True))
        fresh = self.adapter()
        before = self.store.path.read_bytes()
        fresh.preflight()
        fresh.group_read(group)
        fresh.file_metadata(session, file)
        fresh.file_list(session)
        fresh.shared_file_download(token)
        fresh.semantic_observer().observe("file.persisted", file)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_observation_reads_persisted_state_not_mutated_python_dictionary(self):
        adapter = self.adapter(create=True)
        _group, _session, file, _token = self.seed(adapter)
        adapter.files[file]["size"] = 1234  # Not a committed product operation.
        evidence = adapter.semantic_observer().observe("file.persisted", file)
        self.assertEqual(evidence["rows"][0]["size"], 7)
        self.assertEqual(evidence["source"], "local-semantic")

    def test_stale_adapter_cannot_overwrite_another_completed_write(self):
        first, stale = self.adapter(create=True), self.adapter(create=True)
        group, _ = first.group_create({"driver_bot_uuid": "bot_local_driver"})
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(MockStateError, "stale"):
            stale.group_create({"driver_bot_uuid": "bot_local_driver"})
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(set(self.adapter().groups), {group["group_id"]})

    def test_snapshot_failures_never_return_success_and_poison_the_adapter(self):
        adapter = self.adapter(create=True)
        before = self.store.path.read_bytes()
        with patch.object(adapter.store, "save", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                adapter.group_create({"driver_bot_uuid": "bot_local_driver"})
        self.assertEqual(self.store.path.read_bytes(), before)
        with self.assertRaises(MockStateError):
            adapter.group_create({"driver_bot_uuid": "bot_local_driver"})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_missing_corrupt_or_wrong_binding_never_becomes_an_empty_mock(self):
        original = self.store.path.read_bytes()
        for kind in ("missing", "corrupt", "other-run", "other-profile", "symlink"):
            with self.subTest(kind=kind):
                self.store.path.unlink()
                if kind == "corrupt":
                    self.store.path.write_text("broken-json")
                elif kind in {"other-run", "other-profile"}:
                    value = json.loads(original)
                    value["run_id" if kind == "other-run" else "profile_digest"] = "foreign"
                    write(self.store.path, value)
                elif kind == "symlink":
                    self.store.path.symlink_to(self.state / "run.json")
                with self.assertRaises(MockStateError):
                    self.adapter()
                if self.store.path.exists() or self.store.path.is_symlink():
                    self.store.path.unlink()
                self.store.path.write_bytes(original)

    def test_recovery_adapter_cannot_create_or_reset_store(self):
        before = self.store.path.read_bytes()
        with self.assertRaises(MockStateError):
            self.adapter().group_create({"driver_bot_uuid": "bot_local_driver"})
        with self.assertRaises(MockStateError):
            self.store.create()
        self.assertEqual(self.store.path.read_bytes(), before)

    def child(self, action):
        # Both commands are new interpreters; only the test pool root is injected.
        code = '''
import sys
from pathlib import Path
from unittest.mock import patch
import bcs_e2e.pool as pool
from bcs_e2e.cli import main
from bcs_e2e.mock_state import MockStateError
pool.pool_root = lambda: Path(sys.argv[3])
args = [sys.argv[1], "--profile", sys.argv[4], "--state-dir", sys.argv[2],
        "--allow-write", "--confirm-run-id", "persist-run"]
with patch("socket.socket", side_effect=AssertionError("must stay offline")):
    if sys.argv[1] == "run":
        args += ["--run-id", "persist-run", "--case", "PRE-03"]
        with patch("bcs_e2e.persistent_mock.PersistentLocalMockAdapter.file_delete",
                   side_effect=MockStateError("injected cleanup failure")):
            code = main(args)
    else:
        code = main(args)
raise SystemExit(code)
'''
        return subprocess.run([sys.executable, "-c", code, action, str(self.state),
                               str(self.registry), str(self.profile_path)],
                              env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
                              capture_output=True, text=True, timeout=20)

    def test_failed_case_then_fresh_cleanup_process_blocks_without_quiescence_proof(self):
        failed = self.child("run")
        self.assertEqual(failed.returncode, 2, failed.stderr)
        report = json.loads(failed.stdout)
        self.assertEqual(report["functional"], "PASS")
        self.assertEqual(report["cleanup"], "BLOCKED")
        before = self.store.load()
        self.assertEqual((len(before["groups"]), len(before["sessions"]), len(before["files"])), (1, 2, 1))
        cleanup = self.child("cleanup")
        self.assertEqual(cleanup.returncode, 2, cleanup.stdout + cleanup.stderr)
        self.assertEqual(json.loads(cleanup.stdout)["status"], "BLOCKED")
        after = self.store.load()
        self.assertEqual(after["deleted_files"], set())
        self.assertEqual(after["deleted_sessions"], set())
        self.assertEqual(after["deleted"], set())
        self.assertEqual(after["shared_files"], before["shared_files"])
        self.assertEqual(read(self.state / "run.json")["status"], "FAILED")
        self.assertTrue(Path(read(self.state / "run.json")["lock_path"]).exists())


if __name__ == "__main__":
    unittest.main()
