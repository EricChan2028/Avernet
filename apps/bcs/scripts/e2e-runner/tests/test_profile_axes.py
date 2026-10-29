"""Explicit lifecycle/deployment/provider axes for pilot profiles."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bcs_e2e.profile import ConfigError, validate  # noqa: E402


def preprod():
    raw = json.loads((ROOT / "profiles/preprod.example.json").read_text())
    raw["environment"].update(id="pre-axis", expected_bcs_env="pre")
    raw["human"]["user_id"] = "39464"
    for role in ("driver", "member"):
        raw["actors"][role].update(owner_id="39464")
    raw["policy"]["exclusive_use_confirmed"] = True
    return raw


def local_mock():
    raw = json.loads((ROOT / "profiles/local.mock.json").read_text())
    return raw


class ProfileAxisTests(unittest.TestCase):
    def test_examples_explicitly_pin_all_three_axes(self):
        self.assertEqual((preprod()["environment"]["lifecycle"],
                          preprod()["environment"]["deployment"],
                          preprod()["environment"]["provider"]),
                         ("remote-attach", "managed", "internal"))
        self.assertEqual((local_mock()["environment"]["lifecycle"],
                          local_mock()["environment"]["deployment"],
                          local_mock()["environment"]["provider"]),
                         ("local-provision", "singlebox", "local"))
        self.assertEqual(validate(preprod())["environment"]["lifecycle"], "remote-attach")
        self.assertEqual(validate(local_mock())["environment"]["lifecycle"], "local-provision")

    def test_remote_attach_admits_docker_and_aliyun_without_cloud_story_fork(self):
        raw = preprod()
        raw["environment"].update(lifecycle="remote-attach", deployment="docker", provider="aliyun")
        self.assertEqual(validate(raw)["environment"]["deployment"], "docker")

    def test_local_provision_cannot_claim_remote_runtime(self):
        raw = local_mock()
        raw["environment"].update(lifecycle="remote-attach", deployment="managed", provider="internal")
        with self.assertRaisesRegex(ConfigError, "remote-attach"):
            validate(raw)

    def test_remote_attach_cannot_claim_local_provider(self):
        raw = preprod()
        raw["environment"].update(provider="local")
        with self.assertRaisesRegex(ConfigError, "remote-attach"):
            validate(raw)

    def test_remote_attach_requires_remote_entrypoint(self):
        raw = preprod()
        raw["entrypoint"]["kind"] = "mock"
        with self.assertRaisesRegex(ConfigError, "remote-attach"):
            validate(raw)

    def test_unknown_axis_value_is_rejected(self):
        for key in ("lifecycle", "deployment", "provider"):
            raw = preprod()
            raw["environment"][key] = "unknown"
            with self.assertRaisesRegex(ConfigError, f"environment.{key}"):
                validate(raw)

    def test_missing_axis_is_rejected(self):
        raw = preprod()
        raw["environment"].pop("deployment")
        with self.assertRaisesRegex(ConfigError, "environment: missing or unknown fields"):
            validate(copy.deepcopy(raw))

    def test_extra_environment_field_is_rejected(self):
        raw = preprod()
        raw["environment"]["runtime"] = "docker"
        with self.assertRaisesRegex(ConfigError, "environment: missing or unknown fields"):
            validate(raw)


if __name__ == "__main__":
    unittest.main()
