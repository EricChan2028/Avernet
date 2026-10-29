"""Safety guard tests; never launch or terminate a real Singlebox process."""
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
from bcs_e2e.adapters import AdapterCapabilityError, adapter_for
from bcs_e2e.cli import main
from bcs_e2e.profile import fingerprint, validate
from bcs_e2e.run_state import write
from bcs_e2e.runtime import LocalSingleboxRuntime, RuntimeContractError


class RuntimeTests(unittest.TestCase):
    def profile(self) -> dict:
        profile = json.loads((ROOT / "profiles/local.singlebox.example.json").read_text())
        profile["human"]["user_id"] = "human-local"
        for role in ("driver", "member"):
            profile["actors"][role]["bot_id"] = f"bot-local-{role}"
            profile["actors"][role]["owner_id"] = "human-local"
        return validate(profile)

    def test_runtime_dir_must_be_private_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            with self.assertRaisesRegex(RuntimeContractError, "private child"):
                LocalSingleboxRuntime(self.profile(), state, runtime_dir=Path(tmp) / "outside")
            self.assertFalse(state.exists())

    def test_no_configuration_bypasses_disabled_start_or_stop(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("subprocess.run") as shell, patch("subprocess.Popen") as spawn, \
             patch("socket.socket") as socket, patch("os.kill") as signal:
            for ports in ((), (21000,), (45001, 45002)):
                with self.subTest(ports=ports):
                    state = Path(tmp) / "state"
                    runtime = LocalSingleboxRuntime(self.profile(), state, ports=ports,
                                                   runtime_dir=state / "custom")
                    for action in (runtime.start, runtime.stop):
                        with self.assertRaisesRegex(RuntimeContractError, "disabled"):
                            action()
                    for action in (runtime.check, runtime.status):
                        result = action()
                        self.assertEqual(result["status"], "BLOCKED")
                        self.assertFalse(result["ready_to_execute"])
                        self.assertFalse(result["legacy_state_present"])
                    self.assertFalse(state.exists())
            for capability in (shell, spawn, socket, signal):
                capability.assert_not_called()

    def test_legacy_status_and_claimed_ownership_never_authorize_stop(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("subprocess.run") as shell, patch("os.kill") as signal:
            state = Path(tmp)
            profile = self.profile()
            write(state / "run.json", {
                "schema_version": 1, "status": "PREPARED", "run_id": "runtime-run",
                "profile_digest": fingerprint(profile),
            })
            runtime = LocalSingleboxRuntime(profile, state)
            for status in ("STARTING", "RUNNING", "STOPPING", "FAILED", "FAILED_STOP", "STOPPED"):
                with self.subTest(status=status):
                    write(runtime.state_path, {
                        "schema_version": 1, "status": status, "started_by_runner": True,
                        "run_id": "runtime-run", "profile_digest": fingerprint(profile),
                    })
                    before = runtime.state_path.read_bytes()
                    with self.assertRaisesRegex(RuntimeContractError, "disabled"):
                        runtime.stop()
                    self.assertEqual(runtime.status()["status"], "BLOCKED")
                    self.assertTrue(runtime.check()["legacy_state_present"])
                    self.assertEqual(runtime.state_path.read_bytes(), before)
            shell.assert_not_called()
            signal.assert_not_called()

    def test_cli_all_runtime_actions_report_blocked_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run") as shell:
            profile_path = Path(tmp) / "profile.json"
            profile_path.write_text(json.dumps(self.profile()))
            state = Path(tmp) / "state"
            for action in ("check", "status", "start", "stop"):
                with self.subTest(action=action):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        rc = main(["runtime", action, "--profile", str(profile_path),
                                   "--state-dir", str(state)])
                    self.assertEqual(rc, 2)
                    result = json.loads(output.getvalue())
                    self.assertEqual(result["status"], "BLOCKED")
                    self.assertFalse(result["ready_to_execute"])
                    self.assertFalse(state.exists())
            shell.assert_not_called()

    def test_singlebox_never_falls_back_to_remote_adapter(self):
        with patch("bcs_e2e.adapters.RemotePilotAdapter") as remote:
            with self.assertRaisesRegex(AdapterCapabilityError, "run-owned"):
                adapter_for(self.profile())
            remote.assert_not_called()

    def test_legacy_markers_block_cleanup_and_release_without_modifying_state(self):
        for marker_kind in ("RUNNING", "FAILED", "FAILED_STOP", "STOPPED", "malformed", "symlink", "dangling"):
            with self.subTest(marker_kind=marker_kind), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp)
                profile_path = state / "profile.json"
                profile = self.profile()
                profile_path.write_text(json.dumps(profile))
                run_path = state / "run.json"
                write(run_path, {"schema_version": 1, "status": "PREPARED",
                                 "run_id": "runtime-run", "profile_digest": fingerprint(profile)})
                marker = state / "runtime.json"
                if marker_kind in {"symlink", "dangling"}:
                    marker.symlink_to(run_path if marker_kind == "symlink" else state / "absent")
                elif marker_kind == "malformed":
                    marker.write_text("not json")
                else:
                    write(marker, {"schema_version": 1, "status": marker_kind})
                before = run_path.read_bytes()
                marker_before = marker.lstat()
                for command in (
                    ["cleanup", "--profile", str(profile_path), "--allow-write", "--confirm-run-id", "runtime-run"],
                    ["release"],
                ):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output), \
                         patch("bcs_e2e.cli.adapter_for") as adapter, \
                         patch("bcs_e2e.cli.Ledger") as ledger, \
                         patch("bcs_e2e.cli.PoolLease") as lease:
                        rc = main([*command, "--state-dir", str(state)])
                    self.assertEqual(rc, 2)
                    self.assertIn("unverified legacy runtime", json.loads(output.getvalue())["reason"])
                    adapter.assert_not_called()
                    ledger.assert_not_called()
                    self.assertEqual(lease.mock_calls, [])
                    self.assertEqual(run_path.read_bytes(), before)
                    self.assertEqual(marker.lstat(), marker_before)
                    self.assertFalse((state / "cleanup.json").exists())


if __name__ == "__main__":
    unittest.main()
