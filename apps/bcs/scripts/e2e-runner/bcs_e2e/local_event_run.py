"""One explicit owned-local lifecycle for the complete Event Webhook Story."""
from pathlib import Path
import signal

from .errors import has_cancellation
from .inventory import executor_digest, reviewed_release_inventory
from .local_fixtures import OwnedLocalFixtures
from .local_event_contract import CASE_ID, PARENT, SUITE, REQUIREMENTS, event_source_binding
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .local_event_sink import OwnedLocalEventSink
from .profile import identifier
from .run_state import RunStateError, read, write


def local_event_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str, confirm_run_id: str,
                      allow_local_processes: bool, allow_fixture_write: bool,
                      allow_write: bool) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Event Webhook requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Event Webhook Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Event Webhook requires a fresh state directory; existing evidence is retained')
    source = executor_digest()
    original = event_source_binding()
    legacy = reviewed_release_inventory()
    if PARENT not in {row['id'] for row in legacy}:
        raise RunStateError('Event Webhook parent is missing from the original release denominator')
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
               'case_id': CASE_ID, 'run_id': run_id, 'executor_digest': source,
               'bcs_binary_sha256': binary_sha256, 'store_messages': True,
               'source_binding': original, 'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'runtime_dir': str(state_dir.resolve())}
    runtime = identities = sink = None
    case = {}
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedLocalEventSink(run_id) as sink:
                with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                     root=state_dir, run_id=run_id, store_messages=True, event_sink=sink) as runtime:
                    write(runtime.root/'event-binding.json', binding)
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                        fixtures.apply(allow_write=True, role_identity=identities)
                        for role in fixtures.roles:
                            identities.verify(role)
                        from .local_event import OwnedLocalEventStory
                        case = OwnedLocalEventStory(identities,sink).run()
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Event Webhook owned runtime was not created')
        if not case and (runtime.root/'local-event.json').is_file():
            saved = read(runtime.root/'local-event.json')
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        receiver = sink.snapshot() if sink is not None else {'state':'NOT_CREATED'}
        if case:
            case['receiver'] = receiver
            write(runtime.root/'local-event.json',case)
        shutdown = runtime._record()
        credentials = identities.summary() if identities else {'credential_memory': 'NOT_CAPTURED'}
        expected = [{'id': key, 'status': 'PASS'} for key in REQUIREMENTS]
        passed = (error is None and executor_digest() == source
                  and read(runtime.root/'event-binding.json') == binding
                  and bool(case) and read(runtime.root/'local-event.json') == case
                  and case.get('status') == 'LOCAL_EVENT_PASS'
                  and case.get('assertions') == expected
                  and case.get('run_id') == run_id and case.get('case_id') == CASE_ID
                  and case.get('executor_digest') == source
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
                  and credentials.get('credential_memory') == 'CLEARED'
                  and receiver.get('state') == 'CLOSED' and receiver.get('failed') is False
                  and receiver.get('buffered') == 0)
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else 'LOCAL_EVENT_PASS' if passed else 'BLOCKED')
        result = {'schema_version': 1, 'status': status, 'run_id': run_id, 'case_id': CASE_ID,
                  'executor_digest': source, 'case': case, 'shutdown': shutdown,
                  'credentials': credentials, 'receiver': receiver, 'remote_validation': 'NOT_RUN',
                  'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/'event-flow-report.json', result)
        from .local_event_reporting import publish_event
        canonical = publish_event(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
