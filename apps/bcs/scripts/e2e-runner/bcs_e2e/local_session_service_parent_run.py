"""One owned-local runtime lifecycle for the Session Service parent.

The existing parent CaseExecution composes two validator contexts and four
child slices.  This runner supplies those exact contexts, keeps only fresh
owned Bot credentials, and publishes all result files before preserving the
stopped runtime evidence.
"""
from __future__ import annotations

from http.client import HTTPConnection
import json
import os
from pathlib import Path
import signal
from urllib.parse import quote, urlsplit

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest, reviewed_release_inventory
from .local_fixtures import OwnedLocalFixtures
from .local_operator_cli_contract import (
    sessions_services_source_binding,
)
from .local_role_identity import OwnedLocalRoleIdentity
from .local_session_service_parent_case import (
    PARENT_CASE_ID,
    PARENT_CASE_SUITE,
    execute_local_session_service_parent_case,
)
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, identifier
from .run_state import RunStateError, read, write
from .session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION,
    SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION,
    SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION,
    SESSION_CLI_SERVICE_SESSION_READ_OPERATION,
    SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_CHAT_OPERATION,
    SESSION_CLI_SESSION_COMPLETE_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_DELETE_OPERATION,
    SESSION_CLI_SESSION_GROUP_LISTING_OPERATION,
    SESSION_CLI_SESSION_MEMBER_ADD_OPERATION,
    SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION,
    SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION,
    SESSION_CLI_SESSION_MESSAGES_OPERATION,
    SESSION_CLI_SESSION_PATCH_TITLE_OPERATION,
    SESSION_CLI_SESSION_READ_OPERATION,
    SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION,
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_lifecycle_profile,
)
from .workspace_cli_profile import (
    SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
    SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
)
from .workspace_cli_runner import WorkspaceCliRunner

ROLES = ('PM', 'ENG')
DRIVER_TOKEN_ENV = 'BCS_E2E_SESSION_SERVICE_DRIVER_TOKEN'
MEMBER_TOKEN_ENV = 'BCS_E2E_SESSION_SERVICE_MEMBER_TOKEN'
BINDING_PATH = 'session-service-parent-binding.json'
VALIDATION_PROFILE_PATH = 'session-service-validation-profile.json'
EXECUTION_PROFILE_PATH = 'session-service-execution-profile.json'
CASE_PATH = 'local-session-service-parent.json'
FLOW_PATH = 'session-service-parent-flow-report.json'
BINDING_CONTRACT = 'owned-local-session-service-parent-binding-v1'
SUCCESS_STATUS = 'LOCAL_SESSION_SERVICE_PARENT_PASS'


def _ensure_public_actor_visibility(runtime: OwnedBcsProcess,
                                     identities: OwnedLocalRoleIdentity,
                                     role: str) -> None:
    """Make the declared public actor Profile match the live fixture.

    Local Bot registration defaults to protected visibility.  Session group
    creation is therefore admitted from the registered state that the profile
    already declares: each case-owned actor sets only its own visibility to
    public, then reads that exact state back with its own Bot credential.
    """
    bot_id = identities.actor_id(role)
    token = identities._entries[role][1]
    target = urlsplit(runtime.inputs.origin)
    if (target.scheme != 'http' or target.hostname != '127.0.0.1'
            or not target.port or target.path):
        raise RunStateError(role + ' visibility origin is invalid')
    path = '/bots/' + quote(bot_id, safe='') + '/visibility'
    payload = json.dumps({'visibility': 'public'}, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    connection = HTTPConnection(target.hostname, target.port, timeout=5)
    try:
        connection.request(
            'PUT', path, body=payload, headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + token,
            })
        response = connection.getresponse()
        raw = response.read(65_537)
        if len(raw) > 65_536:
            raise RunStateError(
                role + ' visibility update response exceeded budget')
        try:
            value = json.loads(raw.decode('utf-8')) if raw else None
        except json.JSONDecodeError:
            raise RunStateError(
                role + ' visibility update response is invalid') from None
        if (response.status != 200 or not isinstance(value, dict)
                or value.get('success') is not True
                or value.get('data') != {
                    'bot_uuid': bot_id, 'visibility': 'public'}):
            raise RunStateError(
                role + ' public visibility update was not confirmed')
        connection.close()
        connection.connect()
        connection.request('GET', path, headers={
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + token,
        })
        response = connection.getresponse()
        raw = response.read(65_537)
        if len(raw) > 65_536:
            raise RunStateError(role + ' visibility read exceeded budget')
        try:
            value = json.loads(raw.decode('utf-8')) if raw else None
        except json.JSONDecodeError:
            raise RunStateError(
                role + ' visibility read response is invalid') from None
        if (response.status != 200 or not isinstance(value, dict)
                or not isinstance(value.get('data'), dict)
                or value['data'].get('bot_uuid') != bot_id
                or value['data'].get('visibility') != 'public'):
            raise RunStateError(role + ' public visibility readback was not confirmed')
    except RunStateError:
        raise
    except Exception:
        raise RunStateError(
            role + ' public visibility admission transport failed') from None
    finally:
        connection.close()


