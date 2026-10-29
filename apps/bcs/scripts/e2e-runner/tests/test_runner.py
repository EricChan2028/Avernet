from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys

from bcs_e2e.inventory import current_manifest
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main, plan
from bcs_e2e.preflight import check
from bcs_e2e.profile import ConfigError, load, validate
from bcs_e2e.transport import NoRedirect, ObservationError, ReadClient


def profile():
    p = json.loads((ROOT / "profiles/preprod.example.json").read_text())
    p["environment"].update(id="test-pre", expected_bcs_env="pre")
    p["human"]["user_id"] = "owner-1"
    for role, bot in zip(("driver", "member"), ("bot-a", "bot-b")):
        p["actors"][role].update(bot_id=bot, owner_id="owner-1")
    p["policy"]["exclusive_use_confirmed"] = True
    p["observer"].update(server="alisddbquery", database="agentclawdb",
                          environment_binding_confirmed=True)
    return p


def envelope(data):
    return {"code": 20000, "data": data, "message": "OK", "request_id": "server-1"}


def responses():
    return [envelope({"env": "pre"}), envelope({"user_id": "owner-1"}), *[
        envelope({"bot_id": bot, "kind": "bot", "created_by": "owner-1", "env": "pre",
                  "status": "online", "reachability": "offline", "visibility": "protected"})
        for bot in ("bot-a", "bot-b")]]


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.code = code
        self.headers = headers or {}


class ProfileTests(unittest.TestCase):
    def test_example_is_offline_valid(self):
        validate(profile())
        load(ROOT / "profiles/preprod.example.json")
        self.assertEqual(load(ROOT / "profiles/local.singlebox.example.json")["entrypoint"]["kind"], "singlebox")

    def test_profile_path_and_placeholder_errors_are_actionable(self):
        with self.assertRaisesRegex(ConfigError, "file not found"):
            load(ROOT / "profiles/does-not-exist.json")
        example = load(ROOT / "profiles/preprod.example.json")
        from bcs_e2e.profile import require_resolved
        with self.assertRaisesRegex(ConfigError, "REPLACE_WITH"):
            require_resolved(example)

    def test_reject_unknown_fields_at_every_level(self):
        for path in ((), ("environment",), ("entrypoint",), ("human",), ("actors",),
                     ("actors", "driver"), ("auth",), ("observer",), ("policy",)):
            p = profile()
            obj = p
            for key in path:
                obj = obj[key]
            obj["unknown"] = "do not ignore"
            with self.subTest(path=path), self.assertRaises(ConfigError):
                validate(p)

    def test_invalid_bindings(self):
        mutations = [
            lambda p: p["actors"]["member"].update(bot_id="bot-a"),
            lambda p: p["actors"]["driver"].update(owner_id="someone-else"),
            lambda p: p["actors"]["driver"].update(dedicated=False),
            lambda p: p["environment"].update(tier="production"),
            lambda p: p["entrypoint"].update(api_face="legacy"),
            lambda p: p["policy"].update(timeout_seconds=True),
            lambda p: p["policy"].update(max_response_bytes=999999999),
            lambda p: p["auth"].update(header_env={"X-Mock-User-Id": "WHO"}),
            lambda p: p["auth"].update(header_env={"X-Avernet-Principal": "JWT"}),
            lambda p: p["auth"].update(header_env={"Cookie": "literal=secret"}),
        ]
        for mutate in mutations:
            p = profile()
            mutate(p)
            with self.assertRaises(ConfigError):
                validate(p)

    def test_origin_hardening(self):
        for url in ("http://gateway.example.com", "https://u:secret@example.com",
                    "https://example.com/path", "https://example.com?q=secret",
                    "https://example.com#fragment", "https://example.com:invalid", "file:///tmp/key"):
            p = profile()
            p["entrypoint"]["origin"] = url
            with self.subTest(url=url), self.assertRaises(ConfigError):
                validate(p)
        p = profile()
        p["environment"].update(tier="local", lifecycle="local-provision",
                                deployment="singlebox", provider="local")
        p["entrypoint"].update(kind="singlebox", origin="http://127.0.0.1:21000")
        validate(p)

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text('{"auth":{},"auth":{"Cookie":"secret"}}')
            with self.assertRaisesRegex(ConfigError, "duplicate"):
                load(path)


