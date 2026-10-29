from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_panel_asset import CONTRACT, LocalPanelAsset, MAX_ASSET_BYTES
from bcs_e2e.runtime import RuntimeContractError


class LocalPanelAssetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / "source.mjs"
        self.content = b"export const panel = true;\n" + b"x" * 1100
        self.source.write_bytes(self.content)
        self.asset = LocalPanelAsset(
            self.source, hashlib.sha256(self.content).hexdigest())

    def fresh_root(self, name: str = "run") -> Path:
        root = self.base / name
        root.mkdir(mode=0o700)
        return root

    def test_validate_and_install_private_exact_owned_paths(self):
        self.assertIs(self.asset.validate(), self.asset)
        root = self.fresh_root()
        bundle = self.asset.install(root, run_id="run-1")
        owned = root / "panel/index.umd.js"
        receipt_path = root / "panel-asset.json"
        self.assertEqual(bundle, {
            "name": "bcsPanel", "type": "file", "file": str(owned.resolve())})
        self.assertEqual(owned.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE((root / "panel").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(owned.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(receipt_path.read_text()), {
            "schema_version": 1, "run_id": "run-1", "contract": CONTRACT,
            "sha256": self.asset.sha256, "size": len(self.content),
            "owned_path": "panel/index.umd.js",
        })

    def test_wrong_digest_and_malformed_digest_are_rejected(self):
        with self.assertRaisesRegex(RuntimeContractError, "does not match"):
            LocalPanelAsset(self.source, "0" * 64).validate()
        for digest in ("a" * 63, "A" * 64, "g" * 64, 123):
            with self.subTest(digest=digest), self.assertRaisesRegex(
                    RuntimeContractError, "lowercase 64-character"):
                LocalPanelAsset(self.source, digest).validate()

    def test_filename_bounds_and_nonregular_inputs_are_rejected(self):
        wrong_name = self.base / "panel.txt"
        wrong_name.write_bytes(self.content)
        with self.assertRaisesRegex(RuntimeContractError, r"\.js or \.mjs"):
            LocalPanelAsset(wrong_name, hashlib.sha256(self.content).hexdigest()).validate()
        for name, size in (("small.js", 1000), ("large.js", MAX_ASSET_BYTES + 1)):
            path = self.base / name
            path.write_bytes(b"x" * size)
            with self.subTest(size=size), self.assertRaisesRegex(RuntimeContractError, "size|2 MiB"):
                LocalPanelAsset(path, hashlib.sha256(path.read_bytes()).hexdigest()).validate()
        directory = self.base / "directory.js"
        directory.mkdir()
        with self.assertRaises(RuntimeContractError):
            LocalPanelAsset(directory, "0" * 64).validate()

    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        fifo = self.base / "blocked.js"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(RuntimeContractError, "regular"):
            LocalPanelAsset(fifo, "0" * 64).validate()

    def test_symlink_and_hard_link_are_rejected(self):
        symlink = self.base / "linked.js"
        symlink.symlink_to(self.source)
        with self.assertRaisesRegex(RuntimeContractError, "symlink"):
            LocalPanelAsset(symlink, self.asset.sha256).validate()
        hard_link = self.base / "hard.js"
        os.link(self.source, hard_link)
        with self.assertRaisesRegex(RuntimeContractError, "single-link"):
            LocalPanelAsset(hard_link, self.asset.sha256).validate()

    def test_source_mutation_after_validation_is_rejected_and_retained(self):
        self.asset.validate()
        self.source.write_bytes(self.content + b"changed")
        root = self.fresh_root()
        with self.assertRaisesRegex(RuntimeContractError, "SHA256"):
            self.asset.install(root, run_id="mutated")
        self.assertEqual(list(root.iterdir()), [])

    def test_existing_owned_paths_are_never_adopted_or_overwritten(self):
        root = self.fresh_root()
        panel = root / "panel"
        panel.mkdir()
        existing = panel / "index.umd.js"
        existing.write_bytes(b"keep")
        with self.assertRaisesRegex(RuntimeContractError, "never adopt"):
            self.asset.install(root, run_id="existing")
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertFalse((root / "panel-asset.json").exists())

    def test_partial_write_failure_is_preserved_without_receipt(self):
        root = self.fresh_root()
        original_write = os.write
        calls = 0

        def fail_after_partial(fd, value):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_write(fd, value[:37])
            raise OSError("synthetic partial write failure")

        with patch("bcs_e2e.local_panel_asset.os.write", side_effect=fail_after_partial), \
             self.assertRaisesRegex(OSError, "synthetic"):
            self.asset.install(root, run_id="partial")
        partial = root / "panel/index.umd.js"
        self.assertTrue(partial.is_file())
        self.assertEqual(partial.stat().st_size, 37)
        self.assertFalse((root / "panel-asset.json").exists())

    def test_receipt_write_failure_preserves_verified_artifact_and_partial_receipt(self):
        root = self.fresh_root()
        original_write = os.write
        destination_writes = 0

        def fail_receipt(fd, value):
            nonlocal destination_writes
            destination_writes += 1
            if destination_writes == 2:
                original_write(fd, value[:11])
                raise OSError("synthetic receipt write failure")
            return original_write(fd, value)

        with patch("bcs_e2e.local_panel_asset.os.write", side_effect=fail_receipt), \
             self.assertRaisesRegex(OSError, "receipt"):
            self.asset.install(root, run_id="receipt-partial")
        self.assertEqual((root / "panel/index.umd.js").read_bytes(), self.content)
        self.assertEqual((root / "panel-asset.json").stat().st_size, 11)


if __name__ == "__main__":
    unittest.main()
