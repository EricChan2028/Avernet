# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

from pathlib import Path
import sys
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.adapters import RemotePilotAdapter


def adapter():
    value = RemotePilotAdapter.__new__(RemotePilotAdapter)
    value.resource_review = True
    value.profile = {"actors": {"driver": {"bot_id": "driver-bot"}}}
    value.group_read = Mock(return_value=({"group_id": "group-1"}, {}))
    value.session_read = Mock(return_value=({"session_id": "session-1", "group_id": "group-1"}, {}))
    value.file_list = Mock(return_value=({
        "items": [{
            "file_id": "file-1", "session_id": "session-1",
            "file_name": "e2e-pilot-report.txt", "size": 328,
            "owner": {"actor_kind": "bot", "actor_id": "driver-bot"},
            "storage_backend": "baas", "status": "ready",
        }], "total": 1,
    }, {}))
    return value


def records():
    return [
        {"handle": "group", "case_id": "PRE-03", "attempt": 1, "kind": "group", "parent": None,
         "request_id": "req-group", "resource_id": "group-1", "origin": "created", "state": "live"},
        {"handle": "session", "case_id": "PRE-03", "attempt": 1, "kind": "session", "parent": "group",
         "request_id": "req-session", "resource_id": "session-1", "origin": "created", "state": "live"},
        {"handle": "file", "case_id": "PRE-03", "attempt": 1, "kind": "file", "parent": "session",
         "request_id": "req-file", "resource_id": "file-1", "origin": "created", "state": "live"},
    ]


class RemoteCleanupScopeTests(unittest.TestCase):
    @staticmethod
    def test_reviewed_remote_scope_verifies_exact_parent_and_files():
        result = adapter().verify_cleanup_scope("run-1", records())
        assert result["status"] == "VERIFIED"

    @staticmethod
    def test_unrecorded_remote_file_blocks_cleanup_scope():
        value = adapter()
        value.file_list.return_value = ({"items": [
            {"file_id": "file-1"}, {"file_id": "implicit-file"}], "total": 2}, {})
        result = value.verify_cleanup_scope("run-1", records())
        assert result["status"] == "BLOCKED"
        assert result["reason"] == "session has unrecorded or missing live files"

    @staticmethod
    def test_invalid_file_projection_blocks_cleanup_scope():
        value = adapter()
        value.file_list.return_value = ({
            "items": [{
                "file_id": "file-1", "session_id": "session-1",
                "file_name": "e2e-pilot-report.txt", "size": 328,
                "owner": {"actor_kind": "bot", "actor_id": "other-bot"},
                "storage_backend": "baas", "status": "ready",
            }],
            "total": 1,
        }, {})
        result = value.verify_cleanup_scope("run-1", records())
        assert result["status"] == "BLOCKED"
        assert result["reason"] == "session file projection is invalid"

    @staticmethod
    def test_cleanup_scope_requires_review_admission():
        value = adapter()
        value.resource_review = False
        result = value.verify_cleanup_scope("run-1", records())
        assert result["status"] == "BLOCKED"
        assert result["reason"] == "reviewed remote effect admission is required"
