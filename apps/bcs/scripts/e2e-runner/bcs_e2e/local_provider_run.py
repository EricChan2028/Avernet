"""One owned-local lifecycle for the full Provider/Organization parent."""
import hashlib
from pathlib import Path
import signal

from .errors import has_cancellation
from .inventory import executor_digest, reviewed_release_inventory
from .local_fixtures import OwnedLocalFixtures
from .local_provider_contract import CASE_ID, PARENT, SUITE, REQUIREMENTS, provider_source_binding
from .local_provider_sink import OwnedLocalProviderSink
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier
from .run_state import RunStateError, read, write


def local_provider_run(*, binary: Path, binary_sha256: str, state_dir: Path,
                       run_id: str, confirm_run_id: str, allow_local_processes: bool,
                       allow_fixture_write: bool, allow_write: bool) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Provider Story requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Provider Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Provider Story requires a fresh directory; preserve existing evidence')
    source = executor_digest()
    original = provider_source_binding()
    legacy = reviewed_release_inventory()
    if PARENT not in {row['id'] for row in legacy}:
        raise RunStateError('Provider parent is missing from original release inventory')
    runtime = identities = None
    case, binding = {}, {}
    error = None
    sink = OwnedLocalProviderSink(run_id)
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with sink:
                with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                     root=state_dir, run_id=run_id, provider_sink=sink) as runtime:
                    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
                        'case_id': CASE_ID, 'run_id': run_id, 'executor_digest': source,
                        'bcs_binary_sha256': binary_sha256, 'store_messages': False,
                        'source_binding': original, 'selected_requirement_ids': list(REQUIREMENTS),
                        'legacy_inventory': legacy, 'runtime_dir': str(runtime.root.resolve()),
                        'sink_url_sha256': hashlib.sha256(sink.url.encode()).hexdigest()}
                    write(runtime.root/'provider-binding.json', binding)
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                        fixtures.apply(allow_write=True, role_identity=identities)
                        for role in fixtures.roles:
                            identities.verify(role)
                        from .local_provider_api import OwnedLocalProviderApi
                        case = OwnedLocalProviderApi(identities, sink).run()
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Provider owned runtime was not created')
        if not case and (runtime.root/'local-provider-api.json').is_file():
            saved = read(runtime.root/'local-provider-api.json')
            if (saved.get('run_id') == run_id and saved.get('case_id') == CASE_ID
                    and saved.get('executor_digest') == source):
                case = saved
        shutdown = runtime._record()
        downstream = sink.summary()
        write(runtime.root/'provider-sink-final.json', {'schema_version': 1, 'sink': downstream})
        credentials = identities.summary() if identities else {'credential_memory': 'NOT_CAPTURED'}
        passed = (error is None and executor_digest() == source and bool(case)
            and read(runtime.root/'provider-binding.json') == binding
            and read(runtime.root/'local-provider-api.json') == case
            and case.get('status') == 'LOCAL_PROVIDER_PASS'
            and case.get('assertions') == [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS]
            and case.get('run_id') == run_id and case.get('case_id') == CASE_ID
            and case.get('executor_digest') == source
            and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
            and credentials.get('credential_memory') == 'CLEARED'
            and downstream.get('state') == 'CLOSED' and downstream.get('failed') is False)
        status = ('CANCELLED' if error is not None and has_cancellation(error) else
                  'LOCAL_PROVIDER_PASS' if passed else 'BLOCKED')
        result = {'schema_version': 1, 'run_id': run_id, 'case_id': CASE_ID, 'status': status,
                  'executor_digest': source, 'case': case, 'shutdown': shutdown, 'downstream': downstream,
                  'credentials': credentials, 'remote_validation': 'NOT_RUN',
                  'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/'provider-flow-report.json', result)
        from .local_provider_reporting import publish_provider
        canonical = publish_provider(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
