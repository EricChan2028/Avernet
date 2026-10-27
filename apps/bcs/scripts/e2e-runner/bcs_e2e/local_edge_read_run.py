"""One owned-runtime lifecycle for the four read-only Edge case executions."""
from __future__ import annotations

from pathlib import Path
import signal

from .errors import has_cancellation
from .inventory import executor_digest, reviewed_release_inventory
from .edge_read_case import (
    ADMISSION_CASE_ID,
    ADMISSION_PARENT,
    FRIEND_REQUESTS_CASE_ID,
    FRIEND_REQUESTS_PARENT,
    FRIEND_CONNECTIONS_CASE_ID,
    FRIEND_CONNECTIONS_PARENT,
    execute_edge_read_case,
)
from .edge_search_case import (
    CASE_ID as BOT_SEARCH_CASE_ID,
    PARENT as BOT_SEARCH_PARENT,
    edge_search_case_plan,
    execute_edge_search_case,
)
from .edge_read_contract import (
    admission_source_binding,
    friend_requests_list_source_binding,
    friend_connections_list_source_binding,
)
from .edge_search_contract import bot_search_source_binding
from .local_fixtures import OwnedLocalFixtures
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .owned_edge_read_adapter import (
    OwnedBotSearchFixtureAdmission,
    OwnedEdgeReadAdapter,
    OwnedEdgeSearchAdapter,
)
from .profile import identifier
from .run_state import RunStateError, read, write


CASE_IDS = (ADMISSION_CASE_ID, FRIEND_REQUESTS_CASE_ID, FRIEND_CONNECTIONS_CASE_ID,
            BOT_SEARCH_CASE_ID)
PARENTS = (ADMISSION_PARENT, FRIEND_REQUESTS_PARENT, FRIEND_CONNECTIONS_PARENT,
           BOT_SEARCH_PARENT)
SUITE = 'owned-local-edge-read-v1'


def journal_filename(case_id: str) -> str:
    if case_id == BOT_SEARCH_CASE_ID:
        return 'local-edge-bot-search-read.json'
    return 'local-edge-read-' + case_id + '.json'


def _execute_case(case_id, runtime, identities, bot_search_fixture) -> dict:
    if case_id in (ADMISSION_CASE_ID, FRIEND_REQUESTS_CASE_ID, FRIEND_CONNECTIONS_CASE_ID):
        return execute_edge_read_case(case_id, OwnedEdgeReadAdapter(runtime, identities, case_id))
    return execute_edge_search_case(
        OwnedEdgeSearchAdapter(runtime, identities, bot_search_fixture))


def local_edge_read_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str,
                        confirm_run_id: str, allow_local_processes: bool,
                        allow_fixture_write: bool, allow_write: bool) -> dict:
    """Execute all four dual-environment read-only cases in one owned runtime."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Edge read requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Edge read Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Edge read requires a fresh state directory; existing evidence is retained')
    source = executor_digest()
    admission = admission_source_binding()
    friend_requests = friend_requests_list_source_binding()
    friend_connections = friend_connections_list_source_binding()
    bot_search = bot_search_source_binding()
    legacy = reviewed_release_inventory()
    if set(PARENTS) - {row['id'] for row in legacy}:
        raise RunStateError('Edge read parents are missing from the original release denominator')
    selected_requirement_ids = {
        ADMISSION_PARENT: [row['id'] for row in admission['requirements']],
        FRIEND_REQUESTS_PARENT: [row['id'] for row in friend_requests['requirements']],
        FRIEND_CONNECTIONS_PARENT: [row['id'] for row in friend_connections['requirements']],
        BOT_SEARCH_PARENT: list(edge_search_case_plan().requirement_ids),
    }
    binding = {
        'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
        'run_id': run_id, 'case_ids': list(CASE_IDS),
        'expected_assertion_count': sum(map(len, selected_requirement_ids.values())),
        'executor_digest': source, 'bcs_binary_sha256': binary_sha256,
        'selected_requirement_ids': selected_requirement_ids,
        'source_bindings': {ADMISSION_PARENT: admission,
                            FRIEND_REQUESTS_PARENT: friend_requests,
                            FRIEND_CONNECTIONS_PARENT: friend_connections,
                            BOT_SEARCH_PARENT: bot_search},
        'legacy_inventory': legacy, 'runtime_dir': str(state_dir.resolve()),
    }
    runtime = identities = None
    cases: list[dict] = []
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id,
                                 allow_ambient_local_identity=False) as owned_runtime:
                runtime = owned_runtime
                write(runtime.root / 'edge-read-binding.json', binding)
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=('CEO',)) as owned_identities:
                    identities = owned_identities
                    fixtures.apply(allow_write=True, role_identity=identities)
                    identities.verify('CEO')
                    # The historical search case has one fixture mutation. Keep it
                    # in a separate bounded admission, outside the read spine.
                    bot_search_fixture = OwnedBotSearchFixtureAdmission(runtime)
                    bot_search_fixture.admit(allow_write=True)
                    for case_id in CASE_IDS:
                        execution = _execute_case(case_id, runtime, identities, bot_search_fixture)
                        cases.append(execution.snapshot())
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Edge read owned runtime was not created')
        shutdown = runtime._record()
        credentials = (identities.summary() if identities is not None
                       else {'credential_memory': 'NOT_CAPTURED'})
        expected_journals = {case_id: read(runtime.root / journal_filename(case_id))
                             for case_id in CASE_IDS}
        passed = (
            error is None and executor_digest() == source
            and read(runtime.root / 'edge-read-binding.json') == binding
            and len(cases) == len(CASE_IDS)
            and all(expected_journals[row['case_id']].get('status') == 'PASS'
                    and all(step.get('status') == 'PASS'
                            for step in expected_journals[row['case_id']].get('steps', []))
                    for row in cases)
            and all(row['status'] == 'PASS' and row['error_type'] is None
                    and row['completed_count'] == row['total_count'] for row in cases)
            and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
            and credentials.get('credential_memory') == 'CLEARED'
        )
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else 'LOCAL_EDGE_READ_PASS' if passed else 'BLOCKED')
        result = {
            'schema_version': 1, 'status': status, 'run_id': run_id,
            'suite': SUITE, 'case_ids': list(CASE_IDS), 'cases': cases,
            'executor_digest': source, 'shutdown': shutdown,
            'credentials': credentials, 'remote_validation': 'NOT_RUN',
            'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False,
        }
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root / 'edge-read-flow-report.json', result)
        from .local_edge_read_reporting import publish_edge_read
        canonical = publish_edge_read(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
