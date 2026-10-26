"""PRE-01 executor: Human-originated normal-group CRUD only."""
from __future__ import annotations

from functools import wraps
import time
from pathlib import Path

from .adapters import AdapterCapabilityError, adapter_for
from .case_scope import CaseScopedAdapter
from .correlation import CorrelatedEvidence
from .control_steps import (ControlProgress, PilotError, _group_id, _assert_group,
                            _observe_initial_session, _session_id, _assert_session,
                            group_control_steps, session_control_steps)
from .diagnostics import case_log_diagnostics
from .execution_ledger import ExecutionLedger
from .execution_safety import inspect_execution, require_execution_safe
from .ledger import Ledger, LedgerError
from .inventory import executable_case
from .file_steps import FileProgress, file_control_steps
from .observation_gate import AdmittedObserver, ObserverAdmissionError, aggregate_evidence, case_status
from .operation_authority import AuthorizedAdapter
from .pool import PoolLease, PoolLeaseError, bound_pool_path
from .identity_binding import require_pilot_execution_binding
from .profile import ConfigError
from .remote_effects import RemoteBootstrapAdmissionError, REASON as REMOTE_BOOTSTRAP_REASON
from .runtime import require_no_legacy_runtime
from .run_state import read as read_state, require_profile_binding, write as write_state
from .write_transport import WriteTransportError


def _case_lease(state: dict, run_id: str, digest: str) -> PoolLease:
    if state.get("status") not in {"PREPARED", "CASE_COMPLETED"} or state.get("run_id") != run_id:
        raise PilotError("run state is not a prepared or case-completed matching run")
    path = bound_pool_path(state)
    try:
        return PoolLease.from_file(path, run_id, digest)
    except PoolLeaseError:
        # A successful prior case releases the local lease so the next case can
        # reacquire it. Any present-but-invalid lock remains fail-closed.
        if state.get("status") == "CASE_COMPLETED" and not path.exists() and not path.is_symlink():
            return PoolLease.acquire(path, run_id, digest)
        raise


def _apply_persisted_quiescence(report: dict, ledger: Ledger, case_id: str, attempt: int) -> None:
    sessions = [row for row in ledger.resource_snapshot()
                if row["case_id"] == case_id and row["attempt"] == attempt and row["kind"] == "session"]
    if not sessions:
        return
    terminations = {row["resource_handle"]: row for row in ledger.termination_states(
        case_id=case_id, attempt=attempt
    )}
    report["termination"] = list(terminations.values())
    if any(row["state"] not in {"live", "deleted"} for row in sessions):
        report["quiescence"] = "UNKNOWN"
        return
    if any(row["handle"] not in terminations for row in sessions):
        report["quiescence"] = "UNKNOWN"
        return
    phases = {terminations[row["handle"]]["phase"] for row in sessions}
    report["quiescence"] = ("CONFIRMED" if phases <= {"CLEANUP_READY", "CLEANED"}
                             and not phases & {"RETAINED"} else "UNKNOWN")


def _mark_failure_correlation_gap(report: dict) -> None:
    if any(item.get("correlation_status") == "BLOCKED" for item in report.get("evidence", [])
           if isinstance(item, dict)):
        if report["diagnostic_status"] in {"PASS", "NOT_RUN"}:
            report["diagnostic_status"] = "INCOMPLETE"
        report["diagnostic_warnings"].append("failed request correlation could not be fully persisted")


