from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.run_state import RunStateError, read, write


class StateIOTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "state.json"
        self.original = {"schema_version": 1, "status": "OLD"}
        write(self.path, self.original)

    def test_atomic_replacement_is_private_and_unicode_roundtrips(self):
        updated = {"schema_version": 1, "status": "NEW", "label": "本地模拟"}
        write(self.path, updated)
        self.assertEqual(read(self.path), updated)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob(".*.tmp-*")), [])

    def test_write_or_replace_failure_preserves_last_complete_state_and_other_temp_files(self):
        foreign = self.root / ".state.json.tmp-other-writer"
        foreign.write_text("keep")
        for target in ("os.fsync", "os.replace"):
            with self.subTest(target=target), patch("bcs_e2e.run_state." + target, side_effect=OSError("failure")):
                with self.assertRaises(OSError):
                    write(self.path, {"schema_version": 1, "status": "NEW"})
            self.assertEqual(read(self.path), self.original)
            self.assertEqual(list(self.root.glob(".*.tmp-*")), [foreign])

    def test_serialization_failure_does_not_touch_existing_state(self):
        with self.assertRaises(TypeError):
            write(self.path, {"schema_version": 1, "bad": object()})
        self.assertEqual(read(self.path), self.original)
        self.assertEqual(list(self.root.glob(".*.tmp-*")), [])

    def test_symlink_and_nonregular_state_are_never_followed_or_replaced(self):
        for target in (self.path, self.root / "missing"):
            link = self.root / "link.json"
            link.symlink_to(target)
            try:
                with self.assertRaises(RunStateError):
                    write(link, {"schema_version": 1})
                with self.assertRaises(RunStateError):
                    read(link)
                self.assertTrue(link.is_symlink())
                self.assertEqual(read(self.path), self.original)
            finally:
                link.unlink()
        directory = self.root / "directory"
        directory.mkdir()
        for action in (lambda: read(directory), lambda: write(directory, {"schema_version": 1})):
            with self.assertRaises(RunStateError):
                action()

    def test_duplicate_keys_boolean_schema_and_oversized_state_are_rejected(self):
        for value in ('{"schema_version":1,"status":"A","status":"B"}', '{"schema_version":true}'):
            self.path.write_text(value)
            with self.assertRaises(RunStateError):
                read(self.path)
        self.path.write_text(json.dumps({"schema_version": 1, "payload": "large"}))
        with patch("bcs_e2e.run_state.MAX_STATE_BYTES", 8):
            with self.assertRaises(RunStateError):
                read(self.path)
            with self.assertRaises(RunStateError):
                write(self.path, {"schema_version": 1})


if __name__ == "__main__":
    unittest.main()
