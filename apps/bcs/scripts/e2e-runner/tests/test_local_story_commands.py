"""Focused compatibility tests for extracted standalone local Story CLI commands."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.cli import main
from bcs_e2e.local_story_commands import (
    LOCAL_STORY_COMMANDS,
    dispatch_local_story_command,
    register_local_story_commands,
)
from bcs_e2e.run_state import RunStateError


COMMAND_ORDER = (
    'local-coverage-suite',
    'local-event-run',
    'local-event-report',
    'local-structured-run',
    'local-structured-report',
    'local-direct-chat-run',
    'local-direct-chat-report',
    'local-slow-judge-run',
    'local-slow-judge-report',
    'local-fixed-loop-run',
    'local-fixed-loop-report',
    'local-edge-friend-mutations-run',
    'local-edge-friend-mutations-report',
    'local-edge-read-run',
    'local-edge-read-report',
    'local-file-run',
    'local-channel-cli-run',
    'local-channel-cli-report',
    'local-channel-http-run',
    'local-channel-http-report',
    'local-delivery-policy-run',
    'local-delivery-policy-report',
    'local-operator-cli-run',
    'local-operator-cli-report',
    'local-collaboration-team-run',
    'local-collaboration-team-report',
    'local-provider-run',
    'local-provider-report',
    'local-session-run',
    'local-session-report',
    'local-session-sharing-run',
    'local-session-sharing-report',
    'local-session-public-run',
    'local-session-public-report',
    'local-network-run',
    'local-network-report',
    'local-workspace-api',
    'local-workspace-run',
    'local-workspace-report',
    'local-file-report',
    'local-caller-execution-run',
    'local-caller-execution-report',
    'local-draft-group-run',
    'local-draft-group-report',
    'local-result-queue-run',
    'local-result-queue-report',
    'local-nested-fixed-loop-run',
    'local-nested-fixed-loop-report',
    'local-service-policy-run',
    'local-service-policy-report',
    'local-authority-run',
    'local-authority-report',
    'local-session-owner-run',
    'local-session-owner-report',
)


def parser():
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest='command', required=True)
    register_local_story_commands(commands)
    return result, commands


def run_arguments(command, *extra):
    common = [
        command,
        '--binary', '/bcs',
        '--binary-sha256', 'bcs-sha',
        '--state-dir', '/state',
        '--run-id', 'run-1',
        '--confirm-run-id', 'run-1',
    ]
    if command in {'local-file-run', 'local-channel-cli-run', 'local-collaboration-team-run',
                   'local-session-run',
                   'local-session-sharing-run', 'local-workspace-run'}:
        common[1:1] = ['--cli-binary', '/cli', '--cli-binary-sha256', 'cli-sha']
    if command == 'local-nested-fixed-loop-run':
        common[1:1] = ['--cli-binary', '/cli', '--driver', 'driver-bot']
    return [*common, *extra]


class LocalStoryParserTests(unittest.TestCase):
    def test_exact_command_set_and_registration_order(self):
        _, commands = parser()
        self.assertEqual(tuple(commands.choices), COMMAND_ORDER)
        self.assertEqual(LOCAL_STORY_COMMANDS, frozenset(COMMAND_ORDER))

    def test_file_namespace_preserves_types_required_fields_and_defaults(self):
        parsed = parser()[0].parse_args(run_arguments('local-file-run'))
        self.assertEqual(vars(parsed), {
            'command': 'local-file-run',
            'binary': Path('/bcs'),
            'cli_binary': Path('/cli'),
            'state_dir': Path('/state'),
            'binary_sha256': 'bcs-sha',
            'cli_binary_sha256': 'cli-sha',
            'run_id': 'run-1',
            'confirm_run_id': 'run-1',
            'allow_local_processes': False,
            'allow_fixture_write': False,
            'allow_write': False,
        })
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                parser()[0].parse_args(['local-file-run', '--state-dir', '/state'])
        self.assertEqual(raised.exception.code, 2)

    def test_workspace_optional_flags_and_report_defaults_are_unchanged(self):
        parsed = parser()[0].parse_args(run_arguments(
            'local-workspace-run', '--allow-local-processes', '--allow-write',
            '--with-messages', '--with-incident-delete'))
        self.assertTrue(parsed.allow_local_processes)
        self.assertFalse(parsed.allow_fixture_write)
        self.assertTrue(parsed.allow_write)
        self.assertFalse(parsed.with_incident_config)
        self.assertTrue(parsed.with_messages)
        self.assertTrue(parsed.with_incident_delete)
        report = parser()[0].parse_args(['local-workspace-report', '--state-dir', '/state'])
        self.assertEqual(vars(report), {
            'command': 'local-workspace-report',
            'state_dir': Path('/state'),
            'output_dir': None,
        })

    def test_caller_execution_namespaces_require_no_cli_binary(self):
        run = parser()[0].parse_args(run_arguments('local-caller-execution-run'))
        self.assertEqual(vars(run), {
            'command': 'local-caller-execution-run',
            'binary': Path('/bcs'),
            'binary_sha256': 'bcs-sha',
            'state_dir': Path('/state'),
            'run_id': 'run-1',
            'confirm_run_id': 'run-1',
            'allow_local_processes': False,
            'allow_fixture_write': False,
            'allow_write': False,
        })
        report = parser()[0].parse_args(
            ['local-caller-execution-report', '--state-dir', '/state'])
        self.assertEqual(vars(report), {
            'command': 'local-caller-execution-report',
            'state_dir': Path('/state'),
            'output_dir': None,
        })

    def test_result_queue_namespaces_require_no_cli_binary(self):
        run = parser()[0].parse_args(run_arguments('local-result-queue-run'))
        self.assertEqual(vars(run), {
            'command': 'local-result-queue-run',
            'binary': Path('/bcs'),
            'binary_sha256': 'bcs-sha',
            'state_dir': Path('/state'),
            'run_id': 'run-1',
            'confirm_run_id': 'run-1',
            'allow_local_processes': False,
            'allow_fixture_write': False,
            'allow_write': False,
        })
        report = parser()[0].parse_args(
            ['local-result-queue-report', '--state-dir', '/state'])
        self.assertEqual(vars(report), {
            'command': 'local-result-queue-report',
            'state_dir': Path('/state'),
            'output_dir': None,
        })

    def test_nested_fixed_loop_namespace_requires_real_cli_and_driver(self):
        run = parser()[0].parse_args(run_arguments('local-nested-fixed-loop-run'))
        self.assertEqual(vars(run), {
            'command': 'local-nested-fixed-loop-run',
            'binary': Path('/bcs'),
            'binary_sha256': 'bcs-sha',
            'cli_binary': Path('/cli'),
            'driver': 'driver-bot',
            'state_dir': Path('/state'),
            'run_id': 'run-1',
            'confirm_run_id': 'run-1',
            'allow_local_processes': False,
            'allow_fixture_write': False,
            'allow_write': False,
        })
        report = parser()[0].parse_args(
            ['local-nested-fixed-loop-report', '--state-dir', '/state'])
        self.assertEqual(vars(report), {
            'command': 'local-nested-fixed-loop-report',
            'state_dir': Path('/state'),
            'output_dir': None,
        })

    def test_draft_group_namespaces_require_no_cli_binary(self):
        run = parser()[0].parse_args(run_arguments('local-draft-group-run'))
        self.assertEqual(vars(run), {
            'command': 'local-draft-group-run',
            'binary': Path('/bcs'),
            'binary_sha256': 'bcs-sha',
            'state_dir': Path('/state'),
            'run_id': 'run-1',
            'confirm_run_id': 'run-1',
            'allow_local_processes': False,
            'allow_fixture_write': False,
            'allow_write': False,
        })
        report = parser()[0].parse_args(
            ['local-draft-group-report', '--state-dir', '/state'])
        self.assertEqual(vars(report), {
            'command': 'local-draft-group-report',
            'state_dir': Path('/state'),
            'output_dir': None,
        })

    def test_authority_namespaces_require_no_cli_binary(self):
        run = parser()[0].parse_args(run_arguments('local-authority-run'))
        self.assertEqual(vars(run), {
            'command': 'local-authority-run',
            'binary': Path('/bcs'),
            'binary_sha256': 'bcs-sha',
            'state_dir': Path('/state'),
            'run_id': 'run-1',
            'confirm_run_id': 'run-1',
            'allow_local_processes': False,
            'allow_fixture_write': False,
            'allow_write': False,
        })
        report = parser()[0].parse_args(
            ['local-authority-report', '--state-dir', '/state'])
        self.assertEqual(vars(report), {
            'command': 'local-authority-report',
            'state_dir': Path('/state'),
            'output_dir': None,
        })


class LocalStoryDispatchTests(unittest.TestCase):
    def parse(self, argv):
        return parser()[0].parse_args(argv)

    def test_slow_judge_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-slow-judge-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha',
            state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
            allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_SLOW_JUDGE_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_slow_judge_run.local_slow_judge_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_fixed_loop_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-fixed-loop-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha',
            state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
            allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_FIXED_LOOP_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_fixed_loop_run.local_fixed_loop_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_fixed_loop_report_renders_only_outside_original_evidence(self):
        args = self.parse(['local-fixed-loop-report', '--state-dir', '/state',
                           '--output-dir', '/out/reports'])
        with patch('bcs_e2e.local_fixed_loop_reporting.build_fixed_loop_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}) as build:
            writes = Mock()
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(report, {
            'status': 'LOCAL_SUITE_PASS', 'summary': build.return_value,
            'outputs': writes.return_value, 'ready_to_execute': False,
        })
        self.assertEqual(rc, 0)
        build.assert_called_once_with(args.state_dir)
        writes.assert_called_once_with(build.return_value, args.output_dir, {'all'})

    def test_edge_friend_mutations_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-edge-friend-mutations-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha',
            state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
            allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_EDGE_FRIEND_MUTATIONS_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_edge_friend_mutations_run.local_edge_friend_mutations_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_edge_friend_mutations_report_renders_only_outside_original_evidence(self):
        args = self.parse(['local-edge-friend-mutations-report', '--state-dir', '/state',
                           '--output-dir', '/out/reports'])
        with patch('bcs_e2e.local_edge_friend_mutations_reporting.build_edge_friend_mutations_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}) as build:
            writes = Mock()
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(report, {
            'status': 'LOCAL_SUITE_PASS', 'summary': build.return_value,
            'outputs': writes.return_value, 'ready_to_execute': False,
        })
        self.assertEqual(rc, 0)
        build.assert_called_once_with(args.state_dir)
        writes.assert_called_once_with(build.return_value, args.output_dir, {'all'})

    def test_slow_judge_report_renders_only_outside_original_evidence(self):
        args = self.parse(['local-slow-judge-report', '--state-dir', '/state',
                           '--output-dir', '/out/reports'])
        with patch('bcs_e2e.local_slow_judge_reporting.build_slow_judge_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}) as build:
            writes = Mock()
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(report, {
            'status': 'LOCAL_SUITE_PASS', 'summary': build.return_value,
            'outputs': writes.return_value, 'ready_to_execute': False,
        })
        self.assertEqual(rc, 0)
        build.assert_called_once_with(args.state_dir)
        writes.assert_called_once_with(build.return_value, args.output_dir, {'all'})

    def test_channel_cli_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-channel-cli-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha',
            cli_binary=Path('/cli'), cli_binary_sha256='cli-sha',
            state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
            allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_CHANNEL_CLI_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_channel_cli_run.local_channel_cli_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_session_variants_preserve_cli_kwargs_and_completion_flag(self):
        cases = (
            ('local-session-public-run', 'LOCAL_SESSION_PUBLIC_PASS', False, False),
            ('local-session-sharing-run', 'LOCAL_SESSION_SHARING_PASS', False, True),
            ('local-session-run', 'LOCAL_SESSION_FLOW_PASS', True, True),
        )
        for command, status, completion, has_cli in cases:
            with self.subTest(command=command):
                args = self.parse(run_arguments(command))
                with patch('bcs_e2e.local_session_run.local_session_public_run',
                           return_value={'status': status}) as execute:
                    _, rc = dispatch_local_story_command(args, write_reports=Mock())
                expected = dict(
                    binary=Path('/bcs'), binary_sha256='bcs-sha', state_dir=Path('/state'),
                    run_id='run-1', confirm_run_id='run-1', allow_local_processes=False,
                    allow_fixture_write=False, allow_write=False, with_completion=completion)
                if has_cli:
                    expected.update(cli_binary=Path('/cli'), cli_binary_sha256='cli-sha')
                execute.assert_called_once_with(**expected)
                self.assertEqual(rc, 0)

    def test_workspace_run_preserves_optional_flags(self):
        args = self.parse(run_arguments(
            'local-workspace-run', '--with-incident-config', '--with-messages',
            '--with-incident-delete'))
        with patch('bcs_e2e.local_workspace_run.local_workspace_api_run',
                   return_value={'status': 'LOCAL_WORKSPACE_INCIDENT_ROUNDTRIP_PASS'}) as execute:
            _, rc = dispatch_local_story_command(args, write_reports=Mock())
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            binary=Path('/bcs'), binary_sha256='bcs-sha', state_dir=Path('/state'),
            run_id='run-1', confirm_run_id='run-1', allow_local_processes=False,
            allow_fixture_write=False, allow_write=False, cli_binary=Path('/cli'),
            cli_binary_sha256='cli-sha', with_incident=True, with_messages=True,
            with_delete=True)

    def test_report_output_guard_and_file_report_exception_are_preserved(self):
        workspace = self.parse([
            'local-workspace-report', '--state-dir', '/state', '--output-dir', '/state/reports'])
        with patch('bcs_e2e.local_workspace_reporting.build_workspace_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}), self.assertRaisesRegex(
                       RunStateError, 'Offline Workspace rendering cannot overwrite original evidence'):
            dispatch_local_story_command(workspace, write_reports=Mock())

        file_report = self.parse([
            'local-file-report', '--state-dir', '/state', '--output-dir', '/state/reports'])
        writer = Mock(return_value={'json': '/state/reports/summary.json'})
        summary = {'status': 'LOCAL_SUITE_PASS'}
        with patch('bcs_e2e.local_file_reporting.build_file_report', return_value=summary) as build:
            report, rc = dispatch_local_story_command(file_report, write_reports=writer)
        build.assert_called_once_with(Path('/state').resolve())
        writer.assert_called_once_with(summary, Path('/state/reports'), {'all'})
        self.assertEqual(rc, 0)
        self.assertEqual(report['outputs'], {'json': '/state/reports/summary.json'})

    def test_caller_execution_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-caller-execution-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha',
            state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
            allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_CALLER_EXECUTION_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_caller_execution_run.local_caller_execution_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_result_queue_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-result-queue-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha',
            state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
            allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_RESULT_QUEUE_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_result_queue_run.local_result_queue_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_nested_fixed_loop_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-nested-fixed-loop-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha', cli_binary=Path('/cli'),
            driver='driver-bot', state_dir=Path('/state'), run_id='run-1',
            confirm_run_id='run-1', allow_local_processes=True,
            allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_NESTED_FIXED_LOOP_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_nested_fixed_loop_run.local_nested_fixed_loop_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_draft_group_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-draft-group-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha',
            state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
            allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_DRAFT_GROUP_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_draft_group_run.local_draft_group_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_caller_execution_report_renders_only_outside_original_evidence(self):
        args = self.parse(['local-caller-execution-report', '--state-dir', '/state',
                           '--output-dir', '/out/reports'])
        with patch('bcs_e2e.local_caller_execution_reporting.build_caller_execution_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}) as build:
            writes = Mock()
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(report, {
            'status': 'LOCAL_SUITE_PASS', 'summary': build.return_value,
            'outputs': writes.return_value, 'ready_to_execute': False,
        })
        self.assertEqual(rc, 0)
        build.assert_called_once_with(args.state_dir)
        writes.assert_called_once_with(build.return_value, args.output_dir, {'all'})

        guard = self.parse(['local-caller-execution-report', '--state-dir', '/state',
                            '--output-dir', '/state/reports'])
        with patch('bcs_e2e.local_caller_execution_reporting.build_caller_execution_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}), self.assertRaisesRegex(
                       RunStateError,
                       'Offline Caller execution rendering cannot overwrite original evidence'):
            dispatch_local_story_command(guard, write_reports=Mock())

    def test_result_queue_report_renders_only_outside_original_evidence(self):
        args = self.parse(['local-result-queue-report', '--state-dir', '/state',
                           '--output-dir', '/out/reports'])
        with patch('bcs_e2e.local_result_queue_reporting.build_result_queue_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}) as build:
            writes = Mock()
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(report, {
            'status': 'LOCAL_SUITE_PASS', 'summary': build.return_value,
            'outputs': writes.return_value, 'ready_to_execute': False,
        })
        self.assertEqual(rc, 0)
        build.assert_called_once_with(args.state_dir)
        writes.assert_called_once_with(build.return_value, args.output_dir, {'all'})

        guard = self.parse(['local-result-queue-report', '--state-dir', '/state',
                            '--output-dir', '/state/reports'])
        with patch('bcs_e2e.local_result_queue_reporting.build_result_queue_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}), self.assertRaisesRegex(
                       RunStateError,
                       'Offline Result queue rendering cannot overwrite original evidence'):
            dispatch_local_story_command(guard, write_reports=Mock())

    def test_nested_fixed_loop_report_renders_only_outside_original_evidence(self):
        args = self.parse(['local-nested-fixed-loop-report', '--state-dir', '/state',
                           '--output-dir', '/out/reports'])
        with patch('bcs_e2e.local_nested_fixed_loop_reporting.build_nested_fixed_loop_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}) as build:
            writes = Mock()
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(report, {
            'status': 'LOCAL_SUITE_PASS', 'summary': build.return_value,
            'outputs': writes.return_value, 'ready_to_execute': False,
        })
        self.assertEqual(rc, 0)
        build.assert_called_once_with(args.state_dir)
        writes.assert_called_once_with(build.return_value, args.output_dir, {'all'})

        guard = self.parse(['local-nested-fixed-loop-report', '--state-dir', '/state',
                            '--output-dir', '/state/reports'])
        with patch('bcs_e2e.local_nested_fixed_loop_reporting.build_nested_fixed_loop_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}), self.assertRaisesRegex(
                       RunStateError,
                       'Offline Nested fixed-loop rendering cannot overwrite original evidence'):
            dispatch_local_story_command(guard, write_reports=Mock())

    def test_draft_group_report_renders_only_outside_original_evidence(self):
        args = self.parse(['local-draft-group-report', '--state-dir', '/state',
                           '--output-dir', '/out/reports'])
        with patch('bcs_e2e.local_draft_group_reporting.build_draft_group_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}) as build:
            writes = Mock()
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(report, {
            'status': 'LOCAL_SUITE_PASS', 'summary': build.return_value,
            'outputs': writes.return_value, 'ready_to_execute': False,
        })
        self.assertEqual(rc, 0)
        build.assert_called_once_with(args.state_dir)
        writes.assert_called_once_with(build.return_value, args.output_dir, {'all'})

        guard = self.parse(['local-draft-group-report', '--state-dir', '/state',
                            '--output-dir', '/state/reports'])
        with patch('bcs_e2e.local_draft_group_reporting.build_draft_group_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}), self.assertRaisesRegex(
                       RunStateError,
                       'Offline Draft group rendering cannot overwrite original evidence'):
            dispatch_local_story_command(guard, write_reports=Mock())

    def test_authority_kwargs_and_pass_blocked_cancelled_return_codes(self):
        args = self.parse(run_arguments(
            'local-authority-run', '--allow-local-processes',
            '--allow-fixture-write', '--allow-write'))
        expected = dict(
            binary=Path('/bcs'), binary_sha256='bcs-sha',
            state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
            allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        for status, expected_rc in (
                ('LOCAL_AUTHORITY_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_authority_run.local_authority_run',
                    return_value={'status': status}) as execute:
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(**expected)

    def test_authority_report_renders_only_outside_original_evidence(self):
        args = self.parse(['local-authority-report', '--state-dir', '/state',
                           '--output-dir', '/out/reports'])
        with patch('bcs_e2e.local_authority_reporting.build_authority_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}) as build:
            writes = Mock()
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(report, {
            'status': 'LOCAL_SUITE_PASS', 'summary': build.return_value,
            'outputs': writes.return_value, 'ready_to_execute': False,
        })
        self.assertEqual(rc, 0)
        build.assert_called_once_with(args.state_dir)
        writes.assert_called_once_with(build.return_value, args.output_dir, {'all'})

        guard = self.parse(['local-authority-report', '--state-dir', '/state',
                            '--output-dir', '/state/reports'])
        with patch('bcs_e2e.local_authority_reporting.build_authority_report',
                   return_value={'status': 'LOCAL_SUITE_PASS'}), self.assertRaisesRegex(
                       RunStateError,
                       'Offline authority rendering cannot overwrite original evidence'):
            dispatch_local_story_command(guard, write_reports=Mock())

    def test_main_skips_profile_and_passes_cli_writer_explicitly(self):
        argv = run_arguments('local-file-run')
        result = {'status': 'LOCAL_SUITE_PASS', 'ready_to_execute': False}
        writer = Mock(name='cli_write_reports')
        with patch('bcs_e2e.cli.load') as load_profile, \
                patch('bcs_e2e.cli.write_reports', writer), \
                patch('bcs_e2e.cli.dispatch_local_story_command',
                      return_value=(result, 0)) as dispatch, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(argv)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(output.getvalue()), result)
        load_profile.assert_not_called()
        self.assertEqual(dispatch.call_args.args[0].command, 'local-file-run')
        self.assertIs(dispatch.call_args.kwargs['write_reports'], writer)


if __name__ == '__main__':
    unittest.main()