def _leased_case(case_id: str):
    """Hold exclusive command ownership through result/state persistence.

    Failed, cancelled or interrupted executions retain their durable reservation.
    Closing the process-held guard never by itself makes the pool reusable.
    """
    def decorate(execute):
        @wraps(execute)
        def run(profile: dict, state_dir: Path, run_id: str, *,
                observer_client=None, physical_binding_probe=None,
                adapter_factory=None, log_observer=None) -> dict:
            require_pilot_execution_binding(profile)
            state = read_state(state_dir / "run.json")
            digest = require_profile_binding(state, profile)
            require_no_legacy_runtime(state_dir)
            case_definition = executable_case(state_dir, state, case_id)
            if case_id in state.get("attempted_cases", []):
                raise PilotError("case already attempted in this Run; use a new Run after cleanup")
            lease = _case_lease(state, run_id, digest)
            started = False
            authority = None
            try:
                # Re-read after obtaining the process-held lock, not before it.
                state = read_state(state_dir / "run.json")
                require_profile_binding(state, profile)
                case_definition = executable_case(state_dir, state, case_id)
                if state.get("status") not in {"PREPARED", "CASE_COMPLETED"}:
                    raise PilotError("run requires reconciliation before another case")
                attempted = state.get("attempted_cases", [])
                if case_id in attempted:
                    raise PilotError("case already attempted in this Run; use a new Run after cleanup")
                with Ledger(Path(state["ledger_path"]), run_id, digest) as ledger:
                    started_at = int(time.time())
                    state.update(status="RUNNING", active_case=case_id,
                                 attempted_cases=[*attempted, case_id], quiescence="UNKNOWN")
                    write_state(state_dir / "run.json", state)
                    started = True
                    factory = adapter_factory or adapter_for
                    try:
                        adapter = factory(profile, state_dir=state_dir, case_id=case_id,
                                          observer_client=observer_client,
                                          physical_binding_probe=physical_binding_probe)
                        if hasattr(adapter, "bind_execution_ledger"):
                            state["execution_contract_required"] = True
                            write_state(state_dir / "run.json", state)
                            adapter.bind_execution_ledger(ExecutionLedger(ledger), case_id, 1)
                        authority = AuthorizedAdapter(adapter, lease)
                        adapter = CaseScopedAdapter(authority, ledger, digest, case_definition, attempt=1,
                                                    termination_timeout_seconds=profile["policy"]["timeout_seconds"])
                        observer = AdmittedObserver(adapter, profile, case_id,
                                                    resource_scope=adapter.observation_scope)
                    except (ObserverAdmissionError, RemoteBootstrapAdmissionError) as error:
                        bootstrap_blocked = isinstance(error, RemoteBootstrapAdmissionError)
                        report = {
                            "schema_version": 1, "status": "BLOCKED", "ready_to_execute": False,
                            "run_id": run_id, "case_id": case_id, "functional": "NOT_RUN",
                            "cleanup": "NOT_ATTEMPTED", "quiescence": "CONFIRMED",
                            "evidence_status": "BLOCKED",
                            "evidence_required": {"PRE-01": ["api", "ob"],
                                                  "PRE-02": ["api", "websocket", "ob"],
                                                  "PRE-03": ["api", "bytes", "ob"]}[case_id],
                            "semantic_evidence": [], "evidence": [],
                            "assertions": [{"id": f"{case_id}." + ("effect_admission" if bootstrap_blocked else "observer_admission"),
                                            "status": "BLOCKED"}],
                            "remediation": {"observer_ref": profile["observer"]["server"],
                                            "reason": REMOTE_BOOTSTRAP_REASON if bootstrap_blocked else "required semantic observer admission failed"},
                        }
                    else:
                        report = execute(profile, state_dir, run_id, ledger=ledger,
                                         adapter=adapter, observer=observer)
                        _apply_persisted_quiescence(report, ledger, case_id, 1)
                    if authority is not None and authority.invalidated:
                        report["operation_authority"] = "INVALIDATED"
                        report["authority_socket_close"] = authority.socket_close_status
                        report["quiescence"] = "UNKNOWN"
                        if report["status"] == "PASS":
                            report["status"] = "BLOCKED"
                    execution = inspect_execution(ledger, required=state.get("execution_contract_required", False))
                    report["execution_safety"] = execution
                    if execution["status"] == "BLOCKED":
                        report["quiescence"] = "UNKNOWN"
                        if report["status"] == "PASS":
                            report["status"] = "BLOCKED"
                    if (profile["entrypoint"]["kind"] == "singlebox"
                            and profile["observer"]["kind"] == "local-sqlite"):
                        report["evidence_required"] = ["sqlite" if item == "ob" else item
                                                       for item in report["evidence_required"]]
                    if "observer_binding" in state:
                        report["observer_binding"] = state["observer_binding"]
                    report["profile_digest"] = digest
                    report["manifest_digest"] = state["manifest_digest"]
                    report["executor_digest"] = state["executor_digest"]
                    report["case_version"] = case_definition["version"]
                    report["started_at"] = started_at
                    report["finished_at"] = int(time.time())
                    report["diagnostic_status"] = "NOT_RUN"
                    report["diagnostic_evidence"] = []
                    report["diagnostic_warnings"] = ["optional log observer not configured or case not executed"]
                    _mark_failure_correlation_gap(report)
                    # Preserve business and cleanup outcomes before a potentially
                    # delayed diagnostic lookup. The lease remains held.
                    write_state(state_dir / f"report-{case_id}.json", report)
                    if log_observer is not None and report["functional"] != "NOT_RUN":
                        report.update(case_log_diagnostics(
                            ledger, log_observer, case_id=case_id,
                            environment=profile["environment"]["expected_bcs_env"],
                            start_epoch=started_at, end_epoch=report["finished_at"]))
                        _mark_failure_correlation_gap(report)
                        write_state(state_dir / f"report-{case_id}.json", report)
                    state.update(status="QUARANTINED" if authority is not None and authority.invalidated
                                 else "CASE_COMPLETED" if report["status"] == "PASS" else "FAILED",
                                 cleanup_status=report["cleanup"],
                                 quiescence=report["quiescence"], last_report=f"report-{case_id}.json")
                    write_state(state_dir / "run.json", state)
                    if report["status"] == "PASS":
                        if authority is not None:
                            authority.require_active()
                        require_execution_safe(ledger, required=state.get("execution_contract_required", False))
                        if report["quiescence"] != "CONFIRMED":
                            raise PilotError("quiescence must be confirmed before releasing the pool")
                        lease.release()
                    return report
            except BaseException:
                if started:
                    state.update(status="QUARANTINED", quiescence="UNKNOWN")
                    write_state(state_dir / "run.json", state)
                raise
            finally:
                lease.close()
        return run
    return decorate