def _binding(*, binary_sha256: str, cli_binary_sha256: str, run_id: str,
             state_dir: Path, executor: str, source: dict,
             legacy: list, lifecycle_profile: dict,
             complete_profile: dict) -> dict:
    return {
        'schema_version': 1,
        'contract': BINDING_CONTRACT,
        'suite': PARENT_CASE_SUITE,
        'case_id': PARENT_CASE_ID,
        'case_version': 1,
        'run_id': run_id,
        'executor_digest': executor,
        'bcs_binary_sha256': binary_sha256,
        'cli_binary_sha256': cli_binary_sha256,
        'store_messages': False,
        'fixture_set': 'cli-file',
        'fixture_roles': list(ROLES),
        'source_binding': source,
        'selected_requirement_ids': [
            row['id'] for row in source['requirements']],
        'validation_profile_contract': lifecycle_profile['contract'],
        'validation_profile_path': VALIDATION_PROFILE_PATH,
        'validation_profile_digest': fingerprint(lifecycle_profile),
        'execution_profile_contract': complete_profile['contract'],
        'execution_profile_path': EXECUTION_PROFILE_PATH,
        'execution_profile_digest': fingerprint(complete_profile),
        'legacy_inventory': legacy,
        'runtime_dir': str(state_dir.resolve()),
    }


def _route(method: str, path: str, *, auth: str = 'case_owned_driver') -> dict:
    return {
        'method': method,
        'path': path,
        'api_face': 'legacy',
        'auth': auth,
        'response': 'legacy-json',
        'traceparent': 'disabled',
    }


def _lifecycle_routes() -> dict:
    return {
        SESSION_CLI_SESSION_PATCH_TITLE_OPERATION:
            _route('PATCH', '/sessions/{session_id}'),
        SESSION_CLI_SESSION_MEMBER_ADD_OPERATION:
            _route('POST', '/sessions/{session_id}/members'),
        SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION:
            _route('PATCH', '/sessions/{session_id}/members/{member_actor_id}'),
        SESSION_CLI_SESSION_CHAT_OPERATION:
            _route('POST', '/sessions/{session_id}/chat'),
        SESSION_CLI_SESSION_MESSAGES_OPERATION:
            _route('GET', '/sessions/{session_id}/messages'),
        SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION:
            _route('DELETE', '/sessions/{session_id}/members/{member_actor_id}'),
        SESSION_CLI_SESSION_DELETE_OPERATION:
            _route('DELETE', '/sessions/{session_id}?bot_id={driver_actor_id}'),
        SESSION_CLI_SESSION_GROUP_LISTING_OPERATION:
            _route('GET', '/groups/{group_id}/sessions'),
        SESSION_CLI_GROUP_DELETE_OPERATION:
            _route('DELETE', '/groups/{group_id}?bot_id={driver_actor_id}'),
        SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
            _route('GET', '/groups/{group_id}'),
    }


