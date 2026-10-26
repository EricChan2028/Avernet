"""One explicit owned-local lifecycle for the operator CLI Story."""
from pathlib import Path
import signal

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest
from .local_fixtures import OwnedLocalFixtures
from .local_operator_cli import OwnedLocalOperatorCli
from .local_operator_cli_contract import (
    CASE_ID,
    PARENT,
    REQUIREMENTS,
    SUITE,
    operator_cli_source_binding,
)
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier
from .run_state import RunStateError, read, write

ROLES = ('CEO', 'PM', 'ENG', 'QA', 'CS')


def local_operator_cli_run(*, binary: Path, binary_sha256: str,
                             cli_binary: Path, cli_binary_sha256: str,
                             state_dir: Path, run_id: str, confirm_run_id: str,
                             allow_local_processes: bool, allow_fixture_write: bool,
                             allow_write: bool) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Operator CLI requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Operator CLI Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Operator CLI requires a fresh state directory; existing evidence is retained')
    source = executor_digest()
    original = operator_cli_source_binding()
    legacy = current_manifest()['legacy_inventory']
    if len(legacy) != 29 or PARENT not in {row['id'] for row in legacy}:
        raise RunStateError('Operator CLI parent is missing from the original release denominator')
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
               'case_id': CASE_ID, 'run_id': run_id, 'executor_digest': source,
               'bcs_binary_sha256': binary_sha256, 'cli_binary_sha256': cli_binary_sha256,
               'store_messages': False,
               'source_binding': original, 'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'runtime_dir': str(state_dir.resolve())}
    runtime = identities = None
    case = {}
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                     root=state_dir, run_id=run_id) as owned_runtime:
                runtime = owned_runtime
                write(runtime.root/'operator-cli-binding.json', binding)
                fixtures = OwnedLocalFixtures(runtime, fixture_set='trusted-team')
                with OwnedLocalRoleIdentity(fixtures, roles=ROLES) as owned_identities:
                    identities = owned_identities
                    fixtures.apply(allow_write=True, role_identity=identities)
                    for role in ROLES:
                        identities.verify(role)
                    case = OwnedLocalOperatorCli(identities, cli_binary,
                                                      cli_binary_sha256).run()
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Operator CLI owned runtime was not created')
        if not case and (runtime.root/'local-operator-cli.json').is_file():
            saved = read(runtime.root/'local-operator-cli.json')
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        shutdown = runtime._record()
        credentials = identities.summary() if identities else {'credential_memory': 'NOT_CAPTURED'}
        expected = [{'id': key, 'status': 'PASS'} for key in REQUIREMENTS]
        passed = (error is None and executor_digest() == source
                  and read(runtime.root/'operator-cli-binding.json') == binding
                  and bool(case) and read(runtime.root/'local-operator-cli.json') == case
                  and case.get('status') == 'LOCAL_OPERATOR_CLI_PASS'
                  and case.get('assertions') == expected
                  and case.get('run_id') == run_id and case.get('case_id') == CASE_ID
                  and case.get('executor_digest') == source
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
                  and credentials.get('credential_memory') == 'CLEARED')
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else 'LOCAL_OPERATOR_CLI_PASS' if passed else 'BLOCKED')
        result = {'schema_version': 1, 'status': status, 'run_id': run_id, 'case_id': CASE_ID,
                  'executor_digest': source, 'case': case, 'shutdown': shutdown,
                  'credentials': credentials, 'remote_validation': 'NOT_RUN',
                  'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/'operator-cli-flow-report.json', result)
        from .local_operator_cli_reporting import publish_operator_cli
        canonical = publish_operator_cli(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
