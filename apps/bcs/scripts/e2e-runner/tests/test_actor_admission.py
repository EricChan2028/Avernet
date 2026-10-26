from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.observation_gate import ActorAdmissionError, validate_pilot_actor_scope
from bcs_e2e.observation_gate import ObserverAdmissionError, AdmittedObserver
from bcs_e2e.profile import ConfigError, load, validate
from bcs_e2e.adapters import LocalMockAdapter

ROOT = Path(__file__).resolve().parents[1]


class ActorAdmissionTests(unittest.TestCase):
    def profile(self):
        return load(ROOT / "profiles/local.mock.json")

    def test_admits_two_owned_roles_without_touching_friendship(self):
        result = validate_pilot_actor_scope(self.profile())
        self.assertEqual(result["status"], "ADMITTED")
        self.assertEqual(result["friendship"], "unchanged")
        self.assertEqual(result["cross_owner"], "NOT_TESTED")

    def test_wrong_owner_duplicate_bot_or_friendship_policy_blocks(self):
        for mutate in (
            lambda p: p["actors"]["member"].update(owner_id="other-human"),
            lambda p: p["actors"]["member"].update(bot_id=p["actors"]["driver"]["bot_id"]),
            lambda p: p["relationships"].update(friendship="accept_if_needed"),
        ):
            p = self.profile(); mutate(p)
            with self.subTest(profile=p):
                with self.assertRaises(ActorAdmissionError):
                    validate_pilot_actor_scope(p)
                with self.assertRaises(ConfigError):
                    validate(p)

    def test_observer_admission_blocks_before_provider_or_business_write(self):
        p = self.profile(); p["actors"]["driver"]["owner_id"] = "foreign"
        adapter = LocalMockAdapter(self.profile())
        scope = Mock()
        with self.assertRaises(ObserverAdmissionError):
            AdmittedObserver(adapter, p, "PRE-01", resource_scope=scope)
        scope.assert_not_called()

    def test_relationship_policy_is_not_a_friendship_probe(self):
        adapter = LocalMockAdapter(self.profile())
        result = adapter.preflight()
        self.assertEqual(result["status"], "DISCOVERY_PASSED")
        self.assertFalse(any("friend" in str(item).lower() for item in result.get("requests", [])))


if __name__ == "__main__":
    unittest.main()