def _complete_routes() -> dict:
    return {
        SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION:
            _route('GET', '/openapi/v1/collaboration/messages/not-a-message/deliveries?session_id={session_id}'),
        SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION:
            _route('POST', '/openapi/v1/collaboration/sessions/{session_id}/message-deliveries/query'),
        SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION:
            _route('POST', '/openapi/v1/collaboration/messages/not-a-message/deliveries/not-a-delivery/cancel'),
        SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION:
            _route('POST', '/openapi/v1/collaboration/messages/not-a-message/deliveries/cancel'),
        SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION:
            _route('POST', '/openapi/v1/collaboration/messages/not-a-message/deliveries/not-a-delivery/resolve'),
        SESSION_CLI_SESSION_COMPLETE_OPERATION:
            _route('POST', '/sessions/{session_id}/complete'),
        SESSION_CLI_SESSION_READ_OPERATION:
            _route('GET', '/sessions/{session_id}'),
        SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION:
            _route('POST', '/sessions/{session_id}/complete'),
        SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION:
            _route('DELETE', '/sessions/{session_id}?bot_id={driver_actor_id}'),
        SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION:
            _route('DELETE', '/sessions/{session_id}?bot_id=bot:{driver_actor_id}'),
        SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION:
            _route('PATCH', '/groups/{group_id}/settings'),
        SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION:
            _route('POST', '/services/{group_id}/sessions'),
        SESSION_CLI_SERVICE_SESSION_READ_OPERATION:
            _route('GET', '/services/{group_id}/sessions/{session_id}'),
        SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION:
            _route('GET', '/services/{group_id}/sessions/{session_id}',
                   auth='case_owned_member'),
        SESSION_CLI_GROUP_DELETE_OPERATION:
            _route('DELETE', '/groups/{group_id}?bot_id={driver_actor_id}'),
        SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
            _route('GET', '/groups/{group_id}'),
    }


def _profile_values(runtime: OwnedBcsProcess,
                    identities: OwnedLocalRoleIdentity) -> tuple[dict, dict]:
    human = runtime.inputs.human_id
    base = {
        'schema_version': 1,
        'environment': {
            'tier': 'local',
            'id': runtime.run_id,
            'expected_bcs_env': 'local',
        },
        'entrypoint': {
            'kind': 'singlebox',
            'origin': runtime.inputs.origin,
            'api_face': 'legacy',
            'manifest_face': 'legacy',
        },
        'human': {'user_id': human},
        'actors': {
            role: {
                'bot_id': identities.actor_id(logical_role),
                'owner_id': human,
                'dedicated': True,
                'visibility': 'public',
            }
            for role, logical_role in (('driver', 'PM'), ('member', 'ENG'))
        },
        'policy': {
            'fixture_pool': 'owned-local-session-service-cli-file',
            'exclusive_use_confirmed': True,
            'timeout_seconds': 30,
            'max_response_bytes': 65536,
            'failure_retention_hours': 24,
        },
        'cli': {
            'transport': 'product-cli',
            'command_family': 'session',
            'api_face': 'legacy',
        },
    }
    lifecycle = dict(base)
    lifecycle.update(
        contract=SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
        auth={
            'kind': 'owned-local-bot-tokens',
            'driver_token_env': DRIVER_TOKEN_ENV,
        },
        relationships={
            'group': 'bounded-owned-bots-cli-session-lifecycle',
            'initial_session': 'deleted-with-group',
            'explicit_session': 'renamed-muted-chatted-deleted',
            'cleanup': 'exact-owned-group',
            'participant_mode': 'driver-member',
        },
        route_bindings=_lifecycle_routes(),
    )
    complete = dict(base)
    complete.update(
        contract=SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
        auth={
            'kind': 'owned-bot-tokens',
            'driver_token_env': DRIVER_TOKEN_ENV,
            'member_token_env': MEMBER_TOKEN_ENV,
        },
        relationships={
            'group': 'bounded-owned-bots-cli-service-invoke',
            'initial_session': 'deleted-with-group',
            'explicit_session': 'completed-then-creator-deleted',
            'service_session': 'invoked-then-creator-deleted',
            'cleanup': 'exact-owned-group',
            'participant_mode': 'driver-member',
        },
        route_bindings=_complete_routes(),
    )
    return (
        validate_session_cli_lifecycle_profile(lifecycle),
        validate_session_cli_complete_invoke_profile(complete),
    )


