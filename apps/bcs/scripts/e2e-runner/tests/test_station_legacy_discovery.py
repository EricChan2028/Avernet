"""Station legacy (bcs-direct) discovery adjacency.

The deployed station face serves native Human discovery at /auth/user and the
un-prefixed bot detail route at /bots/{bot_uuid}; the gateway face answers the
public-v1 forms. preflight must discover both projections without ever echoing
credential values or fields outside the reviewed admission keys.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.identity_binding import NATIVE, pilot_execution_blocker, resolve_identity_binding
from bcs_e2e.preflight import check, discovered_bot
from bcs_e2e.profile import validate
from bcs_e2e.transport import Observation, ObservationError, ReadClient
import test_runner as helpers


class FakeDiscoveryClient:
    def __init__(self, reads):
        self._reads = reads
        self.evidence = []

    def read(self, operation, bot_id="", **_):
        for key in ((operation, bot_id), operation):
            if key in self._reads:
                return Observation(self._reads[key], {"operation": operation})
        raise ObservationError("unexpected read: " + operation)


def legacy_bot(bot_id, owner, *, status_missing=False, created_by_int=False):
    value = {
        "actor_kind": "bot",
        "bot_uuid": bot_id,
        "created_by": (39464 if created_by_int else owner),
        "env": "pre",
    }
    if status_missing:
        value["dynamic_status"] = "online"
    else:
        value["status"] = "online"
    return value


class StationLegacyDiscoveryTests(unittest.TestCase):
    def profile(self, owner="owner-1"):
        p = helpers.profile()
        p["environment"]["id"] = "test-pre"
        p["human"]["user_id"] = owner
        for role in p["actors"]:
            p["actors"][role]["owner_id"] = owner
        p["entrypoint"].update(kind="bcs-direct", manifest_face="legacy")
        p.pop("identity_binding", None)
        return validate(p)

    def test_bindingless_station_profile_admits_defaults_without_native_blocker(self):
        p = self.profile()
        self.assertEqual(resolve_identity_binding(p), NATIVE)
        self.assertEqual(pilot_execution_blocker(p), "")

    def test_discovered_bot_projects_legacy_and_gateway_forms(self):
        legacy = legacy_bot("bot-a", "owner-1")
        projected = discovered_bot(legacy)
        self.assertEqual(projected, {
            "bot_id": "bot-a", "kind": "bot", "created_by": "owner-1",
            "env": "pre", "status": "online",
        })
        projected_int = discovered_bot(legacy_bot("bot-a", "39464", created_by_int=True))
        self.assertEqual(projected_int["created_by"], "39464")
        projected_missing = discovered_bot(legacy_bot("bot-a", "owner-1", status_missing=True))
        self.assertEqual(projected_missing["status"], "online")
        gateway = helpers.envelope({"bot_id": "bot-a", "kind": "bot",
                                    "created_by": "owner-1", "env": "pre",
                                    "status": "online"})["data"]
        self.assertEqual(discovered_bot(gateway)["bot_id"], "bot-a")
        self.assertEqual(discovered_bot(None), {})
        self.assertEqual(discovered_bot([1]), {})
        self.assertEqual(discovered_bot({"bot_id": 3, "kind": 4}), {
            "bot_id": 3, "kind": 4, "created_by": None, "env": None, "status": None})

    def test_station_face_preflight_passes_on_legacy_forms(self):
        p = self.profile()
        client = FakeDiscoveryClient({
            "environment.read": {"env": "pre", "schema_version": 1},
            "identity.read": {"user_id": "owner-1"},
            ("bot.read", "bot-a"): legacy_bot("bot-a", "owner-1"),
            ("bot.read", "bot-b"): legacy_bot("bot-b", "owner-1"),
        })
        report = check(p, client)
        self.assertEqual(report["status"], "DISCOVERY_PASSED", report)
        self.assertFalse(report["ready_to_execute"])
        by_id = {c["id"]: c["status"] for c in report["checks"]}
        self.assertEqual(by_id.get("target.environment"), "PASS")
        self.assertEqual(by_id.get("identity.human"), "PASS")
        self.assertEqual(by_id.get("actor.driver"), "PASS")
        self.assertEqual(by_id.get("actor.member"), "PASS")
        self.assertNotIn("route.business_adapter", by_id)
        self.assertIn("not_verified", report)

    def test_numeric_owner_projects_to_string_and_still_matches(self):
        p = self.profile(owner="39464")
        client = FakeDiscoveryClient({
            "environment.read": {"env": "pre"},
            "identity.read": {"user_id": "39464"},
            ("bot.read", "bot-a"): legacy_bot("bot-a", "39464", created_by_int=True),
            ("bot.read", "bot-b"): legacy_bot("bot-b", "39464", created_by_int=True),
        })
        report = check(p, client)
        self.assertEqual(report["status"], "DISCOVERY_PASSED", report)

    def test_gateway_face_envelope_forms_still_pass(self):
        p = helpers.profile()
        p.pop("identity_binding", None)
        p = validate(p)
        data = helpers.responses()
        client = FakeDiscoveryClient({
            "environment.read": data[0]["data"],
            "identity.read": data[1]["data"],
            ("bot.read", "bot-a"): data[2]["data"],
            ("bot.read", "bot-b"): data[3]["data"],
        })
        report = check(p, client)
        self.assertEqual(report["status"], "DISCOVERY_PASSED", report)

    def test_read_client_selects_legacy_routes_and_envelope_off(self):
        p = self.profile()
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
                patch('bcs_e2e.transport.build_opener') as opener:
            opener.return_value.open.side_effect = [
                helpers.Reply({'user_id': 'owner-1'}),
                helpers.Reply(legacy_bot("bot-a", "owner-1")),
            ]
            client = ReadClient(p)
            identity = client.read("identity.read")
            self.assertEqual(identity.data["user_id"], "owner-1")
            bot = client.read("bot.read", "bot-a")
            self.assertEqual(bot.data["bot_uuid"], "bot-a")
            urls = [c.args[0].full_url for c in opener.return_value.open.call_args_list]
            self.assertTrue(any(u.endswith("/auth/user") for u in urls), urls)
            self.assertTrue(any(u.endswith("/bots/bot-a") for u in urls), urls)
            self.assertFalse(any("/openapi/v1/collaboration/bots/" in u for u in urls), urls)


if __name__ == "__main__":
    unittest.main()
