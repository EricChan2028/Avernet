"""Offline adapter tests for the Group Context runner wiring; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.collaboration_team_runner import (
    CollaborationTeamRunner,
    CollaborationTeamRunnerError,
)
from bcs_e2e.group_context_adapter import RemoteGroupContextAdapter
from bcs_e2e.run_state import RunStateError

from test_collaboration_team import (
    COOKIE,
    DRIVER_TOKEN,
    ENG,
    PM,
    profile_value,
)

GROUP_ID = "grp-cli-group-context-e2e"
SESSION_ID = "ses-cli-group-context-e2e"
ROUND_ID = SESSION_ID + ":round-cli-e2e-fixed"
CTX = "cli-e2e-group-context"
GROUP_CONTENT = "initial group context from cli e2e"
GROUP_UPDATED = "updated group context from cli e2e"
SESSION_CONTENT = "session group context from cli e2e"
ROUND_CONTENT = "round group context from cli e2e"

WORKSPACE_DATA = {
    "group_id": GROUP_ID,
    "session_id": SESSION_ID,
    "driver_bot": PM,
    "participants": [PM, ENG],
}

OPERATIONS = (
    "group_context.workspace",
    "group_context.add.group",
    "group_context.add.session",
    "group_context.add.round",
    "group_context.get.initial",
    "group_context.update.group",
    "group_context.list.entries",
    "group_context.get.updated",
)

METHODS = (
    "create_workspace",
    "add_group_context_entry",
    "add_session_context_entry",
    "add_round_context_entry",
    "get_initial_context",
    "update_group_context_entry",
    "list_context_entries",
    "get_updated_context",
    "cleanup_group_context",
)


class GroupContextAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.binary_sha256 = hashlib.sha256(
            self.binary.read_bytes()).hexdigest()
        self.runner = CollaborationTeamRunner(
            profile_value(gateway=True),
            binary=self.binary,
            binary_sha256=self.binary_sha256,
            state_dir=self.state,
        )
        self.runner._group_context.update({
            "group_id": GROUP_ID,
            "session_id": SESSION_ID,
            "round_id": ROUND_ID,
            "ctx_name": CTX,
            "group_content": GROUP_CONTENT,
            "group_updated": GROUP_UPDATED,
            "session_content": SESSION_CONTENT,
            "round_content": ROUND_CONTENT,
        })
        self.operations: list[tuple[str, dict]] = []
        self.delete_calls: list[str] = []
        self.workspace_data = dict(WORKSPACE_DATA)
        self.get_items: dict[str, list] = {}
        self.list_contexts: list = [{"name": CTX, "permission": "WRO"}]
        self.junk_runner_result = False
        self.runner.execute = self.fake_execute
        self.runner.delete_group = self.fake_delete_group
        self.adapter = RemoteGroupContextAdapter(self.runner)

    def tearDown(self):
        self.temp.cleanup()

    def run_nine_obligations(self):
        return [getattr(self.adapter, method)() for method in METHODS]

    def fake_execute(self, operation, *, request_id="", group_id="",
                     topic=""):
        self.operations.append((operation, {"topic": topic}))
        if self.junk_runner_result:
            return {"data": self.respond(operation),
                    "evidence": self.evidence(operation),
                    "junk": "unexpected"}
        return {"data": self.respond(operation),
                "evidence": self.evidence(operation)}

    def fake_delete_group(self, group_id):
        self.delete_calls.append(group_id)
        return {"data": {"deleted": True},
                "evidence": {"transport": "PRODUCT_HTTP",
                             "route_declared": "DELETE /groups/{group_id}",
                             "remote_validation": "NOT_RUN",
                             "grants_release_credit": False}}

    @staticmethod
    def evidence(operation: str) -> dict:
        return {"transport": "PRODUCT_CLI", "operation": operation,
                "credential_kind": "human_cookie_with_owned_driver_bot_token",
                "command_arguments": ["bcs-cli"],
                "remote_validation": "NOT_RUN",
                "grants_release_credit": False}

    def respond(self, operation: str) -> dict:
        if operation == "group_context.workspace":
            return json.loads(json.dumps(self.workspace_data))
        if operation == "group_context.list.entries":
            return {"contexts": [dict(row) for row in self.list_contexts]}
        if operation == "group_context.get.initial":
            rows = self.get_items.get(operation) or [
                {"name": CTX, "content": GROUP_CONTENT}]
            return {"items": [dict(row) for row in rows]}
        if operation == "group_context.get.updated":
            rows = self.get_items.get(operation) or [
                {"name": CTX, "content": GROUP_UPDATED}]
            return {"items": [dict(row) for row in rows]}
        return {"status": "ok"}

    def test_nine_obligations_drive_the_frozen_runner_operations(self):
        results = self.run_nine_obligations()
        self.assertEqual(len(results), 9)
        self.assertEqual(
            [operation for operation, _ in self.operations],
            list(OPERATIONS))
        self.assertEqual(self.delete_calls, [GROUP_ID])
        workspace = results[0]
        self.assertEqual(workspace.data["group_id"], GROUP_ID)
        self.assertEqual(workspace.data["session_id"], SESSION_ID)
        self.assertEqual(
            workspace.evidence["requests"][0]["operation"],
            "group_context.workspace")

    def test_workspace_requires_the_owned_driver_bot(self):
        self.workspace_data["driver_bot"] = "foreign-bot"
        with self.assertRaises(RunStateError):
            self.adapter.create_workspace()

    def test_workspace_requires_group_and_session_identifiers(self):
        del self.workspace_data["group_id"]
        with self.assertRaises(RunStateError):
            self.adapter.create_workspace()

    def test_add_entries_carry_run_local_scope_content(self):
        group = self.adapter.add_group_context_entry()
        self.assertEqual(group.data["scope_level"], "group")
        self.assertEqual(group.data["context_name"], CTX)
        self.assertEqual(group.data["content"], GROUP_CONTENT)
        session = self.adapter.add_session_context_entry()
        self.assertEqual(session.data["scope_level"], "session")
        self.assertEqual(session.data["content"], SESSION_CONTENT)
        rounded = self.adapter.add_round_context_entry()
        self.assertEqual(rounded.data["scope_level"], "round")
        self.assertEqual(rounded.data["content"], ROUND_CONTENT)

    def test_add_acknowledgement_must_be_exact_ok(self):
        self.runner.execute = lambda operation, **kwargs: {
            "data": {"status": "failed"},
            "evidence": self.evidence(operation)}
        with self.assertRaises(RunStateError):
            self.adapter.add_group_context_entry()

    def test_get_entries_require_exact_run_local_name_and_content(self):
        initial = self.adapter.get_initial_context()
        self.assertEqual(initial.data["context_name"], CTX)
        self.assertEqual(initial.data["content"], GROUP_CONTENT)
        updated = self.adapter.get_updated_context()
        self.assertEqual(updated.data["context_name"], CTX)
        self.assertEqual(updated.data["content"], GROUP_UPDATED)

    def test_get_fails_when_items_do_not_match_the_context(self):
        self.get_items = {
            "group_context.get.initial":
                [{"name": "foreign-name", "content": "foreign-content"}],
            "group_context.get.updated":
                [{"name": CTX, "content": GROUP_UPDATED[:-1]}],
        }
        with self.assertRaises(RunStateError):
            self.adapter.get_initial_context()
        with self.assertRaises(RunStateError):
            self.adapter.get_updated_context()
        self.get_items = {}
        for method, operation in (("get_initial_context",
                                   "group_context.get.initial"),
                                  ("get_updated_context",
                                   "group_context.get.updated")):
            self.runner.execute = lambda op, operation=operation, **kwargs: {
                "data": {"items": []},
                "evidence": self.evidence(op)}
            with self.assertRaises(RunStateError):
                getattr(self.adapter, method)()

    def test_update_acknowledgement_and_merged_content(self):
        updated = self.adapter.update_group_context_entry()
        self.assertEqual(updated.data["context_name"], CTX)
        self.assertEqual(updated.data["content"], GROUP_UPDATED)

    def test_list_requires_an_owner_write_permission_row(self):
        listed = self.adapter.list_context_entries()
        self.assertEqual(listed.data["context_name"], CTX)
        self.assertEqual(listed.data["visible_permission"], "WRO")
        self.assertEqual(listed.data["returned_contexts"], 1)

    def test_list_fails_without_owner_permission(self):
        self.list_contexts = [{"name": CTX, "permission": "RO"}]
        with self.assertRaises(RunStateError):
            self.adapter.list_context_entries()

    def test_list_fails_when_contexts_are_missing(self):
        self.runner.execute = lambda operation, **kwargs: {
            "data": {}, "evidence": self.evidence(operation)}
        with self.assertRaises(RunStateError):
            self.adapter.list_context_entries()

    def test_cleanup_deletes_exactly_the_created_group(self):
        cleanup = self.adapter.cleanup_group_context()
        self.assertTrue(cleanup.data["deleted"])
        self.assertEqual(cleanup.evidence["case_operation"],
                         "group_context.cleanup")

    def test_cleanup_requires_exact_deletion_acknowledgement(self):
        self.runner.delete_group = lambda group_id: {
            "data": {"deleted": False},
            "evidence": {"transport": "PRODUCT_HTTP"}}
        with self.assertRaises(RunStateError):
            self.adapter.cleanup_group_context()

    def test_cleanup_rejects_unexpected_runner_result_shape(self):
        self.runner.delete_group = lambda group_id: {
            "data": {"deleted": True},
            "evidence": {"transport": "PRODUCT_HTTP"},
            "junk": "unexpected"}
        with self.assertRaises(RunStateError):
            self.adapter.cleanup_group_context()

    def test_runner_result_shape_is_enforced(self):
        self.junk_runner_result = True
        with self.assertRaises(RunStateError):
            self.adapter.create_workspace()

    def test_runner_rejects_cleanup_of_a_foreign_group(self):
        fresh = CollaborationTeamRunner(
            profile_value(gateway=True),
            binary=self.binary,
            binary_sha256=self.binary_sha256,
            state_dir=self.state,
        )
        fresh._group_context["group_id"] = "grp-foreign"
        with self.assertRaises(CollaborationTeamRunnerError):
            fresh.delete_group(GROUP_ID)

    def test_constructor_rejects_a_non_runner(self):
        with self.assertRaises(RunStateError):
            RemoteGroupContextAdapter(object())

    def seal_all_obligations(self) -> str:
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE,
                                     "BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN}):
            results = self.run_nine_obligations()
        return json.dumps([{"data": call.data, "evidence": call.evidence}
                           for call in results])

    def test_evidence_and_data_do_not_leak_credentials(self):
        serialized = self.seal_all_obligations()
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn(DRIVER_TOKEN, serialized)
        self.assertNotIn("Bearer", serialized)


if __name__ == "__main__":
    unittest.main()