class _SequentialProfile(dict):
    """Supply the four exact validation views through the parent signature.

    The parent creates two lifecycle adapters followed by two
    completion-invocation adapters.  Each validator asks for
    ``route_bindings`` exactly once, so this small deterministic context view
    advances only at that boundary and leaves every returned profile plain.
    """

    def __init__(self, lifecycle: dict, complete: dict):
        super().__init__()
        self._views = (lifecycle, lifecycle, complete, complete)
        self._index = 0
        self._load_current()

    def _load_current(self) -> None:
        self.clear()
        self.update(self._views[self._index])

    def get(self, key, default=None):
        if key == 'route_bindings' and self._index < len(self._views):
            self.clear()
            self.update(self._views[self._index])
            self._index += 1
        return super().get(key, default)

    def current(self) -> dict:
        return dict(self._views[self._index])


class _ParentCliRunner:
    """Delegate while allowing reviewed Group creation once per child."""

    def __init__(self, profile: dict, *, binary: Path,
                 binary_sha256: str, state_dir: Path):
        self._runner = WorkspaceCliRunner(
            profile, binary=binary, binary_sha256=binary_sha256,
            state_dir=state_dir)

    @property
    def profile(self) -> dict:
        return self._runner.profile

    def execute(self, operation: str, **kwargs) -> dict:
        self._runner.executed.clear()
        return self._runner.execute(operation, **kwargs)