def _delete_initial_session(ledger: Ledger, handle: str, group_id: str, adapter, evidence) -> None:
    resource = ledger._get(handle)
    if resource["origin"] != "created" or resource["state"] != "live" or not resource["resource_id"]:
        raise PilotError("initial Session creation is unresolved; preserve its parent group")
    session_id = resource["resource_id"]
    data, record = adapter.session_read(session_id)
    evidence.append(record)
    _assert_session(data, session_id)
    if data.get("group_id") != group_id:
        raise PilotError("initial Session parent differs from the created group")
    data, record = adapter.session_delete(session_id)
    evidence.append(record)
    if not isinstance(data, dict) or data.get("deleted") is not True:
        raise PilotError("initial Session deletion was not explicitly confirmed")
    data, record = adapter.session_verify_deleted(session_id)
    evidence.append(record)
    ledger.mark_deleted(handle)


@_leased_case("PRE-01")
def run_pre01(profile: dict, state_dir: Path, run_id: str, *, ledger: Ledger, adapter, observer: AdmittedObserver) -> dict:
    control = ControlProgress()
    evidence = CorrelatedEvidence(ledger, "PRE-01", 1)
    assertions: list[dict] = []
    semantic_evidence: list[dict] = []
    initial_session_deleted = False
    functional = "FAIL"
    cleanup = "NOT_ATTEMPTED"

    def assertion(name: str, passed: bool, detail: str = ""):
        stable = {"owner_matches": "owner_matches", "driver_and_members_match": "driver_and_members_match",
                  "update_persisted": "update_persisted", "ob_group_matches": "ob_group_matches"}.get(name, name)
        assertions.append({"id": f"PRE-01.{stable}", "status": "PASS" if passed else "FAIL", "detail": detail})
        if not passed:
            raise PilotError(detail or name)

    try:
        group_control_steps(profile, run_id, "PRE-01", ledger=ledger, adapter=adapter,
                            observer=observer, control=control, evidence=evidence,
                            assertions=assertions, semantic_evidence=semantic_evidence, assertion=assertion)
        functional = "PASS"
    except (PilotError, LedgerError, PoolLeaseError, WriteTransportError, RuntimeError) as error:
        evidence.record_failure(error)
        assertions.append({"id": "PRE-01.execution", "status": "FAIL", "detail": str(error)})
    finally:
        # Only a known, current-run group is eligible. Unknown create outcomes are
        # deliberately retained for explicit recovery; no name/list sweep occurs.
        if control.group_id and control.group_handle:
            try:
                _delete_initial_session(ledger, control.initial_session_handle, control.group_id, adapter, evidence)
                initial_session_deleted = True
                data, record = adapter.group_delete(control.group_id)
                evidence.append(record)
                if not isinstance(data, dict) or data.get("deleted") is not True:
                    raise PilotError("group deletion was not explicitly confirmed")
                data, record = adapter.group_verify_deleted(control.group_id)
                evidence.append(record)
                ledger.mark_deleted(control.group_handle)
                cleanup = "PASS"
            except (PilotError, LedgerError, PoolLeaseError, WriteTransportError, RuntimeError) as error:
                evidence.record_failure(error)
                cleanup = "BLOCKED"
                assertions.append({"id": "PRE-01.cleanup", "status": "FAIL", "detail": str(error)})
        else:
            cleanup = "NOT_ATTEMPTED" if not control.group_handle else "BLOCKED"

    assertions.append({"id": "PRE-01.owned_initial_session_deleted",
                       "status": "PASS" if initial_session_deleted else "BLOCKED"})
    assertions.append({"id": "PRE-01.owned_group_deleted", "status": "PASS" if cleanup == "PASS" else "BLOCKED"})
    status = case_status(functional, cleanup, assertions, control.evidence_status)
    report = {
        "schema_version": 1, "status": status, "ready_to_execute": False,
        "run_id": run_id, "case_id": "PRE-01", "functional": functional,
        "quiescence": "CONFIRMED",
        "evidence_status": control.evidence_status, "evidence_required": ["api", "ob"],
        "semantic_evidence": semantic_evidence,
        "cleanup": cleanup, "assertions": assertions, "evidence": evidence,
        "note": "Pilot subset only; full release qualification is not established.",
    }
    return report


