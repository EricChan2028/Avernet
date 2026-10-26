"""One owned-runtime lifecycle for the seven edge friend-mutation
cases. All seven obligations execute against one runner-owned local
BCS; no case may reinterpret a shared obligation, retry, or fall
back to another environment."""
from __future__ import annotations

from pathlib import Path
import signal

from .case_executor import execute_case
from .edge_friend_mutation_case import (
    CASE_IDS,
    PARENTS,
    edge_friend_mutation_case_plan,
    edge_friend_mutation_source_binding,
)
from .errors import has_cancellation
from .inventory import current_manifest, executor_digest
from .local_edge_friend_mutations_case import (
    OwnedEdgeFriendFixtureAdmission,
    OwnedEdgeFriendMutationsAdapter,
)
from .local_fixtures import OwnedLocalFixtures
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier
from .run_state import RunStateError, read, write

SUITE = 'owned-local-edge-friend-mutations-v1'
ROLE_MUTATIONS = ('CEO', 'PM', 'ENG', 'QA')


def journal_filename(case_id: str) -> str:
    return 'local-edge-friend-mutations-' + case_id + '.json'


def local_edge_friend_mutations_run(*, binary: Path, binary_sha256: str,
                                      state_dir: Path, run_id: str,
                                      confirm_run_id: str,
                                      allow_local_processes: bool,
                                      allow_fixture_write: bool,
                                      allow_write: bool) -> dict:
    """Execute all seven friend-mutation cases in one owned runtime."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Edge friend mutation requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Edge friend mutation Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Edge friend mutation requires a fresh state directory; existing evidence is retained')
    source = executor_digest()
    bindings = {case_id: edge_friend_mutation_source_binding(case_id)
                for case_id in CASE_IDS}
    legacy = current_manifest()['legacy_inventory']
    if (len(legacy) != 29 or set(PARENTS) - {row['id'] for row in legacy}):
        raise RunStateError('Edge friend-mutation parents are missing from the original release denominator')
    binding = {
        'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
        'run_id': run_id, 'case_ids': list(CASE_IDS),
        'expected_assertion_count': len(CASE_IDS),
        'executor_digest': source, 'bcs_binary_sha256': binary_sha256,
        'selected_requirement_ids': {
            case_id: [row['id'] for row in bindings[case_id]['requirements']]
            for case_id in CASE_IDS},
        'source_bindings': bindings,
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
                write(runtime.root / 'edge-friend-mutations-binding.json', binding)
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=ROLE_MUTATIONS) as owned_identities:
                    identities = owned_identities
                    fixtures.apply(allow_write=True, role_identity=identities)
                    for role in ROLE_MUTATIONS:
                        identities.verify(role)
                    # The single service-credential admission is owned
                    # once and admitted by the ensure-bot obligation
                    # itself, keeping the original entrypoint order.
                    fixture_admission = OwnedEdgeFriendFixtureAdmission(
                        runtime, bot_id=identities.actor_id('CEO'))
                    for case_id in CASE_IDS:
                        plan = edge_friend_mutation_case_plan(case_id)
                        adapter = OwnedEdgeFriendMutationsAdapter(
                            runtime, identities, fixture_admission,
                            case_id=case_id, allow_write=allow_write)
                        execution = execute_case(plan, adapter)
                        cases.append(execution.snapshot())
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Edge friend-mutation owned runtime was not created')
        shutdown = runtime._record()
        credentials = (identities.summary() if identities is not None
                       else {'credential_memory': 'NOT_CAPTURED'})
        expected_journals = {case_id: read(runtime.root / journal_filename(case_id))
                               for case_id in CASE_IDS}
        passed = (
            error is None and executor_digest() == source
            and read(runtime.root / 'edge-friend-mutations-binding.json') == binding
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
                  else 'LOCAL_EDGE_FRIEND_MUTATIONS_PASS' if passed
                  else 'BLOCKED')
        result = {
            'schema_version': 1, 'status': status, 'run_id': run_id,
            'suite': SUITE, 'case_ids': list(CASE_IDS), 'cases': cases,
            'executor_digest': source, 'shutdown': shutdown,
            'credentials': credentials, 'remote_validation': 'NOT_RUN',
            'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False,
        }
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root / 'edge-friend-mutations-flow-report.json', result)
        from .local_edge_friend_mutations_reporting import (
            publish_edge_friend_mutations,
        )
        canonical = publish_edge_friend_mutations(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
