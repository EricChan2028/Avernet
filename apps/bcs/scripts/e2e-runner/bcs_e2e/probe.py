"""Explicit, read-only transport probes.

Probes exercise only finite, explicitly authorized surfaces. They do not
create fixtures, issue session tokens, or fall back to a second origin. The
WebSocket probe may connect only to a live group/session already owned by the
Run and receives its short-lived token through an environment reference.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .ledger import Ledger, LedgerError
from .profile import ConfigError, fingerprint, identifier
from .run_state import read as read_state, require_profile_binding
from .transport import ObservationError, ReadClient
from .ws_transport import SessionWebSocketClient, WebSocketTransportError


_READ_OPERATIONS = ("environment.read", "identity.read", "bot.read")
_TOKEN_ENV = re.compile(r"^[A-Z][A-Z0-9_]{2,100}$")


def _credential_values(profile: dict) -> tuple[str, ...]:
    values = []
    for reference in profile["auth"]["header_env"].values():
        value = os.environ.get(reference, "")
        if value:
            values.append(value)
    return tuple(values)


def http_probe(profile: dict, operation: str, *, bot_id: str = "") -> dict:
    """Run one explicit GET and return metadata only, never the response body."""
    if operation not in _READ_OPERATIONS:
        raise ConfigError("probe http: unsupported read-only operation")
    if operation == "bot.read" and not bot_id:
        raise ConfigError("probe http: bot.read requires --bot-id")
    client = ReadClient(profile)
    try:
        observation = client.read(operation, bot_id)
        if operation == "identity.read" and observation.data.get("user_id") != profile["human"]["user_id"]:
            raise ObservationError("authenticated identity differs from the expected Human")
    except ObservationError as error:
        return {
            "schema_version": 1, "status": "BLOCKED", "operation": operation,
            "auth_leak_check": "PASS", "cross_origin_auth": "NOT_SENT",
            "reason": "read-only HTTP probe failed",
        }
    evidence = observation.evidence
    prerequisites = (client.evidence[:-1]
                     if evidence.get("identity_basis") == "server-authorized-human-view" else [])
    serialized = json.dumps([*prerequisites, evidence], ensure_ascii=False, sort_keys=True)
    if any(secret in serialized for secret in _credential_values(profile)):
        return {
            "schema_version": 1, "status": "BLOCKED", "operation": operation,
            "auth_leak_check": "FAIL", "cross_origin_auth": "NOT_SENT",
            "reason": "credential appeared in probe evidence",
        }
    return {
        "schema_version": 1, "status": "PROBE_PASSED", "operation": operation,
        "auth_leak_check": "PASS", "cross_origin_auth": "NOT_SENT",
        "redirect_policy": "REJECTED", "evidence": evidence,
        **({"prerequisite_requests": prerequisites} if prerequisites else {}),
    }


def _owned_ws_scope(state_dir: Path, run_id: str, profile: dict,
                    group_id: str, session_id: str) -> dict:
    state = read_state(state_dir / "run.json")
    if state.get("run_id") != run_id:
        raise ConfigError("probe ws: run ID does not match state")
    digest = require_profile_binding(state, profile)
    if (not isinstance(state.get('status'), str)
            or state['status'] not in {'PREPARED', 'RUNNING'}
            or 'last_recovery' in state or 'recovery_origin_status' in state):
        raise ConfigError('probe ws: Run is not admitted for a new diagnostic connection')
    ledger_path = state.get("ledger_path")
    canonical_ledger = state_dir / "run.sqlite"
    if (not isinstance(ledger_path, str) or Path(ledger_path).is_symlink()
            or canonical_ledger.is_symlink()
            or Path(ledger_path).resolve() != canonical_ledger.resolve()):
        raise ConfigError("probe ws: journal must be the existing Run-local ledger")
    try:
        with Ledger(canonical_ledger, run_id, digest, read_only=True) as ledger:
            rows = ledger.resource_snapshot()
    except (LedgerError, OSError):
        raise ConfigError("probe ws: prepared ledger is unreadable") from None
    groups = {row["handle"]: row for row in rows
              if row["kind"] == "group" and row["resource_id"] == group_id
              and row["origin"] == "created" and row["state"] == "live"}
    sessions = [row for row in rows
                if row["kind"] == "session" and row["resource_id"] == session_id
                and row["origin"] == "created" and row["state"] == "live"]
    if len(groups) != 1 or len(sessions) != 1 or sessions[0]["parent"] not in groups:
        raise ConfigError("probe ws: group/session are not live resources owned by this Run")
    return {'run': fingerprint(state), 'scope': fingerprint([*groups.values(), *sessions])}


def ws_probe(profile: dict, state_dir: Path, run_id: str, *, token_env: str,
             group_id: str, session_id: str, client_factory=SessionWebSocketClient) -> dict:
    """Probe a run-owned session without creating or deleting product resources."""
    if profile["entrypoint"]["kind"] == "mock":
        raise ConfigError("probe ws: local mock profiles have no WebSocket endpoint")
    if not isinstance(token_env, str) or not _TOKEN_ENV.fullmatch(token_env):
        raise ConfigError("probe ws: token environment reference is invalid")
    if not isinstance(group_id, str) or not isinstance(session_id, str):
        raise ConfigError("probe ws: group and session IDs are required")
    try:
        identifier(group_id, "group_id")
        identifier(session_id, "session_id")
    except ConfigError:
        raise ConfigError("probe ws: resource ID is invalid") from None
    before = _owned_ws_scope(state_dir, run_id, profile, group_id, session_id)
    token = os.environ.get(token_env, "")
    if not token or not token.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise ConfigError("probe ws: session token environment value is missing or invalid")
    connection = None
    report = {'schema_version': 1, 'status': 'BLOCKED', 'ready_to_execute': False,
              'auth_leak_check': 'PASS', 'cross_origin_auth': 'NOT_SENT',
              'socket_close_status': 'NOT_ACQUIRED', 'close_verification': 'LOCAL_HANDLE_ONLY',
              'execution_status': 'UNCONFIRMED', 'pool_release_authorized': False,
              'failure_phases': []}
    phase = 'HANDSHAKE'
    try:
        client = client_factory(profile["entrypoint"]["origin"],
                                timeout_seconds=profile["policy"]["timeout_seconds"])
        connection, record = client.connect(token, group_id, session_id)
        if connection is None:
            raise WebSocketTransportError('probe did not acquire a connection')
        report['socket_close_status'] = 'UNCONFIRMED'
        phase = 'SCOPE_RECHECK'
        if _owned_ws_scope(state_dir, run_id, profile, group_id, session_id) != before:
            raise ConfigError('probe scope changed during the handshake')
        phase = 'EVIDENCE'
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if token in serialized:
            report.update(auth_leak_check='FAIL', reason='session token appeared in WebSocket probe evidence')
            report['failure_phases'].append('EVIDENCE')
        else:
            report.update(status='PROBE_PASSED', group_id=group_id, session_id=session_id, evidence=record)
    except KeyboardInterrupt:
        report.update(status='CANCELLED', reason='WebSocket probe cancelled')
        report['failure_phases'].append(phase)
    except Exception:
        report.update(status='BLOCKED', reason='WebSocket probe failed')
        report['failure_phases'].append(phase)
    finally:
        if connection is not None:
            try:
                connection.close()
            except KeyboardInterrupt:
                report.update(status='CANCELLED', reason='WebSocket probe cancelled while closing its connection')
                report['failure_phases'].append('LOCAL_CLOSE')
            except Exception:
                report['failure_phases'].append('LOCAL_CLOSE')
                if report['status'] == 'PROBE_PASSED':
                    report.update(status='BLOCKED', reason='WebSocket probe local connection closure is unconfirmed')
            else:
                report['socket_close_status'] = 'CLOSED'
    return report
