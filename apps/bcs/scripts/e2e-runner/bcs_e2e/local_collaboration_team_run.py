"""One explicit owned-local lifecycle for the collaboration-team CLI Story."""
from __future__ import annotations

from http.client import HTTPConnection
import json
import os
from pathlib import Path
import signal
from urllib.parse import quote, urlsplit

from .collaboration_team_adapter import RemoteCollaborationTeamAdapter
from .collaboration_team_contract import (
    ADAPTER_METHODS,
    CASE_ID,
    REQUIREMENT_IDS,
    SUITE,
    collaboration_team_case_plan,
    execute_collaboration_team_case,
)
from .collaboration_team_profile import validate_collaboration_team_profile
from .collaboration_team_runner import CollaborationTeamRunner
from .errors import has_cancellation
from .inventory import executor_digest, reviewed_release_inventory
from .local_fixtures import OwnedLocalFixtures
from .local_operator_cli_contract import collaboration_team_source_binding
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier, unique_keys
from .run_state import RunStateError, read, write

ROLES = ('CEO', 'PM', 'ENG', 'QA', 'CS')
BINDING = 'owned-local-collaboration-team-binding-v1'
CASE_PATH = 'local-collaboration-team.json'
PROFILE_PATH = 'collaboration-team-profile.json'
FLOW_PATH = 'collaboration-team-flow-report.json'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _request(runtime: OwnedBcsProcess, method: str, path: str, *, token: str,
              body: bytes | None = None) -> tuple[int, object]:
    target = urlsplit(runtime.inputs.origin)
    _require(target.scheme == 'http' and target.hostname == '127.0.0.1'
             and target.port and not target.path, 'collaboration-team origin is invalid')
    connection = HTTPConnection(target.hostname, target.port, timeout=5)
    try:
        headers = {'Accept': 'application/json', 'Authorization': 'Bearer '+token}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(65_537)
        _require(len(raw) <= 65_536, 'collaboration-team response exceeds budget')
        value = json.loads(raw.decode(), object_pairs_hook=unique_keys) if raw else None
        return response.status, value
    finally:
        connection.close()


def _admit_friendship(runtime: OwnedBcsProcess,
                      identities: OwnedLocalRoleIdentity,
                      caller_role: str, target_role: str) -> None:
    """Establish one reviewed prerequisite owned by this Run's Human."""
    target = identities.actor_id(target_role)
    body = json.dumps({'to_bot': target}).encode()
    status, value = _request(
        runtime, 'POST', '/friends/request',
        token=identities._entries[caller_role][1], body=body)
    _require(status in (200, 201) and isinstance(value, dict),
             caller_role+' prerequisite request to '+target_role+' differs')
    row = value.get('data') if isinstance(value.get('data'), dict) else None
    request_id = identifier(row.get('id') if row is not None else None,
                             'collaboration-team friend request ID')
    path = '/friends/requests/'+quote(request_id, safe='')+'/accept'
    status, value = _request(runtime, 'POST', path,
                             token=identities._entries[target_role][1],
                             body=b'{}')
    _require(status == 200 and isinstance(value, dict),
             target_role+' did not accept the '+caller_role+' prerequisite')


def _profile(runtime: OwnedBcsProcess, identities: OwnedLocalRoleIdentity) -> dict:
    value = {
        'schema_version': 1,
        'contract': 'dual-collaboration-team-cli-profile-v1',
        'environment': {'tier': 'local', 'id': 'local', 'expected_bcs_env': 'local'},
        'entrypoint': {'kind': 'singlebox', 'origin': runtime.inputs.origin,
                        'api_face': 'legacy', 'manifest_face': 'legacy'},
        'human': {'user_id': runtime.inputs.human_id},
        'actors': {},
        'auth': {
            'kind': 'owned-local-bot-tokens',
            'engineering_token_env': 'BCS_E2E_ENG_BOT_TOKEN',
            'customer_service_token_env': 'BCS_E2E_CS_BOT_TOKEN',
            'driver_token_env': 'BCS_E2E_PM_BOT_TOKEN',
            'qa_token_env': 'BCS_E2E_QA_BOT_TOKEN',
        },
        'relationships': {
            'visibility': 'owned-cs-protect-then-public-restore',
            'friendship': 'owned-eng-cs-request-then-reject',
            'group': 'two-owned-pm-qa-groups',
            'cleanup': 'none-runtime-or-operator-created',
            'retention': 'request-and-group-history',
        },
        'policy': {
            'fixture_pool': 'trusted-team', 'exclusive_use_confirmed': True,
            'timeout_seconds': 30, 'max_response_bytes': 65_536,
            'failure_retention_hours': 24,
        },
        'cli': {'transport': 'product-cli', 'command_family': 'collaboration-team',
                 'api_face': 'legacy'},
    }
    for logical, role in (('engineering', 'ENG'), ('customer_service', 'CS'),
                            ('driver', 'PM'), ('qa', 'QA')):
        value['actors'][logical] = {
            'bot_id': identities.actor_id(role),
            'owner_id': runtime.inputs.human_id,
            'dedicated': True,
            'visibility': 'public',
        }
    return validate_collaboration_team_profile(value)