@_leased_case("PRE-02")
def run_pre02(profile: dict, state_dir: Path, run_id: str, *, ledger: Ledger, adapter, observer: AdmittedObserver) -> dict:
    control = ControlProgress()
    evidence = CorrelatedEvidence(ledger, "PRE-02", 1)
    assertions: list[dict] = []
    semantic_evidence: list[dict] = []
    initial_session_deleted = False
    session_deleted = False
    group_deleted = False
    functional = "FAIL"
    cleanup = "NOT_ATTEMPTED"

    def assertion(name: str, passed: bool, detail: str = ""):
        stable = {"session_parent_matches": "session_parent_matches", "title_persisted": "title_persisted",
                  "websocket_upgrade": "websocket_upgrade", "session_binding_matches": "session_binding_matches",
                  "ob_session_matches": "ob_session_matches"}.get(name, name)
        assertions.append({"id": f"PRE-02.{stable}", "status": "PASS" if passed else "FAIL", "detail": detail})
        if not passed:
            raise PilotError(detail or name)

    try:
        session_control_steps(profile, run_id, "PRE-02", ledger=ledger, adapter=adapter,
                            observer=observer, control=control, evidence=evidence,
                            assertions=assertions, semantic_evidence=semantic_evidence, assertion=assertion)
        adapter.session_quiesce(control.session_id)
        data, record = adapter.session_ws_close(control.session_id)
        control.ws_open = False
        evidence.append(record)
        assertion("websocket_closed", True)
        functional = "PASS"
    except (PilotError, LedgerError, PoolLeaseError, WriteTransportError,
            AdapterCapabilityError, RuntimeError) as error:
        evidence.record_failure(error)
        assertions.append({"id": "PRE-02.execution", "status": "FAIL", "detail": str(error)})
    finally:
        try:
            if control.ws_open and control.session_id:
                try:
                    adapter.session_quiesce(control.session_id)
                finally:
                    # Even failed termination must release our held socket;
                    # closing it never turns unknown effects into quiescence.
                    data, record = adapter.session_ws_close(control.session_id)
                    control.ws_open = False
                    evidence.append(record)
            if control.session_id and control.session_handle:
                data, record = adapter.session_delete(control.session_id)
                evidence.append(record)
                data, record = adapter.session_verify_deleted(control.session_id)
                evidence.append(record)
                ledger.mark_deleted(control.session_handle)
                session_deleted = True
            elif control.session_handle:
                cleanup = "BLOCKED"
            if control.group_id and control.group_handle and (not control.session_handle or session_deleted):
                _delete_initial_session(ledger, control.initial_session_handle, control.group_id, adapter, evidence)
                initial_session_deleted = True
                data, record = adapter.group_delete(control.group_id)
                evidence.append(record)
                if not isinstance(data, dict) or data.get("deleted") is not True:
                    raise PilotError("group deletion was not explicitly confirmed")
                data, record = adapter.group_verify_deleted(control.group_id)
                evidence.append(record)
                ledger.mark_deleted(control.group_handle)
                group_deleted = True
            elif control.group_handle and not session_deleted:
                cleanup = "BLOCKED"
            if session_deleted and group_deleted:
                cleanup = "PASS"
            elif cleanup != "BLOCKED":
                cleanup = "NOT_ATTEMPTED"
        except (PilotError, LedgerError, PoolLeaseError, WriteTransportError,
                AdapterCapabilityError, RuntimeError) as error:
            evidence.record_failure(error)
            cleanup = "BLOCKED"
            assertions.append({"id": "PRE-02.cleanup", "status": "FAIL", "detail": str(error)})

    assertions.append({"id": "PRE-02.owned_initial_session_deleted",
                       "status": "PASS" if initial_session_deleted else "BLOCKED"})
    assertions.append({"id": "PRE-02.owned_resources_cleaned", "status": "PASS" if cleanup == "PASS" else "BLOCKED"})
    status = case_status(functional, cleanup, assertions, control.evidence_status)
    report = {
        "schema_version": 1, "status": status, "ready_to_execute": False,
        "run_id": run_id, "case_id": "PRE-02", "functional": functional,
        "quiescence": "CONFIRMED" if not control.ws_open else "UNKNOWN",
        "evidence_status": control.evidence_status, "evidence_required": ["api", "websocket", "ob"],
        "semantic_evidence": semantic_evidence,
        "cleanup": cleanup, "assertions": assertions, "evidence": evidence,
        "note": "Pilot subset only; full release qualification is not established.",
    }
    return report