def local_session_service_parent_run(*, binary: Path, binary_sha256: str,
                                     cli_binary: Path,
                                     cli_binary_sha256: str,
                                     state_dir: Path, run_id: str,
                                     confirm_run_id: str,
                                     allow_local_processes: bool,
                                     allow_fixture_write: bool,
                                     allow_write: bool) -> dict:
    """Own one Session Service parent execution from start to frozen report."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError(
            'Session Service parent requires process/fixture/business '
            'permissions and exact Run confirmation')
    identifier(run_id, 'Session Service parent Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError(
            'Session Service parent requires a fresh state directory; '
            'existing evidence is retained')
    executor = executor_digest()
    source = sessions_services_source_binding()
    legacy = current_manifest()['legacy_inventory']
    reviewed = reviewed_release_inventory()
    if (source['parent'] not in {row.get('id') for row in reviewed}):
        raise RunStateError(
            'Session Service parent is missing from the release denominator')

    runtime = None
    case: dict = {}
    credentials = {'credential_memory': 'NOT_CAPTURED'}
    binding: dict = {}
    error = None
    previous = signal.signal(
        signal.SIGTERM,
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(
                    binary=binary, binary_sha256=binary_sha256,
                    root=state_dir, run_id=run_id,
                    store_messages=False) as owned_runtime:
                runtime = owned_runtime
                fixtures = OwnedLocalFixtures(
                    runtime, fixture_set='cli-file')
                with OwnedLocalRoleIdentity(
                        fixtures, roles=ROLES) as owned_identities:
                    fixtures.apply(allow_write=True,
                                   role_identity=owned_identities)
                    for role in ROLES:
                        owned_identities.verify(role)
                        _ensure_public_actor_visibility(
                            runtime, owned_identities, role)
                    lifecycle_profile, complete_profile = _profile_values(
                        runtime, owned_identities)
                    binding = _binding(
                        binary_sha256=binary_sha256,
                        cli_binary_sha256=cli_binary_sha256,
                        run_id=run_id, state_dir=runtime.root,
                        executor=executor, source=source, legacy=legacy,
                        lifecycle_profile=lifecycle_profile,
                        complete_profile=complete_profile)
                    write(runtime.root / BINDING_PATH, binding)
                    write(runtime.root / VALIDATION_PROFILE_PATH,
                          lifecycle_profile)
                    write(runtime.root / EXECUTION_PROFILE_PATH,
                          complete_profile)
                    runner = _ParentCliRunner(
                        complete_profile, binary=cli_binary,
                        binary_sha256=cli_binary_sha256,
                        state_dir=runtime.root)
                    tokens = {
                        DRIVER_TOKEN_ENV:
                            owned_identities._entries['PM'][1],
                        MEMBER_TOKEN_ENV:
                            owned_identities._entries['ENG'][1],
                    }
                    previous_environment = dict(os.environ)
                    os.environ.update(tokens)
                    try:
                        execution = execute_local_session_service_parent_case(
                            _SequentialProfile(lifecycle_profile,
                                               complete_profile),
                            runner)
                        snapshot = execution.snapshot()
                        snapshot.update(
                            schema_version=1,
                            run_id=run_id,
                            executor_digest=executor,
                            binding_contract=BINDING_CONTRACT,
                            validation_profile_contract=(
                                lifecycle_profile['contract']),
                            execution_profile_contract=(
                                complete_profile['contract']),
                        )
                        case = snapshot
                        write(runtime.root / CASE_PATH, case)
                    finally:
                        os.environ.clear()
                        os.environ.update(previous_environment)
                credentials = owned_identities.summary()
        except BaseException as failure:
            error = failure

        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError(
                'Session Service parent owned runtime was not created')

        if not case and (runtime.root / CASE_PATH).is_file():
            saved = read(runtime.root / CASE_PATH)
            if (saved.get('case_id') == PARENT_CASE_ID
                    and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == executor):
                case = saved

        shutdown = runtime._record()
        passed = (error is None and executor_digest() == executor
                  and bool(binding) and bool(case)
                  and read(runtime.root / BINDING_PATH) == binding
                  and read(runtime.root / CASE_PATH) == case
                  and case.get('status') == 'PASS'
                  and case.get('completed_count') == 4
                  and case.get('total_count') == 4
                  and case.get('error_type') is None
                  and credentials.get('credential_memory') == 'CLEARED'
                  and shutdown.get('status') == 'STOPPED'
                  and type(shutdown.get('returncode')) is int
                  and shutdown.get('returncode') == 0
                  and shutdown.get('forced_stop') is False)
        status = (
            'CANCELLED' if error is not None and has_cancellation(error)
            else SUCCESS_STATUS if passed else 'BLOCKED')
        flow = {
            'schema_version': 1,
            'status': status,
            'run_id': run_id,
            'case_id': PARENT_CASE_ID,
            'executor_digest': executor,
            'binding_digest': fingerprint(binding),
            'case': case,
            'shutdown': shutdown,
            'credentials': credentials,
            'remote_validation': 'NOT_RUN',
            'full_release_status': 'NOT_QUALIFIED',
            'ready_to_execute': False,
        }
        if error is not None:
            flow['error_type'] = type(error).__name__
        write(runtime.root / FLOW_PATH, flow)
        from .local_session_service_parent_reporting import (
            publish_session_service_parent,
        )
        canonical = publish_session_service_parent(runtime, binding, flow)
        flow.update(canonical_summary=canonical['summary'],
                    outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            flow['status'] = 'BLOCKED'
        return flow
    finally:
        signal.signal(signal.SIGTERM, previous)
