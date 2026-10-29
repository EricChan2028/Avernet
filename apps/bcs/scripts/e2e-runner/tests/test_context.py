from __future__ import annotations

import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
import contextlib
import io
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.context import create_ephemeral_context, remove_ephemeral_context
from bcs_e2e.cli import main
from bcs_e2e.profile import ConfigError, fingerprint, validate


ROOT = Path(__file__).resolve().parents[1]


def profile() -> dict:
    value = json.loads((ROOT / "profiles/preprod.example.json").read_text())
    value["environment"].update(id="test-pre", expected_bcs_env="pre")
    value["human"]["user_id"] = "owner-1"
    for role, bot in zip(("driver", "member"), ("bot-a", "bot-b")):
        value["actors"][role].update(bot_id=bot, owner_id="owner-1")
    value["observer"].update(server="alisddbquery", database="agentclawdb")
    value["policy"]["exclusive_use_confirmed"] = True
    return validate(value)


class EphemeralContextTests(unittest.TestCase):
    def test_create_copies_validated_profile_and_remove_requires_marker(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp) / "context"
            p = profile()
            created = create_ephemeral_context(p, root)
            self.assertEqual(created["status"], "CREATED")
            self.assertTrue(root.is_dir())
            self.assertEqual(json.loads((root / "profile.json").read_text()), p)
            marker = json.loads((root / "context.json").read_text())
            self.assertEqual(marker["profile_digest"], fingerprint(p))
            self.assertEqual((root.stat().st_mode & 0o777), 0o700)
            removed = remove_ephemeral_context(root)
            self.assertEqual(removed["context_id"], created["context_id"])
            self.assertFalse(root.exists())

    def test_create_rejects_home_and_nonempty_or_symlink_destinations(self):
        p = profile()
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            outside = Path(tmp)
            with self.assertRaisesRegex(ConfigError, "user home"):
                create_ephemeral_context(p, Path.home() / "bcs-e2e-context-test")
            nonempty = outside / "nonempty"
            nonempty.mkdir()
            (nonempty / "keep").write_text("do not remove")
            with self.assertRaisesRegex(ConfigError, "empty"):
                create_ephemeral_context(p, nonempty)
            target = outside / "target"
            target.mkdir()
            link = outside / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            with self.assertRaisesRegex(ConfigError, "symlink"):
                create_ephemeral_context(p, link)
            self.assertTrue(target.is_dir())

    def test_remove_rejects_tampered_marker_or_profile(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp) / "context"
            p = profile()
            create_ephemeral_context(p, root)
            marker = json.loads((root / "context.json").read_text())
            marker["profile_digest"] = "0" * 64
            (root / "context.json").write_text(json.dumps(marker))
            with self.assertRaisesRegex(ConfigError, "does not match"):
                remove_ephemeral_context(root)
            self.assertTrue(root.exists())

    def test_remove_never_accepts_unmarked_directory(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp) / "unmarked"
            root.mkdir()
            with self.assertRaisesRegex(ConfigError, "marker"):
                remove_ephemeral_context(root)
            self.assertTrue(root.exists())

    def test_remove_preserves_unexpected_files_and_nested_run_state(self):
        for extra in ("notes.txt", "run.sqlite", "run"):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "context"
                create_ephemeral_context(profile(), root)
                target = root / extra
                if extra == "run":
                    target.mkdir()
                    target = target / "run.json"
                target.write_text("must remain")
                original = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
                with self.assertRaises(ConfigError):
                    remove_ephemeral_context(root)
                self.assertEqual(target.read_text(), "must remain")
                for name, content in original.items():
                    self.assertEqual((root / name).read_bytes(), content)

    def test_remove_rejects_linked_metadata_before_deleting_anything(self):
        for name in ("context.json", "profile.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "context"
                create_ephemeral_context(profile(), root)
                target = Path(tmp) / "outside.json"
                target.write_bytes((root / name).read_bytes())
                original = target.read_bytes()
                (root / name).unlink()
                (root / name).symlink_to(target)
                with self.assertRaises(ConfigError):
                    remove_ephemeral_context(root)
                self.assertEqual(target.read_bytes(), original)
                self.assertTrue((root / name).is_symlink())
                self.assertEqual(len(list(root.iterdir())), 2)

    def test_copied_context_marker_does_not_authorize_another_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, copied = Path(tmp) / "context", Path(tmp) / "copy"
            create_ephemeral_context(profile(), root)
            copied.mkdir()
            for name in ("context.json", "profile.json"):
                (copied / name).write_bytes((root / name).read_bytes())
            with self.assertRaises(ConfigError):
                remove_ephemeral_context(copied)
            self.assertEqual(len(list(copied.iterdir())), 2)
            self.assertEqual(len(list(root.iterdir())), 2)

    def test_dangling_context_root_is_rejected_without_creating_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, link = Path(tmp) / "absent", Path(tmp) / "link"
            link.symlink_to(target)
            with self.assertRaises(ConfigError):
                create_ephemeral_context(profile(), link)
            self.assertFalse(target.exists())

    def test_legacy_unbound_marker_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "context"
            create_ephemeral_context(profile(), root)
            marker = json.loads((root / "context.json").read_text())
            del marker["context_dir"]
            (root / "context.json").write_text(json.dumps(marker))
            with self.assertRaisesRegex(ConfigError, "legacy"):
                remove_ephemeral_context(root)
            self.assertEqual(len(list(root.iterdir())), 2)

    def test_late_unrelated_file_is_not_recursively_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "context"
            create_ephemeral_context(profile(), root)
            original_rmdir = os.rmdir
            def concurrent_file(path):
                (root / "new-report.json").write_text("preserve report")
                original_rmdir(path)
            with patch("bcs_e2e.context.os.rmdir", side_effect=concurrent_file):
                with self.assertRaisesRegex(ConfigError, "incomplete"):
                    remove_ephemeral_context(root)
            self.assertEqual((root / "new-report.json").read_text(), "preserve report")

    def test_private_files_are_created_private_even_with_permissive_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "context"
            previous = os.umask(0)
            try:
                create_ephemeral_context(profile(), root)
            finally:
                os.umask(previous)
            for name in ("profile.json", "context.json"):
                self.assertEqual((root / name).stat().st_mode & 0o777, 0o600)

    def test_cli_context_create_and_remove_do_not_need_credentials(self):
        p = profile()
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            source = Path(tmp) / "profile.json"
            source.write_text(json.dumps(p))
            root = Path(tmp) / "cli-context"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["context", "create", "--profile", str(source),
                                       "--context-dir", str(root)]), 0)
            created = json.loads(output.getvalue())
            self.assertEqual(created["status"], "CREATED")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["context", "remove", "--context-dir", str(root)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "REMOVED")


if __name__ == "__main__":
    unittest.main()