class ManifestTests(unittest.TestCase):
    def test_source_inventory_parity(self):
        legacy = ROOT.parent / "e2e-test"
        regular, conditional = set(), set()
        main_source = (legacy / "stories.sh").read_text()
        regular |= set(re.findall(r'"(story_[^"]+)"', main_source.split('E2E_TESTS_STORIES=(')[1].split(')')[0]))
        for sh in sorted(legacy.glob("*.sh")):
            for line in sh.read_text().splitlines():
                top = re.match(r'E2E_TESTS_STORIES\+=\("?(story_[\w]+)"?\)\s*$', line)
                if top:
                    regular.add(top.group(1))
                    continue
                gated = re.match(r'^\s+E2E_TESTS_STORIES\+=\("?(story_[\w]+)"?\)\s*$', line)
                if gated:
                    conditional.add(gated.group(1))
        edge = (legacy / "edge_permission.sh").read_text()
        edges = set(re.findall(r'"(test_ep_[^"]+)"', edge.split('E2E_TESTS_EDGE_PERMISSION=(')[1].split(')')[0]))
        inventory = json.loads((ROOT / "manifest.json").read_text())["legacy_inventory"]
        self.assertFalse(regular & conditional)
        self.assertEqual({i["id"] for i in inventory}, regular | conditional | edges)
        self.assertEqual(len(inventory), len(current_manifest()['legacy_inventory']))
        for item in inventory:
            self.assertRegex((legacy / item["source"]).read_text(), r"(?m)^" + item["id"] + r"\(\)")
            self.assertEqual(item["conditional"], item["id"] in conditional)

    def test_plan_never_resolves_credentials_or_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("bcs_e2e.transport.build_opener") as network:
            result = plan(validate(profile()))
        network.assert_not_called()
        self.assertEqual([c["id"] for c in result["cases"]], ["PRE-01", "PRE-02", "PRE-03"])
        self.assertFalse(result["ready_to_execute"])
        assertions = [a for c in result["cases"] for a in c["assertions"]]
        self.assertEqual(len(assertions), len(set(assertions)))
        self.assertIn("IMPLEMENTED", result["cases"][0]["implementation_status"])
        self.assertIn("IMPLEMENTED", result["cases"][1]["implementation_status"])
        self.assertEqual(result["cases"][2]["implementation_status"], "IMPLEMENTED_SHARED_LOCAL_LIVE_AND_REMOTE_TRANSPORT")
        for case in result["cases"]:
            self.assertIn("ob", case["required_evidence"])


class PreflightTests(unittest.TestCase):
    def run_check(self, bodies, p=None):
        p = p or profile()
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": "private-session-secret"}), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.side_effect = [b if isinstance(b, Exception) else Reply(b) for b in bodies]
            client = ReadClient(validate(p))
            report = check(p, client)
            requests = [call.args[0] for call in factory.return_value.open.call_args_list]
        return report, requests

    def test_read_only_human_owned_bots_without_friend_or_model_checks(self):
        report, requests = self.run_check(responses())
        self.assertEqual(report["status"], "DISCOVERY_PASSED")
        self.assertFalse(report["ready_to_execute"])
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(r.method == "GET" and r.data is None for r in requests))
        self.assertTrue(all("friend" not in r.full_url and "ensure" not in r.full_url for r in requests))
        self.assertNotIn("private-session-secret", json.dumps(report))
        self.assertIn("live OB schema/access", report["not_verified"])

    def test_wrong_environment_stops_before_identity(self):
        report, requests = self.run_check([envelope({"env": "prod"})])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(len(requests), 1)

    def test_wrong_human_stops_before_bot_queries(self):
        report, requests = self.run_check([responses()[0], envelope({"user_id": "different-owner"})])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(len(requests), 2)

    def test_owner_and_hidden_and_kind_and_env_rejected(self):
        for field, value in (("created_by", "other"), ("status", "hidden"), ("kind", "human"), ("env", "prod")):
            bodies = responses()
            bodies[2]["data"][field] = value
            report, _ = self.run_check(bodies)
            self.assertEqual(report["status"], "BLOCKED")

    def test_network_error_is_redacted(self):
        report, requests = self.run_check([URLError("secret-cookie-leaked-in-exception")])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertNotIn("secret-cookie", json.dumps(report))
        self.assertEqual(len(requests), 1)

    def test_denial_or_redirect_has_no_retry_and_no_body_leak(self):
        for code in (301, 302, 401, 403, 500):
            error = HTTPError("https://gateway.example.com", code, "secret-error", {},
                              io.BytesIO(b"private-session-secret"))
            report, requests = self.run_check([error])
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(len(requests), 1)
            self.assertNotIn("secret", json.dumps(report))
            self.assertEqual(report["requests"][0]["http_status"], code)

    def test_false_success_and_malformed_and_oversized_body(self):
        for body in ({"code": 40000, "data": {"env": "pre"}}, b"<html>login</html>",
                     b"x" * 1048577, ["not", "object"], {"code": 20000, "data": []}):
            report, _ = self.run_check([body])
            self.assertEqual(report["status"], "BLOCKED")

    def test_legacy_manifest_is_explicit_not_fallback(self):
        p = profile()
        p["entrypoint"]["manifest_face"] = "legacy"
        bodies = responses()
        bodies[0] = {"env": "pre"}
        report, requests = self.run_check(bodies, p)
        self.assertEqual(report["status"], "DISCOVERY_PASSED")
        self.assertEqual(requests[0].full_url, "https://gateway.example.com/manifest")

    def test_missing_declarations_block_even_if_http_ok(self):
        p = profile()
        p["policy"]["exclusive_use_confirmed"] = False
        p["observer"]["environment_binding_confirmed"] = False
        report, _ = self.run_check(responses(), p)
        self.assertEqual(report["status"], "BLOCKED")


