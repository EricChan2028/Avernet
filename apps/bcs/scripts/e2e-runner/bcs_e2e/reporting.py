"""Structured report generation for pilot runs.

Reports distinguish the pilot result from the full legacy release inventory.
They intentionally contain only redacted evidence summaries, not response bodies,
credentials, signed URLs or chat content.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import time
import xml.etree.ElementTree as ET

from .execution_safety import inspect_execution
from .global_policy_scope import global_policy_view
from .retained_reporting import retained_summary, retained_markdown, retained_axes, retained_properties
from .ledger import Ledger, LedgerError
from .inventory import frozen_manifest
from .run_state import RunStateError
from .profile import ConfigError, fingerprint
from .retained_contract import retained_suites
from .remote_observer import binding_summary, frozen_binding
from .run_state import read as read_state


_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "secret", "password", "token", "signature", "credential", "api_key")
_SENSITIVE_QUERY_PARTS = _SENSITIVE_KEY_PARTS + ("access_key", "security_token", "session")
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[^\s,;]+")
_QUERY_RE = re.compile(r'(?P<url>https?://[^\s<>\"\']+)')


def _redact_string(value: str) -> str:
    value = _BEARER_RE.sub(r"\1[REDACTED]", value)
    def replace_url(match):
        raw = match.group("url")
        trailing = ""
        while raw and raw[-1] in ".,);]":
            trailing, raw = raw[-1] + trailing, raw[:-1]
        try:
            parts = urlsplit(raw)
            query = [(key, "[REDACTED]" if any(part in key.lower() for part in _SENSITIVE_QUERY_PARTS) else val)
                     for key, val in parse_qsl(parts.query, keep_blank_values=True)]
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), "")) + trailing
        except ValueError:
            return "[REDACTED_URL]"
    return _QUERY_RE.sub(replace_url, value)


def redact(value):
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in _SENSITIVE_KEY_PARTS):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _read_json(path: Path) -> dict | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _case_reports(state: dict, profile: dict, state_dir: Path, manifest: dict) -> tuple[dict[str, dict], list[str]]:
    """Load only reports that belong to this Run/Profile and frozen manifest."""
    expected = {case.get("id") for case in manifest.get("cases", []) if isinstance(case, dict)}
    reports: dict[str, dict] = {}
    issues: list[str] = []
    for path in sorted(state_dir.glob("report-*.json")):
        value = _read_json(path)
        file_case = path.name[len("report-"):-len(".json")]
        if file_case not in expected:
            issues.append(f"unknown report file: {path.name}")
            continue
        if value is None:
            issues.append(f"unreadable report: {path.name}")
            continue
        case_id = value.get("case_id")
        if case_id != file_case:
            issues.append(f"report filename/case mismatch: {path.name}")
            continue
        if value.get("run_id") != state.get("run_id"):
            issues.append(f"report run mismatch: {path.name}")
            continue
        if value.get("profile_digest") != fingerprint(profile):
            issues.append(f"report profile mismatch: {path.name}")
            continue
        if value.get("schema_version") != 1:
            issues.append(f"report schema mismatch: {path.name}")
            continue
        case = next(item for item in manifest['cases'] if item['id'] == case_id)
        if (value.get('manifest_digest') != state.get('manifest_digest')
                or value.get('executor_digest') != state.get('executor_digest')
                or type(value.get('case_version')) is not int or value.get('case_version') != case['version']):
            issues.append(f"report manifest/executor/Case version mismatch: {path.name}")
            continue
        if value.get("observer_binding") != state.get("observer_binding"):
            issues.append(f"report observer binding mismatch: {path.name}")
            continue
        if case_id in reports:
            issues.append(f"duplicate case report: {case_id}")
            continue
        reports[case_id] = value
    return reports, issues


def _execution_observation_summary(value) -> dict:
    """Project bounded counters only; observed tasks never imply scope completion."""
    invalid = {'status': 'INVALID', 'scope_status': 'UNCONFIRMED',
               'session_count': 0, 'known_completed_send_count': 0}
    if (not isinstance(value, dict) or value.get('scope_status') != 'UNCONFIRMED'
            or value.get('status') not in {'NOT_CONFIGURED', 'NOT_RUN', 'OBSERVED', 'INCOMPLETE', 'BLOCKED', 'TIMED_OUT', 'CANCELLED'}):
        return invalid
    sessions = value.get('sessions', [])
    if not isinstance(sessions, list) or len(sessions) > 2:
        return invalid
    count = 0
    for session in sessions:
        if not isinstance(session, dict) or session.get('scope_status') != 'UNCONFIRMED':
            return invalid
        completed = session.get('known_completed_send_ids')
        if (not isinstance(completed, list) or len(completed) > 99
                or any(not isinstance(item, str) or len(item) > 128 for item in completed)):
            return invalid
        count += len(completed)
    result = {'status': value['status'], 'scope_status': 'UNCONFIRMED',
              'session_count': len(sessions), 'known_completed_send_count': count}
    maximum_rounds = 3
    from .observation_budget import BUDGETED_WAIT_CONTRACT, reviewed_budget
    if value.get('contract') == BUDGETED_WAIT_CONTRACT or 'observation_budget' in value:
        try:
            budget = reviewed_budget(value.get('contract'), value.get('observation_budget'))
        except ConfigError:
            return invalid
        if budget is None:
            return invalid
        maximum_rounds = budget.max_rounds
        result.update(observation_timeout_seconds=budget.timeout_seconds,
                      maximum_observation_rounds=budget.max_rounds,
                      poll_interval_seconds=budget.poll_interval_seconds)
    if 'rounds' in value:
        rounds = value['rounds']
        stop = value.get('stop_reason')
        if (not isinstance(rounds, list) or len(rounds) > maximum_rounds
                or stop not in {'KNOWN_DELIVERIES_SETTLED', 'OBSERVATION_LIMIT', 'INCONSISTENT_HISTORY',
                                'TIMED_OUT', 'BLOCKED', 'CANCELLED'}):
            return invalid
        result.update(observation_rounds=len(rounds), stop_reason=stop)
    if 'downstream_observation' in value:
        downstream = value['downstream_observation']
        if (not isinstance(downstream, dict) or downstream.get('scope_status') != 'UNCONFIRMED'
                or downstream.get('status') not in {'NOT_RUN', 'OBSERVED', 'INCOMPLETE', 'CANCELLED'}
                or not isinstance(downstream.get('runs'), list) or len(downstream['runs']) > 4):
            return invalid
        completed = 0
        for run in downstream['runs']:
            if (not isinstance(run, dict) or run.get('scope_status') != 'UNCONFIRMED'
                    or run.get('callback_status') != 'UNCONFIRMED'
                    or type(run.get('known_run_result_completed')) is not bool):
                return invalid
            completed += run['known_run_result_completed']
        result.update(downstream_status=downstream['status'], downstream_run_count=len(downstream['runs']),
                      known_downstream_completed_count=completed, downstream_callback_status='UNCONFIRMED')
    return result


def _validate_case(case: dict, actual: dict | None, required_evidence: list[str]) -> list[str]:
    if actual is None:
        return []
    issues: list[str] = []
    statuses = {"PASS", "FAIL", "BLOCKED", "SKIPPED", "CANCELLED", "ERROR"}
    declared_evidence = actual.get("evidence_required")
    if (not isinstance(declared_evidence, list)
            or len(declared_evidence) != len(required_evidence)
            or any(not isinstance(item, str) for item in declared_evidence)
            or len(set(declared_evidence)) != len(declared_evidence)
            or set(declared_evidence) != set(required_evidence)):
        issues.append(f"{case['id']} required evidence declaration is missing, malformed or mismatched")
    if ('execution_observation' in actual
            and _execution_observation_summary(actual['execution_observation'])['status'] == 'INVALID'):
        issues.append(f"{case['id']} execution observation has invalid or unsupported scope evidence")
    for key in ("status", "functional", "cleanup", "evidence_status"):
        if not isinstance(actual.get(key), str) or not actual[key]:
            issues.append(f"{case['id']} missing {key}")
    if actual.get("status") not in statuses:
        issues.append(f"{case['id']} has invalid case status")
    assertions = actual.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        issues.append(f"{case['id']} has no assertion records")
        return issues
    ids = [item.get("id") for item in assertions if isinstance(item, dict)]
    if len(ids) != len(assertions) or any(not isinstance(item, str) or not item for item in ids):
        issues.append(f"{case['id']} has malformed assertion IDs")
    if len(set(ids)) != len(ids):
        issues.append(f"{case['id']} has duplicate assertion IDs")
    declared = set(case.get("assertions", []))
    missing = sorted(declared - set(ids))
    if actual.get("status") == "PASS" and missing:
        issues.append(f"{case['id']} is missing declared assertions: {','.join(missing)}")
    for item in assertions:
        if not isinstance(item, dict) or item.get("status") not in statuses:
            issues.append(f"{case['id']} has invalid assertion status")
    if actual.get("status") == "PASS" and any(
            isinstance(item, dict) and item.get("status") != "PASS" for item in assertions):
        issues.append(f"{case['id']} claims PASS with a nonpassing assertion")
    return issues


def _routes(reports: dict[str, dict]) -> list[dict]:
    seen = set()
    result = []
    for report in reports.values():
        for item in report.get("evidence", []):
            if not isinstance(item, dict):
                continue
            operation = item.get("operation")
            method = item.get("method")
            path = item.get("path")
            key = (operation, method, path)
            if all(isinstance(part, str) for part in key) and key not in seen:
                seen.add(key)
                result.append({"operation": operation, "method": method, "path": path})
    return result


def _diagnostic_status(rows: list[dict], case_id: str, actual: dict | None = None) -> str:
    values = [row.get("status") for row in rows if row.get("case_id") == case_id]
    if actual:
        values.append(actual.get("diagnostic_status", "NOT_RUN"))
    # A query budget can be exhausted even when every persisted row observed
    # so far succeeded. Never let those rows overwrite the collection summary.
    if any(value not in {"OBSERVED", "PASS", "NOT_RUN", "INCOMPLETE"} for value in values):
        return "BLOCKED"
    if "INCOMPLETE" in values:
        return "INCOMPLETE"
    return "PASS" if any(value in {"OBSERVED", "PASS"} for value in values) else "NOT_RUN"


def _timing(reports: dict[str, dict]) -> dict:
    values = []
    for report in reports.values():
        for item in report.get("evidence", []):
            if isinstance(item, dict) and isinstance(item.get("elapsed_ms"), (int, float)):
                elapsed = float(item["elapsed_ms"])
                if math.isfinite(elapsed) and elapsed >= 0:
                    values.append(elapsed)
    return {
        "steps": len(values),
        "total_elapsed_ms": round(sum(values), 2),
        "max_step_elapsed_ms": round(max(values), 2) if values else 0.0,
        "interpretation": "functional diagnostic timing only; not a performance benchmark",
    }


def build_report(profile: dict, state_dir: Path, manifest: dict | None = None) -> dict:
    if profile.get('schema_version') == 2:
        from .trust_reporting import build_trust_report
        return build_trust_report(profile, state_dir)
    state = read_state(state_dir / "run.json")
    integrity: list[str] = []
    try:
        manifest = frozen_manifest(state_dir, state)
    except (RunStateError, OSError):
        integrity.append("Run frozen manifest or executor binding is missing, unreadable or mismatched")
        # Supplied inventory can help display a historical diagnostic, never
        # establish qualification for an unbound Run. No current-file fallback.
        manifest = manifest or {"cases": [], "legacy_inventory": []}
    if state.get("profile_digest") != fingerprint(profile):
        integrity.append("Run/profile fingerprint mismatch")
    observer_binding = {"verification_mode": "NOT_ATTACHED", "live_verification": "NOT_PERFORMED"}
    if "observer_binding" in state:
        try:
            observer_binding = binding_summary(frozen_binding(state_dir, state, profile, execution=False))
        except (ConfigError, RunStateError, OSError):
            observer_binding = {"verification_mode": "INVALID", "live_verification": "NOT_PERFORMED"}
            integrity.append("Run observer binding is missing, unreadable or mismatched")
    reports, report_issues = _case_reports(state, profile, state_dir, manifest)
    integrity.extend(report_issues)
    serial = None
    if 'admission_policy' in state:
        try:
            from .retained_serial import policy_view
            if manifest.get('suite') not in retained_suites():
                raise ConfigError('serial policy applies only to retained Cases')
            serial = policy_view(profile, state, state_dir, require_finalized=True)
        except (ConfigError, RunStateError, OSError):
            integrity.append('serial admission policy or receipt is invalid')
    correlations = []
    diagnostics = []
    resources = None
    execution_required = state.get("execution_contract_required", False) or any(
        report.get("execution_safety", {}).get("status") in {"CONFIRMED", "BLOCKED"}
        for report in reports.values())
    execution = {"status": "NOT_TRACKED", "issues": []}
    ledger_path = state.get("ledger_path")
    if isinstance(ledger_path, str):
        try:
            with Ledger(Path(ledger_path), state.get("run_id", ""), fingerprint(profile), read_only=True) as ledger:
                if any(case.get("finalization") == "retain" for case in manifest.get("cases", [])):
                    resources = ledger.resource_snapshot()
                correlations = ledger.correlations()
                diagnostics = ledger.diagnostics()
                execution = inspect_execution(ledger, required=execution_required)
                if execution["status"] == "BLOCKED" and serial is None:
                    integrity.extend(execution["issues"])
        except (LedgerError, OSError, sqlite3.Error):
            execution = {"status": "BLOCKED", "issues": ["execution ledger unreadable"]}
            correlations = []
            diagnostics = []
            integrity.append("correlation ledger is missing, unreadable or has an unsupported schema")
    if execution_required and execution["status"] == "NOT_TRACKED" and serial is None:
        execution = {"status": "BLOCKED", "issues": ["required execution ledger is missing"]}
        integrity.extend(execution["issues"])
    if serial is not None:
        execution['qualification_role'] = 'DIAGNOSTIC'
    retention, retention_issues = retained_summary(profile, state, state_dir, manifest, reports,
                                                   resources, now_epoch=int(time.time()))
    integrity.extend(retention_issues)
    declared = manifest.get("cases", [])
    cases = []
    for case in declared:
        case_id = case["id"]
        actual = reports.get(case_id)
        required_evidence = ["sqlite" if item == "ob"
                             and profile["entrypoint"]["kind"] == "singlebox"
                             and profile["observer"]["kind"] == "local-sqlite" else item
                             for item in case.get("required_evidence", [])]
        case_issues = _validate_case(case, actual, required_evidence)
        integrity.extend(case_issues)
        status = actual.get("status", "BLOCKED") if actual else "BLOCKED"
        if status == "PASS" and case_issues:
            status = "BLOCKED"
        allowed_evidence = {"PASS"}
        if profile["entrypoint"]["kind"] == "mock" and profile["environment"]["tier"] == "local":
            allowed_evidence.add("LOCAL_SURROGATE")
        if status == "PASS" and actual and (
            state.get("status") in {"QUARANTINED", "RECOVERING", "RECOVERED"}
            or "last_recovery" in state or "recovery_origin_status" in state
            or actual.get("operation_authority") == "INVALIDATED"
            or (serial is None and execution["status"] == "BLOCKED")
            or (serial is None and actual.get("quiescence") == "UNKNOWN")
            or (case.get("finalization") == "retain" and bool(retention_issues))
            or actual.get("evidence_status") not in allowed_evidence
            or actual.get("functional") != "PASS"
            or actual.get("cleanup") != ("RETAINED" if serial is not None else "PASS")
            or (serial is not None and actual.get("socket_status") not in {"NOT_OPENED", "CLOSED"})
        ):
            status = "BLOCKED"
        cases.append({
            "id": case_id,
            "title": case.get("title", ""),
            "version": case.get("version"),
            "required": True,
            "execution_safety": execution["status"],
            "status": status,
            "functional": actual.get("functional", "NOT_RUN") if actual else "NOT_RUN",
            "cleanup": actual.get("cleanup", "NOT_RUN") if actual else "NOT_RUN",
            "evidence_status": actual.get("evidence_status", "NOT_RUN") if actual else "NOT_RUN",
            "required_evidence": required_evidence,
            "diagnostic_evidence": case.get("diagnostic_evidence", []),
            "diagnostic_status": _diagnostic_status(diagnostics, case_id, actual),
            "diagnostic_warnings": actual.get("diagnostic_warnings", []) if actual else [],
            "diagnostic_summary": actual.get("diagnostic_evidence", []) if actual else [],
            "evidence_observation_count": len(actual.get("semantic_evidence", [])) if actual else 0,
            "assertion_count": len(actual.get("assertions", [])) if actual else 0,
            "remote_validation": case.get("remote_validation", "NOT_RUN"),
            **(retained_axes(case_id, actual, state, retention)
               if case.get('finalization') == 'retain' else {}),
            **({'execution_observation': _execution_observation_summary(actual['execution_observation'])}
               if actual and 'execution_observation' in actual else {}),
        })
    all_pilot_pass = bool(cases) and all(
        case["status"] == "PASS" and case["evidence_status"] in {"PASS", "LOCAL_SURROGATE"}
        for case in cases
    )
    cleanup_file = _read_json(state_dir / "cleanup.json")
    cleanup_statuses = [case["cleanup"] for case in cases]
    if cleanup_file:
        cleanup_statuses.append(cleanup_file.get("status", "UNKNOWN"))
    cleanup_ok = all(status in {"PASS", "NOT_RUN", "DRY_RUN"} for status in cleanup_statuses)
    mock_boundary = {
        "entrypoint_kind": profile["entrypoint"]["kind"],
        "local_mock": profile["entrypoint"]["kind"] == "mock",
        "not_proven": [
            "remote deployment behavior" if profile["entrypoint"]["kind"] == "mock" else "none",
            "remote code coverage",
            "performance benchmark qualification",
        ],
    }
    if profile["entrypoint"]["kind"] == "singlebox":
        mock_boundary["not_proven"] = ["Gateway/SSO authentication (owned local mock identity)",
                                        "OB persistence (local SQLite is not OB)",
                                        "remote deployment behavior", "remote code coverage",
                                        "performance benchmark qualification"]
    if cleanup_file is not None:
        if cleanup_file.get("run_id") != state.get("run_id"):
            integrity.append("cleanup report Run mismatch")
        if cleanup_file.get("status") not in {"PASS", "BLOCKED", "DRY_RUN"}:
            integrity.append("cleanup report has invalid status")
    summary_status = "PILOT_PASS" if all_pilot_pass and cleanup_ok and not integrity else "BLOCKED"
    selected = state.get('attempted_cases', []) if serial is not None else []
    if (serial is not None and len(selected) == 1 and set(reports) == set(selected)
            and not integrity and all(c['status'] == 'PASS' for c in cases if c['id'] in selected)):
        summary_status = 'RETAINED_CASE_PASS'
    return redact({
        "schema_version": 1,
        "status": summary_status,
        "run_id": state.get("run_id"),
        "profile_digest": fingerprint(profile),
        "runner": {"version": 1, "baseline_commit": manifest.get("baseline_commit", "unknown"),
                   "executor_digest": state.get("executor_digest", "unknown")},
        "target": {
            "tier": profile["environment"]["tier"],
            "environment_id": profile["environment"]["id"],
            "entrypoint": profile["entrypoint"],
            "deployment_version": "unknown",
        },
        "scope": {
            "suite": manifest.get("suite", "unknown"),
            "manifest_digest": state.get("manifest_digest", "unknown"),
            "global_policy": global_policy_view(),
            "pilot_cases": [case["id"] for case in cases],
            **({'selected_cases': selected, 'admission_policy': serial['contract'],
                'qualification_scope': 'SELECTED_RETAINED_CASE_ONLY'} if serial is not None else {}),
            "legacy_inventory_count": len(manifest.get("legacy_inventory", [])),
            "full_release_status": "NOT_QUALIFIED",
            "reason": "pilot reports do not cover the frozen legacy release inventory",
        },
        "observer_binding": observer_binding,
        "cases": cases,
        "routes": _routes(reports),
        "timing": _timing(reports),
        "mock_boundary": mock_boundary,
        "cleanup": {"status": cleanup_file.get("status") if cleanup_file else "CASE_SCOPED",
                    "case_statuses": cleanup_statuses,
                    "verification_scope": "REGISTERED_RESOURCE_API_CONVERGENCE",
                    "verification_mode": "LOCAL_SURROGATE" if profile["entrypoint"]["kind"] == "mock" else "PRODUCT_API",
                    "physical_data_erasure": "NOT_VERIFIED",
                    "associated_history_purge": "NOT_VERIFIED",
                    "retention_disposition": "ENVIRONMENT_OWNER_REVIEW_REQUIRED",
                    "note": "Cleanup PASS does not prove removal of all history, storage replicas or backups; no DB/bucket-wide cleanup is authorized."},
        "evidence": [redact(report.get("evidence", [])) for report in reports.values()],
        "execution_safety": execution,
        **({"retention": retention} if retention else {}),
        "correlations": redact(correlations),
        "diagnostics": redact(diagnostics),
        "integrity": {"status": "PASS" if not integrity else "BLOCKED", "issues": integrity},
        "reports": {"case_count": len(reports), "declared_case_count": len(cases),
                    "expected_assertion_count": sum(len(case.get("assertions", [])) for case in declared),
                    "observed_assertion_count": sum(len(report.get("assertions", [])) for report in reports.values())},
        "generated_at": int(time.time()),
    })


def render_junit(summary: dict) -> str:
    failures = sum(case["status"] == "FAIL" for case in summary["cases"])
    errors = sum(case["status"] == "ERROR" for case in summary["cases"])
    skipped = sum(case["status"] in {"BLOCKED", "SKIPPED", "CANCELLED"} for case in summary["cases"])
    suite = summary.get("scope", {}).get("suite")
    suite_name = {
        "owned-local-trusted-team-v1": "bcs-e2e-trusted-team",
        "owned-local-workspace-v1": "bcs-e2e-workspace",
        "owned-local-network-v1": "bcs-e2e-network",
        "owned-local-provider-v1": "bcs-e2e-provider",
        "owned-local-channel-http-v1": "bcs-e2e-channel-http",
        "owned-local-channel-cli-v1": "bcs-e2e-channel-cli",
        "owned-local-operator-cli-v1": "bcs-e2e-operator-cli",
        "owned-local-direct-chat-v1": "bcs-e2e-direct-chat",
        "owned-local-edge-read-v1": "bcs-e2e-edge-read",
        "owned-local-session-public-v1": "bcs-e2e-session-public",
        "owned-local-session-sharing-v1": "bcs-e2e-session-sharing",
        "owned-local-session-flow-v1": "bcs-e2e-session-flow",
    }.get(suite, "bcs-e2e-pilot")
    root = ET.Element("testsuite", {
        "name": suite_name,
        "tests": str(len(summary["cases"])),
        "failures": str(failures),
        "errors": str(errors),
        "skipped": str(skipped),
    })
    properties = ET.SubElement(root, "properties")
    for key, value in summary.get("observer_binding", {}).items():
        ET.SubElement(properties, "property", {"name": "observer_binding." + key, "value": str(value)})
    for key, value in retained_properties(summary.get('retention', {})).items():
        ET.SubElement(properties, "property", {"name": key, "value": str(value)})
    if summary.get('scope', {}).get('suite') in {'owned-local-trusted-team-v1', 'owned-local-cli-file-v1', 'owned-local-workspace-v1', 'owned-local-network-v1', 'owned-local-session-public-v1', 'owned-local-session-sharing-v1', 'owned-local-session-flow-v1', 'owned-local-provider-v1', 'owned-local-channel-http-v1', 'owned-local-channel-cli-v1', 'owned-local-delivery-policy-v1', 'owned-local-direct-chat-v1', 'owned-local-edge-read-v1', 'owned-local-operator-cli-v1'}:
        for key in ('expected_assertion_count','observed_assertion_count'):
            ET.SubElement(properties, 'property', {'name':key, 'value':str(summary['reports'][key])})
        for requirement in summary.get('requirements', []):
            ET.SubElement(properties, 'property', {'name':'requirement.'+requirement['id'], 'value':requirement['status']})
    for case in summary["cases"]:
        node = ET.SubElement(root, "testcase", {"classname": "bcs-e2e", "name": case["id"]})
        properties = ET.SubElement(node, "properties")
        for name, value in (("runner_status", case["status"]),
                            ("case_version", case.get("version", "unknown")),
                            ("manifest_digest", summary.get("scope", {}).get("manifest_digest", "unknown")),
                            ("executor_digest", summary.get("runner", {}).get("executor_digest", "unknown")),
                            ("functional_status", case["functional"]),
                            ("evidence_status", case["evidence_status"]),
                            ("cleanup_status", case["cleanup"]),
                            ("execution_safety", case.get("execution_safety", "NOT_TRACKED")),
                            ("cleanup_verification_scope", summary.get("cleanup", {}).get("verification_scope", "NOT_VERIFIED")),
                            ("physical_data_erasure", summary.get("cleanup", {}).get("physical_data_erasure", "NOT_VERIFIED")),
                            ("associated_history_purge", summary.get("cleanup", {}).get("associated_history_purge", "NOT_VERIFIED")),
                            ("diagnostic_status", case.get("diagnostic_status", "NOT_RUN"))):
            ET.SubElement(properties, "property", {"name": name, "value": str(value)})
        for name, value in case.get('retained_visibility', {}).items():
            ET.SubElement(properties, 'property', {'name': 'retained_visibility.' + name, 'value': str(value)})
        for name in ('execution_status', 'resource_disposition', 'retention_inventory', 'pool_disposition', 'invocation_status'):
            if name in case:
                ET.SubElement(properties, "property", {"name": name, "value": str(case[name])})
        retention = summary.get('retention', {})
        if retention.get('case_id') == case['id'] or case.get('retention_inventory') == 'UNVERIFIED':
            for name, value in retained_properties(retention).items():
                ET.SubElement(properties, "property", {"name": name, "value": str(value)})
        for name, value in case.get('execution_observation', {}).items():
            ET.SubElement(properties, "property", {"name": "execution_observation." + name, "value": str(value)})
        if case["status"] == "FAIL":
            failure = ET.SubElement(node, "failure", {"message": case["status"]})
            failure.text = f"functional={case['functional']}; cleanup={case['cleanup']}"
        elif case["status"] == "ERROR":
            error = ET.SubElement(node, "error", {"message": case["status"]})
            error.text = f"functional={case['functional']}; cleanup={case['cleanup']}"
        elif case["status"] in {"BLOCKED", "SKIPPED", "CANCELLED"}:
            skipped_node = ET.SubElement(node, "skipped", {"message": f"{case['status']}: case did not qualify"})
            skipped_node.text = f"functional={case['functional']}; cleanup={case['cleanup']}"
    return ET.tostring(root, encoding="unicode") + "\n"


def render_markdown(summary: dict) -> str:
    lines = [
        "# BCS E2E Trusted-Team Report" if summary.get("scope", {}).get("suite") == "owned-local-trusted-team-v1" else "# BCS E2E Workspace Report" if summary.get("scope", {}).get("suite") == "owned-local-workspace-v1" else "# BCS E2E Agent-network Report" if summary.get("scope", {}).get("suite") == "owned-local-network-v1" else "# BCS E2E Session Public Slice Report" if summary.get("scope", {}).get("suite") == "owned-local-session-public-v1" else "# BCS E2E Session Sharing Slice Report" if summary.get("scope", {}).get("suite") == "owned-local-session-sharing-v1" else "# BCS E2E Complete Local Session Report" if summary.get("scope", {}).get("suite") == "owned-local-session-flow-v1" else "# BCS E2E Provider Story Report" if summary.get("scope", {}).get("suite") == "owned-local-provider-v1" else "# BCS E2E Channel HTTP Report" if summary.get("scope", {}).get("suite") == "owned-local-channel-http-v1" else "# BCS E2E Channel CLI Report" if summary.get("scope", {}).get("suite") == "owned-local-channel-cli-v1" else "# BCS E2E Operator CLI Report" if summary.get("scope", {}).get("suite") == "owned-local-operator-cli-v1" else "# BCS E2E Direct Chat Report" if summary.get("scope", {}).get("suite") == "owned-local-direct-chat-v1" else "# BCS E2E Edge Read Report" if summary.get("scope", {}).get("suite") == "owned-local-edge-read-v1" else "# BCS E2E Pilot Report", "",
        f"- Status: **{summary['status']}**",
        f"- Run ID: `{summary['run_id']}`",
        f"- Target: `{summary['target']['tier']}/{summary['target']['environment_id']}`",
        f"- Full release status: **{summary['scope']['full_release_status']}**",
        f"- Frozen manifest: `{summary['scope'].get('manifest_digest', 'unknown')}`",
        "",
        "## Pilot cases", "", "| Case | Version | Status | Functional | Cleanup | Diagnostics (optional) |", "|---|---|---|---|---|---|",
    ]
    lines += [f"| {case['id']} | {case.get('version', 'unknown')} | {case['status']} | {case['functional']} | {case['cleanup']} | {case.get('diagnostic_status', 'NOT_RUN')} |"
              for case in summary["cases"]]
    lines += ["", "## Scope and safety", "",
              f"- Execution safety: {summary.get('execution_safety', {}).get('status', 'NOT_TRACKED')}",
              f"- Declared legacy inventory: {summary['scope']['legacy_inventory_count']}",
              f"- Actual routes recorded: {len(summary['routes'])}",
              ("- Cleanup scope: original visibility and owned runtime only; relationships/history retained."
               if summary.get("cleanup", {}).get("verification_scope") == "OWNED_VISIBILITY_AND_RUNTIME_ONLY" else
               "- Cleanup scope: owned File and runtime only; Group/Session history retained."
               if summary.get("cleanup", {}).get("verification_scope") == "OWNED_FILE_AND_RUNTIME_ONLY" else
               "- Cleanup scope: owned Incident and Proposal Groups/Sessions only; other Groups and message history retained."
               if summary.get("cleanup", {}).get("verification_scope") == "OWNED_INCIDENT_AND_PROPOSAL_ONLY" else
               "- Cleanup scope: owned Incident Group and its initial Session only; other Groups and message history retained."
               if summary.get("cleanup", {}).get("verification_scope") == "OWNED_INCIDENT_ONLY" else
               "- Cleanup scope: registered-resource API convergence only; not complete physical erasure."),
              f"- Physical data erasure: {summary.get('cleanup', {}).get('physical_data_erasure', 'NOT_VERIFIED')}",
              f"- Associated history purge: {summary.get('cleanup', {}).get('associated_history_purge', 'NOT_VERIFIED')}",
              "- Retained data needs an environment-owner disposition; expiry labels are not deletion evidence.",
              f"- Mock boundaries: {', '.join(
                  (summary.get('mock_boundary') or summary.get('verification_boundary')
                   or {'not_proven': []})['not_proven'])}",
              f"- Timing interpretation: {summary['timing']['interpretation']}", ""]
    if summary.get('scope', {}).get('suite') in {'owned-local-trusted-team-v1', 'owned-local-cli-file-v1', 'owned-local-workspace-v1', 'owned-local-network-v1', 'owned-local-session-public-v1', 'owned-local-session-sharing-v1', 'owned-local-session-flow-v1', 'owned-local-provider-v1', 'owned-local-channel-http-v1', 'owned-local-channel-cli-v1', 'owned-local-delivery-policy-v1', 'owned-local-direct-chat-v1', 'owned-local-edge-read-v1', 'owned-local-operator-cli-v1'}:
        lines += ['## Parent Story requirements', '',
                  f"- Required assertions: {summary['reports']['expected_assertion_count']}",
                  f"- Observed assertions: {summary['reports']['observed_assertion_count']}",
                  '', '| Requirement | Status |', '|---|---|']
        lines += [f"| {item['id']} | {item['status']} |" for item in summary.get('requirements', [])]
        lines += ['', ('A local slice PASS is not complete parent, remote or full release qualification.'
                       if summary.get('scope', {}).get('suite') in {'owned-local-session-public-v1', 'owned-local-session-sharing-v1'}
                       else 'A local parent PASS is not remote or full release qualification.'), '']
    observed = [case for case in summary["cases"] if 'execution_observation' in case]
    if observed:
        lines += ["## Execution observations", "",
                  "Known completed tasks are not proof that all scoped execution has stopped.", "",
                  "| Case | Observation | Sessions | Known completed Sends | Scope |",
                  "|---|---|---|---|---|"]
        for case in observed:
            observation = case['execution_observation']
            lines.append(f"| {case['id']} | {observation['status']} | {observation['session_count']} | "
                         f"{observation['known_completed_send_count']} | {observation['scope_status']} |")
        for case in observed:
            observation = case['execution_observation']
            if 'observation_rounds' in observation:
                lines.append(f"- {case['id']}: {observation['observation_rounds']} observation rounds; stop={observation['stop_reason']}.")
            if 'observation_timeout_seconds' in observation:
                lines.append(f"- {case['id']}: observation budget={observation['observation_timeout_seconds']}s; "
                             f"round limit={observation['maximum_observation_rounds']}; poll interval={observation['poll_interval_seconds']}s.")
        for case in observed:
            observation = case['execution_observation']
            if 'downstream_status' in observation:
                lines.append(f"- {case['id']}: downstream={observation['downstream_status']}; "
                             f"known completed results={observation['known_downstream_completed_count']}; callbacks=UNCONFIRMED.")
        lines.append("")
    for case in summary['cases']:
        if 'retained_visibility' in case:
            visibility = case['retained_visibility']
            lines.append(f"- {case['id']} retained fixture visibility: "
                         + '; '.join(f'{name}={value}' for name, value in visibility.items()))
    lines += retained_markdown(summary)
    binding = summary.get("observer_binding", {})
    lines += ["## Observer binding provenance", "",
              f"- Verification mode: {binding.get('verification_mode', 'NOT_ATTACHED')}",
              "- Operator review is not live deployment verification.",
              f"- Live verification: {binding.get('live_verification', 'NOT_PERFORMED')}",
              f"- Frozen receipt digest: {binding.get('receipt_digest', 'none')}",
              f"- Declared deployment commit (not probed): {binding.get('declared_deployment_commit', 'unknown')}",
              *([f"- Mapping review basis: {binding['review_basis']}",
                 f"- Independent config review: {binding['independent_config_review']}",
                 f"- Shared database read scope: {binding['read_scope']}"]
                if "review_basis" in binding else []),
              f"- Review validity epochs: {binding.get('reviewed_at', 'unknown')} .. {binding.get('expires_at', 'unknown')}", ""]
    return "\n".join(lines)


def _write_private_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError("report output must be a regular file")
    if path.parent.is_symlink():
        raise OSError("report output directory cannot be a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{next(tempfile._get_candidate_names())}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_reports(summary: dict, output_dir: Path, formats: set[str]) -> dict:
    allowed = {"all", "json", "junit", "markdown"}
    if not formats or not formats <= allowed:
        raise ValueError("unsupported report format")
    output_dir = Path(output_dir)
    outputs = {}
    if "json" in formats or "all" in formats:
        path = output_dir / "summary.json"
        _write_private_atomic(path, (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode())
        outputs["json"] = str(path)
    if "junit" in formats or "all" in formats:
        path = output_dir / "junit.xml"
        _write_private_atomic(path, render_junit(summary).encode())
        outputs["junit"] = str(path)
    if "markdown" in formats or "all" in formats:
        path = output_dir / "summary.md"
        _write_private_atomic(path, render_markdown(summary).encode())
        outputs["markdown"] = str(path)
    return {"status": summary["status"], "outputs": outputs, "summary": summary}
