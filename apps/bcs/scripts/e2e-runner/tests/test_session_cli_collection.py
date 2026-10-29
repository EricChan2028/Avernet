"""Cross-environment SESSION.collection product-CLI tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner
from bcs_e2e.session_cli_collection import (
    RemoteSessionCliCollectionAdapter,
    execute_session_cli_collection_case,
    session_cli_collection_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
)
from bcs_e2e.session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_COLLECT_OPERATION,
    SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
    SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION,
    SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_UNCOLLECT_OPERATION,
    validate_session_cli_collection_profile,
)
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
QA = "qa-bot"
GROUP = "bcs_grp_session_cli_col"
SESSION = GROUP + ":c011ec71"
TOKEN = "owned-collect-token"
MEMBER_TOKEN = "owned-isolation-token"
QA_TOKEN = "owned-qa-token"
COOKIE = "owned-preprod-cookie"
PARTICIPANTS = (DRIVER, MEMBER)
POOL = (DRIVER, MEMBER, QA)


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-session-cli-collection-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": "session-cli-collection-preprod" if preprod
                  else "session-cli-collection-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": "https://gateway.example.com" if preprod
                      else "http://127.0.0.1:21000",
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": "39464"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "qa": {"bot_id": QA, "owner_id": "39464",
                   "dedicated": True, "visibility": "public"},
        },
        "auth": (
            {
                "kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE",
            } if preprod else
            {
                "kind": "owned-local-bot-tokens",
                "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN",
                "qa_token_env": "BCS_E2E_QA_BOT_TOKEN",
            }
        ),
        "relationships": {
            "group": "bounded-owned-bots-cli-session-collection",
            "initial_session": "deleted-with-group",
            "explicit_session": "collected-uncollected-deleted-with-group",
            "cleanup": "exact-owned-group", "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "session-cli-collection-pool",
            "exclusive_use_confirmed": True, "timeout_seconds": 10,
            "max_response_bytes": 65536, "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "session",
                "api_face": "legacy"},
        "route_bindings": {
            SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION: {
                "method": "GET",
                "path": ("/groups/{group_id}/sessions"
                         "?participant={driver_actor_id}&collected=true"),
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION: {
                "method": "GET",
                "path": ("/groups/{group_id}/sessions"
                         "?participant={member_actor_id}&collected=true"),
                "api_face": "legacy", "auth": "case_owned_member",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION: {
                "method": "GET",
                "path": "/groups/{group_id}/sessions?collected=true",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_COLLECT_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/collect",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_UNCOLLECT_OPERATION: {
                "method": "DELETE",
                "path": ("/sessions/{session_id}/collect"
                         "?participant={driver_actor_id}"),
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }
    if mutate is not None:
        mutate(value)
    return value


def group_output():
    return (f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
            f"  Participants: {DRIVER},{MEMBER}\n"
            f"  Session: {GROUP}:00000001\n").encode()


def session_output():
    return json.dumps({
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": "collect-sess",
    }).encode()


def collected_item():
    return {
        "session_id": SESSION, "group_id": GROUP,
        "session_title": "collect-sess", "status": "active",
        "collected": True,
    }


class SessionCliCollectionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_case(self, *, preprod=False, deform=None):
        profile = validate_session_cli_collection_profile(
            profile_value(preprod=preprod))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                      binary_sha256=self.digest,
                                      state_dir=self.state)
        commands, contexts = [], []
        collected: set[tuple[str, str]] = set()

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            result = group_output() if len(commands) == 1 else session_output()
            contexts.append((
                kwargs["environment"]["HOME"],
                kwargs["environment"].get("BCS_COOKIE"),
            ))
            return subprocess.CompletedProcess(cmd, 0, result, b"")

        def resolve_caller(self_client):
            if preprod:
                if self_client.headers.get("Cookie") == COOKIE:
                    return "human"
                raise AssertionError(
                    "preprod requests must carry the owned Human Cookie")
            authorization = self_client.headers.get("Authorization", "")
            for identity, token in ((DRIVER, TOKEN),
                                        (MEMBER, MEMBER_TOKEN),
                                        (QA, QA_TOKEN)):
                if authorization == token:
                    return identity
            raise AssertionError(
                "Singlebox requests must carry a dedicated pool Bot token")

        def binding_participant(self_client, operation):
            path = self_client.profile["route_bindings"][operation]["path"]
            if "participant={driver_actor_id}" in path:
                return self_client.profile["actors"]["driver"]["bot_id"]
            if "participant={member_actor_id}" in path:
                return self_client.profile["actors"]["member"]["bot_id"]
            return None

        def respond(self_client, caller, operation, kwargs):
            if operation == SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION:
                return (400, {"error": "invalid_params",
                              "message": "collected filter requires "
                                         "participant"})
            if operation in (SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
                             SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION):
                participant = binding_participant(self_client, operation)
                if caller == "human":
                    if participant not in POOL:
                        raise AssertionError(
                            "preprod listings must name an owned pool Bot")
                elif caller != participant:
                    return (403, {"error": "forbidden",
                                  "message": f"caller is not authorized to "
                                             f"view collections for bot "
                                             f"{participant}"})
                items = ([collected_item()]
                         if (SESSION, participant) in collected else [])
                return (200, {"items": items, "group_id": GROUP})
            if operation == SESSION_CLI_SESSION_COLLECT_OPERATION:
                if caller == "human":
                    collector = (kwargs.get("body") or {}).get("participant")
                    if collector not in POOL:
                        raise AssertionError(
                            "preprod human collect must name an owned pool "
                            "Bot participant")
                else:
                    collector = caller
                    if (kwargs.get("body") or {}).get("participant") != caller:
                        raise AssertionError(
                            "Singlebox Bot collect body must declare its own "
                            "identity")
                if collector not in PARTICIPANTS:
                    return (404, {"error": "participant {} not in session {}"
                                    .format(collector, SESSION)})
                collected.add((SESSION, collector))
                return (200, {"collected": True, "session_id": SESSION})
            if operation == SESSION_CLI_SESSION_UNCOLLECT_OPERATION:
                if caller not in (DRIVER, "human"):
                    raise AssertionError(
                        "uncollect must run as the driver collector")
                collector = (binding_participant(self_client, operation)
                             if caller == "human" else caller)
                collected.discard((SESSION, collector))
                return (200, {"collected": False, "session_id": SESSION})
            if operation == SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION:
                if caller not in (DRIVER, "human"):
                    raise AssertionError(
                        "creator delete must run as the driver")
                return (200, {"deleted": True, "session_id": SESSION})
            if operation == SESSION_CLI_GROUP_DELETE_OPERATION:
                if caller not in (DRIVER, "human"):
                    raise AssertionError(
                        "group delete must run as the driver")
                return (200, {"deleted": True, "id": GROUP})
            if operation == SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
                return (404, {"status": 404, "code": "group_not_found",
                              "message": "Group not found",
                              "params": {"group_id": GROUP}})
            raise AssertionError(f"unexpected operation {operation}")

        def fake_call(self_client, operation, **kwargs):
            caller = resolve_caller(self_client)
            status, payload = respond(self_client, caller, operation, kwargs)
            if deform is not None:
                override = deform(payload, operation, kwargs)
                if isinstance(override, dict):
                    if "data" in override:
                        payload = override["data"]
                    if "status" in override:
                        status = override["status"]
            return payload, {
                "operation": operation, "http_status": status,
                "path": "/session-collection",
                "submitted_request_id": "e2e-session-cli-collection",
            }

        if preprod:
            env = {"BCS_E2E_HUMAN_COOKIE": COOKIE}
        else:
            env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
                   "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN,
                   "BCS_E2E_QA_BOT_TOKEN": QA_TOKEN}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture",
                   side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_call, autospec=True):
            adapter = RemoteSessionCliCollectionAdapter(profile, runner)
            execution = execute_session_cli_collection_case(adapter)
        return execution, adapter, commands, contexts

    def test_local_and_preprod_collection_flow_and_cleanup(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter, commands, contexts = self.run_case(
                    preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(adapter.session_id, "")
                self.assertEqual(commands[0][-1], "bcs-e2e-session-collection")
                self.assertIn("--driver", commands[0])
                self.assertEqual(commands[1][-2:], ["--title", "collect-sess"])
                if preprod:
                    self.assertEqual(contexts[0][1], COOKIE)
                else:
                    self.assertIsNone(contexts[0][1])
                snapshot = result.snapshot()
                requests = snapshot["steps"][0]["evidence"]["requests"]
                expected = (
                    "setup", "session-create", "collected-list-precondition",
                    "collected-filter-rejection", "session-collect",
                    "session-collect-idempotent", "collected-list-contains",
                    "collected-list-isolation",
                    "non-participant-collect-rejection", "session-uncollect",
                    "session-uncollect-idempotent",
                    "collected-list-postcondition", "session-creator-delete",
                    "cleanup-delete", "cleanup-404-proof",
                )
                self.assertEqual(
                    [record["case_class"] for record in requests],
                    list(expected))
                self.assertEqual(requests[7]["caller_role"], "case_owned_member")
                self.assertEqual(requests[8]["caller_role"], "case_owned_qa")
                self.assertEqual(requests[2]["submitted_request_id"],
                                 "e2e-session-cli-collection")
                self.assertEqual(snapshot["steps"][0]["data"]["group_absence_verified"], True)
                serialized = json.dumps(snapshot, sort_keys=True)
                self.assertNotIn(TOKEN, serialized)
                self.assertNotIn(MEMBER_TOKEN, serialized)
                self.assertNotIn(QA_TOKEN, serialized)
                self.assertNotIn(COOKIE, serialized)

    def test_collect_acknowledgement_drift_is_blocked(self):
        collects = {"count": 0}

        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_COLLECT_OPERATION:
                collects["count"] += 1
                if collects["count"] == 1:
                    return {"data": {"collected": False,
                                     "session_id": SESSION}}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_repeat_collect_not_idempotent_is_blocked(self):
        collects = {"count": 0}

        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_COLLECT_OPERATION:
                collects["count"] += 1
                if collects["count"] == 2:
                    return {"status": 409, "data": None}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_collected_filter_without_participant_not_rejected_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION:
                return {"status": 200,
                        "data": {"items": [], "group_id": GROUP}}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_collected_list_precondition_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION:
                return {"data": {"items": [collected_item()],
                                 "group_id": GROUP}}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_member_collection_isolation_leak_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION:
                return {"data": {"items": [collected_item()],
                                 "group_id": GROUP}}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_non_participant_collect_accepted_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_COLLECT_OPERATION:
                return {"status": 200,
                        "data": {"collected": True, "session_id": SESSION}}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_uncollect_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_UNCOLLECT_OPERATION:
                return {"data": {"collected": True,
                                 "session_id": SESSION}}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_case_guard_accepts_the_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_cli_runner.py",
                 ROOT / "bcs_e2e/session_cli_collection.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_collection_route_drift(self):
        mutations = {
            "member-listing-route": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION].update(
                path="/groups/{group_id}/sessions"
                     "?participant={member_actor_id}"),
            "collect-method": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_COLLECT_OPERATION].update(method="PUT"),
            "uncollect-route": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_UNCOLLECT_OPERATION].update(
                path="/sessions/{session_id}/collect"),
            "member-auth-owner": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION].update(
                auth="case_owned_driver"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_session_cli_collection_profile(
                        profile_value(mutate=mutate))

    def test_profile_rejects_actor_and_credential_drift(self):
        def drop_qa(value):
            del value["actors"]["qa"]
            del value["auth"]["qa_token_env"]

        def qa_is_member(value):
            value["actors"]["qa"]["bot_id"] = MEMBER

        def local_cookie_auth(value):
            value["auth"] = {"kind": "human-cookie",
                             "cookie_env": "BCS_E2E_HUMAN_COOKIE"}

        def preprod_bot_tokens(value):
            value["auth"] = {
                "kind": "owned-bot-tokens",
                "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN",
                "qa_token_env": "BCS_E2E_QA_BOT_TOKEN"}

        for name, mutate in (("missing-qa", drop_qa),
                             ("qa-is-member", qa_is_member),
                             ("local-cookie", local_cookie_auth),
                             ("preprod-bot-tokens", preprod_bot_tokens)):
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_session_cli_collection_profile(
                        profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = session_cli_collection_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows
                    if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"], ["SESSION.collection"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
