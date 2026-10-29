"""Unit-only lifecycle and offline publication tests for Session Service."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.local_session_service_parent_case import PARENT_CASE_ID
from bcs_e2e.local_session_service_parent_reporting import (
    build_session_service_parent_report,
)
from bcs_e2e.local_session_service_parent_run import (
    _ParentCliRunner,
    _ensure_public_actor_visibility,
    _SequentialProfile,
    _profile_values,
    local_session_service_parent_run,
)
from bcs_e2e.local_operator_cli_contract import (
    SESSIONS_SERVICES_PARENT,
    SESSIONS_SERVICES_REQUIREMENTS,
)
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.session_cli_profile import (
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_lifecycle_profile,
)
from bcs_e2e.workspace_cli_profile import (
    SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
    SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
)

METHODS = (
    'execute_local_session_service_lifecycle_case',
    'execute_local_session_service_member_chat_case',
    'execute_local_session_service_complete_case',
    'execute_local_session_service_roundtrip_case',
)
FIXTURE_EFFECTS = (
    'OWNED_SESSION_LIFECYCLE_THEN_GENERIC_GROUP_DELETE',
    'OWNED_SESSION_MEMBER_CHAT_THEN_GENERIC_GROUP_DELETE',
    'OWNED_SESSION_COMPLETION_THEN_GENERIC_GROUP_DELETE',
    'OWNED_SESSION_COMPLETION_AND_INVOCATION_THEN_CREATOR_SCOPED_CLEANUP',
)
DRIVER = 'driver-bot'
MEMBER = 'member-bot'
DRIVER_TOKEN = 'driver-secret-token'
MEMBER_TOKEN = 'member-secret-token'


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n',
                    encoding='utf-8')


def step(index: int, status: str = 'PASS') -> dict:
    effect = FIXTURE_EFFECTS[index]
    data_keys = (
        ('group_id', 'session_id', 'session_created', 'session_listed',
         'session_read', 'session_renamed', 'group_deleted',
         'group_absence_verified'),
        ('group_id', 'session_id', 'member_admitted', 'member_muted',
         'chat_delivered', 'history_observable', 'invite_minted',
         'member_removed', 'group_deleted', 'group_absence_verified'),
        ('group_id', 'session_id', 'session_completed',
         'completion_output_preserved', 'group_deleted',
         'group_absence_verified'),
        ('group_id', 'session_id', 'service_session_id',
         'service_identity_preserved', 'bounded_wait_contract_exact',
         'chat_session_deleted', 'service_session_deleted', 'group_deleted',
         'group_absence_verified'),
    )[index]
    data = {'group_id': f'group-{index + 1}',
            'session_id': f'group-{index + 1}:session',
            **{key: True for key in data_keys[2:]}}
    if index == 3:
        data['service_session_id'] = f'group-{index + 1}:service'
    evidence = {} if status != 'PASS' else {
        'requests': [{
            'operation': f'operation-{index}',
            'case_class': f'class-{index}',
            'transport': 'PRODUCT_CLI',
            'credential_kind': 'owned_local_bot_token',
            'fixture_effect': effect,
            'exit_status': 0,
            'command_arguments': ['session'],
            'http_method_declared': 'POST',
            'route_declared': '/groups',
            'remote_validation': 'NOT_RUN',
            'grants_release_credit': False,
        }],
        'fixture_effect': effect,
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }
    return {
        'requirement_id': SESSIONS_SERVICES_REQUIREMENTS[index],
        'method': METHODS[index],
        'status': status,
        'data': data if status == 'PASS' else None,
        'evidence': evidence,
        'error_type': None if status == 'PASS' else 'RunStateError',
    }


def execution(completed: int = 4) -> CaseExecution:
    rows = []
    for index in range(4):
        rows.append(StepOutcome(
            requirement_id=SESSIONS_SERVICES_REQUIREMENTS[index],
            method=METHODS[index],
            status='PASS' if index < completed else 'NOT_RUN',
            data=None if index >= completed else (
                step(index)['data']
            ),
            evidence={} if index >= completed else (
                step(index)['evidence']
            ),
            error_type=None if index < completed else 'RunStateError',
        ))
    value = CaseExecution(
        case_id=PARENT_CASE_ID,
        parent_id=SESSIONS_SERVICES_PARENT,
        status='PASS' if completed == 4 else 'BLOCKED',
        steps=tuple(rows),
        completed_count=completed,
        total_count=4,
        error_type=None if completed == 4 else 'RunStateError',
        ready_to_execute=completed == 4,
    )
    value.validate()
    return value


def case(completed: int = 4) -> dict:
    value = execution(completed).snapshot()
    value.update(
        schema_version=1,
        run_id='session-service-run',
        executor_digest='0' * 64,
        binding_contract='owned-local-session-service-parent-binding-v1',
        validation_profile_contract=SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
        execution_profile_contract=SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
    )
    return value


class FakeRuntime:
    def __init__(self, *, binary, binary_sha256, root, run_id,
                 store_messages=False):
        self.kwargs = {'binary': binary, 'binary_sha256': binary_sha256,
                       'root': root, 'run_id': run_id,
                       'store_messages': store_messages}
        Path(root).mkdir(parents=True, exist_ok=True)
        self.root = Path(root)
        self.run_id = run_id
        self.inputs = SimpleNamespace(
            origin='http://127.0.0.1:21000', human_id='human-1')
        write(self.root / 'bcs-config.json', {
            'schema_version': 1, 'run_id': run_id,
            'origin': self.inputs.origin, 'store_messages': False,
        })

    def _record(self):
        value = {
            'schema_version': 1, 'run_id': self.run_id,
            'status': 'STOPPED', 'origin': self.inputs.origin,
            'pid': 1, 'returncode': 0,
            'binary_sha256': self.kwargs['binary_sha256'],
            'ready': False, 'forced_stop': False,
            'ownership': 'live-child-handle-only; saved PID never authorizes stop',
        }
        write(self.root / 'owned-bcs.json', value)
        return value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeIdentity:
    def __init__(self, fixtures, *, roles):
        self._roles = tuple(roles)
        self.actors = {'PM': DRIVER, 'ENG': MEMBER}
        self._entries = {'PM': (DRIVER, DRIVER_TOKEN),
                         'ENG': (MEMBER, MEMBER_TOKEN)}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def apply(self, *_args, **_kwargs):
        write(self.fixtures.root / 'local-fixtures.json', {
            'schema_version': 1, 'status': 'APPLIED',
            'run_id': self.fixtures.runtime.run_id,
            'fixture_set': 'cli-file',
            'runtime_origin': self.fixtures.runtime.inputs.origin,
            'human': {'user_id': 'human-1'},
            'roles': {role: {'bot_id': self.actors[role]}
                      for role in self._roles},
        })

    def verify(self, _role):
        return {'status': 'VERIFIED'}

    def actor_id(self, role):
        return self.actors[role]

    def summary(self):
        return {
            'state': 'CLOSED',
            'selected_roles': list(self._roles),
            'verified_roles': list(self._roles),
            'credential_memory': 'CLEARED',
            'memory_cleanup_scope': 'HOLDER_REFERENCES_ONLY',
            'secure_erasure': False,
            'remote_authentication': 'NOT_TESTED',
        }


class FakeFixtures:
    def __init__(self, runtime, *, fixture_set='cli-file'):
        self.runtime = runtime
        self.root = runtime.root
        self.fixture_set = fixture_set
        self.roles = ('PM', 'ENG')

    def apply(self, **kwargs):
        role_identity = kwargs['role_identity']
        role_identity.fixtures = self
        role_identity.apply()


def arguments(root: Path, **overrides):
    values = dict(
        binary=Path('/unused-bcs'), binary_sha256='b' * 64,
        cli_binary=Path('/unused-cli'), cli_binary_sha256='c' * 64,
        state_dir=root, run_id='session-service-run',
        confirm_run_id='session-service-run',
        allow_local_processes=True, allow_fixture_write=True,
        allow_write=True,
    )
    values.update(overrides)
    return values


class SessionServiceProfileTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(
            run_id='session-service-run',
            inputs=SimpleNamespace(
                origin='http://127.0.0.1:21000', human_id='human-1'),
            root=Path('/unused'),
        )
        self.identity = SimpleNamespace(
            actor_id=lambda role: DRIVER if role == 'PM' else MEMBER)

    def test_profile_values_validate_both_child_contracts(self):
        lifecycle, complete = _profile_values(self.runtime, self.identity)
        self.assertEqual(lifecycle['contract'],
                         SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT)
        self.assertEqual(complete['contract'],
                         SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT)
        self.assertEqual(lifecycle['actors']['driver']['bot_id'], DRIVER)
        self.assertEqual(complete['actors']['member']['bot_id'], MEMBER)
        self.assertEqual(sorted(lifecycle['route_bindings']), sorted([
            'session_cli.session.patch_title',
            'session_cli.session.member.add',
            'session_cli.session.member.patch_mode',
            'session_cli.session.chat',
            'session_cli.session.messages',
            'session_cli.session.member.remove',
            'session_cli.session.delete',
            'session_cli.session.group_listing',
            'session_cli.group.delete',
            'session_cli.group.verify_deleted',
        ]))

    def test_sequential_profile_supplies_four_exact_validator_contexts(self):
        lifecycle, complete = _profile_values(self.runtime, self.identity)
        sequential = _SequentialProfile(lifecycle, complete)
        first = validate_session_cli_lifecycle_profile(sequential)
        second = validate_session_cli_lifecycle_profile(sequential)
        third = validate_session_cli_complete_invoke_profile(sequential)
        fourth = validate_session_cli_complete_invoke_profile(sequential)
        self.assertEqual([
            first['contract'], second['contract'],
            third['contract'], fourth['contract'],
        ], [SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
            SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
            SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
            SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT])
        for value in (first, second, third, fourth):
            self.assertNotIsInstance(value, _SequentialProfile)

    def test_parent_runner_resets_exactly_one_child_scope_per_execute(self):
        class Running:
            def __init__(self):
                self.profile = {'contract': 'runner-profile'}
                self.executed = ['previous']
                self.calls = []

            def execute(self, *args, **kwargs):
                self.calls.append((tuple(args), kwargs, list(self.executed)))
                self.executed.append(args[0])
                return {'data': {}, 'evidence': {}}

        running = Running()
        with patch('bcs_e2e.local_session_service_parent_run.'
                   'WorkspaceCliRunner', return_value=running):
            runner = _ParentCliRunner(
                {'contract': 'runner-profile'}, binary=Path('/unused-cli'),
                binary_sha256='c' * 64, state_dir=Path('/unused-state'))
        result = runner.execute('session-cli.group.create', group_id='g')
        self.assertEqual(result, {'data': {}, 'evidence': {}})
        self.assertEqual(running.calls[0][2], [])
        self.assertEqual(running.executed, ['session-cli.group.create'])

    def test_public_actor_visibility_admission_updates_and_reads_exact_state(self):
        class Response:
            def __init__(self, status, body):
                self.status = status
                self._body = body

            def read(self, _limit):
                return self._body

        class Connection:
            def __init__(self, *_args, **_kwargs):
                self.requests = []

            def request(self, method, path, body=b'', headers=None):
                self.requests.append((method, path, body, dict(headers or {})))

            def getresponse(self):
                if len(self.requests) == 1:
                    return Response(200, json.dumps({
                        'success': True,
                        'data': {'bot_uuid': DRIVER, 'visibility': 'public'},
                    }).encode())
                return Response(200, json.dumps({
                    'data': {'bot_uuid': DRIVER, 'visibility': 'public'},
                }).encode())

            def close(self):
                pass

            def connect(self):
                pass

        runtime = SimpleNamespace(inputs=SimpleNamespace(
            origin='http://127.0.0.1:21000'))
        identity = SimpleNamespace(
            actor_id=lambda _role: DRIVER, _entries={'PM': (DRIVER, DRIVER_TOKEN)})
        connection = Connection()
        with patch('bcs_e2e.local_session_service_parent_run.HTTPConnection',
                   return_value=connection) as constructor:
            _ensure_public_actor_visibility(runtime, identity, 'PM')
        constructor.assert_called_once_with('127.0.0.1', 21000, timeout=5)
        first = connection.requests[0]
        self.assertEqual(first[:3], (
            'PUT', '/bots/' + DRIVER + '/visibility',
            json.dumps({'visibility': 'public'}, separators=(',', ':')).encode(),
        ))
        second = connection.requests[1]
        self.assertEqual(second[0], 'GET')
        self.assertEqual(second[2], b'')


class SessionServiceRunTests(unittest.TestCase):
    def test_permissions_confirmation_and_directory_precede_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('bcs_e2e.local_session_service_parent_run.'
                       'OwnedBcsProcess') as process:
                for key, value in (
                        ('allow_local_processes', False),
                        ('allow_fixture_write', False),
                        ('allow_write', False),
                        ('confirm_run_id', 'foreign')):
                    with self.subTest(key=key), self.assertRaises(RunStateError):
                        local_session_service_parent_run(
                            **arguments(Path(tmp) / key, **{key: value}))
                    process.assert_not_called()
                existing = Path(tmp) / 'run'
                existing.mkdir()
                with self.assertRaises(RunStateError):
                    local_session_service_parent_run(**arguments(existing))
                process.assert_not_called()

    def run_parent(self, completed: int = 4, parent_override=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'run'
        captured = {}
        parent = parent_override if parent_override is not None else execution(completed)

        def fake_parent(profile, runner):
            captured['profile_current'] = profile.current()
            captured['runner'] = runner
            return parent

        environment = {'PATH': os.environ.get('PATH', '')}
        with patch.dict(os.environ, environment, clear=True), \
             patch('bcs_e2e.local_session_service_parent_run.OwnedBcsProcess',
                   FakeRuntime), \
             patch('bcs_e2e.local_session_service_parent_run.OwnedLocalFixtures',
                   FakeFixtures), \
             patch('bcs_e2e.local_session_service_parent_run.'
                   'OwnedLocalRoleIdentity', FakeIdentity), \
             patch('bcs_e2e.local_session_service_parent_run.'
                   '_ensure_public_actor_visibility') as visibility, \
             patch('bcs_e2e.local_session_service_parent_run.'
                   'execute_local_session_service_parent_case',
                   side_effect=fake_parent), \
             patch('bcs_e2e.local_session_service_parent_run.'
                   'WorkspaceCliRunner') as runner:
            visibility.side_effect = (
                lambda runtime, identities, role:
                captured.setdefault('visibility_calls', []).append(role)
            )
            runner.return_value.profile = case().get(
                'execution_profile_contract')
            runner.return_value.executed = []
            result = local_session_service_parent_run(**arguments(root))
        self.assertFalse(os.environ.get(
            'BCS_E2E_SESSION_SERVICE_DRIVER_TOKEN'))
        self.assertFalse(os.environ.get(
            'BCS_E2E_SESSION_SERVICE_MEMBER_TOKEN'))
        return result, root, captured

    def test_success_owns_reports_without_major_process_and_without_tokens(self):
        result, root, captured = self.run_parent(4)
        self.assertEqual(
            result['status'], 'LOCAL_SESSION_SERVICE_PARENT_PASS')
        self.assertEqual(result['canonical_summary']['status'],
                         'LOCAL_SUITE_PASS')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(captured['runner'].__class__, _ParentCliRunner)
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(DRIVER_TOKEN, serialized)
        self.assertNotIn(MEMBER_TOKEN, serialized)
        for name in ('reports/summary.json', 'reports/junit.xml',
                     'reports/summary.md'):
            self.assertTrue((root / name).is_file())
        offline = build_session_service_parent_report(root)
        self.assertEqual(offline, result['canonical_summary'])

    def test_bounded_service_wait_timeout_evidence_projects_full_pass(self):
        parent = execution(4)
        request = parent.steps[3].evidence['requests'][0]
        request['operation'] = 'session-cli.service.wait'
        request['case_class'] = 'session-cli.service.wait'
        request['exit_status'] = 1
        parent.steps[3].evidence['requests'].append({
            'operation': 'session_cli.group.delete',
            'case_class': 'cleanup-delete',
            'transport': 'PRODUCT_HTTP',
            'credential_kind': 'owned_local_bot_token',
            'fixture_effect': FIXTURE_EFFECTS[3],
            'http_status': 200,
            'method': 'DELETE',
            'path': '/groups/group-4',
            'remote_validation': 'NOT_RUN',
            'grants_release_credit': False,
        })
        result, _root, _captured = self.run_parent(4, parent_override=parent)
        self.assertEqual(result['status'],
                         'LOCAL_SESSION_SERVICE_PARENT_PASS')
        self.assertEqual(result['canonical_summary']['status'],
                         'LOCAL_SUITE_PASS')
        self.assertEqual(result['canonical_summary']['functional'], 'PASS')

    def test_child_failure_cannot_be_projected_as_parent_pass(self):
        result, root, _captured = self.run_parent(1)
        self.assertEqual(result['status'], 'BLOCKED')
        summary = result['canonical_summary']
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertEqual(summary['functional'], 'INCOMPLETE')
        self.assertEqual(summary['requirements'], [
            {'id': name, 'status': 'PASS' if index == 0 else 'NOT_RUN'}
            for index, name in enumerate(SESSIONS_SERVICES_REQUIREMENTS)
        ])
        self.assertEqual(
            build_session_service_parent_report(root),
            result['canonical_summary'])

    def test_source_corruption_blocks_offline_projection(self):
        result, root, _captured = self.run_parent(4)
        path = root / 'local-session-service-parent.json'
        original = path.read_bytes()
        try:
            path.write_bytes(original + b' ')
            self.assertEqual(
                build_session_service_parent_report(root)['status'],
                'BLOCKED')
        finally:
            path.write_bytes(original)
        self.assertEqual(build_session_service_parent_report(root),
                         result['canonical_summary'])

        pending = root / 'session-service-parent-publication-pending.json'
        write(pending, {'schema_version': 1, 'run_id': 'foreign'})
        try:
            self.assertEqual(
                build_session_service_parent_report(root)['status'],
                'BLOCKED')
        finally:
            pending.unlink()

    def test_offline_projection_makes_no_network_request_or_file_change(self):
        result, root, _captured = self.run_parent(4)
        before = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()
        }
        with patch('urllib.request.OpenerDirector.open',
                   side_effect=AssertionError('offline report called a service')):
            offline = build_session_service_parent_report(root)
        after = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(offline, result['canonical_summary'])

    def test_kept_fixture_role_and_profile_bindings_must_be_exact(self):
        result, root, _captured = self.run_parent(4)
        fixtures = read(root / 'local-fixtures.json')
        self.assertEqual(fixtures['fixture_set'], 'cli-file')
        self.assertEqual(
            sorted(fixtures['roles']), ['ENG', 'PM'])
        binding = read(root / 'session-service-parent-binding.json')
        self.assertEqual(binding['fixture_roles'], ['PM', 'ENG'])
        self.assertEqual(binding['validation_profile_contract'],
                         SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT)
        self.assertEqual(binding['execution_profile_contract'],
                         SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT)
        self.assertEqual(result['credentials']['credential_memory'],
                         'CLEARED')


if __name__ == '__main__':
    unittest.main()
