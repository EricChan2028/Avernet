"""One explicit owned-local lifecycle for the complete Structured Story."""
from pathlib import Path
import signal

from .errors import has_cancellation
from .inventory import executor_digest, reviewed_release_inventory
from .local_fixtures import OwnedLocalFixtures
from .local_structured_contract import CASE_ID, PARENT, SUITE, REQUIREMENTS, structured_source_binding
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .local_template_asset import LocalTemplateAsset
from .profile import identifier
from .run_state import RunStateError, read, write


def local_structured_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str, confirm_run_id: str,
                      allow_local_processes: bool, allow_fixture_write: bool,
                      allow_write: bool, template_dir: Path, template_manifest: Path,
                      template_manifest_sha256: str) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Structured requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Structured Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Structured requires a fresh state directory; existing evidence is retained')
    template_asset = LocalTemplateAsset(template_dir, template_manifest, template_manifest_sha256)
    template_asset.validate()
    source = executor_digest()
    original = structured_source_binding()
    legacy = reviewed_release_inventory()
    if PARENT not in {row['id'] for row in legacy}:
        raise RunStateError('Structured parent is missing from the original release denominator')
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
               'case_id': CASE_ID, 'run_id': run_id, 'executor_digest': source,
               'bcs_binary_sha256': binary_sha256, 'store_messages': False,
               'source_binding': original, 'template_manifest_sha256': template_manifest_sha256, 'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'runtime_dir': str(state_dir.resolve())}
    runtime = identities = None
    case = {}
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id, template_asset=template_asset) as runtime:
                write(runtime.root/'structured-binding.json', binding)
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                    fixtures.apply(allow_write=True, role_identity=identities)
                    for role in fixtures.roles:
                        identities.verify(role)
                    from .local_structured import OwnedLocalStructured
                    case = OwnedLocalStructured(identities).run()
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Structured owned runtime was not created')
        if not case and (runtime.root/'local-structured.json').is_file():
            saved = read(runtime.root/'local-structured.json')
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        shutdown = runtime._record()
        credentials = identities.summary() if identities else {'credential_memory': 'NOT_CAPTURED'}
        expected = [{'id': key, 'status': 'PASS'} for key in REQUIREMENTS]
        passed = (error is None and executor_digest() == source
                  and read(runtime.root/'structured-binding.json') == binding
                  and bool(case) and read(runtime.root/'local-structured.json') == case
                  and case.get('status') == 'LOCAL_STRUCTURED_PASS'
                  and case.get('assertions') == expected
                  and case.get('run_id') == run_id and case.get('case_id') == CASE_ID
                  and case.get('executor_digest') == source
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
                  and credentials.get('credential_memory') == 'CLEARED')
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else 'LOCAL_STRUCTURED_PASS' if passed else 'BLOCKED')
        result = {'schema_version': 1, 'status': status, 'run_id': run_id, 'case_id': CASE_ID,
                  'executor_digest': source, 'case': case, 'shutdown': shutdown,
                  'credentials': credentials, 'remote_validation': 'NOT_RUN',
                  'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/'structured-flow-report.json', result)
        from .local_structured_reporting import publish_structured
        canonical = publish_structured(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