@_leased_case("PRE-03")
def run_pre03(profile: dict, state_dir: Path, run_id: str, *, ledger: Ledger, adapter, observer: AdmittedObserver) -> dict:
    """Run the small Session-file lifecycle through the adapter contract.

    The local adapter owns bytes in memory. A future remote adapter must preserve
    the same assertions while using the product file API and its returned URLs;
    it must never turn a local-mock pass into evidence of BaaS health.
    """
    evidence = CorrelatedEvidence(ledger, "PRE-03", 1)
    assertions: list[dict] = []
    semantic_evidence: list[dict] = []
    control = FileProgress()
    group_handle = group_id = initial_session_handle = ""
    initial_session_deleted = False
    session_handle = session_id = ""
    file_handle = file_id = ""
    file_deleted = session_deleted = group_deleted = False
    functional = "FAIL"
    cleanup = "NOT_ATTEMPTED"

    def assertion(name: str, passed: bool, detail: str = ""):
        assertions.append({"id": f"PRE-03.{name}", "status": "PASS" if passed else "FAIL", "detail": detail})
        if not passed:
            raise PilotError(detail or name)

    try:
        file_control_steps(profile, run_id, "PRE-03", ledger=ledger, adapter=adapter,
                           observer=observer, control=control, evidence=evidence,
                           assertions=assertions, semantic_evidence=semantic_evidence,
                           assertion=assertion,
                           expected_backend="local" if profile["environment"]["tier"] == "local" else "baas")
        functional = "PASS"
    except (PilotError, LedgerError, PoolLeaseError, WriteTransportError,
            AdapterCapabilityError, RuntimeError) as error:
        evidence.record_failure(error)
        assertions.append({"id": "PRE-03.execution", "status": "FAIL", "detail": str(error)})
    finally:
        group_handle, group_id = control.group_handle, control.group_id
        initial_session_handle = control.initial_session_handle
        session_handle, session_id = control.session_handle, control.session_id
        file_handle, file_id = control.file_handle, control.file_id
        try:
            if file_id and file_handle:
                data, record = adapter.file_delete(session_id, file_id)
                evidence.append(record)
                if not isinstance(data, dict) or data.get("deleted") is not True:
                    raise PilotError("file deletion was not explicitly confirmed")
                data, record = adapter.file_verify_deleted(session_id, file_id)
                evidence.append(record)
                if control.share_token:
                    data, record = adapter.shared_file_verify_deleted(control.share_token)
                    evidence.append(record)
                ledger.mark_deleted(file_handle)
                file_deleted = True
            elif file_handle:
                cleanup = "BLOCKED"
            if session_id and session_handle and (not file_handle or file_deleted):
                data, record = adapter.session_delete(session_id)
                evidence.append(record)
                data, record = adapter.session_verify_deleted(session_id)
                evidence.append(record)
                ledger.mark_deleted(session_handle)
                session_deleted = True
            elif session_handle and not file_deleted:
                cleanup = "BLOCKED"
            if group_id and group_handle and (not session_handle or session_deleted):
                _delete_initial_session(ledger, initial_session_handle, group_id, adapter, evidence)
                initial_session_deleted = True
                data, record = adapter.group_delete(group_id)
                evidence.append(record)
                if not isinstance(data, dict) or data.get("deleted") is not True:
                    raise PilotError("group deletion was not explicitly confirmed")
                data, record = adapter.group_verify_deleted(group_id)
                evidence.append(record)
                ledger.mark_deleted(group_handle)
                group_deleted = True
            elif group_handle and not session_deleted:
                cleanup = "BLOCKED"
            if file_deleted and session_deleted and group_deleted:
                cleanup = "PASS"
            elif cleanup != "BLOCKED":
                cleanup = "NOT_ATTEMPTED"
        except (PilotError, LedgerError, PoolLeaseError, WriteTransportError,
                AdapterCapabilityError, RuntimeError) as error:
            evidence.record_failure(error)
            cleanup = "BLOCKED"
            assertions.append({"id": "PRE-03.cleanup", "status": "FAIL", "detail": str(error)})

    assertions.append({"id": "PRE-03.owned_initial_session_deleted",
                       "status": "PASS" if initial_session_deleted else "BLOCKED"})
    assertions.append({"id": "PRE-03.owned_resources_cleaned",
                       "status": "PASS" if cleanup == "PASS" else "BLOCKED"})
    status = case_status(functional, cleanup, assertions, control.evidence_status)
    report = {
        "schema_version": 1, "status": status, "ready_to_execute": False,
        "run_id": run_id, "case_id": "PRE-03", "functional": functional,
        "quiescence": "CONFIRMED",
        "evidence_status": control.evidence_status, "evidence_required": ["api", "bytes", "ob"],
        "semantic_evidence": semantic_evidence,
        "cleanup": cleanup, "assertions": assertions, "evidence": evidence,
        "note": "Pilot Web API file lifecycle only; evidence provenance identifies mock, SQLite or OB. No CLI/full-release qualification.",
    }
    return report


