from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from bcs_e2e.cli import main
from bcs_e2e.docker_binding import review_docker_binding


def binding():
    return {
        "schema_version": 1,
        "environment": {
            "lifecycle": "remote-attach",
            "deployment": "docker",
            "provider": "aliyun",
        },
        "target": {
            "kind": "kubernetes-namespace",
            "resource_id": "ocb-e2e-preprod",
            "image_digest": "registry.hub.example.com/ocb/ocb@sha256:" + "a" * 64,
        },
        "runner": {
            "image_digest": "registry.hub.example.com/ocb/bcs-e2e-runner@sha256:" + "b" * 64,
        },
        "mounts": {
            "profile": {
                "source": "/etc/bcs-e2e/profile.json",
                "target": "/etc/bcs-e2e/profile.json",
                "read_only": True,
            },
            "secret": {
                "source": "/etc/bcs-e2e/secret.env",
                "target": "/etc/bcs-e2e/secret.env",
                "read_only": True,
            },
            "report": {
                "source": "/var/lib/bcs-e2e/reports",
                "target": "/var/lib/bcs-e2e/reports",
                "read_only": False,
            },
        },
        "network": {
            "api_origin": "https://ocb-preprod.example.com",
            "callback": "https://callback-preprod.example.com",
            "protocols": ["HTTPS", "WEBSOCKET", "MCP"],
        },
        "security": {
            "tls": {
                "min_version": "TLS1_2",
                "verification": "SYSTEM_TRUSTED_CA",
            },
        },
        "observer": {
            "kind": "alisddbquery",
            "server": "bcs-preprod-observer",
            "database": "agentclawdb",
            "environment": "preprod",
            "binding_confirmed": True,
        },
        "runtime": {
            "termination_signal": "SIGTERM",
            "kill_signal": "SIGKILL",
            "timeout_seconds": 600,
        },
    }


