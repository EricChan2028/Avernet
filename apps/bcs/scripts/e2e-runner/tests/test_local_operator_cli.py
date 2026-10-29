"""Operator CLI parent: source map, guards and offline evidence."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_operator_cli import OwnedLocalOperatorCli
from bcs_e2e.local_operator_cli_contract import (
    CASE_ID,
    COMMANDS,
    PARENT,
    REQUIREMENTS,
    SUITE,
    operator_cli_source_binding,
)
from bcs_e2e.local_operator_cli_reporting import (
    _business_evidence,
    build_operator_cli_report,
)
from bcs_e2e.local_operator_cli_run import local_operator_cli_run
from bcs_e2e.run_state import RunStateError, read

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI_BINARY = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_SHA = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


def args(root, **changes):
    base = dict(binary=Path(BINARY), binary_sha256=SHA,
                cli_binary=Path(CLI_BINARY), cli_binary_sha256=CLI_SHA,
                state_dir=root, run_id='operator-test',
                confirm_run_id='operator-test',
                allow_local_processes=True, allow_fixture_write=True,
                allow_write=True)
    base.update(changes)
    return base


class OperatorGuards(unittest.TestCase):
    def test_original_complete_source_map_and_drift_rejection(self):
        original = operator_cli_source_binding()
        self.assertEqual(original['parent'], PARENT)
        self.assertEqual([x['id'] for x in original['requirements']], list(REQUIREMENTS))
        self.assertEqual([x['command'] for x in original['requirements']], list(COMMANDS))
        for mutation in ('missing', 'reverse', 'command', 'expectation', 'hash'):
            value = copy.deepcopy(original)
            if mutation == 'missing':
                value['requirements'].pop()
            elif mutation == 'reverse':
                value['requirements'].reverse()
            elif mutation == 'command':
                value['requirements'][3]['command'] = 'bcs-cli foreign'
            elif mutation == 'expectation':
                value['requirements'][3]['expectation'] = ''
            else:
                value['sources']['cli.sh'] = '0'*64
            with self.subTest(mutation=mutation), patch('bcs_e2e.local_operator_cli_contract.read', return_value=value):
                with self.assertRaises(RunStateError):
                    operator_cli_source_binding()

    def test_registry_keeps_operator_parent_separate_and_no_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        row = next(r for r in plan['local_registered_executors'] if r['case_id'] == CASE_ID)
        self.assertEqual(row['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(row['execution_command'], 'local-operator-cli-run')
        self.assertEqual(row['report_command'], 'local-operator-cli-report')
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(row['legacy_id'], PARENT)
        self.assertEqual(plan['legacy_inventory_count'], 34)
        self.assertEqual(plan['migrated_story_count'], 0)
        self.assertTrue(any(m.get('parent') == PARENT for m in plan['additional_source_maps']))

    def test_permissions_and_fresh_directory_precede_process_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_operator_cli_run.OwnedBcsProcess') as proc:
            for key, value in (('allow_write', False), ('allow_fixture_write', False),
                               ('allow_local_processes', False), ('confirm_run_id', 'other')):
                with self.subTest(key=key), self.assertRaises(RunStateError):
                    local_operator_cli_run(**args(Path(tmp)/'run', **{key: value}))
            with self.assertRaises(RunStateError):
                local_operator_cli_run(**args(Path(tmp)))
            proc.assert_not_called()

    def test_unmanaged_identity_and_missing_cli_permissions_refused(self):
        with self.assertRaises(RunStateError):
            OwnedLocalOperatorCli(object(), Path('/missing'), '0'*64)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(['local-operator-cli-run', '--binary', '/unused', '--binary-sha256', '0'*64,
                       '--cli-binary', '/unused', '--cli-binary-sha256', '0'*64,
                       '--state-dir', str(Path(tmp)/'run'), '--run-id', 'denied', '--confirm-run-id', 'denied'])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out.getvalue())['status'], 'BLOCKED')

    def test_command_wiring_and_cli_binary_arguments(self):
        from bcs_e2e.local_story_commands import register_local_story_commands
        import argparse
        commands = argparse.ArgumentParser().add_subparsers()
        register_local_story_commands(commands)
        parsed = commands._name_parser_map['local-operator-cli-run'].parse_args([
            '--binary', '/bcs', '--binary-sha256', 'b'*64,
            '--cli-binary', '/bcs-cli', '--cli-binary-sha256', 'c'*64,
            '--state-dir', '/state', '--run-id', 'run', '--confirm-run-id', 'run',
            '--allow-local-processes', '--allow-fixture-write', '--allow-write'])
        self.assertEqual(parsed.cli_binary, Path('/bcs-cli'))
        self.assertEqual(parsed.cli_binary_sha256, 'c'*64)
        self.assertTrue(parsed.allow_local_processes)
        with self.assertRaises(SystemExit):
            commands._name_parser_map['local-operator-cli-run'].parse_args([
                '--binary', '/bcs', '--binary-sha256', 'b'*64,
                '--state-dir', '/state', '--run-id', 'run', '--confirm-run-id', 'run'])


@unittest.skipUnless(BINARY and SHA and CLI_BINARY and CLI_SHA and shutil.which('lsof'), 'approved real BCS required')
class OperatorLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)/'run'
        cls.result = local_operator_cli_run(**args(cls.root))
        if cls.result['status'] != 'LOCAL_OPERATOR_CLI_PASS':
            raise AssertionError(json.dumps(cls.result, indent=2))

    def test_all13_obligations_pass_resources_owned_and_actual_stop(self):
        result, summary = self.result, self.result['canonical_summary']
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['requirements'], [{'id': x, 'status': 'PASS'} for x in REQUIREMENTS])
        self.assertEqual(summary['scope']['suite'], SUITE)
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['cleanup']['verification_scope'], 'OPERATOR_CLI_OWNED_RUNTIME_ONLY')
        self.assertEqual(summary['reports']['expected_assertion_count'], 13)
        self.assertEqual(summary['reports']['observed_assertion_count'], 13)
        case = result['case']
        self.assertEqual(case['status'], 'LOCAL_OPERATOR_CLI_PASS')
        self.assertEqual(len(case['operations']), 13)
        self.assertEqual(case['assertions'], [{'id': x, 'status': 'PASS'} for x in REQUIREMENTS])
        self.assertEqual(case['fixture_roles'], ['CEO', 'PM', 'ENG', 'QA', 'CS'])
        self.assertEqual(case['cli_binary_sha256'], CLI_SHA)
        self.assertEqual(case['cli_timeout_seconds'], 20)
        self.assertTrue(case['visibility_round_trip']['round_trip_verified'])
        self.assertIn(case['visibility_round_trip']['original'], ('public', 'protected', 'private'))
        self.assertTrue(case['direct_onboard']['onboarded'])
        self.assertEqual(case['direct_onboard']['disposition'], 'DELETED_404_VERIFIED')
        self.assertEqual(case['group_create']['disposition'], 'DELETED_404_VERIFIED')
        self.assertEqual(case['chat']['disposition'], 'OWNED_RUNTIME_SCOPED')
        self.assertEqual(case['friendship']['admission'], 'ACCEPTED')
        self.assertEqual(case['chat']['prerequisite']['admission'], 'ACCEPTED')
        self.assertEqual(case['chat']['prerequisite']['target_bot_id'],
                         case['role_bot_ids']['PM'])
        connection = case['chat']['prerequisite']['connection']
        self.assertEqual(connection['endpoint'], '/ws/bot')
        self.assertEqual(connection['bot_id'], case['role_bot_ids']['PM'])
        self.assertTrue(connection['request_id'])
        self.assertTrue(connection['reconnected'])
        self.assertEqual(case['operations'][10]['observations'][0],
                         {'kind': 'bot_ws', 'endpoint': '/ws/bot',
                          'request_id': connection['request_id']})
        self.assertEqual(
            {row['id'] for row in case['resources']['created']
             if row['kind'] == 'friendship'},
            {case['role_bot_ids']['CEO']+':'+case['role_bot_ids']['PM'],
             case['role_bot_ids']['CEO']+':'+case['role_bot_ids']['ENG']})
        kinds_created = {row['kind'] for row in case['resources']['created']}
        kinds_deleted = {row['kind'] for row in case['resources']['deleted']}
        self.assertEqual(kinds_created, {'bot', 'group', 'chat_run', 'friendship'})
        self.assertEqual(kinds_deleted, {'bot', 'group'})
        self.assertEqual(case['resources']['unknown'], [])
        self.assertIn('DELETED_EXACTLY_ONCE', case['resource_disposition'])
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        junit = ET.parse(self.root/'reports/junit.xml').getroot()
        self.assertEqual(junit.attrib['name'], 'bcs-e2e-operator-cli')
        self.assertEqual([junit.attrib[k] for k in ('tests', 'failures', 'errors', 'skipped')], ['1', '0', '0', '0'])
        for name in REQUIREMENTS:
            self.assertIn(name, (self.root/'reports/summary.md').read_text())

    def test_offline_read_only_and_source_corruption_rejected(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob('*') if p.is_file()}
        with patch('http.client.HTTPConnection.request', side_effect=AssertionError('offline')):
            self.assertEqual(build_operator_cli_report(self.root), self.result['canonical_summary'])
        receipt = read(self.root/'operator-cli-completion.json')
        for name in [*receipt['sources'], *receipt['outputs']]:
            p = self.root/name
            raw = p.read_bytes()
            try:
                p.write_bytes(raw+b' ')
                try:
                    value = build_operator_cli_report(self.root)
                except (RunStateError, ValueError):
                    continue
                self.assertNotEqual(value['status'], 'LOCAL_SUITE_PASS')
            finally:
                p.write_bytes(raw)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in self.root.rglob('*') if p.is_file()})
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['local-operator-cli-report', '--state-dir', str(self.root),
                       '--output-dir', str(self.root/'reports')])
        self.assertEqual(rc, 2)

    def test_semantic_projection_rejects_unknown_foreign_or_duplicate(self):
        binding = read(self.root/'operator-cli-binding.json')
        case = self.result['case']
        self.assertTrue(_business_evidence(self.root, binding, case))
        for edit in ('unknown', 'foreign-command', 'foreign-observation', 'foreign-path',
                     'group-create-driver', 'transition', 'created-resource',
                     'visibility-original', 'friendship-target', 'chat-disposition',
                     'chat-prerequisite', 'chat-prerequisite-connection',
                     'friendship-pair'):
            value = copy.deepcopy(case)
            if edit == 'unknown':
                value['operations'][0]['status'] = 'UNKNOWN'
            elif edit == 'foreign-command':
                value['operations'][0]['cli_command'] = 'bcs-cli foreign'
            elif edit == 'foreign-observation':
                value['operations'][1]['observations'].append(
                    {'kind': 'cli', 'command': 'bcs-cli foreign', 'exit_code': 0})
            elif edit == 'foreign-path':
                value['operations'][7]['observations'][0]['path'] = '/bots/foreign'
            elif edit == 'group-create-driver':
                value['group_create']['driver_bot'] = 'intruder'
            elif edit == 'transition':
                value['operations'][2]['transitions'] = ['INTENDED']
            elif edit == 'created-resource':
                value['resources']['created'] = ['unexpected']
            elif edit == 'visibility-original':
                value['visibility_round_trip']['original'] = 'unknown'
            elif edit == 'friendship-target':
                value['friendship']['target_bot_id'] = 'intruder'
            elif edit == 'chat-prerequisite':
                value['chat']['prerequisite']['admission'] = 'PENDING'
            elif edit == 'chat-prerequisite-connection':
                value['chat']['prerequisite']['connection']['reconnected'] = False
            elif edit == 'friendship-pair':
                for resource in value['resources']['created']:
                    if (resource['kind'] == 'friendship'
                            and resource['id'].endswith(':'+value['role_bot_ids']['ENG'])):
                        resource['id'] = 'intruder:'+value['role_bot_ids']['ENG']
            else:
                value['chat']['disposition'] = 'UNVERIFIED'
            with self.subTest(edit=edit):
                self.assertFalse(_business_evidence(self.root, binding, value))