def local_collaboration_team_run(*, binary: Path, binary_sha256: str,
                                 cli_binary: Path, cli_binary_sha256: str,
                                 state_dir: Path, run_id: str, confirm_run_id: str,
                                 allow_local_processes: bool, allow_fixture_write: bool,
                                 allow_write: bool) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Collaboration-team requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Collaboration-team Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Collaboration-team requires a fresh state directory; existing evidence is retained')
    source = executor_digest()
    contract = collaboration_team_source_binding()
    plan = collaboration_team_case_plan()
    legacy = reviewed_release_inventory()
    _require(contract['parent'] in {row['id'] for row in legacy},
             'Collaboration-team parent is missing from the original release denominator')
    binding = {'schema_version': 1, 'contract': BINDING, 'suite': SUITE,
               'case_id': CASE_ID, 'case_version': 1, 'run_id': run_id,
               'executor_digest': source, 'bcs_binary_sha256': binary_sha256,
               'cli_binary_sha256': cli_binary_sha256, 'store_messages': False,
               'source_binding': contract, 'selected_requirement_ids': list(REQUIREMENT_IDS),
               'adapter_methods': list(ADAPTER_METHODS), 'legacy_inventory': legacy,
               'runtime_dir': str(state_dir.resolve())}
    runtime = identities = None
    case = {}
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                     root=state_dir, run_id=run_id) as owned_runtime:
                runtime = owned_runtime
                write(runtime.root/'collaboration-team-binding.json', binding)
                fixtures = OwnedLocalFixtures(runtime, fixture_set='trusted-team')
                with OwnedLocalRoleIdentity(fixtures, roles=ROLES) as owned_identities:
                    identities = owned_identities
                    fixtures.apply(allow_write=True, role_identity=identities)
                    for role in ROLES:
                        identities.verify(role)
                    _admit_friendship(runtime, identities, 'PM', 'ENG')
                    _admit_friendship(runtime, identities, 'PM', 'QA')
                    profile = _profile(runtime, identities)
                    write(runtime.root/PROFILE_PATH, profile)
                    runner = CollaborationTeamRunner(
                        profile, binary=cli_binary, binary_sha256=cli_binary_sha256,
                        state_dir=runtime.root)
                    old_environ = dict(os.environ)
                    tokens = {profile['auth']['engineering_token_env']: identities._entries['ENG'][1],
                              profile['auth']['customer_service_token_env']: identities._entries['CS'][1],
                              profile['auth']['driver_token_env']: identities._entries['PM'][1],
                              profile['auth']['qa_token_env']: identities._entries['QA'][1]}
                    os.environ.update(tokens)
                    try:
                        execution = execute_collaboration_team_case(
                            RemoteCollaborationTeamAdapter(runner))
                    finally:
                        os.environ.clear()
                        os.environ.update(old_environ)
                    snapshot = execution.snapshot()
                    snapshot['schema_version'] = 1
                    snapshot.update(run_id=run_id, executor_digest=source,
                                    binding_contract=BINDING,
                                    profile_contract=profile['contract'])
                    case = snapshot
                    write(runtime.root/CASE_PATH, case)
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Collaboration-team owned runtime was not created')
        if not case and (runtime.root/CASE_PATH).is_file():
            saved = read(runtime.root/CASE_PATH)
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        shutdown = runtime._record()
        credentials = identities.summary() if identities else {'credential_memory': 'NOT_CAPTURED'}
        passed = (error is None and executor_digest() == source
                  and read(runtime.root/'collaboration-team-binding.json') == binding
                  and read(runtime.root/CASE_PATH) == case
                  and case.get('status') == 'PASS'
                  and case.get('completed_count') == case.get('total_count') == len(REQUIREMENT_IDS)
                  and case.get('error_type') is None
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
                  and credentials.get('credential_memory') == 'CLEARED')
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else 'LOCAL_COLLABORATION_TEAM_PASS' if passed else 'BLOCKED')
        result = {'schema_version': 1, 'status': status, 'run_id': run_id, 'case_id': CASE_ID,
                  'executor_digest': source, 'case': case, 'shutdown': shutdown,
                  'credentials': credentials, 'remote_validation': 'NOT_RUN',
                  'profile_contract': 'dual-collaboration-team-cli-profile-v1',
                  'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/FLOW_PATH, result)
        from .local_collaboration_team_reporting import publish_collaboration_team
        canonical = publish_collaboration_team(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