class TransportTests(unittest.TestCase):
    def test_redirect_handler_refuses_all_redirects(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))

    def test_no_arbitrary_operation_or_unbound_bot(self):
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": "private-secret"}), \
                patch("bcs_e2e.transport.build_opener") as factory:
            client = ReadClient(profile())
            for operation, bot in (("delete.group", ""), ("bot.read", "unbound")):
                with self.assertRaises(ObservationError):
                    client.read(operation, bot)
            factory.return_value.open.assert_not_called()

    def test_credential_errors_do_not_echo_secret(self):
        for value in ("", "cookie\r\nHeader: injected", "nonascii-密码"):
            with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": value}), self.assertRaises(ConfigError) as cm:
                ReadClient(profile())
            self.assertNotIn("injected", str(cm.exception))

    def test_bound_id_is_encoded_not_interpreted_as_path(self):
        p = profile()
        p["actors"]["driver"]["bot_id"] = "bot:owner"
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": "cookie"}), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(envelope({}))
            ReadClient(p).read("bot.read", "bot:owner")
            self.assertTrue(factory.return_value.open.call_args.args[0].full_url.endswith("bot%3Aowner"))


class CliTests(unittest.TestCase):
    def setUp(self):
        registry = tempfile.TemporaryDirectory()
        self.addCleanup(registry.cleanup)
        root_patch = patch("bcs_e2e.pool.pool_root", return_value=Path(registry.name))
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_check_requires_explicit_permission_and_missing_auth_blocks(self):
        for flags in ([], ["--allow-read"]):
            stdout = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(stdout):
                rc = main(["check", "--profile", str(ROOT / "profiles/preprod.example.json"), *flags])
            self.assertEqual(rc, 2)
            self.assertFalse(json.loads(stdout.getvalue())["ready_to_execute"])

    def test_run_requires_explicit_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            main(["run"])
        self.assertEqual(cm.exception.code, 2)

    def test_prepare_and_release_are_local_only_and_fail_closed(self):
        p = profile()
        p["policy"]["exclusive_use_confirmed"] = True
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.json"
            profile_path.write_text(json.dumps(p))
            state = Path(tmp) / "state"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["prepare", "--profile", str(profile_path),
                                       "--run-id", "run-local-1", "--state-dir", str(state)]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "PREPARED")
            self.assertFalse(report["ready_to_execute"])
            self.assertTrue(Path(json.loads((state / "run.json").read_text())["lock_path"]).is_file())
            self.assertTrue((state / "run.sqlite").is_file())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["prepare", "--profile", str(profile_path),
                                       "--run-id", "run-local-2", "--state-dir", str(state)]), 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["release", "--state-dir", str(state)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "RELEASED")
            self.assertFalse(Path(json.loads((state / "run.json").read_text())["lock_path"]).exists())


if __name__ == "__main__":
    unittest.main()

class ReadCorrelationTests(unittest.TestCase):
    def test_public_read_requires_and_records_response_request_id(self):
        p = profile()
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": "cookie"}), \
             patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(envelope({"user_id": "owner-1"}), headers={"x-request-id": "echo"})
            observation = ReadClient(p).read("identity.read")
        self.assertEqual(observation.evidence["response_request_id"], "server-1")
        self.assertFalse(observation.evidence["request_id_echoed"])

    def test_public_read_rejects_missing_or_malformed_response_request_id(self):
        for body in ({"code": 20000, "message": "OK", "data": {}},
                     {"code": 20000, "message": "OK", "request_id": "", "data": {}},
                     {"code": 20000, "message": "OK", "request_id": "r"},
                     {"code": "20000", "message": "OK", "request_id": "r", "data": {}}):
            with self.subTest(body=body), patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": "cookie"}), \
                 patch("bcs_e2e.transport.build_opener") as factory:
                factory.return_value.open.return_value = Reply(body)
                with self.assertRaises(ObservationError):
                    ReadClient(profile()).read("identity.read")
