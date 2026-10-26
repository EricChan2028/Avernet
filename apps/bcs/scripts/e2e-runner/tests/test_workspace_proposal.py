"""Cross-environment WORKSPACE proposal legacy-HTTP tests; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.workspace_proposal_contract import (
    CASE_ID,
    RemoteWorkspaceProposalAdapter,
    execute_workspace_proposal_case,
    workspace_proposal_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    WORKSPACE_PROPOSAL_CONFIRM_OPERATION,
    WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION,
    WORKSPACE_PROPOSAL_DELETE_OPERATION,
    WORKSPACE_PROPOSAL_DETAIL_OPERATION,
    WORKSPACE_PROPOSAL_REQUEST_OPERATION,
    validate_workspace_proposal_profile,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.write_transport import (
    PROPOSAL_REDACTED_CONFIRM_PATH,
    WriteClient,
    WriteTransportError,
)

DRIVER = "driver-bot"
MEMBER = "member-bot"
QA = "qa-bot"
GROUP = "bcs_grp_workspace_proposal_01"
TOKEN = "123e4567-e89b-12d3-a456-426614174000"
CONFIRM_PATH = "/groups/" + TOKEN + "/confirm"
CONFIRM_URL = "https://gateway.example.com" + CONFIRM_PATH
TOPIC = "Review release readiness"
DRIVER_TOKEN = "owned-proposal-driver-token"
COOKIE = "human-proposal-cookie"


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-proposal-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": ("workspace-proposal-preprod" if preprod
                   else "workspace-proposal-local"),
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": ("https://gateway.example.com" if preprod
                       else "http://127.0.0.1:21000"),
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
        "auth": {"kind": "proposal-bundle",
                 "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                 "cookie_env": "BCS_E2E_HUMAN_COOKIE"},
        "relationships": {
            "proposal": "finalized-bounded-owned-bots-group-proposal",
            "confirmation": (
                "finalized-bounded-owned-bots-proposal-confirmation"),
            "finalization": "exact-owned-group-delete-and-not-found",
        },
        "policy": {
            "fixture_pool": "workspace-proposal-pool",
            "exclusive_use_confirmed": True, "timeout_seconds": 10,
            "max_response_bytes": 65536, "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "group",
                "api_face": "legacy"},
        "route_bindings": {
            WORKSPACE_PROPOSAL_REQUEST_OPERATION: {
                "method": "POST", "path": "/groups/request",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION: {
                "method": "GET",
                "path": "/groups/{confirm_token}/confirm",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_PROPOSAL_CONFIRM_OPERATION: {
                "method": "POST",
                "path": "/groups/{confirm_token}/confirm",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_PROPOSAL_DETAIL_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_PROPOSAL_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }
    if mutate is not None:
        mutate(value)
    return value


def proposal_digest() -> str:
    return hashlib.sha256(CONFIRM_URL.encode("utf-8")).hexdigest()


def proposal_created():
    return {"proposal_created": True, "driver_bot": DRIVER,
            "participants": [MEMBER, QA],
            "member_intros": [MEMBER, QA],
            "confirm_path": CONFIRM_PATH,
            "confirm_url_sha256": proposal_digest(),
            "expires_in_seconds": 300,
            "message": "release-readiness proposal requested"}


def confirmation_page():
    return {"confirm_path": PROPOSAL_REDACTED_CONFIRM_PATH,
            "topic_present": True, "form_action_matches": True}


def confirmation_created():
    return {"created": True, "group_id": GROUP,
            "driver_bot": DRIVER}


def proposal_detail():
    return {"id": GROUP, "driver_bot": DRIVER,
            "participants": [DRIVER, MEMBER, QA],
            "group_kind": "normal", "label": TOPIC}


def proposal_delete_ack():
    return {"deleted": True, "id": GROUP}


def request_record() -> dict:
    return {"operation": WORKSPACE_PROPOSAL_REQUEST_OPERATION,
            "method": "POST", "path": "/groups/request",
            "submitted_request_id": "e2e-workspace-proposal",
            "http_status": 200, "traceparent_mode": "disabled"}


def capability_record(operation: str) -> dict:
    return {"operation": operation, "method": "POST",
            "path": PROPOSAL_REDACTED_CONFIRM_PATH,
            "submitted_request_id": "e2e-workspace-proposal",
            "http_status": 200, "traceparent_mode": "disabled"}


def json_record(operation: str, method: str, path: str,
                   http_status: int = 200) -> dict:
    return {"operation": operation, "method": method, "path": path,
            "submitted_request_id": "e2e-workspace-proposal",
            "http_status": http_status,
            "traceparent_mode": "disabled"}


class ProposalTests(unittest.TestCase):
    def run_case(self, *, preprod=False, deform=None):
        profile = profile_value(preprod=preprod)
        detail_calls = {"count": 0}

        def fake_request(self_client, *, topic,
                            suggested_participants,
                            suggested_driver):
            data = proposal_created()
            if deform is not None:
                deform(data, "request", {})
            return data, request_record()

        def fake_preview(self_client, *, confirm_path, topic):
            data = confirmation_page()
            if deform is not None:
                deform(data, "preview", {})
            return data, capability_record(
                WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION)

        def fake_confirm(self_client, *, confirm_path):
            data = confirmation_created()
            if deform is not None:
                deform(data, "confirm", {})
            return data, capability_record(
                WORKSPACE_PROPOSAL_CONFIRM_OPERATION)

        def fake_call(self_client, operation, **kwargs):
            if operation == WORKSPACE_PROPOSAL_DETAIL_OPERATION:
                # call order: confirmation read-back, post-delete
                # not-found verification
                detail_calls["count"] += 1
                if detail_calls["count"] == 1:
                    data = proposal_detail()
                    if deform is not None:
                        deform(data, "detail", kwargs)
                    return data, json_record(
                        operation, "GET", "/groups/" + GROUP)
                return None, json_record(
                    operation, "GET", "/groups/" + GROUP,
                    http_status=404)
            data = proposal_delete_ack()
            if deform is not None:
                deform(data, "delete", kwargs)
            return data, json_record(
                operation, "DELETE",
                "/groups/" + GROUP + "?bot_id=" + DRIVER)

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_call, autospec=True), \
             patch("bcs_e2e.write_transport.WriteClient."
                    "request_group_proposal",
                   side_effect=fake_request, autospec=True), \
             patch("bcs_e2e.write_transport.WriteClient."
                    "preview_group_proposal_confirmation",
                   side_effect=fake_preview, autospec=True), \
             patch("bcs_e2e.write_transport.WriteClient."
                    "confirm_group_proposal",
                   side_effect=fake_confirm, autospec=True):
            adapter = RemoteWorkspaceProposalAdapter(profile)
            execution = execute_workspace_proposal_case(adapter)
        return execution, adapter

    def test_local_and_preprod_proposal(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(result.completed_count, 1)
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(adapter.confirm_path, CONFIRM_PATH)
                snapshot = result.snapshot()
                self.assertEqual(snapshot["steps"][0]["data"][
                    "group_id"], GROUP)
                self.assertEqual(snapshot["steps"][0]["data"][
                    "proposal_created"], True)
                self.assertEqual(snapshot["steps"][0]["data"][
                    "confirmation_previewed"], True)
                self.assertEqual(snapshot["steps"][0]["data"][
                    "confirmed"], True)
                self.assertEqual(snapshot["steps"][0]["data"][
                    "proposed_driver"], DRIVER)
                self.assertEqual(snapshot["steps"][0]["data"][
                    "proposed_participants"], [MEMBER, QA])
                self.assertEqual(snapshot["steps"][0]["data"][
                    "confirmation_topic"], TOPIC)
                serialized = json.dumps(snapshot, sort_keys=True)
                self.assertNotIn(COOKIE, serialized)
                self.assertNotIn(DRIVER_TOKEN, serialized)
                self.assertNotIn(TOKEN, serialized)
                self.assertNotIn(CONFIRM_URL, serialized)
                evidence = snapshot["steps"][0]["evidence"]
                self.assertEqual(evidence["credential_kind"],
                                 "owned_local_bot_token")
                if preprod:
                    self.assertEqual(evidence["credential_kinds"],
                                     ["owned_local_bot_token"]
                                     + ["human_cookie"] * 5)
                else:
                    self.assertEqual(evidence["credential_kinds"],
                                     ["owned_local_bot_token"] * 6)
                bindings = [row["route_caller_binding"]
                            for row in evidence["requests"]]
                self.assertEqual(bindings,
                                 ["route_bound", "documented_gap",
                                  "documented_gap", "open_read",
                                  "route_bound", "open_read"])
                self.assertEqual(evidence["requests"][0]["path"],
                                 "/groups/request")
                for row in evidence["requests"][1:3]:
                    self.assertEqual(row["path"],
                                     PROPOSAL_REDACTED_CONFIRM_PATH)

    def test_proposal_not_created_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "request":
                data["proposal_created"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 0)
        self.assertEqual(result.steps[0].status, "NOT_RUN")

    def test_proposal_participant_drift_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "request":
                data["participants"] = [MEMBER, QA, "foreign-bot"]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_proposal_driver_drift_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "request":
                data["driver_bot"] = "foreign-bot"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_proposal_digest_drift_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "request":
                data["confirm_url_sha256"] = "not-a-digest"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_confirmation_page_drift_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "preview":
                data["topic_present"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_confirmation_form_action_drift_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "preview":
                data["form_action_matches"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_confirmation_created_drift_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "confirm":
                data["created"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_confirmed_detail_drift_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "detail":
                data["driver_bot"] = "foreign-bot"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_delete_acknowledgement_drift_is_blocked(self):
        def deform(data, phase, kwargs):
            if phase == "delete":
                data["deleted"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_proposal_group_not_finalized_is_blocked(self):
        # the not-found verification reads a live Group instead of 404
        profile = profile_value()
        detail_calls = {"count": 0}

        def fake_request(self_client, *, topic,
                            suggested_participants,
                            suggested_driver):
            return proposal_created(), request_record()

        def fake_preview(self_client, *, confirm_path, topic):
            return confirmation_page(), capability_record(
                WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION)

        def fake_confirm(self_client, *, confirm_path):
            return confirmation_created(), capability_record(
                WORKSPACE_PROPOSAL_CONFIRM_OPERATION)

        def live_call(self_client, operation, **kwargs):
            if operation == WORKSPACE_PROPOSAL_DETAIL_OPERATION:
                detail_calls["count"] += 1
                if detail_calls["count"] >= 2:
                    return proposal_detail(), json_record(
                        operation, "GET", "/groups/" + GROUP)
                return proposal_detail(), json_record(
                    operation, "GET", "/groups/" + GROUP)
            return proposal_delete_ack(), json_record(
                operation, "DELETE",
                "/groups/" + GROUP + "?bot_id=" + DRIVER)

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=live_call, autospec=True), \
             patch("bcs_e2e.write_transport.WriteClient."
                    "request_group_proposal",
                   side_effect=fake_request, autospec=True), \
             patch("bcs_e2e.write_transport.WriteClient."
                    "preview_group_proposal_confirmation",
                   side_effect=fake_preview, autospec=True), \
             patch("bcs_e2e.write_transport.WriteClient."
                    "confirm_group_proposal",
                   side_effect=fake_confirm, autospec=True):
            adapter = RemoteWorkspaceProposalAdapter(profile)
            execution = execute_workspace_proposal_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.steps[0].status, "NOT_RUN")

    def test_transport_request_rejects_foreign_suggested_driver(self):
        profile = profile_value()
        fake_call, _ = self.standard_call()
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_call, autospec=True):
            client = WriteClient(
                {"auth": {"kind": "authenticated-product",
                          "header_env": {"Authorization":
                                         "BCS_E2E_DRIVER_BOT_TOKEN"}},
                 **self.transport_profile(profile)})
            with self.assertRaises(WriteTransportError):
                client.request_group_proposal(
                    topic=TOPIC,
                    suggested_participants=[MEMBER, QA],
                    suggested_driver="foreign-bot")

    def test_transport_request_rejects_foreign_participants(self):
        profile = profile_value()
        fake_call, _ = self.standard_call()
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_call, autospec=True):
            client = WriteClient(
                {"auth": {"kind": "authenticated-product",
                          "header_env": {"Authorization":
                                         "BCS_E2E_DRIVER_BOT_TOKEN"}},
                 **self.transport_profile(profile)})
            with self.assertRaises(WriteTransportError):
                client.request_group_proposal(
                    topic=TOPIC,
                    suggested_participants=[MEMBER, "foreign-bot"],
                    suggested_driver=DRIVER)

    def test_transport_request_rejects_escaped_confirm_url(self):
        profile = profile_value(preprod=True)
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env):
            client = WriteClient(
                {"auth": {"kind": "authenticated-product",
                          "header_env": {"Authorization":
                                         "BCS_E2E_DRIVER_BOT_TOKEN"}},
                 **self.transport_profile(profile)})
        with self.assertRaises(WriteTransportError):
            client._derived_proposal_confirm_path(
                "https://evil.example.com" + CONFIRM_PATH)
        with self.assertRaises(WriteTransportError):
            client._derived_proposal_confirm_path(
                "https://gateway.example.com" + CONFIRM_PATH
                + "?recipient=driver")
        with self.assertRaises(WriteTransportError):
            client._derived_proposal_confirm_path(
                "https://gateway.example.com" + CONFIRM_PATH
                + "#fragment")
        with self.assertRaises(WriteTransportError):
            client._derived_proposal_confirm_path(
                "/groups/not-a-uuid/confirm")
        # The owned-origin capability URL derives to its path.
        self.assertEqual(
            client._derived_proposal_confirm_path(CONFIRM_URL),
            CONFIRM_PATH)

    def standard_call(self):
        def fake_call(self_client, operation, **kwargs):
            data = proposal_created()
            data["confirm_url"] = CONFIRM_URL
            return data, request_record()

        return fake_call, None

    @staticmethod
    def transport_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        value["auth"] = {"kind": "authenticated-product",
                         "header_env": {
                             "Authorization":
                                 "BCS_E2E_DRIVER_BOT_TOKEN"}}
        return value

    def test_case_guard_accepts_the_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_proposal_contract.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_proposal_route_drift(self):
        mutations = {
            "request-method": lambda value: value["route_bindings"][
                WORKSPACE_PROPOSAL_REQUEST_OPERATION].update(
                    method="PUT"),
            "request-path": lambda value: value["route_bindings"][
                WORKSPACE_PROPOSAL_REQUEST_OPERATION].update(
                    path="/groups/proposals"),
            "confirm-page-method": lambda value: value[
                "route_bindings"][
                WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION].update(
                    method="POST"),
            "confirm-page-path": lambda value: value[
                "route_bindings"][
                WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION].update(
                    path="/groups/{confirm_token}/review"),
            "confirm-method": lambda value: value["route_bindings"][
                WORKSPACE_PROPOSAL_CONFIRM_OPERATION].update(
                    method="PUT"),
            "detail-path": lambda value: value["route_bindings"][
                WORKSPACE_PROPOSAL_DETAIL_OPERATION].update(
                    path="/openapi/v1/collaboration/groups/{group_id}"),
            "delete-target": lambda value: value["route_bindings"][
                WORKSPACE_PROPOSAL_DELETE_OPERATION].update(
                    path="/groups/{group_id}?bot_id={qa_actor_id}"),
            "delete-method": lambda value: value["route_bindings"][
                WORKSPACE_PROPOSAL_DELETE_OPERATION].update(
                    method="POST"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_proposal_profile(
                        profile_value(mutate=mutate))

    def test_profile_rejects_proposal_contract_drift(self):
        mutations = {
            "auth-kind": lambda value: value["auth"].update(
                kind="roundtrip-bundle"),
            "missing-cookie": lambda value: value["auth"].pop(
                "cookie_env"),
            "missing-qa-actor": lambda value: value["actors"].pop("qa"),
            "effect-drift": lambda value: value["relationships"].update(
                proposal="unreviewed-effect"),
            "family-drift": lambda value: value["cli"].update(
                command_family="session"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_proposal_profile(
                        profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = workspace_proposal_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows
                    if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"],
                         ["WORKSPACE.proposal"])
        self.assertEqual(row["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
