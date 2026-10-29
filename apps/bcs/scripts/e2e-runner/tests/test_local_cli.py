"""Product CLI harness contracts; fake executables are not product coverage."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.ledger import Ledger
from bcs_e2e.local_cli import (
    LocalCliError,
    OwnedLocalSessionCli,
    _execute,
    create_custom_collaboration_group,
    run_custom_collaboration_trial,
    validate_collaboration_yaml,
)
from bcs_e2e.owned_bcs import file_sha256
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load, fingerprint

ROOT = Path(__file__).resolve().parents[1]


class LocalCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.state = self.root / 'state'
        self.profile = load(ROOT / 'profiles/local.mock.json')
        with patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools'), contextlib.redirect_stdout(io.StringIO()):
            prepare_run(self.profile, self.state, 'cli-harness')
        self.runtime = Mock(run_id='cli-harness')
        self.runtime.inputs.origin = 'http://127.0.0.1:45000'
        self.binary = self.root / 'fake-cli'
        self.binary.write_text('#!/bin/sh\nexit 0\n'); self.binary.chmod(0o700)
        self.auth = Mock()
        with patch('bcs_e2e.local_cli.OwnedLocalAuth', return_value=self.auth):
            self.cli = OwnedLocalSessionCli(self.runtime, self.profile, state_dir=self.state,
                case_id='PRE-02', binary=self.binary, binary_sha256=file_sha256(self.binary))
        with Ledger(self.state / 'run.sqlite', 'cli-harness', fingerprint(self.profile)) as ledger:
            self.group = ledger.intent('PRE-02', 1, 'group', 'request-g1')
            ledger.observed(self.group, 'g1', origin='created')
            self.session = ledger.intent('PRE-02', 1, 'session', 'request-s1', parent=self.group)
            ledger.observed(self.session, 's1', origin='created')
        self.response = {'id': 's1', 'session_id': 's1', 'group_id': 'g1',
                         'session_title': 'test-title', 'status': 'running', 'private': 'must-not-report'}
        self.yaml = self.state / 'custom-collaboration.yaml'
        self.yaml.write_text('name: Owned-local validation\nparticipants: 2\nnodes: 2\n')

    def permission_response(self):
        driver = self.profile['actors']['driver']['bot_id']
        return {'session_id': 's1', 'group_id': 'g1', 'caller_bot_id': driver,
                'group_owner_bot_id': driver, 'allowed': True, 'reason_code': 'allowed',
                'policy_version': 'session_state_machine_v1', 'group_strategy': 'chat',
                'message': 'private-server-payload'}

    def permission(self):
        return self.cli.read_permission('s1', 'g1', self.profile['actors']['driver']['bot_id'], 'test-token')

    def test_permission_uses_private_context_only_then_clears_credential(self):
        def execute(binary, environment, root, session_id, timeout, **kwargs):
            self.assertEqual(kwargs, {'permission': True})
            self.assertEqual(session_id, 's1')
            self.assertNotIn('BCN_BOT_TOKEN', environment)
            self.assertNotIn('test-token', json.dumps(environment))
            context = Path(environment['BOT_DATA_DIR'])/'.bcs/session.json'
            self.assertEqual(json.loads(context.read_text())['token'], 'test-token')
            self.assertEqual(context.stat().st_mode & 0o777, 0o600)
            return json.dumps(self.permission_response()).encode()
        with patch('bcs_e2e.local_cli._execute', side_effect=execute) as process:
            result = self.permission()
        process.assert_called_once()
        self.assertEqual(result['status'], 'LOCAL_CLI_AUTH_PASS')
        self.assertEqual(result['authentication'], 'OWNED_LOCAL_BOT')
        self.assertEqual(result['credential_cleanup'], 'CLEARED')
        self.assertNotIn('test-token', json.dumps(result))
        self.assertNotIn('private-server-payload', json.dumps(result))
        self.assertEqual(json.loads((Path(result['context_dir'])/'bot/.bcs/session.json').read_text())['token'], '')

    def test_permission_rejects_wrong_identity_resource_or_policy_and_clears_token(self):
        for field, value in (('caller_bot_id', 'other'), ('group_owner_bot_id', 'other'),
                             ('session_id', 'other'), ('group_id', 'other'), ('allowed', 1),
                             ('allowed', False), ('reason_code', 'denied'), ('policy_version', 'unknown'),
                             ('active_run_id', 'active-run')):
            with self.subTest(field=field, value=value), \
                 patch('bcs_e2e.local_cli._execute', return_value=json.dumps({**self.permission_response(), field: value}).encode()), \
                 self.assertRaises(LocalCliError):
                self.permission()

    def test_permission_denial_cancel_and_invalid_output_clear_token(self):
        for error in (LocalCliError('nonzero exit status'), KeyboardInterrupt(), ValueError('bad json')):
            with self.subTest(error=type(error).__name__), patch('bcs_e2e.local_cli._execute', side_effect=error), \
                 self.assertRaises((LocalCliError, KeyboardInterrupt)):
                self.permission()
        for path in self.state.glob('product-cli-*/bot/.bcs/session.json'):
            self.assertEqual(json.loads(path.read_text())['token'], '')

    def test_permission_missing_or_wrong_role_credential_never_spawns(self):
        for bot, value in (('other', 'test-token'), (self.profile['actors']['driver']['bot_id'], ''),
                           (self.profile['actors']['driver']['bot_id'], 'bad\nvalue')):
            with patch('bcs_e2e.local_cli._execute') as process, self.assertRaises(LocalCliError):
                self.cli.read_permission('s1', 'g1', bot, value)
            process.assert_not_called()
        self.assertFalse(list(self.state.glob('product-cli-*')))

    def test_permission_scrub_failure_is_unconfirmed_not_pass(self):
        from bcs_e2e.run_state import write
        calls = []
        def scrub_failure(path, value):
            calls.append(value)
            if len(calls) == 3:
                raise OSError('context clear failed')
            write(path, value)
        with patch('bcs_e2e.local_cli._execute', return_value=json.dumps(self.permission_response()).encode()), \
             patch('bcs_e2e.local_cli.write', side_effect=scrub_failure), self.assertRaises(OSError) as caught:
            self.permission()
        self.assertEqual(caught.exception.credential_cleanup, 'UNCONFIRMED')
        self.assertTrue(Path(caught.exception.credential_context).is_dir())

    def test_relative_or_symlink_state_is_rejected_before_auth_or_spawn(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.state, target_is_directory=True)
        for state in (Path('state'), alias):
            with patch('bcs_e2e.local_cli.OwnedLocalAuth') as auth, \
                 patch('bcs_e2e.local_cli._execute') as execute, self.assertRaisesRegex(LocalCliError, 'absolute regular'):
                OwnedLocalSessionCli(self.runtime, self.profile, state_dir=state,
                    case_id='PRE-02', binary=self.binary, binary_sha256=file_sha256(self.binary))
            auth.assert_not_called()
            execute.assert_not_called()
        self.assertFalse(list(self.state.glob('product-cli-*')))

    def test_private_context_and_allowlisted_environment_do_not_inherit_credentials(self):
        with patch.dict(os.environ, {'BCN_BOT_TOKEN': 'private-token', 'BCS_URL': 'https://wrong.test',
                                     'HTTP_PROXY': 'https://proxy.test', 'BCS_COOKIE': 'private-cookie'}), \
             patch('bcs_e2e.local_cli._execute', return_value=json.dumps(self.response).encode()) as execute:
            report = self.cli.read_session('s1', 'g1', 'test-title')
        args = execute.call_args.args
        environment, root = args[1], args[2]
        self.assertEqual(set(environment), {'HOME', 'PATH', 'BOT_DATA_DIR', 'TMPDIR', 'LANG', 'SERVER_ENV', 'tc_sdb_nenv'})
        self.assertNotIn('private', json.dumps(environment))
        self.assertEqual(environment['SERVER_ENV'], 'local')
        self.assertEqual(environment['tc_sdb_nenv'], 'production')
        context = Path(environment['BOT_DATA_DIR']) / '.bcs/session.json'
        self.assertEqual(json.loads(context.read_text()), {'token': '', 'api_base_url': self.runtime.inputs.origin})
        self.assertEqual(context.stat().st_mode & 0o777, 0o600)
        for directory in (root, root / 'home', root / 'tmp', root / 'bot', context.parent):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(report['status'], 'LOCAL_CLI_SLICE_PASS')
        self.assertEqual(report['authentication'], 'NONE')
        self.assertNotIn('must-not-report', json.dumps(report))
        self.assertEqual(self.auth.require_bound.call_count, 2)

    def test_foreign_borrowed_deleted_or_other_case_resources_never_spawn(self):
        for field, value in (('origin', 'borrowed'), ('state', 'deleted'), ('case_id', 'PRE-01'), ('attempt', 2), ('parent', None)):
            with Ledger(self.state / 'run.sqlite', 'cli-harness', fingerprint(self.profile)) as ledger:
                original = dict(ledger.db.execute('SELECT * FROM resource WHERE handle=?', (self.session,)).fetchone())
                with ledger.db:
                    ledger.db.execute(f'UPDATE resource SET {field}=? WHERE handle=?', (value, self.session))
            with patch('bcs_e2e.local_cli._execute') as execute, self.assertRaises(LocalCliError):
                self.cli.read_session('s1', 'g1', 'test-title')
            execute.assert_not_called()
            with Ledger(self.state / 'run.sqlite', 'cli-harness', fingerprint(self.profile)) as ledger:
                with ledger.db:
                    ledger.db.execute(f'UPDATE resource SET {field}=? WHERE handle=?', (original[field], self.session))
        with patch('bcs_e2e.local_cli._execute') as execute, self.assertRaises(LocalCliError):
            self.cli.read_session('foreign', 'g1', 'test-title')
        execute.assert_not_called()
        self.assertFalse(list(self.state.glob('product-cli-*')))

    def test_digest_drift_blocks_before_context_creation(self):
        self.binary.write_text('#!/bin/sh\nexit 1\n')
        with patch('bcs_e2e.local_cli._execute') as execute, self.assertRaises(LocalCliError):
            self.cli.read_session('s1', 'g1', 'test-title')
        execute.assert_not_called()
        self.assertFalse(list(self.state.glob('product-cli-*')))

    def test_runtime_loss_and_post_execution_ownership_drift_never_pass(self):
        self.auth.require_bound.side_effect = RuntimeError('owned runtime stopped')
        with patch('bcs_e2e.local_cli._execute') as execute, self.assertRaisesRegex(RuntimeError, 'runtime stopped'):
            self.cli.read_session('s1', 'g1', 'test-title')
        execute.assert_not_called()
        self.assertFalse(list(self.state.glob('product-cli-*')))
        self.auth.require_bound.side_effect = None
        def changed(*args):
            with Ledger(self.state / 'run.sqlite', 'cli-harness', fingerprint(self.profile)) as ledger:
                with ledger.db:
                    ledger.db.execute("UPDATE resource SET origin='borrowed' WHERE handle=?", (self.session,))
            return json.dumps(self.response).encode()
        with patch('bcs_e2e.local_cli._execute', side_effect=changed), self.assertRaises(LocalCliError):
            self.cli.read_session('s1', 'g1', 'test-title')

    def test_zero_exit_auth_error_wrong_binding_and_invalid_output_never_pass(self):
        for raw in (b'{"status":"auth_required"}', b'{}{}', b'not-json',
                    b'{"id":"s1","id":"s1"}',
                    *(json.dumps({**self.response, key: value}).encode() for key, value in
                      (('id', 'other'), ('group_id', 'other'), ('session_id', 'other'),
                       ('session_title', 'other'), ('status', 'closed')))):
            with self.subTest(raw=raw), patch('bcs_e2e.local_cli._execute', return_value=raw), self.assertRaises(LocalCliError):
                self.cli.read_session('s1', 'g1', 'test-title')

    def test_subprocess_secondary_oserror_keeps_cancellation_through_sanitization(self):
        def interrupted(*args):
            try:
                raise KeyboardInterrupt()
            except KeyboardInterrupt:
                raise OSError('private-secondary-error')
        with patch('bcs_e2e.local_cli._execute', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.cli.read_session('s1', 'g1', 'test-title')

    def test_subprocess_budget_and_timeout_reap_only_held_child(self):
        import subprocess
        spawn = subprocess.Popen
        for script, reason in (("print('x' * 100000)", 'budget'), ('import time; time.sleep(30)', 'deadline'),
                               ("import sys; print('private-stderr',file=sys.stderr); sys.exit(2)", 'nonzero')):
            executable = self.root / 'process-fixture'
            executable.write_text(f'#!{sys.executable}\n' + script + '\n'); executable.chmod(0o700)
            children = []
            def recorded(*args, **kwargs):
                child = spawn(*args, **kwargs); children.append(child); return child
            with patch('bcs_e2e.bounded_process.subprocess.Popen', side_effect=recorded), self.assertRaisesRegex(LocalCliError, reason) as caught:
                # Budget/nonzero cases must reach their intended fault even on a
                # busy host; the explicit deadline case retains its one-second limit.
                timeout = 1 if reason == 'deadline' else 10
                _execute(executable, {'PATH': '/usr/bin:/bin'}, self.root, 's1', timeout, output_limit=1024)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            self.assertNotIn('private', str(caught.exception))


    def test_custom_collaboration_validation_uses_product_command_and_clean_private_context(self):
        response = {'valid': True, 'errors': [],
                    'summary': {'participants': 2, 'nodes': 2},
                    'participants': [{'binding': 'planner'}, {'binding': 'writer'}]}
        observed = {}
        def product_capture(command, **kwargs):
            observed['command'] = command
            observed['environment'] = kwargs['environment']
            observed['root'] = kwargs['cwd']
            context = Path(kwargs['environment']['BOT_DATA_DIR']) / '.bcs/session.json'
            self.assertEqual(json.loads(context.read_text()),
                             {'token': '', 'api_base_url': self.runtime.inputs.origin})
            return Mock(returncode=0, stdout=json.dumps(response).encode())
        with patch.dict(os.environ, {'BCN_BOT_TOKEN': 'host-token', 'HTTP_PROXY': 'https://proxy.test'}), \
             patch('bcs_e2e.local_cli.capture', side_effect=product_capture):
            result = validate_collaboration_yaml(self.runtime, state_dir=self.state,
                case_id='LOCAL-CUSTOM-COLLAB-VALIDATE-01', binary=self.binary,
                binary_sha256=file_sha256(self.binary), yaml_path=self.yaml)
        self.assertEqual(observed['command'], [str(self.binary.resolve()), '--json', '--log-level', 'error',
                                                'collaboration', 'validate', str(self.yaml.resolve())])
        self.assertEqual(set(observed['environment']),
                         {'HOME', 'PATH', 'BOT_DATA_DIR', 'TMPDIR', 'LANG', 'SERVER_ENV', 'tc_sdb_nenv'})
        self.assertNotIn('host-token', json.dumps(observed['environment']))
        self.assertNotIn('proxy.test', json.dumps(observed['environment']))
        self.assertFalse(observed['root'].exists())
        self.assertEqual(result['status'], 'LOCAL_CUSTOM_COLLABORATION_VALIDATE_PASS')
        self.assertEqual(result['parent_requirement_id'], 'CUSTOM-COLLABORATION.validate-yaml')
        self.assertEqual(result['summary'], {'participants': 2, 'nodes': 2})
        self.assertEqual(result['context_cleanup'], 'REMOVED')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertFalse(result['grants_release_credit'])
        self.assertEqual(result['full_parent_coverage'], 'NOT_RUN')

    def test_custom_collaboration_create_group_uses_product_cli_and_clears_credential(self):
        driver = self.profile['actors']['driver']['bot_id']
        reviewer = self.profile['actors']['member']['bot_id']
        create_output = {
            'id': 'custom-participants-group', 'driver_bot': driver,
            'participants': [reviewer, driver],
            'chat_url': 'http://127.0.0.1:21000/groups/custom-participants-group',
            'session_id': 'custom-participants-group:initial',
            'group_kind': 'normal', 'created': True,
        }
        read_output = {
            'id': 'custom-participants-group', 'driver_bot': driver,
            'participants': [
                {'bot_uuid': driver, 'role': 'planner'},
                {'bot_uuid': reviewer, 'role': 'reviewer'},
            ],
            'group_strategy': 'state_machine', 'status': 'active',
            'secret_server_field': 'must-not-report',
        }
        commands = []

        def capture_command(command, **kwargs):
            commands.append(tuple(command))
            if tuple(command[4:6]) == ('collaboration', 'create'):
                raw = json.dumps(create_output).encode()
            elif tuple(command[4:6]) == ('get-group', '--id'):
                raw = json.dumps(read_output).encode()
            else:
                raise AssertionError(f'unexpected product command: {command!r}')
            return Mock(returncode=0, stdout=raw, stderr=b'')

        with patch('bcs_e2e.local_cli.capture', side_effect=capture_command) as process, \
             patch.dict(os.environ, {'BCN_BOT_TOKEN': 'host-token'}):
            result = create_custom_collaboration_group(
                self.runtime, state_dir=self.state,
                case_id='LOCAL-CUSTOM-COLLAB-CREATE-01', binary=self.binary,
                binary_sha256=file_sha256(self.binary), yaml_path=self.yaml,
                driver_id=driver, reviewer_id=reviewer, driver_token='local-driver-token')
        self.assertEqual(len(process.call_args_list), 2)
        self.assertEqual(commands[0], (
            str(self.binary.resolve()), '--json', '--log-level', 'error',
            'collaboration', 'create', str(self.yaml.resolve()), '--driver', driver,
            '--binding', f'planner={driver}', '--binding', f'reviewer={reviewer}',
            '--context', 'Review release readiness through a custom workflow',
            '--topic', 'CLI custom release workflow'))
        self.assertEqual(commands[1], (
            str(self.binary.resolve()), '--json', '--log-level', 'error',
            'get-group', '--id', 'custom-participants-group'))
        self.assertEqual(result['status'], 'LOCAL_CUSTOM_COLLABORATION_CREATE_GROUP_PASS')
        self.assertEqual(result['parent_requirement_id'], 'CUSTOM-COLLABORATION.create-persistent-group')
        self.assertEqual(result['group_id'], 'custom-participants-group')
        self.assertEqual(result['session_id'], 'custom-participants-group:initial')
        self.assertEqual(result['participants'], sorted([driver, reviewer]))
        self.assertEqual(result['group_strategy'], 'state_machine')
        self.assertEqual(result['context_cleanup'], 'REMOVED')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertFalse(result['grants_release_credit'])
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn('must-not-report', serialized)
        for path in self.state.glob('product-cli-*/bot/.bcs/session.json'):
            self.assertEqual(json.loads(path.read_text()), {'token': '', 'bot_uuid': '', 'api_base_url': ''})


    def test_custom_collaboration_create_group_rejects_missing_session(self):
        driver = self.profile['actors']['driver']['bot_id']
        reviewer = self.profile['actors']['member']['bot_id']
        response = {'id': 'custom-participants-group', 'driver_bot': driver,
                    'participants': [driver, reviewer], 'created': True}
        with patch('bcs_e2e.local_cli.capture',
                   return_value=Mock(returncode=0, stdout=json.dumps(response).encode())), \
             self.assertRaisesRegex(LocalCliError, 'initial Session ID'):
            create_custom_collaboration_group(
                self.runtime, state_dir=self.state,
                case_id='LOCAL-CUSTOM-COLLAB-CREATE-01', binary=self.binary,
                binary_sha256=file_sha256(self.binary), yaml_path=self.yaml,
                driver_id=driver, reviewer_id=reviewer, driver_token='local-driver-token')



    def trial_outputs(self):
        driver = self.profile['actors']['driver']['bot_id']
        reviewer = self.profile['actors']['member']['bot_id']
        human = 'human_1001'
        group_id = 'trial-group'
        session_id = group_id + ':00000001'
        create = (
            'Group created:\n'
            '  ID: trial-group\n'
            '  Driver: ' + driver + '\n'
            '  Participants: ' + driver + ',' + reviewer + '\n'
            '  Chat URL: http://127.0.0.1:45000/groups/trial-group\n'
            '  Session: ' + session_id + '\n'
        ).encode()
        member = {'session_id': session_id, 'participants': [
            {'bot_uuid': driver, 'role': 'driver'},
            {'actor_id': human, 'role': 'consultant'},
        ], 'private_membership_field': 'must-not-report'}
        permission = {'session_id': session_id, 'group_id': group_id,
                      'caller_bot_id': driver, 'group_owner_bot_id': driver,
                      'allowed': True, 'reason_code': 'allowed',
                      'policy_version': 'session_state_machine_v1',
                      'group_strategy': 'chat', 'message': 'private-server-payload'}
        run = {'run': {'run_id': 'run-trial', 'session_id': session_id,
                       'group_id': group_id, 'status': 'running'},
               'private_run_field': 'must-not-report'}
        graph = {'run': {'run_id': 'run-trial', 'session_id': session_id,
                         'group_id': group_id, 'status': 'running'},
                 'nodes': [{'node_id': 'plan', 'status': 'pending'},
                           {'node_id': 'review', 'status': 'pending'}],
                 'edges': []}
        history = [{'message': 'bcsPanel.StateMachineRunView',
                    'params': {'run_id': 'run-trial'},
                    'private_history_field': 'must-not-report'}]
        return {'driver': driver, 'reviewer': reviewer, 'human': human,
                'group_id': group_id, 'session_id': session_id, 'create': create,
                'member': json.dumps(member).encode(),
                'permission': json.dumps(permission).encode(),
                'run': json.dumps(run).encode(),
                'graph': json.dumps(graph).encode(),
                'pending': b'[]',
                'history': json.dumps(history).encode()}

    def trial_capture(self, output):
        def captured(command, **kwargs):
            tail = tuple(command[4:])
            if tail[0:1] == ('create-group',):
                raw = output['create']
            elif tail[0:2] == ('session', 'add-member'):
                raw = output['member']
            elif tail[0:2] == ('collaborate', 'permission'):
                raw = output['permission']
            elif tail[0:2] == ('collaborate', 'run'):
                raw = output['run']
            elif tail[0:2] == ('collaborate', 'query') and tail[-1] == '--graph':
                raw = output['graph']
            elif tail[0:2] == ('collaborate', 'query') and tail[-1] == '--pending':
                raw = output['pending']
            elif tail[0:2] == ('collaborate', 'respond'):
                return Mock(returncode=2, stdout=b'', stderr=b'not pending')
            elif tail[0:2] == ('session', 'messages'):
                raw = output['history']
            else:
                raise AssertionError(f'unexpected trial command: {command!r}')
            return Mock(returncode=0, stdout=raw, stderr=b'')
        return captured

    def trial_http(self, calls):
        def request(runtime, method, path, body, token, expected_status):
            calls.append((method, path, body, expected_status))
            self.assertEqual(runtime, self.runtime)
            self.assertEqual(token, 'local-driver-token')
            if method == 'POST':
                return {'run': {'run_id': 'run-trial', 'status': 'aborted'}}
            if method == 'GET':
                return {'session_id': 'trial-group:00000001',
                        'group_id': 'trial-group', 'status': 'running'}
            return {'deleted': True, 'id': 'trial-group'}
        return request

    def test_custom_collaboration_trial_uses_exact_cli_and_http_order_and_clears_credential(self):
        output = self.trial_outputs(); commands = []; http_calls = []
        with patch('bcs_e2e.local_cli.capture',
                   side_effect=self.trial_capture(output)) as process, \
             patch('bcs_e2e.local_cli._request_trial_json',
                   side_effect=self.trial_http(http_calls)) as http, \
             patch.dict(os.environ, {'BCN_BOT_TOKEN': 'host-token'}):
            result = run_custom_collaboration_trial(
                self.runtime, state_dir=self.state,
                case_id='LOCAL-CUSTOM-COLLAB-TRIAL-01', binary=self.binary,
                binary_sha256=file_sha256(self.binary), yaml_path=self.yaml,
                driver_id=output['driver'], reviewer_id=output['reviewer'],
                human_id=output['human'], driver_token='local-driver-token')
        self.assertEqual(len(process.call_args_list), 8)
        actual = [tuple(call.args[0][4:]) for call in process.call_args_list]
        driver, reviewer, human = (output['driver'], output['reviewer'], output['human'])
        self.assertEqual(actual[0], ('create-group', '--driver', driver,
                                     '--participants', driver + ',' + reviewer,
                                     '--context', 'Release coordination through bcs-cli',
                                     '--topic', 'CLI one-shot custom collaboration trial'))
        self.assertEqual(actual[1], ('session', 'add-member', output['session_id'],
                                     '--bot-uuid', human, '--role', 'consultant'))
        self.assertEqual(actual[2], ('collaborate', 'permission', '--session',
                                     output['session_id']))
        self.assertEqual(actual[3], ('collaborate', 'run', str(self.yaml.resolve()),
                                     '--session', output['session_id'],
                                     '--binding', 'planner=' + driver,
                                     '--binding', 'reviewer=' + reviewer,
                                     '--input', '{"question":"Review the release workflow before persisting it."}'))
        self.assertEqual(actual[4], ('collaborate', 'query', '--run', 'run-trial', '--graph'))
        self.assertEqual(actual[5], ('collaborate', 'query', '--run', 'run-trial', '--pending'))
        self.assertEqual(actual[6], ('collaborate', 'respond', '--run', 'run-trial',
                                     '--node', 'plan', '--content', 'invalid Human reply'))
        self.assertEqual(actual[7], ('session', 'messages', output['session_id'],
                                     '--view-bot', driver, '--limit', '50'))
        self.assertEqual([row[:2] for row in http_calls], [
            ('POST', '/state-machine-runs/run-trial/cancel'),
            ('GET', '/sessions/trial-group%3A00000001'),
            ('DELETE', '/groups/trial-group?bot_id=' + driver),
        ])
        self.assertEqual([row[3] for row in http_calls], [200, 200, 200])
        self.assertEqual(result['status'], 'LOCAL_CUSTOM_COLLABORATION_TRIAL_PASS')
        self.assertEqual(result['parent_requirement_id'],
                         'CUSTOM-COLLABORATION.trial-one-shot-lifecycle')
        self.assertEqual(result['group_id'], output['group_id'])
        self.assertEqual(result['session_id'], output['session_id'])
        self.assertEqual(result['run_id'], 'run-trial')
        self.assertTrue(result['graph_valid'])
        self.assertEqual(result['respond_guard'], 'REFUSED')
        self.assertEqual(result['group_cleanup'], 'DELETED')
        self.assertEqual(result['context_cleanup'], 'REMOVED')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertFalse(result['grants_release_credit'])
        self.assertEqual(result['full_parent_coverage'], 'NOT_RUN')
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn('local-driver-token', serialized)
        self.assertNotIn('host-token', serialized)
        self.assertNotIn('must-not-report', serialized)
        self.assertFalse(list(self.state.glob('product-cli-*/bot/.bcs/session.json')))
        self.assertFalse(list(self.state.glob('collaboration-team-cli-*')))

    def test_custom_collaboration_trial_failure_deletes_created_group_and_clears_context(self):
        output = self.trial_outputs(); output['member'] = b'{}'
        http_calls = []
        with patch('bcs_e2e.local_cli.capture',
                   side_effect=self.trial_capture(output)) as process, \
             patch('bcs_e2e.local_cli._request_trial_json',
                   side_effect=self.trial_http(http_calls)), \
             self.assertRaisesRegex(LocalCliError, 'membership output'):
            run_custom_collaboration_trial(
                self.runtime, state_dir=self.state,
                case_id='LOCAL-CUSTOM-COLLAB-TRIAL-01', binary=self.binary,
                binary_sha256=file_sha256(self.binary), yaml_path=self.yaml,
                driver_id=output['driver'], reviewer_id=output['reviewer'],
                human_id=output['human'], driver_token='local-driver-token')
        self.assertEqual(len(process.call_args_list), 2)
        self.assertEqual([row[:2] for row in http_calls],
                         [('DELETE', '/groups/trial-group?bot_id=' + output['driver'])])
        self.assertFalse(list(self.state.glob('product-cli-*/bot/.bcs/session.json')))
        for path in self.state.glob('product-cli-*/bot/.bcs/session.json'):
            self.assertEqual(json.loads(path.read_text())['token'], '')

    def test_custom_collaboration_validation_rejects_wrong_output(self):
        good = {'valid': True, 'summary': {'participants': 2, 'nodes': 2}}
        mutations = (
            {'valid': False, 'summary': good['summary']},
            {'valid': 1, 'summary': good['summary']},
            {'valid': True, 'summary': {'participants': 1, 'nodes': 2}},
            {'valid': True, 'summary': {'participants': 2, 'nodes': 1}},
            {'valid': True, 'summary': {'participants': '2', 'nodes': 2}},
            {'valid': True},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), \
                 patch('bcs_e2e.local_cli.capture',
                       return_value=Mock(returncode=0, stdout=json.dumps(mutation).encode())), \
                 self.assertRaisesRegex(LocalCliError, 'summary differs'):
                validate_collaboration_yaml(self.runtime, state_dir=self.state,
                    case_id='LOCAL-CUSTOM-COLLAB-VALIDATE-01', binary=self.binary,
                    binary_sha256=file_sha256(self.binary), yaml_path=self.yaml)
        for raw in (b'not-json', b'[]'):
            with self.subTest(raw=raw), \
                 patch('bcs_e2e.local_cli.capture', return_value=Mock(returncode=0, stdout=raw)), \
                 self.assertRaisesRegex(LocalCliError, 'one valid JSON'):
                validate_collaboration_yaml(self.runtime, state_dir=self.state,
                    case_id='LOCAL-CUSTOM-COLLAB-VALIDATE-01', binary=self.binary,
                    binary_sha256=file_sha256(self.binary), yaml_path=self.yaml)

    def test_custom_collaboration_input_binding_and_admission_precede_product_spawn(self):
        outside = self.root / 'outside.yaml'
        outside.write_text('outside\n')
        linked = self.state / 'linked.yaml'
        linked.symlink_to(outside)
        oversized = self.state / 'oversized.yaml'
        oversized.write_bytes(b'x' * 1_048_577)
        foreign_runtime = Mock(run_id='foreign-run', inputs=self.runtime.inputs)
        cases = (
            (('state_dir', self.state / 'missing'), {'state_dir': self.state / 'missing'}),
            (('timeout', 0), {'timeout_seconds': 0}),
            (('outside-yaml', outside), {'yaml_path': outside}),
            (('symlink-yaml', linked), {'yaml_path': linked}),
            (('oversized-yaml', oversized), {'yaml_path': oversized}),
            (('foreign-runtime', foreign_runtime), {'runtime': foreign_runtime}),
        )
        for (_label, invalid), overrides in cases:
            values = {'state_dir': self.state, 'runtime': self.runtime,
                      'case_id': 'LOCAL-CUSTOM-COLLAB-VALIDATE-01', 'binary': self.binary,
                      'binary_sha256': file_sha256(self.binary), 'yaml_path': self.yaml,
                      'timeout_seconds': 10}
            values.update(overrides)
            with self.subTest(label=_label), patch('bcs_e2e.local_cli.capture') as capture, \
                 self.assertRaises(LocalCliError):
                validate_collaboration_yaml(**values)
            capture.assert_not_called()

    def test_interrupt_reaps_child_and_context_remains_for_inspection(self):
        import subprocess
        executable = self.root / 'process-fixture'
        executable.write_text(f'#!{sys.executable}\nimport time; time.sleep(30)\n'); executable.chmod(0o700)
        spawn = subprocess.Popen; children = []
        def recorded(*args, **kwargs):
            child = spawn(*args, **kwargs); children.append(child); return child
        with patch('bcs_e2e.bounded_process.subprocess.Popen', side_effect=recorded), \
             patch('bcs_e2e.bounded_process.selectors.DefaultSelector.select', side_effect=KeyboardInterrupt()), \
             self.assertRaises(KeyboardInterrupt):
            _execute(executable, {'PATH': '/usr/bin:/bin'}, self.root, 's1', 10)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(self.root.is_dir())
