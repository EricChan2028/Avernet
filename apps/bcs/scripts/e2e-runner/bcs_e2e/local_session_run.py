"""One explicit owned-local lifecycle for the public API/JWT/WS helper of the Session parent."""
from pathlib import Path
import signal

from .errors import has_cancellation
from .inventory import executor_digest, reviewed_release_inventory
from .local_fixtures import OwnedLocalFixtures
from .local_session_contract import PUBLIC_CASE as CASE_ID, PARENT, PUBLIC_SUITE as SUITE, REQUIREMENTS, session_source_binding
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier
from .run_state import RunStateError, read, write


def local_session_public_run(*, binary: Path, binary_sha256: str,
                      state_dir: Path, run_id: str, confirm_run_id: str,
                      allow_local_processes: bool, allow_fixture_write: bool,
                      allow_write: bool, cli_binary: Path | None = None,
                      cli_binary_sha256: str | None = None, with_completion: bool = False) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Session public API/JWT/WS requires process/fixture/business permissions and exact Run confirmation')
    if (cli_binary is None) != (cli_binary_sha256 is None):
        raise RunStateError('Session sharing requires both CLI binary and digest')
    if type(with_completion) is not bool or with_completion and cli_binary is None:
        raise RunStateError('Complete Session flow requires the approved actual CLI pair')
    with_sharing = cli_binary is not None
    if with_sharing:
        from .local_cli import require_cli_binary
        require_cli_binary(cli_binary, cli_binary_sha256)
    identifier(run_id, 'Session public API/JWT/WS Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Session public API/JWT/WS requires a fresh state directory; existing evidence is retained')
    source = executor_digest()
    original = session_source_binding()
    legacy = reviewed_release_inventory()
    if PARENT not in {row['id'] for row in legacy}:
        raise RunStateError('Session public API/JWT/WS parent is missing from the original release denominator')
    binding = {'schema_version': 1, 'contract': 'owned-local-session-public-v1', 'suite': SUITE,
               'case_id': CASE_ID, 'run_id': run_id, 'executor_digest': source,
               'bcs_binary_sha256': binary_sha256, 'store_messages': True,
               'source_binding': original, 'selected_requirement_ids': [REQUIREMENTS[0]],
               'parent_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'runtime_dir': str(state_dir.resolve())}
    runtime = identities = None
    case = {}
    sharing_case = {}
    sharing_binding = {}
    completion_case = {}
    completion_binding = {}
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id, store_messages=True) as runtime:
                write(runtime.root/'session-binding.json', binding)
                if with_sharing:
                    write(runtime.root/'session-sharing-plan.json', dict(binding,
                        contract='owned-local-session-sharing-v1', suite='owned-local-session-sharing-v1',
                        case_id='LOCAL-SESSION-SHARING-01', cli_binary_sha256=cli_binary_sha256,
                        selected_requirement_ids=list(REQUIREMENTS[:6])))
                if with_completion:
                    write(runtime.root/'session-completion-plan.json', dict(binding,
                        contract='owned-local-session-flow-v1', suite='owned-local-session-flow-v1',
                        case_id='LOCAL-SESSION-FLOW-01', cli_binary_sha256=cli_binary_sha256,
                        selected_requirement_ids=list(REQUIREMENTS)))
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                    fixtures.apply(allow_write=True, role_identity=identities)
                    for role in fixtures.roles:
                        identities.verify(role)
                    from .local_session_public import OwnedLocalSessionPublic
                    case = OwnedLocalSessionPublic(identities).run()
                    if with_sharing:
                        from .profile import fingerprint
                        from .local_session_sharing import OwnedLocalSessionSharing
                        sharing_binding = dict(binding, contract='owned-local-session-sharing-v1',
                            suite='owned-local-session-sharing-v1', case_id='LOCAL-SESSION-SHARING-01',
                            cli_binary_sha256=cli_binary_sha256, selected_requirement_ids=list(REQUIREMENTS[:6]),
                            public_journal_digest=fingerprint(case))
                        write(runtime.root/'session-sharing-binding.json', sharing_binding)
                        sharing_case = OwnedLocalSessionSharing(identities, cli_binary, cli_binary_sha256).run()
                        if with_completion:
                            from .local_session_completion import OwnedLocalSessionCompletion
                            completion_binding = dict(binding, contract='owned-local-session-flow-v1',
                                suite='owned-local-session-flow-v1', case_id='LOCAL-SESSION-FLOW-01',
                                cli_binary_sha256=cli_binary_sha256, selected_requirement_ids=list(REQUIREMENTS),
                                public_journal_digest=fingerprint(case), sharing_journal_digest=fingerprint(sharing_case))
                            write(runtime.root/'session-completion-binding.json', completion_binding)
                            completion_case = OwnedLocalSessionCompletion(identities, cli_binary, cli_binary_sha256).run()


        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Session public API/JWT/WS owned runtime was not created')
        if not case and (runtime.root/'local-session-public.json').is_file():
            saved = read(runtime.root/'local-session-public.json')
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        shutdown = runtime._record()
        credentials = identities.summary() if identities else {'credential_memory': 'NOT_CAPTURED'}
        expected = [{'id': REQUIREMENTS[0], 'status': 'PASS'}]
        passed = ((error is None or with_sharing and bool(case)) and executor_digest() == source
                  and read(runtime.root/'session-binding.json') == binding
                  and bool(case) and read(runtime.root/'local-session-public.json') == case
                  and case.get('status') == 'LOCAL_SESSION_PUBLIC_PASS'
                  and case.get('assertions') == expected
                  and case.get('run_id') == run_id and case.get('case_id') == CASE_ID
                  and case.get('executor_digest') == source
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
                  and credentials.get('credential_memory') == 'CLEARED')
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else 'LOCAL_SESSION_PUBLIC_PASS' if passed else 'BLOCKED')
        public_status = ('LOCAL_SESSION_PUBLIC_PASS' if passed else 'CANCELLED' if status == 'CANCELLED' else 'BLOCKED')
        result = {'schema_version': 1, 'status': public_status, 'run_id': run_id, 'case_id': CASE_ID,
                  'executor_digest': source, 'case': case, 'shutdown': shutdown,
                  'credentials': credentials, 'remote_validation': 'NOT_RUN',
                  'full_release_status': 'NOT_QUALIFIED', 'full_story_status': 'NOT_MIGRATED',
                  'remaining_requirement_ids': list(REQUIREMENTS[1:]), 'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/'session-public-flow-report.json', result)
        from .local_session_reporting import publish_session_public
        canonical = publish_session_public(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SLICE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        if with_sharing:
            if not sharing_binding:
                # Public/setup failed before the sharing binding existed; keep
                # original evidence but never call a partial run sharing success.
                result.update(status='CANCELLED' if status == 'CANCELLED' else 'BLOCKED',
                              case_id='LOCAL-SESSION-SHARING-01', sharing_status='NOT_RUN')
                if with_completion:
                    result.update(case_id='LOCAL-SESSION-FLOW-01', completion_status='NOT_RUN')
                return result
            if not sharing_case and (runtime.root/'local-session-sharing.json').is_file():
                sharing_case = read(runtime.root/'local-session-sharing.json')
            sharing_pass = ((error is None or with_completion and bool(sharing_case)) and canonical['status'] == 'LOCAL_SLICE_PASS'
                            and sharing_case.get('status') == 'LOCAL_SESSION_SHARING_PASS'
                            and sharing_case.get('assertions') == [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS[1:6]]
                            and read(runtime.root/'local-session-sharing.json') == sharing_case)
            result = {'schema_version': 1, 'case_id': 'LOCAL-SESSION-SHARING-01', 'run_id': run_id,
                      'executor_digest': source, 'status': 'LOCAL_SESSION_SHARING_PASS' if sharing_pass else
                          'CANCELLED' if status == 'CANCELLED' else 'BLOCKED',
                      'case': sharing_case, 'shutdown': shutdown, 'credentials': credentials,
                      'public_status': canonical['status'], 'full_story_status': 'NOT_MIGRATED',
                      'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}
            if error is not None:
                result['error_type'] = type(error).__name__
            write(runtime.root/'session-sharing-flow-report.json', result)
            from .local_session_sharing_reporting import publish_sharing
            combined = publish_sharing(runtime, sharing_binding, result)
            result.update(canonical_summary=combined['summary'], outputs=combined['outputs'])
            if combined['status'] not in ('LOCAL_SLICE_PASS', 'CANCELLED'):
                result['status'] = 'BLOCKED'
        if with_completion:
            if not completion_binding:
                result.update(status='CANCELLED' if status == 'CANCELLED' else 'BLOCKED',
                              case_id='LOCAL-SESSION-FLOW-01', completion_status='NOT_RUN')
                return result
            if not completion_case and (runtime.root/'local-session-completion.json').is_file():
                completion_case = read(runtime.root/'local-session-completion.json')
            completion_pass = (error is None and combined['status'] == 'LOCAL_SLICE_PASS'
                and completion_case.get('status') == 'LOCAL_SESSION_COMPLETION_PASS'
                and completion_case.get('assertions') == [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS[6:]]
                and read(runtime.root/'local-session-completion.json') == completion_case)
            result = {'schema_version': 1, 'case_id': 'LOCAL-SESSION-FLOW-01', 'run_id': run_id,
                      'executor_digest': source, 'status': 'LOCAL_SESSION_FLOW_PASS' if completion_pass else
                          'CANCELLED' if status == 'CANCELLED' else 'BLOCKED',
                      'case': completion_case, 'shutdown': shutdown, 'credentials': credentials,
                      'sharing_status': combined['status'], 'full_release_status': 'NOT_QUALIFIED',
                      'remote_validation': 'NOT_RUN', 'ready_to_execute': False}
            if error is not None:
                result['error_type'] = type(error).__name__
            write(runtime.root/'session-flow-report.json', result)
            from .local_session_flow_reporting import publish_session_flow
            completed = publish_session_flow(runtime, completion_binding, result)
            result.update(canonical_summary=completed['summary'], outputs=completed['outputs'])
            if completed['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
                result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
