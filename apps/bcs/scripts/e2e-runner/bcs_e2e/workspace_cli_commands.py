"""Command contract for reviewed Workspace and Session product-CLI slices."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .session_cli_profile import SESSION_CLI_COLLECTION_PROFILE_CONTRACT


class WorkspaceCliRunnerError(RuntimeError):
    pass


GROUP_CREATE_OPERATIONS = frozenset({
    "group.create", "list.group.create", "status.group.create",
    "detail.group.create", "fuse.group.create", "session-file.group.create",
    "session-cli.group.create", "termination.group.create",
    "session-cli.service.group.create", "member.group.create",
})


@dataclass(frozen=True)
class WorkspaceCliCommandContext:
    profile_contract: str
    driver_id: str
    member_id: str
    session_file_source: Path
    session_file_download: Path


def _require_group_id(group_id: object) -> str:
    if not isinstance(group_id, str) or not group_id:
        raise WorkspaceCliRunnerError("workspace CLI operation requires the exact Group ID")
    return group_id


def _create_contract(context: WorkspaceCliCommandContext, operation: str):
    if operation == "group.create":
        topic = "bcs-e2e-workspace-cli-create-get"
    elif operation == "list.group.create":
        topic = "bcs-e2e-workspace-cli-list"
    elif operation == "status.group.create":
        topic = "bcs-e2e-workspace-cli-status-completion"
    elif operation == "detail.group.create":
        topic = "bcs-e2e-workspace-cli-detail"
    elif operation == "fuse.group.create":
        topic = "bcs-e2e-workspace-cli-fuse"
    elif operation == "session-file.group.create":
        topic = "bcs-e2e-session-file-core"
    elif operation == "session-cli.group.create":
        topic = ("bcs-e2e-session-collection"
                 if context.profile_contract
                 == SESSION_CLI_COLLECTION_PROFILE_CONTRACT
                 else "bcs-e2e-session-cli-create-list-get")
    elif operation == "member.group.create":
        topic = "bcs-e2e-workspace-cli-member"
    elif operation == "session-cli.service.group.create":
        return ("driver", "POST", "/groups", (
            "create-group", "--manager", context.driver_id,
            "--participants", f"{context.driver_id},{context.member_id}",
            "--topic", "bcs-e2e-session-complete-invoke",
        ))
    elif operation == "termination.group.create":
        topic = "bcs-e2e-workspace-cli-status-termination"
    else:
        raise WorkspaceCliRunnerError("unsupported workspace CLI Group creation")
    return ("driver", "POST", "/groups", (
        "create-group", "--driver", context.driver_id,
        "--participants", f"{context.driver_id},{context.member_id}", "--topic", topic,
    ))


def build_contract(context: WorkspaceCliCommandContext, operation: str,
                   group_id: str | None, session_id: str | None = None,
                   file_id: str | None = None, session_title: str | None = None,
                   message: str | None = None):
    if operation in GROUP_CREATE_OPERATIONS:
        return _create_contract(context, operation)
    if operation in ("group.get", "detail.group.get"):
        gid = _require_group_id(group_id)
        return ("driver", "GET", f"/groups/{gid}", ("get-group", "--id", gid))
    if operation == "fuse.group.fuse":
        gid = _require_group_id(group_id)
        return ("driver", "POST", f"/groups/{gid}/fuse", (
            "fuse", "--group", gid, "--question", "how to align?",
            "--participants", f"{context.driver_id},{context.member_id}",
        ))
    if operation == "member.group.add-member":
        gid = _require_group_id(group_id)
        return ("driver", "POST", f"/groups/{gid}/members", (
            "add-member", "--group", gid, "--bot-uuid", context.member_id,
        ))
    if operation == "member.group.read":
        gid = _require_group_id(group_id)
        return ("driver", "GET", f"/groups/{gid}", ("get-group", "--id", gid))
    if operation == "session-file.session.create":
        gid = _require_group_id(group_id)
        return ("driver", "POST", f"/groups/{gid}/sessions",
                ("session", "create", "--group", gid, "--title", "file-ws"))
    if operation == "session-cli.session.create":
        gid = _require_group_id(group_id)
        title = session_title or "cli-e2e-sess"
        return ("driver", "POST", f"/groups/{gid}/sessions",
                ("session", "create", "--group", gid, "--title", title))
    if operation == "session-cli.session.list":
        gid = _require_group_id(group_id)
        return ("driver", "GET", f"/groups/{gid}/sessions",
                ("session", "list", "--group", gid))
    if operation in ("session-cli.session.get", "session-cli.session.get.muted", "session-cli.session.get.removed"):
        sid = _require_group_id(session_id)
        return ("driver", "GET", f"/sessions/{sid}",
                ("session", "get", sid))
    if operation == "session-cli.session.patch":
        sid = _require_group_id(session_id)
        title = session_title or "CLI release sign-off"
        return ("driver", "PATCH", f"/sessions/{sid}",
                ("session", "patch", sid, "--title", title))
    if operation == "session-cli.session.invite":
        sid = _require_group_id(session_id)
        return ("driver", "POST", f"/sessions/{sid}/invite-link",
                ("session", "invite-link", sid, "--ttl-seconds", "300"))
    if operation == "session-cli.session.complete":
        sid = _require_group_id(session_id)
        return ("driver", "POST", f"/sessions/{sid}/complete",
                ("session", "complete", sid, "--output",
                 '{"summary":"approved by CLI"}'))
    if operation == "session-cli.session.member.add":
        sid = _require_group_id(session_id)
        return ("driver", "POST", f"/sessions/{sid}/members", (
            "session", "add-member", sid, "--bot-uuid", context.member_id,
            "--role", "consultant",
        ))
    if operation == "session-cli.session.member.patch_mode":
        sid = _require_group_id(session_id)
        return (
            "driver", "PATCH", f"/sessions/{sid}/members/{context.member_id}",
            ("session", "set-member-mode", sid, context.member_id,
             "--mode", "muted"),
        )
    if operation == "session-cli.session.chat":
        sid = _require_group_id(session_id)
        if not isinstance(message, str) or not message:
            raise WorkspaceCliRunnerError("Session CLI chat requires its exact message")
        return ("driver", "POST", f"/sessions/{sid}/chat", (
            "session", "chat", "--session", sid, "--message", message,
        ))
    if operation == "session-cli.session.messages":
        sid = _require_group_id(session_id)
        return ("driver", "GET", f"/sessions/{sid}/messages", (
            "session", "messages", sid, "--view-bot", context.driver_id,
            "--limit", "50",
        ))
    if operation == "session-cli.session.member.remove":
        sid = _require_group_id(session_id)
        return (
            "driver", "DELETE", f"/sessions/{sid}/members/{context.member_id}",
            ("session", "remove-member", sid, context.member_id),
        )
    if operation == "session-cli.service.invoke":
        gid = _require_group_id(group_id)
        return (
            "driver", "POST", f"/services/{gid}/sessions", (
                "service", "invoke", "--group", gid,
                "--input", '{"commit":"cli123"}',
                "--meta", '{"source":"cli-story"}',
                "--caller-id", "cli-release-pipeline",
                "--title", "CLI automated release audit", "--detach",
            ),
        )
    if operation in ("session-cli.service.status", "session-cli.service.wait"):
        gid = _require_group_id(group_id)
        sid = _require_group_id(session_id)
        if not sid.startswith(gid + ":"):
            raise WorkspaceCliRunnerError(
                "Session Service CLI service route requires its exact Session")
        if operation == "session-cli.service.status":
            arguments = ("service", "status", sid)
        else:
            arguments = ("service", "wait", sid, "--timeout-ms", "1")
        return ("driver", "GET", f"/services/{gid}/sessions/{sid}", arguments)
    if operation == "session-file.upload":
        sid = _require_group_id(session_id)
        return ("driver", "POST", f"/sessions/{sid}/files", (
            "session", "file", "upload", "--session", sid,
            "--path", str(context.session_file_source),
        ))
    if operation == "session-file.list":
        sid = _require_group_id(session_id)
        return ("driver", "GET", f"/sessions/{sid}/files",
                ("session", "file", "list", "--session", sid))
    if operation == "session-file.capabilities":
        sid = _require_group_id(session_id)
        return ("driver", "GET", f"/sessions/{sid}/files/capabilities",
                ("session", "file", "capabilities", "--session", sid))
    if operation == "session-file.download":
        sid = _require_group_id(session_id)
        fid = _require_group_id(file_id)
        return ("driver", "GET", f"/sessions/{sid}/files/{fid}/content", (
            "session", "file", "download", "--session", sid,
            "--file-id", fid, "--out", str(context.session_file_download),
        ))
    if operation == "session-file.share":
        sid = _require_group_id(session_id)
        fid = _require_group_id(file_id)
        return ("driver", "POST", f"/sessions/{sid}/files/{fid}/share",
                ("session", "file", "share", "--session", sid,
                 "--file-id", fid))
    if operation == "session-file.delete":
        sid = _require_group_id(session_id)
        fid = _require_group_id(file_id)
        return ("driver", "DELETE", f"/sessions/{sid}/files/{fid}",
                ("session", "file", "delete", "--session", sid,
                 "--file-id", fid))
    if operation == "list.group.list":
        return ("driver", "GET", "/groups/my?offset=0&limit=50",
                ("list-groups", "--batch-size", "50"))
    if operation == "status.group.read.active":
        gid = _require_group_id(group_id)
        return ("driver", "GET", f"/groups/{gid}", ("get-group", "--id", gid))
    if operation == "status.group.status":
        gid = _require_group_id(group_id)
        return ("driver", "PUT", f"/groups/{gid}/status", (
            "group-status", "--group", gid, "--status", "completed",
            "--reason", "bcs-e2e-workspace-cli-completion"))
    if operation == "status.group.read.completed":
        gid = _require_group_id(group_id)
        return ("driver", "GET", f"/groups/{gid}", ("get-group", "--id", gid))
    if operation == "termination.group.read.active":
        gid = _require_group_id(group_id)
        return ("driver", "GET", f"/groups/{gid}", ("get-group", "--id", gid))
    if operation == "termination.group.terminate":
        gid = _require_group_id(group_id)
        return ("driver", "POST", f"/groups/{gid}/terminate",
                ("terminate-group", "--group", gid))
    if operation == "termination.group.read.completed":
        gid = _require_group_id(group_id)
        return ("driver", "GET", f"/groups/{gid}", ("get-group", "--id", gid))
    raise WorkspaceCliRunnerError("unsupported workspace CLI operation")
