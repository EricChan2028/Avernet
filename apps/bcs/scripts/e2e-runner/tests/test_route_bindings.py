from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.profile import ConfigError, load, validate
from bcs_e2e.route_bindings import ROUTES
from bcs_e2e.write_transport import WriteClient
from test_write_transport import Reply, profile as remote_profile

ROOT = Path(__file__).resolve().parents[1]


class RouteBindingTests(unittest.TestCase):
    def test_remote_example_contains_every_finite_pilot_binding(self):
        profile = load(ROOT / "profiles/preprod.example.json")
        self.assertEqual(set(profile["route_bindings"]), set(ROUTES))
        self.assertEqual(validate(profile)["route_bindings"], profile["route_bindings"])

    def test_missing_unknown_or_mutated_binding_is_rejected(self):
        base = remote_profile()
        cases = []
        missing = copy.deepcopy(base); del missing["route_bindings"]["group.create"]; cases.append(missing)
        unknown = copy.deepcopy(base); unknown["route_bindings"]["group.extra"] = {}; cases.append(unknown)
        for operation, field, value in (("group.create", "method", "PUT"),
                                        ("group.read", "path", "/api/v1/collaboration/groups/{group_id}"),
                                        ("group.update", "api_face", "internal-v1"),
                                        ("session.delete", "auth", "app"),
                                        ("session.read", "response", "raw")):
            changed = copy.deepcopy(base)
            changed["route_bindings"][operation][field] = value
            cases.append(changed)
        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaises(ConfigError):
                    validate(changed)

    def test_mock_profile_cannot_smuggle_remote_route_bindings(self):
        profile = json.loads((ROOT / "profiles/local.mock.json").read_text())
        profile["route_bindings"] = {operation: {} for operation in ROUTES}
        with self.assertRaises(ConfigError):
            validate(profile)

    def test_write_client_consumes_validated_binding_path(self):
        profile = remote_profile()
        body = {"code": 20000, "message": "OK", "request_id": "r", "data": {"group_id": "g-1"}}
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(body)
            WriteClient(profile).call("group.read", group_id="g-1")
            request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://gateway.example.test/openapi/v1/collaboration/groups/g-1")


if __name__ == "__main__":
    unittest.main()
