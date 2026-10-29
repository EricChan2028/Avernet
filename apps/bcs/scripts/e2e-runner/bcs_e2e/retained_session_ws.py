"""Offline Session/WS closure review for PRE-02 evidence.

This module does not open sockets or issue remote requests.  It reviews two
frozen Run directories so a retained Session/WS slice can be reported without
accidentally granting full PRE-02 release credit.  A future full PRE-02 review
also stays offline and consumes only artifacts produced by an authorized Run.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from .ledger import Ledger
from .profile import ConfigError, fingerprint, identifier
from .retained_serial import CONTRACT as RETAINED_SERIAL_CONTRACT
from .run_state import read


RETAINED_CASE_ID = 'RETAIN-SESSION-01'
FULL_CASE_ID = 'PRE-02'
RETAINED_PAIR_CONTRACT = 'retained-session-ws-pair-v1'
FULL_PAIR_CONTRACT = 'pre02-remote-session-ws-closure-v1'
FULL_RUN_RECEIPT = 'pre02-remote-serial-run-v1'

RETAINED_ASSERTIONS = frozenset({
    'RETAIN-SESSION-01.preflight',
    'RETAIN-SESSION-01.group_created',
    'RETAIN-SESSION-01.session_created',
    'RETAIN-SESSION-01.session_binding_matches',
    'RETAIN-SESSION-01.title_updated',
    'RETAIN-SESSION-01.session_parent_matches',
    'RETAIN-SESSION-01.title_persisted',
    'RETAIN-SESSION-01.ob_session_matches',
    'RETAIN-SESSION-01.messages_read',
    'RETAIN-SESSION-01.connection_token_issued',
    'RETAIN-SESSION-01.websocket_upgrade',
    'RETAIN-SESSION-01.websocket_closed',
})
FULL_ASSERTIONS = frozenset({
    'PRE-02.preflight',
    'PRE-02.group_created',
    'PRE-02.session_created',
    'PRE-02.session_binding_matches',
    'PRE-02.title_updated',
    'PRE-02.session_parent_matches',
    'PRE-02.title_persisted',
    'PRE-02.ob_session_matches',
    'PRE-02.messages_read',
    'PRE-02.connection_token_issued',
    'PRE-02.websocket_upgrade',
    'PRE-02.websocket_closed',
    'PRE-02.owned_initial_session_deleted',
    'PRE-02.owned_resources_cleaned',
})

_TOKEN_QUERY = re.compile(r'(?i)(?:^|[?&])token=([^&\s]+)')


def _require(condition: object, message: str) -> None:
    if condition is not True:
        raise ConfigError(message)


def _single(records: list[dict], operation: str) -> tuple[dict, list[dict]]:
    matches = [row for row in records if isinstance(row, dict) and row.get('operation') == operation]
    if len(matches) != 1:
        raise ConfigError(f'{operation} evidence must occur exactly once')
    return matches[0], records


def _reject_unredacted_credentials(value: object) -> None:
    """Reject common secret leakage without repeating the offending value."""
    if isinstance(value, Mapping):
        if {'token', 'connection_token'} & set(value):
            for key in ('token', 'connection_token'):
                _require(value.get(key) == '[REDACTED]',
                         'Session token evidence is not credential-redacted')
        for item in value.values():
            _reject_unredacted_credentials(item)
    elif isinstance(value, list):
        for item in value:
            _reject_unredacted_credentials(item)
    elif isinstance(value, str):
        for match in _TOKEN_QUERY.finditer(value):
            _require(match.group(1) == '[REDACTED]',
                     'raw WebSocket token query appeared in Session/WS evidence')


def _record_path_is_redacted(record: dict) -> None:
    path = record.get('path')
    _require(isinstance(path, str) and bool(path), 'Session/WS evidence has no request path')
    _reject_unredacted_credentials(path)


def _semantic_evidence(report: dict) -> dict:
    evidence = report.get('semantic_evidence')
    _require(isinstance(evidence, list) and len(evidence) == 2,
             'Session/WS report must contain Group and Session persistence evidence')
    rows_by_key: dict[str, dict] = {}
    for row in evidence:
        _require(isinstance(row, dict) and row.get('status') == 'OBSERVED',
                 'semantic persistence evidence is not observed')
        plan = row.get('plan')
        rows = row.get('rows')
        _require(isinstance(plan, dict) and isinstance(rows, list) and len(rows) == 1
                 and isinstance(rows[0], dict),
                 'semantic persistence evidence is incomplete')
        assertion = plan.get('assertion_id')
        _require(assertion in {'group.persisted', 'session.persisted'},
                 'semantic persistence assertion is outside Session/WS scope')
        _require(assertion not in rows_by_key,
                 'semantic persistence evidence repeats one assertion')
        rows_by_key[assertion] = rows[0]

    group = rows_by_key.get('group.persisted')
    session = rows_by_key.get('session.persisted')
    _require(isinstance(group, dict) and isinstance(session, dict),
             'semantic persistence evidence lacks Group or Session rows')
    group_id = identifier(group.get('group_id'), 'Session semantic Group ID')
    session_id = identifier(session.get('session_id'), 'Session semantic Session ID')
    _require(session.get('group_id') == group_id,
             'Session semantic parent differs from the observed Group')
    return {'group_id': group_id, 'session_id': session_id, 'group': group, 'session': session}


def _optional_identifier(record: dict, field: str, semantic_value: str) -> None:
    value = record.get(field)
    if value is None:
        return
    identifier(value, f'Session WebSocket {field}')
    _require(value == semantic_value, f'Session WebSocket {field} differs from persisted evidence')


def _ws_evidence(report: dict, semantic: dict) -> dict:
    evidence = report.get('evidence')
    _require(isinstance(evidence, list), 'Session/WS report has no evidence array')
    token, records = _single(evidence, 'session.token')
    connect, records = _single(records, 'session.ws.connect')
    close, _ = _single(records, 'session.ws.close')

    _require(token.get('http_status') == 200,
             'Session connection token request did not return HTTP 200')
    _require(connect.get('http_status') == 101,
             'Session WebSocket Upgrade did not return HTTP 101')
    _require(connect.get('request_id_echoed') is True,
             'Session WebSocket Upgrade did not echo its request ID')
    identifier(connect.get('submitted_request_id'), 'Session WebSocket Upgrade request ID')
    _optional_identifier(connect, 'group_id', semantic['group_id'])
    _optional_identifier(connect, 'session_id', semantic['session_id'])
    connect_id = connect.get('connect_request_id')
    if connect_id is not None:
        identifier(connect_id, 'Session WebSocket connect request ID')

    close_code = close.get('close_code', close.get('http_status'))
    _require(close_code == 1000,
             'Session WebSocket was not closed with a protocol close frame')
    local_closed = close.get('local_socket_closed')
    _require(local_closed is None or local_closed is True,
             'owned Session WebSocket closure is not confirmed')
    for record in (token, connect, close):
        _record_path_is_redacted(record)
    _reject_unredacted_credentials(report)

    return {
        'group_id': semantic['group_id'],
        'session_id': semantic['session_id'],
        'upgrade_request_id': connect['submitted_request_id'],
        'upgrade_http_status': connect['http_status'],
        'close_code': close_code,
    }


def _validate_assertions(report: dict, case_id: str) -> None:
    assertions = report.get('assertions')
    expected = RETAINED_ASSERTIONS if case_id == RETAINED_CASE_ID else FULL_ASSERTIONS
    _require(isinstance(assertions, list), 'Session/WS report has no assertions')
    by_id: dict[str, dict] = {}
    for row in assertions:
        _require(isinstance(row, dict) and isinstance(row.get('id'), str),
                 'Session/WS report contains an invalid assertion')
        _require(row['id'] not in by_id,
                 'Session/WS report contains a duplicate assertion')
        by_id[row['id']] = row
    _require(expected <= set(by_id), 'Session/WS report is missing required assertions')
    for name in expected:
        _require(by_id[name].get('status') == 'PASS',
                 f'Session/WS assertion {name} did not pass')


def _validate_state_binding(profile: dict, state_dir: Path, report: dict, case_id: str) -> dict:
    state = read(state_dir / 'run.json')
    digest = fingerprint(profile)
    _require(state.get('profile_digest') == digest,
             'Session/WS Run is not bound to the selected profile')
    _require(report.get('profile_digest') == digest,
             'Session/WS report is not bound to the selected profile')
    _require(state.get('run_id') == report.get('run_id'),
             'Session/WS report Run ID differs from its Run state')
    _require(state.get('attempted_cases') == [case_id],
             'Session/WS Run attempted case inventory differs')
    _require(state.get('active_case') == case_id,
             'Session/WS Run active case differs')
    _require(report.get('case_id') == case_id,
             'Session/WS report case differs from the Run')
    if state.get('manifest_digest') is not None:
        _require(report.get('manifest_digest') == state.get('manifest_digest'),
                 'Session/WS report is not bound to the frozen manifest')
    if state.get('executor_digest') is not None:
        _require(report.get('executor_digest') == state.get('executor_digest'),
                 'Session/WS report is not bound to the frozen executor')
    if state.get('observer_binding') is not None:
        _require(report.get('observer_binding') == state.get('observer_binding'),
                 'Session/WS report is not bound to the frozen observer')
    identifier(state.get('run_id'), 'Session/WS Run ID')
    return state


def _retained_serial_receipts(profile: dict, states: list[dict], state_dirs: list[Path],
                              run_ids: list[str]) -> list[dict]:
    receipts = []
    for index, (state, state_dir, run_id) in enumerate(zip(states, state_dirs, run_ids)):
        receipt = read(state_dir / 'serial-admission.json')
        expected_previous = None if index == 0 else run_ids[index - 1]
        _require(receipt.get('contract') == RETAINED_SERIAL_CONTRACT,
                 'retained Session/WS Run does not use the serial admission contract')
        _require(receipt.get('run_id') == run_id,
                 'retained serial receipt Run ID differs')
        _require(receipt.get('profile_digest') == fingerprint(profile),
                 'retained serial receipt profile differs')
        _require(receipt.get('previous_run_id') == expected_previous,
                 'retained Session/WS pair is not an exact serial chain')
        _require(receipt.get('phase') == 'RESERVED_AFTER_PREFLIGHT',
                 'retained serial admission is not a finalized scheduler handoff')
        _require(receipt.get('remote_termination_confirmed') is False,
                 'retained serial receipt falsely claims remote termination')
        _require(receipt.get('cleanup_authorized') is False,
                 'retained serial receipt falsely claims cleanup authority')
        _require(state.get('serial_admission_digest') == fingerprint(receipt),
                 'retained serial receipt differs from its frozen Run')
        receipts.append(receipt)
    return receipts


def _same_version(states: list[dict]) -> None:
    for key in ('profile_digest', 'manifest_digest', 'executor_digest', 'observer_binding'):
        values = {state.get(key) for state in states}
        _require(len(values) == 1, f'Session/WS pair does not share the same {key}')


def _validate_retained_run(profile: dict, state_dir: Path) -> dict:
    report = read(state_dir / f'report-{RETAINED_CASE_ID}.json')
    state = _validate_state_binding(profile, state_dir, report, RETAINED_CASE_ID)
    _validate_assertions(report, RETAINED_CASE_ID)
    _require(report.get('status') == 'PASS', 'retained Session/WS Case did not pass')
    _require(report.get('functional') == 'PASS', 'retained Session/WS functional result did not pass')
    _require(report.get('evidence_status') in {'PASS', 'LOCAL_SURROGATE'},
             'retained Session/WS persistence evidence did not pass')
    _require(report.get('socket_status') == 'CLOSED',
             'retained Session WebSocket was not closed')
    _require(report.get('operation_authority') == 'NOT_INVALIDATED',
             'retained Session/WS Run lost operation authority')
    _require(report.get('execution_status') == 'UNCONFIRMED',
             'retained Session/WS result cannot claim full execution closure')
    _require(report.get('quiescence') == 'UNKNOWN',
             'retained Session/WS result cannot claim quiescence')
    _require(report.get('cleanup') == 'RETAINED',
             'retained Session/WS resources are not retained by policy')
    _require(report.get('resource_disposition') == 'RETAINED_UNRESOLVED',
             'retained Session/WS resource disposition is unsupported')
    _require(report.get('pool_disposition') == 'RETAINED_SERIAL',
             'retained Session/WS pool disposition is unsupported')
    _require(report.get('next_run_admission') == 'FRESH_CHECK_REQUIRED',
             'retained Session/WS next admission differs from the policy')
    semantic = _semantic_evidence(report)
    ws = _ws_evidence(report, semantic)
    return {'state': state, 'report': report, 'ws': ws, 'semantic': semantic}


def retained_session_ws_pair_review(profile: dict, first_state_dir: Path,
                                    second_state_dir: Path) -> dict:
    """Review two real retained Session/WS Runs without extending their credit."""
    state_dirs = [Path(first_state_dir), Path(second_state_dir)]
    _require(len({path.resolve() for path in state_dirs}) == 2,
             'retained Session/WS pair requires distinct state directories')
    runs = [_validate_retained_run(profile, path) for path in state_dirs]
    states = [row['state'] for row in runs]
    run_ids = [row['state']['run_id'] for row in runs]
    for run_id in run_ids:
        identifier(run_id, 'retained Session/WS Run ID')
    _require(run_ids[0] != run_ids[1],
             'retained Session/WS pair repeats one Run')
    _same_version(states)
    _retained_serial_receipts(profile, states, state_dirs, run_ids)

    return {
        'schema_version': 1,
        'contract': RETAINED_PAIR_CONTRACT,
        'status': 'RETAINED_SESSION_WS_PAIR_PASS',
        'ready_to_execute': False,
        'profile_digest': fingerprint(profile),
        'run_ids': run_ids,
        'case_id': RETAINED_CASE_ID,
        'session_ws_binding_and_closure': 'PASS',
        'execution_closure': 'UNCONFIRMED',
        'cleanup': 'RETAINED',
        'next_run_admission': 'FRESH_CHECK_REQUIRED',
        'full_pre02': {
            'status': 'NOT_QUALIFIED',
            'missing': [
                'full PRE-02 execution-effect convergence proof',
                'exact initial/explicit Session and Group cleanup',
                'full PRE-02 two-Run closure review',
            ],
        },
        'grants_release_credit': False,
    }


def _full_run_receipt(profile: dict, state_dir: Path, run_id: str,
                      previous_run_id: str | None) -> None:
    receipt = read(state_dir / 'pre02-serial-run.json')
    _require(receipt.get('schema_version') == 1,
             'full PRE-02 serial receipt schema differs')
    _require(receipt.get('contract') == FULL_RUN_RECEIPT,
             'full PRE-02 serial receipt contract differs')
    _require(receipt.get('run_id') == run_id,
             'full PRE-02 serial receipt Run ID differs')
    _require(receipt.get('previous_run_id') == previous_run_id,
             'full PRE-02 pair is not an exact serial chain')
    _require(receipt.get('profile_digest') == fingerprint(profile),
             'full PRE-02 serial receipt profile differs')
    _require(receipt.get('case_id') == FULL_CASE_ID,
             'full PRE-02 serial receipt is not bound to PRE-02')


def _validate_full_ledger(profile: dict, state: dict, ws: dict) -> None:
    ledger_path = Path(state['ledger_path'])
    with Ledger(ledger_path, state['run_id'], state['profile_digest'], read_only=True) as ledger:
        rows = ledger.resource_snapshot()
        _require(len(rows) == 3,
                 'full PRE-02 must own exactly one Group and two Sessions')
        _require(all(row['case_id'] == FULL_CASE_ID and row['attempt'] == 1
                     and row['origin'] == 'created' and row['state'] == 'deleted'
                     for row in rows),
                 'full PRE-02 ledger is not an exact deleted three-resource inventory')
        groups = [row for row in rows if row['kind'] == 'group']
        sessions = [row for row in rows if row['kind'] == 'session']
        _require(len(groups) == 1 and len(sessions) == 2,
                 'full PRE-02 ledger resource kinds differ from the Story budget')
        group = groups[0]
        _require(group['resource_id'] == ws['group_id'],
                 'full PRE-02 ledger Group differs from the WebSocket binding')
        _require(all(row['parent'] == group['handle'] for row in sessions),
                 'full PRE-02 Session ledger parents differ from the owned Group')
        _require(ws['session_id'] in {row['resource_id'] for row in sessions},
                 'full PRE-02 ledger does not contain the observed Session')


def _validate_full_run(profile: dict, state_dir: Path, previous_run_id: str | None) -> dict:
    report = read(state_dir / f'report-{FULL_CASE_ID}.json')
    state = _validate_state_binding(profile, state_dir, report, FULL_CASE_ID)
    _validate_assertions(report, FULL_CASE_ID)
    _require(report.get('status') == 'PASS', 'full PRE-02 Case did not pass')
    _require(report.get('functional') == 'PASS', 'full PRE-02 functional result did not pass')
    _require(report.get('evidence_status') == 'PASS',
             'full PRE-02 persistence evidence did not pass')
    _require(report.get('cleanup') == 'PASS', 'full PRE-02 exact cleanup did not pass')
    _require(report.get('quiescence') == 'CONFIRMED',
             'full PRE-02 execution convergence is not confirmed')
    semantic = _semantic_evidence(report)
    ws = _ws_evidence(report, semantic)
    _full_run_receipt(profile, state_dir, state['run_id'], previous_run_id)
    _validate_full_ledger(profile, state, ws)
    return {'state': state, 'report': report, 'ws': ws, 'semantic': semantic}


def full_pre02_pair_review(profile: dict, first_state_dir: Path,
                           second_state_dir: Path) -> dict:
    """Future offline review of two completed full PRE-02 remote closures."""
    state_dirs = [Path(first_state_dir), Path(second_state_dir)]
    _require(len({path.resolve() for path in state_dirs}) == 2,
             'full PRE-02 closure requires distinct Run directories')
    first = _validate_full_run(profile, state_dirs[0], None)
    second = _validate_full_run(profile, state_dirs[1], first['state']['run_id'])
    states = [first['state'], second['state']]
    run_ids = [row['state']['run_id'] for row in (first, second)]
    _require(run_ids[0] != run_ids[1], 'full PRE-02 closure repeats one Run')
    _same_version(states)

    return {
        'schema_version': 1,
        'contract': FULL_PAIR_CONTRACT,
        'status': 'PRE02_REMOTE_CLOSURE_PASS',
        'ready_to_execute': False,
        'profile_digest': fingerprint(profile),
        'run_ids': run_ids,
        'case_id': FULL_CASE_ID,
        'session_ws_binding_and_closure': 'PASS',
        'execution_convergence': 'CONFIRMED',
        'exact_cleanup': 'PASS',
        'next_run_admission': 'FRESH_CHECK_REQUIRED',
        'full_release_status': 'NOT_QUALIFIED',
        'grants_release_credit': False,
    }