PILOT_EXECUTORS = {
    "PRE-01": run_pre01,
    "PRE-02": run_pre02,
    "PRE-03": run_pre03,
}


def run_case(profile: dict, state_dir: Path, run_id: str, case_id: str, *,
             observer_client=None, physical_binding_probe=None,
             adapter_factory=None, log_observer=None) -> dict:
    """Run one frozen pilot case through the shared Story implementation.

    Deployment runners inject transport/observer composition here; they do not
    fork a second remote Story implementation. The CLI remains the guarded
    default entrypoint and does not expose arbitrary dependency loading.
    """
    executor = PILOT_EXECUTORS.get(case_id)
    if executor is None:
        raise PilotError("unsupported pilot case")
    return executor(profile, state_dir, run_id,
                    observer_client=observer_client,
                    physical_binding_probe=physical_binding_probe,
                    adapter_factory=adapter_factory, log_observer=log_observer)


def evaluate(summary: dict) -> dict:
    """Honest qualification gate separate from pilot execution/report rendering."""
    cases = summary.get("cases", [])
    integrity = summary.get("integrity", {})
    reasons: list[str] = []
    if summary.get("status") != "PILOT_PASS":
        reasons.append("pilot execution/report is not PASS")
    if integrity.get("status") != "PASS":
        reasons.append("report integrity is not PASS")
    if summary.get("scope", {}).get("full_release_status") != "QUALIFIED":
        reasons.append("frozen release inventory is not qualified")
    if not cases or any(case.get("status") != "PASS" for case in cases):
        reasons.append("one or more required pilot cases are not PASS")
    if any(case.get("evidence_status") not in {"PASS", "LOCAL_SURROGATE"} for case in cases):
        reasons.append("one or more required evidence results are incomplete")
    return {
        "schema_version": 1,
        "status": "QUALIFIED" if not reasons else "NOT_QUALIFIED",
        "run_id": summary.get("run_id"),
        "pilot_status": summary.get("status", "UNKNOWN"),
        "full_release_status": summary.get("scope", {}).get("full_release_status", "UNKNOWN"),
        "reasons": reasons,
        "note": "Functional pilot success is not full release qualification.",
    }
