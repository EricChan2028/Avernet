"""One explicit owned-local lifecycle for the complete slow-Judge Story."""
from __future__ import annotations

from pathlib import Path
import signal

from .case_executor import execute_slow_judge_case
from .case_rewrite import slow_judge_case_rewrite
from .errors import has_cancellation
from .inventory import current_manifest, executor_digest
from .local_fixtures import OwnedLocalFixtures
from .local_judge_contract import CASE_ID, PARENT, SUITE, judge_source_binding
from .local_judge_sandbox import OwnedLocalJudgeSandbox
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .owned_local_case_adapter import OwnedLocalCaseAdapter
from .profile import identifier
from .run_state import RunStateError, read, write


def local_slow_judge_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str,
                          confirm_run_id: str, allow_local_processes: bool,
                          allow_fixture_write: bool, allow_write: bool) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Slow Judge requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Slow Judge Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Slow Judge requires a fresh state directory; existing evidence is retained')
    source = executor_digest()
    original = judge_source_binding()
    plan = slow_judge_case_rewrite()
    legacy = current_manifest()['legacy_inventory']
    if len(legacy) != 29 or PARENT not in {row['id'] for row in legacy}:
        raise RunStateError('Slow Judge parent is missing from the original release denominator')
    binding = {
        'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
        'run_id': run_id, 'executor_digest': source, 'bcs_binary_sha256': binary_sha256,
        'store_messages': True, 'source_binding': original,
        'selected_requirement_ids': list(plan.requirement_ids),
        'adapter_family': 'OwnedLocalCaseAdapter', 'legacy_inventory': legacy,
        'runtime_dir': str(state_dir.resolve()),
    }
    runtime = identities = sandbox = None
    case = {}
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedLocalJudgeSandbox(run_id) as sandbox_value:
                sandbox = sandbox_value
                with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                     root=state_dir, run_id=run_id, store_messages=True,
                                     judge_sandbox=sandbox_value) as runtime_value:
                    runtime = runtime_value
                    write(runtime.root/'slow-judge-binding.json', binding)
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities_context:
                        identities = identities_context
                        fixtures.apply(allow_write=True, role_identity=identities)
                        for role in fixtures.roles:
                            identities.verify(role)
                        adapter = OwnedLocalCaseAdapter(runtime, sandbox_value, identities)
                        execution = execute_slow_judge_case(adapter)
                        case = execution.snapshot()
                        case.update(schema_version=1, run_id=run_id, case_id=CASE_ID,
                                    executor_digest=source)
                        write(runtime.root/'local-slow-judge.json', case)
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Slow Judge owned runtime was not created')
        if not case and (runtime.root/'local-slow-judge.json').is_file():
            saved = read(runtime.root/'local-slow-judge.json')
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        shutdown = runtime._record()
        credentials = identities.summary() if identities is not None else {'credential_memory': 'NOT_CAPTURED'}
        judge_closed = sandbox.snapshot() if sandbox is not None else {'state': 'NOT_CREATED'}
        passed = (error is None and executor_digest() == source
                  and read(runtime.root/'slow-judge-binding.json') == binding
                  and bool(case) and read(runtime.root/'local-slow-judge.json') == case
                  and case.get('status') == 'PASS'
                  and case.get('completed_count') == len(plan.requirements)
                  and [step.get('requirement_id') for step in case.get('steps', [])] == list(plan.requirement_ids)
                  and all(step.get('status') == 'PASS' for step in case.get('steps', []))
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
                  and credentials.get('credential_memory') == 'CLEARED'
                  and judge_closed.get('state') == 'CLOSED' and judge_closed.get('failed') is False)
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else 'LOCAL_SLOW_JUDGE_PASS' if passed else 'BLOCKED')
        result = {
            'schema_version': 1, 'status': status, 'run_id': run_id, 'case_id': CASE_ID,
            'executor_digest': source, 'case': case, 'shutdown': shutdown,
            'credentials': credentials, 'judge_sandbox': judge_closed,
            'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
            'ready_to_execute': False,
        }
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/'slow-judge-flow-report.json', result)
        from .local_slow_judge_reporting import publish_slow_judge
        canonical = publish_slow_judge(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
