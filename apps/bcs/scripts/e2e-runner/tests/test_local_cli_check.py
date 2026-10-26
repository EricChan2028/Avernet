"""Selection/report gates for the optional local product CLI check."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.local_cli import LocalCliError
from bcs_e2e.local_cli_check import LocalCliCheck
from bcs_e2e.local_run import local_run
from bcs_e2e.owned_bcs import file_sha256
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read


class LocalCliCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'fake-cli'
        self.binary.write_text('#!/bin/sh\nexit 0\n'); self.binary.chmod(0o700)
        self.digest = file_sha256(self.binary)
        self.check = LocalCliCheck(self.binary, self.digest)
        self.check.capture_driver_token('bot-driver', 'test-token')
        self.context = self.root / ('product-cli-' + 'a' * 32)
        self.context.mkdir()
        self.result = {'source': 'product-bcs-cli', 'api_face': 'legacy',
                       'context_dir': str(self.context), 'elapsed_ms': 1.0, 'status': 'LOCAL_CLI_SLICE_PASS', 'operation': 'cli.session.get',
                       'binary_sha256': self.digest, 'session_id': 's1', 'group_id': 'g1',
                       'authentication': 'NONE', 'full_cli_coverage': 'NOT_QUALIFIED',
                       'remote_authentication': 'NOT_TESTED'}

        self.permission = {'status': 'LOCAL_CLI_AUTH_PASS', 'operation': 'cli.collaborate.permission',
            'source': 'product-bcs-cli', 'api_face': 'legacy', 'authentication': 'OWNED_LOCAL_BOT',
            'credential_cleanup': 'CLEARED', 'binary_sha256': self.digest, 'caller_bot_id': 'bot-driver',
            'group_owner_bot_id': 'bot-driver', 'session_id': 's1', 'group_id': 'g1', 'allowed': True,
            'reason_code': 'allowed', 'group_strategy': 'chat', 'policy_version': 'session_state_machine_v1',
            'remote_authentication': 'NOT_TESTED', 'full_cli_coverage': 'NOT_QUALIFIED'}

    def observe(self):
        self.check.observe(Mock(), {}, self.root, 's1', 'g1', 'title')

    def test_one_success_is_required_and_repeated_check_never_executes_twice(self):
        self.assertFalse(self.check.passed)
        with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli') as cli:
            cli.return_value.read_permission.return_value = self.permission
            cli.return_value.read_session.return_value = self.result
            self.observe()
            self.assertTrue(self.check.passed)
            self.assertEqual(read(self.root / 'product-cli-check.json'), self.check.result)
            self.observe()
            self.assertEqual(cli.call_count, 1)
        self.assertFalse(self.check.passed)
        self.assertEqual(self.check.result['error_type'], 'RepeatedCliCheck')

    def test_permission_failure_is_required_and_credentials_are_forgotten(self):
        with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli') as cli:
            cli.return_value.read_session.return_value = self.result
            cli.return_value.read_permission.side_effect = LocalCliError('private-detail')
            self.observe()
        self.assertFalse(self.check.passed)
        self.assertEqual(self.check.result['status'], 'BLOCKED')
        self.assertEqual(self.check._driver_token, '')
        self.assertNotIn('private-detail', json.dumps(self.check.result))
        self.assertNotIn('test-token', json.dumps(self.check.result))
        cli.return_value.read_permission.assert_called_once_with('s1', 'g1', 'bot-driver', 'test-token')

    def test_permission_projection_cannot_override_report_or_return_credential(self):
        with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli') as cli:
            cli.return_value.read_session.return_value = self.result
            cli.return_value.read_permission.return_value = {**self.permission, 'echo': 'test-token', 'attempts': 0}
            self.observe()
        self.assertTrue(self.check.passed)
        self.assertEqual(self.check.result['attempts'], 1)
        self.assertNotIn('test-token', json.dumps(self.check.result))
        self.assertEqual(self.check._driver_token, '')

    def test_unconfirmed_credential_scrub_remains_visible_with_cancellation(self):
        process_failure = LocalCliError('process deadline')
        interrupted = KeyboardInterrupt()
        process_failure.child_shutdown_status = 'UNCONFIRMED'
        interrupted.child_shutdown_status = 'UNCONFIRMED'
        for primary in (None, process_failure, interrupted):
            check = LocalCliCheck(self.binary, self.digest)
            check.capture_driver_token('bot-driver', 'test-token')
            def failed(*args):
                try:
                    if primary is not None:
                        raise primary
                finally:
                    error = OSError('scrub-failure')
                    error.credential_cleanup = 'UNCONFIRMED'
                    error.credential_context = str(self.context)
                    raise error
            with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli') as cli:
                cli.return_value.read_session.return_value = self.result
                cli.return_value.read_permission.side_effect = failed
                if not isinstance(primary, KeyboardInterrupt):
                    check.observe(Mock(), {}, self.root, 's1', 'g1', 'title')
                else:
                    with self.assertRaises(KeyboardInterrupt):
                        check.observe(Mock(), {}, self.root, 's1', 'g1', 'title')
            self.assertFalse(check.passed)
            self.assertEqual(check.result['credential_cleanup'], 'UNCONFIRMED')
            self.assertEqual(check.result['credential_context'], str(self.context))
            self.assertEqual(check._driver_token, '')
            self.assertEqual(check.result['status'], 'CANCELLED' if isinstance(primary, KeyboardInterrupt) else 'BLOCKED')
            if primary is not None:
                self.assertEqual(check.result['child_shutdown_status'], 'UNCONFIRMED')

    def test_malformed_result_cannot_supply_success_or_override_attempt_count(self):
        with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli') as cli:
            cli.return_value.read_session.return_value = {'status': 'LOCAL_CLI_SLICE_PASS', 'attempts': 0}
            self.observe()
        self.assertFalse(self.check.passed)
        self.assertEqual(self.check.result['attempts'], 1)
        self.assertEqual(self.check.result['error_type'], 'LocalCliError')

    def test_missing_or_invalid_provenance_context_and_timing_cannot_pass(self):
        for key, value in (('source', None), ('source', 'local-semantic'), ('api_face', 'public'),
                           ('context_dir', 'relative'), ('context_dir', str(self.root)),
                           ('elapsed_ms', float('nan')), ('elapsed_ms', True), ('elapsed_ms', -1)):
            check = LocalCliCheck(self.binary, self.digest)
            check.capture_driver_token('bot-driver', 'test-token')
            with self.subTest(key=key, value=value), patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli') as cli:
                cli.return_value.read_session.return_value = {**self.result, key: value}
                check.observe(Mock(), {}, self.root, 's1', 'g1', 'title')
            self.assertFalse(check.passed)
            self.assertEqual(check.result['error_type'], 'LocalCliError')

    def test_secondary_exception_cannot_turn_cancellation_into_an_ordinary_failure(self):
        def interrupted(*args, **kwargs):
            try:
                raise KeyboardInterrupt()
            except KeyboardInterrupt:
                raise OSError('private-secondary-error')
        with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.observe()
        self.assertEqual(read(self.root / 'product-cli-check.json')['status'], 'CANCELLED')
        self.assertEqual(self.check.result['secondary_error_type'], 'OSError')
        self.assertNotIn('private-secondary-error', json.dumps(self.check.result))

    def test_failure_is_separate_finite_and_never_retried(self):
        with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli', side_effect=LocalCliError('private-output')) as cli:
            self.observe()
        self.assertEqual(cli.call_count, 1)
        self.assertEqual(self.check.result['status'], 'BLOCKED')
        self.assertNotIn('private-output', json.dumps(self.check.result))
        self.assertFalse(self.check.passed)
        self.assertEqual(read(self.root / 'product-cli-check.json'), self.check.result)

    def test_unconfirmed_child_shutdown_remains_visible_on_failure_and_cancel(self):
        for error in (LocalCliError('deadline'), KeyboardInterrupt()):
            error.child_shutdown_status = 'UNCONFIRMED'
            check = LocalCliCheck(self.binary, self.digest)
            check.capture_driver_token('bot-driver', 'test-token')
            with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli', side_effect=error):
                if isinstance(error, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        check.observe(Mock(), {}, self.root, 's1', 'g1', 'title')
                else:
                    check.observe(Mock(), {}, self.root, 's1', 'g1', 'title')
            self.assertEqual(check.result['child_shutdown_status'], 'UNCONFIRMED')
            self.assertFalse(check.passed)

    def test_cancellation_is_checkpointed_and_propagated(self):
        with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli', side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.observe()
        self.assertEqual(read(self.root / 'product-cli-check.json')['status'], 'CANCELLED')
        self.assertFalse(self.check.passed)

    def test_failed_checkpoint_blocks_qualification_without_hiding_check_outcome(self):
        from bcs_e2e.run_state import write
        calls = []
        def persist(path, result):
            calls.append(result['status'])
            if len(calls) == 2: raise OSError('private-path')
            write(path, result)
        with patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli') as cli, \
             patch('bcs_e2e.local_cli_check.write', side_effect=persist):
            cli.return_value.read_permission.return_value = self.permission
            cli.return_value.read_session.return_value = self.result
            self.observe()
        self.assertEqual(self.check.result['status'], 'LOCAL_CLI_SLICE_PASS')
        self.assertEqual(self.check.result['persistence_error_type'], 'OSError')
        self.assertFalse(self.check.passed)
        self.assertNotIn('private-path', json.dumps(self.check.result))

    def test_final_checkpoint_preserves_direct_and_chained_cancellation(self):
        from bcs_e2e.run_state import write
        for chained in (False, True):
            check = LocalCliCheck(self.binary, self.digest)
            check.capture_driver_token('bot-driver', 'test-token')
            calls = []
            def persist(path, result):
                calls.append(result['status'])
                if len(calls) == 2:
                    try:
                        raise KeyboardInterrupt()
                    except KeyboardInterrupt:
                        if chained:
                            raise OSError('private-checkpoint-error')
                        raise
                write(path, result)
            with self.subTest(chained=chained), patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli') as cli, \
                 patch('bcs_e2e.local_cli_check.write', side_effect=persist), self.assertRaises(KeyboardInterrupt):
                cli.return_value.read_permission.return_value = self.permission
                cli.return_value.read_session.return_value = self.result
                check.observe(Mock(), {}, self.root, 's1', 'g1', 'title')
            self.assertEqual(calls, ['RUNNING', 'LOCAL_CLI_SLICE_PASS'])
            self.assertEqual(check.result['status'], 'CANCELLED')
            self.assertFalse(check.passed)
            if chained:
                self.assertEqual(check.result['persistence_error_type'], 'OSError')
            self.assertNotIn('private-checkpoint-error', json.dumps(check.result))

    def test_incomplete_pair_wrong_digest_and_missing_binary_block_before_runtime(self):
        args = dict(binary=self.binary, binary_sha256=self.digest, state_dir=self.root / 'state',
                    run_id='cli-options', allow_local_processes=True, allow_fixture_write=True,
                    allow_write=True, confirm_run_id='cli-options')
        for options in ({'cli_binary': self.binary}, {'cli_binary_sha256': self.digest},
                        {'cli_binary': self.binary, 'cli_binary_sha256': '0' * 64},
                        {'cli_binary': self.root / 'absent', 'cli_binary_sha256': self.digest}):
            with self.subTest(options=options), patch('bcs_e2e.local_run.OwnedBcsProcess') as runtime:
                with self.assertRaises((ConfigError, LocalCliError)):
                    local_run(**args, **options)
                runtime.assert_not_called()
            self.assertFalse(args['state_dir'].exists())

    def test_runner_cli_forwards_explicit_selection_and_returns_failure_code(self):
        argv = ['local-run', '--binary', str(self.binary), '--binary-sha256', self.digest,
                '--state-dir', str(self.root / 'state'), '--run-id', 'r', '--confirm-run-id', 'r',
                '--allow-local-processes', '--allow-fixture-write', '--allow-write',
                '--cli-binary', str(self.binary), '--cli-binary-sha256', self.digest]
        with patch('bcs_e2e.cli.local_run', return_value={'status': 'BLOCKED'}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv), 2)
        self.assertEqual(run.call_args.kwargs['cli_binary'], self.binary)
        self.assertEqual(run.call_args.kwargs['cli_binary_sha256'], self.digest)
        with patch('bcs_e2e.cli.local_run', side_effect=LocalCliError('invalid CLI digest')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(argv), 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')