class DockerBindingTests(unittest.TestCase):
    def test_contract_is_ready_and_still_requires_live_validation(self):
        result = review_docker_binding(binding())
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["ready_to_execute"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["scope"], "OFFLINE_REVIEW_ONLY")
        self.assertEqual(result["remote_validation"], "NOT_RUN")
        self.assertFalse(result["grants_release_credit"])
        self.assertTrue(result["requires_live_validation"])

    def test_provider_contract_must_be_remote_docker_aliyun(self):
        mutations = {
            "lifecycle": lambda value: value["environment"].update(lifecycle="local"),
            "deployment": lambda value: value["environment"].update(deployment="singlebox"),
            "provider": lambda value: value["environment"].update(provider="local"),
        }
        expected = {
            "lifecycle": "environment.lifecycle:expected remote-attach",
            "deployment": "environment.deployment:expected docker",
            "provider": "environment.provider:expected aliyun",
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                value = copy.deepcopy(binding())
                mutate(value)
                result = review_docker_binding(value)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn(expected[name], result["blockers"])

    def test_target_must_have_exact_kind_resource_and_digest(self):
        mutations = {
            "kind": lambda value: value["target"].update(kind="physical-machine"),
            "resource": lambda value: value["target"].update(resource_id=""),
            "digest": lambda value: value["target"].update(image_digest="ocb:latest"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                value = copy.deepcopy(binding())
                mutate(value)
                result = review_docker_binding(value)
                self.assertEqual(result["status"], "BLOCKED")
                expected = {
                    "kind": "target.kind:unsupported",
                    "resource": "target.resource_id:invalid",
                    "digest": "target.image_digest:invalid",
                }[name]
                self.assertIn(expected, result["blockers"])

    def test_runner_digest_cannot_be_a_mutable_tag(self):
        value = copy.deepcopy(binding())
        value["runner"]["image_digest"] = "registry.hub.example.com/ocb/bcs-e2e-runner:1.0"
        result = review_docker_binding(value)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["blockers"], ["runner.image_digest:invalid"])
        self.assertIsNone(result["binding"])

    def test_profile_and_secret_mounts_must_be_read_only_with_report_writable(self):
        value = copy.deepcopy(binding())
        value["mounts"]["profile"]["read_only"] = False
        value["mounts"]["report"]["read_only"] = True
        result = review_docker_binding(value)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(
            "mounts.read_only:profile and secret must be read-only",
            result["blockers"],
        )
        self.assertIn("mounts.report:must be writable", result["blockers"])

    def test_distinct_mount_targets_are_enforced(self):
        value = copy.deepcopy(binding())
        value["mounts"]["report"]["target"] = value["mounts"]["profile"]["target"]
        result = review_docker_binding(value)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("mounts.target:must be distinct", result["blockers"])

    def test_network_and_security_must_use_trusted_https(self):
        value = copy.deepcopy(binding())
        value["network"]["api_origin"] = "http://ocb-preprod.example.com"
        value["security"]["tls"]["verification"] = "INSECURE_SKIP"
        result = review_docker_binding(value)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("network.api_origin:invalid", result["blockers"])
        self.assertIn("security.tls.verification:expected SYSTEM_TRUSTED_CA", result["blockers"])

    def test_observer_environment_must_be_bound_and_confirmed(self):
        value = copy.deepcopy(binding())
        value["observer"]["environment"] = "dev"
        value["observer"]["binding_confirmed"] = False
        result = review_docker_binding(value)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("observer.environment:unsupported", result["blockers"])
        self.assertIn("observer.binding_confirmed:expected true", result["blockers"])

    def test_runtime_must_declare_explicit_signals_and_timeout(self):
        value = copy.deepcopy(binding())
        value["runtime"]["termination_signal"] = "QUIT"
        value["runtime"]["kill_signal"] = "KILL"
        value["runtime"]["timeout_seconds"] = 0
        result = review_docker_binding(value)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("runtime.termination_signal:expected SIGTERM", result["blockers"])
        self.assertIn("runtime.kill_signal:expected SIGKILL", result["blockers"])
        self.assertIn("runtime.timeout_seconds:integer outside bounds", result["blockers"])

    def test_corrupt_contract_has_no_leaked_binding_or_exception(self):
        value = copy.deepcopy(binding())
        value.pop("mounts")
        result = review_docker_binding(value)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["blockers"], ["binding.keys"])
        self.assertIsNone(result["binding"])
        self.assertEqual(result["remote_validation"], "NOT_RUN")
        self.assertFalse(result["grants_release_credit"])

    def test_blockers_never_echo_invalid_input_values(self):
        value = copy.deepcopy(binding())
        value["target"]["image_digest"] = "cryptic but not a real digest"
        value["mounts"]["secret"]["target"] = "relative-path"
        result = review_docker_binding(value)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["blockers"], [
            "target.image_digest:invalid",
            "mounts.secret.target:invalid",
        ])
        self.assertIsNone(result["binding"])

    def test_example_template_is_intentionally_blocked_and_has_exact_contract_keys(self):
        template_path = (
            Path(__file__).resolve().parents[1]
            / "aliyun-docker-binding.example.json"
        )
        with template_path.open(encoding="utf-8") as file:
            raw = json.load(file)
        self.assertEqual(set(raw), {
            "schema_version", "environment", "target", "runner", "mounts",
            "network", "security", "observer", "runtime",
        })
        result = review_docker_binding(raw)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("target.kind:unsupported", result["blockers"])
        self.assertIn("target.image_digest:invalid", result["blockers"])
        self.assertIn("runner.image_digest:invalid", result["blockers"])
        self.assertIn("observer.binding_confirmed:expected true", result["blockers"])
        self.assertIn("network.api_origin:invalid", result["blockers"])
        self.assertIn("network.callback:invalid", result["blockers"])
        self.assertIn("target.resource_id:invalid", result["blockers"])
        self.assertIn("mounts.profile.source:invalid", result["blockers"])
        self.assertIn("observer.server:invalid", result["blockers"])
        self.assertIsNone(result["binding"])
        self.assertTrue(result["requires_live_validation"])

    def test_cli_reviews_a_ready_binding_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "docker-binding.json"
            path.write_text(json.dumps(binding()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = main(["docker-binding-review", "--binding", str(path)])
        self.assertEqual(rc, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "READY")
        self.assertTrue(report["ready_to_execute"])
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])

    def test_cli_reports_blockers_without_a_profile_or_execution(self):
        invalid = copy.deepcopy(binding())
        invalid["target"]["image_digest"] = "ocb:latest"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "docker-binding.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = main(["docker-binding-review", "--binding", str(path)])
        self.assertEqual(rc, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("target.image_digest:invalid", report["blockers"])
        self.assertEqual(report["scope"], "OFFLINE_REVIEW_ONLY")
        self.assertTrue(report["requires_live_validation"])


if __name__ == "__main__":
    unittest.main()
