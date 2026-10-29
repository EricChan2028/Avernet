from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

RUNNER = Path(__file__).resolve().parents[1]
BCS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RUNNER))

from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_template_asset import (
    CONTRACT,
    MAX_FILE_BYTES,
    LocalTemplateAsset,
)
from bcs_e2e.owned_bcs import OwnedBcsProcess, file_sha256
from bcs_e2e.runtime import RuntimeContractError

ACTUAL_CATALOG = BCS / "seeds/collaboration-templates"


def catalog_files(directory: Path) -> list[Path]:
    return [directory / "registry.yaml", *sorted((directory / "en-US").glob("*.yaml")),
            *sorted((directory / "zh-CN").glob("*.yaml"))]


def write_manifest(directory: Path, destination: Path, *, files: list[dict] | None = None) -> tuple[Path, str]:
    if files is None:
        files = []
        for path in catalog_files(directory):
            content = path.read_bytes()
            files.append({
                "path": path.relative_to(directory).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            })
    document = {"schema_version": 1, "contract": CONTRACT, "files": files}
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    destination.write_bytes(payload)
    return destination, hashlib.sha256(payload).hexdigest()


class LocalTemplateAssetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def asset(self, directory: Path = ACTUAL_CATALOG, *, name: str = "manifest.json") -> LocalTemplateAsset:
        manifest, digest = write_manifest(directory, self.base / name)
        return LocalTemplateAsset(directory, manifest, digest)

    def copied_catalog(self, name: str) -> Path:
        destination = self.base / name
        shutil.copytree(ACTUAL_CATALOG, destination)
        return destination

    def fresh_runtime_root(self, name: str) -> Path:
        root = self.base / name
        root.mkdir(mode=0o700)
        (root / "templates").mkdir(mode=0o700)
        return root

    def test_actual_repository_catalog_installs_exact_private_assets_and_receipt(self):
        asset = self.asset()
        self.assertIs(asset.validate(), asset)
        inputs = prepare_local_bcs_inputs(
            self.base / "runtime", run_id="template-catalog", port=45001, template_asset=asset)
        expected = {path.relative_to(ACTUAL_CATALOG).as_posix() for path in catalog_files(ACTUAL_CATALOG)}
        installed = {
            path.relative_to(inputs.root / "templates").as_posix()
            for path in (inputs.root / "templates").rglob("*") if path.is_file()
        }
        self.assertEqual(installed, expected)
        self.assertNotIn("README.md", installed)
        self.assertIn("en-US/single-bot-guided-answer.yaml", installed)
        self.assertIn("zh-CN/single-bot-guided-answer.yaml", installed)
        for relative in expected:
            source = ACTUAL_CATALOG / relative
            owned = inputs.root / "templates" / relative
            self.assertEqual(owned.read_bytes(), source.read_bytes())
            self.assertEqual(stat.S_IMODE(owned.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((inputs.root / "templates/en-US").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((inputs.root / "templates/zh-CN").stat().st_mode), 0o700)
        receipt_path = inputs.root / "template-assets.json"
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        self.assertEqual(receipt["contract"], CONTRACT)
        self.assertEqual(receipt["run_id"], "template-catalog")
        self.assertEqual(receipt["manifest_sha256"], asset.manifest_sha256)
        self.assertEqual({item["path"] for item in receipt["files"]}, expected)
        config = json.loads(inputs.config_path.read_text())
        self.assertEqual(Path(config["collaboration"]["templates"]["base_dir"]), inputs.root / "templates")

    def test_default_is_unchanged_and_typed_inputs_are_required_before_root_creation(self):
        default = prepare_local_bcs_inputs(self.base / "default", run_id="default", port=45001)
        self.assertEqual(list((default.root / "templates").iterdir()), [])
        self.assertFalse((default.root / "template-assets.json").exists())
        denied = self.base / "denied"
        with self.assertRaisesRegex(RuntimeContractError, "explicit approved manifest"):
            prepare_local_bcs_inputs(denied, run_id="denied", port=45002, template_asset=object())
        self.assertFalse(denied.exists())

    def test_owned_process_accepts_only_the_typed_opt_in_without_starting(self):
        binary = self.base / "fake-bcs"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o700)
        asset = self.asset(name="process-manifest.json")
        runtime = OwnedBcsProcess(
            binary=binary, binary_sha256=file_sha256(binary), root=self.base / "owned",
            run_id="owned-template", template_asset=asset)
        self.assertIs(runtime.template_asset, asset)
        default = OwnedBcsProcess(
            binary=binary, binary_sha256=file_sha256(binary), root=self.base / "owned-default",
            run_id="owned-default")
        self.assertIsNone(default.template_asset)
        with self.assertRaisesRegex(RuntimeContractError, "explicit approved manifest"):
            OwnedBcsProcess(binary=binary, binary_sha256=file_sha256(binary),
                            root=self.base / "bad-owned", run_id="bad-owned", template_asset=object())

    def test_manifest_digest_traversal_and_path_collisions_fail_closed(self):
        manifest, digest = write_manifest(ACTUAL_CATALOG, self.base / "wrong-digest.json")
        with self.assertRaisesRegex(RuntimeContractError, "manifest SHA256"):
            LocalTemplateAsset(ACTUAL_CATALOG, manifest, "0" * 64).validate()

        original = json.loads(manifest.read_text())["files"]
        traversal = [*original, {"path": "../escape.yaml", "sha256": "0" * 64, "size": 1}]
        traversal_path, traversal_digest = write_manifest(
            ACTUAL_CATALOG, self.base / "traversal.json", files=traversal)
        with self.assertRaisesRegex(RuntimeContractError, "traversal"):
            LocalTemplateAsset(ACTUAL_CATALOG, traversal_path, traversal_digest).validate()

        duplicate = [*original, dict(original[0])]
        collision_path, collision_digest = write_manifest(
            ACTUAL_CATALOG, self.base / "collision.json", files=duplicate)
        with self.assertRaisesRegex(RuntimeContractError, "collide"):
            LocalTemplateAsset(ACTUAL_CATALOG, collision_path, collision_digest).validate()

    def test_symlink_hardlink_and_oversize_catalog_files_fail_closed(self):
        symlink_catalog = self.copied_catalog("symlink")
        symlink_file = symlink_catalog / "en-US/single-bot-guided-answer.yaml"
        symlink_file.unlink()
        symlink_file.symlink_to(symlink_catalog / "zh-CN/single-bot-guided-answer.yaml")
        symlink_manifest, symlink_digest = write_manifest(
            ACTUAL_CATALOG, self.base / "symlink-manifest.json")
        with self.assertRaisesRegex(RuntimeContractError, "symlink"):
            LocalTemplateAsset(symlink_catalog, symlink_manifest, symlink_digest).validate()

        hardlink_catalog = self.copied_catalog("hardlink")
        hardlink_file = hardlink_catalog / "en-US/single-bot-guided-answer.yaml"
        hardlink_file.unlink()
        os.link(hardlink_catalog / "zh-CN/single-bot-guided-answer.yaml", hardlink_file)
        hardlink_manifest, hardlink_digest = write_manifest(
            ACTUAL_CATALOG, self.base / "hardlink-manifest.json")
        with self.assertRaisesRegex(RuntimeContractError, "single-link|hard links"):
            LocalTemplateAsset(hardlink_catalog, hardlink_manifest, hardlink_digest).validate()

        oversize_catalog = self.copied_catalog("oversize")
        oversized = oversize_catalog / "en-US/single-bot-guided-answer.yaml"
        oversized.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        oversize_manifest, oversize_digest = write_manifest(
            oversize_catalog, self.base / "oversize-manifest.json")
        with self.assertRaisesRegex(RuntimeContractError, "size"):
            LocalTemplateAsset(oversize_catalog, oversize_manifest, oversize_digest).validate()

    def test_drift_unapproved_files_and_destination_collisions_fail_closed(self):
        drift_catalog = self.copied_catalog("drift")
        drift_asset = self.asset(drift_catalog, name="drift-manifest.json")
        drift_asset.validate()
        (drift_catalog / "registry.yaml").write_bytes(b"changed\n")
        drift_root = self.fresh_runtime_root("drift-root")
        with self.assertRaisesRegex(RuntimeContractError, "drifted"):
            drift_asset.install(drift_root, run_id="drift")
        self.assertEqual(list((drift_root / "templates").iterdir()), [])
        self.assertFalse((drift_root / "template-assets.json").exists())

        extra_catalog = self.copied_catalog("extra")
        extra_asset = self.asset(extra_catalog, name="extra-manifest.json")
        (extra_catalog / "en-US/unapproved.yaml").write_text("name: unapproved\n")
        with self.assertRaisesRegex(RuntimeContractError, "exactly bind"):
            extra_asset.validate()

        collision_root = self.fresh_runtime_root("collision-root")
        existing = collision_root / "templates/registry.yaml"
        existing.write_text("keep\n")
        with self.assertRaisesRegex(RuntimeContractError, "never adopt"):
            self.asset(name="destination-manifest.json").install(collision_root, run_id="collision")
        self.assertEqual(existing.read_text(), "keep\n")
        self.assertFalse((collision_root / "template-assets.json").exists())


if __name__ == "__main__":
    unittest.main()
