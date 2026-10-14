import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "locked_workspace_write.py"


class LockedWorkspaceWriteTest(unittest.TestCase):
    def run_write(self, workspace: Path, block: str):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--workspace",
                str(workspace),
                "--target",
                "MEMORY.md",
                "--operation",
                "append",
            ],
            input=block,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_concurrent_appends_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            blocks = [f"### rule-{index}\ncontent-{index}" for index in range(20)]
            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(lambda block: self.run_write(workspace, block), blocks))

            self.assertTrue(all(result.returncode == 0 for result in results), results)
            content = (workspace / "MEMORY.md").read_text(encoding="utf-8")
            for block in blocks:
                self.assertEqual(content.count(block), 1)

    def test_exact_append_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            first = self.run_write(workspace, "### principle\nbe careful")
            second = self.run_write(workspace, "### principle\nbe careful")
            self.assertEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(json.loads(second.stdout)["status"], "already_exists")
            content = (workspace / "MEMORY.md").read_text(encoding="utf-8")
            self.assertEqual(content.count("### principle"), 1)

    def test_append_does_not_treat_substring_as_existing_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            first = self.run_write(workspace, "### principle extended\nbe careful always")
            second = self.run_write(workspace, "### principle")
            self.assertEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(json.loads(second.stdout)["status"], "applied")
            content = (workspace / "MEMORY.md").read_text(encoding="utf-8")
            self.assertIn("### principle extended", content)
            self.assertTrue(content.endswith("### principle\n"))

    def test_replace_rejects_stale_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "MEMORY.md"
            target.write_text("newer\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--workspace",
                    str(workspace),
                    "--target",
                    "MEMORY.md",
                    "--operation",
                    "replace",
                    "--expected-sha256",
                    "0" * 64,
                ],
                input="replacement\n",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 3)
            self.assertEqual(json.loads(result.stdout)["status"], "conflict")
            self.assertEqual(target.read_text(encoding="utf-8"), "newer\n")


if __name__ == "__main__":
    unittest.main()
