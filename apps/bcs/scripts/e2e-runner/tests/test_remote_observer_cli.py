"""Real command composition; only Gateway and MCP process replies are synthetic.

No office service, credential, socket, database or installed mcporter is used.
"""
import contextlib
import copy
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.alisddbquery import AlisdDbQueryObserver, McporterAlisdDbQueryClient
from bcs_e2e.cli import main
from bcs_e2e.evidence import ASSERTIONS, _make_plan
from bcs_e2e.ledger import Ledger
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import ConfigError, fingerprint, load
from bcs_e2e.remote_observer import (FILENAME, ReviewedBinding, ReviewedMcpClient, binding_summary,
                                    remote_dependencies, validate_binding)
from bcs_e2e.reporting import build_report, render_junit, render_markdown
from bcs_e2e.run_state import RunStateError, read, write
from test_remote_pilot_all_cases import RemoteShapedAdapter, RemoteShapedSemanticProvider


class RemoteObserverCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        pool = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools')
        pool.start(); self.addCleanup(pool.stop)
        self.profile = load(ROOT / 'profiles/preprod.example.json')
        self.profile['environment'].update(id='reviewed-pre', expected_bcs_env='pre')
        self.profile['human']['user_id'] = 'test-owner'
        for role in ('driver', 'member'):
            self.profile['actors'][role].update(bot_id='test-' + role, owner_id='test-owner')
        self.profile['observer'].update(server='test-mcp', database='testdb', environment_binding_confirmed=True)
        self.profile['policy'].update(fixture_pool='reviewed-pool', exclusive_use_confirmed=True)
        self.profile_path = self.root / 'profile.json'; write(self.profile_path, self.profile)
        self.state = self.root / 'run'; self.run_id = 'reviewed-run'
        self.receipt_path = self.root / 'review.json'
        now = int(time.time())
        self.receipt = {'schema_version': 1, 'verification_mode': 'OPERATOR_ATTESTED',
            'receipt_id': 'synthetic-review', 'reviewer': 'private-reviewer',
            'reviewed_at': now - 60, 'expires_at': now + 3600, 'profile_digest': fingerprint(self.profile),
            'target': {'environment_id': 'reviewed-pre', 'api_origin': self.profile['entrypoint']['origin'],
                       'server': 'test-mcp', 'database': 'testdb', 'env': 'pre'},
            'deployment_commit': 'a' * 40, 'query_authorized': True,
            'source_reviews': {'deployment_config': {'reference': 'private-deployment-ref', 'sha256': 'b' * 64},
                               'mcp_datasource': {'reference': 'private-datasource-ref', 'sha256': 'c' * 64}}}
        write(self.receipt_path, self.receipt)
        # Explicit executable is validated but never spawned by these tests.
        self.executable = self.root / 'mock-mcporter'
        self.executable.write_text('#!/bin/sh\nexit 91\n'); self.executable.chmod(0o700)
        self.calls = []
        self.adapter = None

    def command(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(args)
        return rc, json.loads(output.getvalue())

    def prepare(self):
        rc, result = self.command(['prepare', '--profile', str(self.profile_path), '--run-id', self.run_id,
            '--state-dir', str(self.state), '--observer-binding', str(self.receipt_path)])
        self.assertEqual(rc, 0, result)
        return result

    def run_args(self, case='PRE-01'):
        return ['run', '--profile', str(self.profile_path), '--state-dir', str(self.state),
                '--run-id', self.run_id, '--case', case, '--allow-write', '--confirm-run-id', self.run_id,
                '--mcporter-binary', str(self.executable), '--allow-observer-read']

    def process(self, command, **kwargs):
        self.calls.append(command)
        self.assertFalse(kwargs['shell'])
        self.assertEqual(command[:2], [str(self.executable.resolve()), 'call'])
        self.assertEqual(command[-2:], ['--output', 'json'])
        name = command[2].removeprefix('test-mcp.')
        args = json.loads(command[4]) if '--args' in command else {}
        if name == 'db_list':
            data = ['testdb']
        elif name == 'table_list':
            self.assertEqual(args, {'db': 'testdb'})
            data = [{'Tables_in_testdb': spec[0]} for spec in ASSERTIONS.values()]
        elif name == 'table_describe':
            self.assertEqual(args['db'], 'testdb')
            fields = next(spec[1] for spec in ASSERTIONS.values() if spec[0] == args['table'])
            ddl = 'CREATE TABLE `' + args['table'] + '` (' + ','.join('`' + f + '` varchar(128)' for f in fields) + ')'
            data = [{'Table': args['table'], 'Create Table': ddl}]
        elif name == 'db_query':
            assertion = next(key for key, spec in ASSERTIONS.items() if spec[0] == args['table'])
            _, fields, key = ASSERTIONS[assertion]
            match = re.fullmatch(r"env = 'pre' AND " + key + r" = '([A-Za-z0-9_.:@-]+)'", args['where'])
            self.assertIsNotNone(match)
            self.assertEqual(args['db'], 'testdb'); self.assertEqual(args['limit'], '2')
            self.assertEqual(args['select'], ','.join(fields))
            raw = RemoteShapedSemanticProvider(self.adapter).observe(assertion, match.group(1))
            data = raw['rows']
            if assertion == 'file.persisted': data[0]['status'] = 'Ready'
        else:
            self.fail('unreviewed tool was invoked')
        return subprocess.CompletedProcess(command, 0, json.dumps({'success': True, 'data': data}), '')

    @contextlib.contextmanager
    def composed(self, runner=None):
        def factory(profile, *, observer_client, physical_binding_probe, **kwargs):
            adapter = RemoteShapedAdapter(profile)
            adapter.semantic_observer = lambda: AlisdDbQueryObserver(client=observer_client,
                database=profile['observer']['database'], environment=profile['environment']['expected_bcs_env'],
                environment_binding_confirmed=True, physical_binding_probe=physical_binding_probe)
            self.adapter = adapter
            return adapter
        def client(**kwargs):
            return McporterAlisdDbQueryClient(**kwargs, runner=runner or self.process)
        with patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient', side_effect=client), \
             patch('bcs_e2e.pilot.adapter_for', side_effect=factory), \
             patch('socket.create_connection', side_effect=AssertionError('no network in conformance tests')):
            yield

    def test_prepare_is_offline_freezes_receipt_and_never_resolves_credentials(self):
        with patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client, \
             patch('bcs_e2e.transport.ReadClient') as http:
            self.prepare()
        client.assert_not_called(); http.assert_not_called()
        self.assertEqual(read(self.state / FILENAME), self.receipt)
        self.assertEqual((self.state / FILENAME).stat().st_mode & 0o777, 0o600)
        self.assertEqual(read(self.state / 'run.json')['observer_binding'], binding_summary(self.receipt))
        with Ledger(self.state / 'run.sqlite', self.run_id, fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual(ledger.resource_snapshot(), [])

    def test_run_command_composes_all_three_shared_stories_through_real_mcp_wire_client(self):
        self.prepare()
        with self.composed():
            for case in ('PRE-01', 'PRE-02', 'PRE-03'):
                rc, report = self.command(self.run_args(case))
                self.assertEqual(rc, 0, report)
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['cleanup'], 'PASS')
                self.assertEqual(report['evidence_status'], 'PASS')
                self.assertEqual(report['observer_binding'], binding_summary(self.receipt))
        names = [call[2] for call in self.calls]
        self.assertEqual(names.count('test-mcp.db_query'), 4)
        self.assertEqual(names.count('test-mcp.db_list'), 3)
        with Ledger(self.state / 'run.sqlite', self.run_id, fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual(len(ledger.resource_snapshot()), 9)
            self.assertEqual({r['state'] for r in ledger.resource_snapshot()}, {'deleted'})
        self.assertFalse(Path(read(self.state / 'run.json')['lock_path']).exists())
        summary = build_report(self.profile, self.state)
        self.assertEqual(summary['status'], 'PILOT_PASS')
        self.assertEqual(summary['target']['deployment_version'], 'unknown')
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['observer_binding']['live_verification'], 'NOT_PERFORMED')
        text = json.dumps(summary) + render_markdown(summary) + render_junit(summary)
        for private in ('private-reviewer', 'private-deployment-ref', 'private-datasource-ref'):
            self.assertNotIn(private, text)
        props = ET.fromstring(render_junit(summary)).findall('./properties/property')
        self.assertIn(('observer_binding.verification_mode', 'OPERATOR_ATTESTED'),
                      [(p.get('name'), p.get('value')) for p in props])

    def test_expired_historical_receipt_replays_offline_without_reauthorizing_execution(self):
        self.prepare()
        with self.composed():
            rc, _ = self.command(self.run_args())
        self.assertEqual(rc, 0)
        before = build_report(self.profile, self.state)
        with patch('bcs_e2e.remote_observer.time.time', return_value=self.receipt['expires_at'] + 1), \
             patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client:
            after = build_report(self.profile, self.state)
            self.assertEqual(after['cases'], before['cases'])
            self.assertEqual(after['integrity'], before['integrity'])
            self.assertEqual(after['observer_binding'], before['observer_binding'])
            self.assertEqual(self.command(self.run_args('PRE-02'))[0], 2)
        client.assert_not_called()
        self.assertEqual(read(self.state / 'run.json')['attempted_cases'], ['PRE-01'])

    def test_missing_read_permission_executable_or_write_confirmation_never_constructs_clients(self):
        self.prepare()
        for flag in ('--allow-observer-read', '--mcporter-binary', '--allow-write'):
            args = self.run_args(); index = args.index(flag)
            del args[index:index + (2 if flag == '--mcporter-binary' else 1)]
            with self.subTest(flag=flag), patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client:
                rc, result = self.command(args)
                self.assertEqual(rc, 2, result); client.assert_not_called()
        args = self.run_args(); args[args.index('--confirm-run-id') + 1] = 'wrong-run'
        with patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client:
            self.assertEqual(self.command(args)[0], 2); client.assert_not_called()
        self.assertEqual(read(self.state / 'run.json')['status'], 'PREPARED')

    def test_boolean_profile_alone_and_metadata_probe_report_are_not_review_receipts(self):
        for value in ({'schema_version': 1, 'status': 'METADATA_COMPATIBLE', 'physical_environment_binding': 'UNVERIFIED'},
                      {'schema_version': 1, 'environment_binding_confirmed': True}):
            with self.assertRaises(ConfigError): validate_binding(value, self.profile)
        prepare_run(self.profile, self.state, self.run_id)
        with patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client:
            self.assertEqual(self.command(self.run_args())[0], 2); client.assert_not_called()

    def test_missing_unknown_fields_and_unconfirmed_query_authorization_are_rejected(self):
        for field in self.receipt:
            value = copy.deepcopy(self.receipt); del value[field]
            with self.subTest(field=field), self.assertRaises(ConfigError): validate_binding(value, self.profile)
        for field, invalid in (('query_authorized', False), ('query_authorized', 1),
                               ('verification_mode', 'LIVE_VERIFIED'), ('schema_version', True),
                               ('profile_digest', 'd' * 64), ('deployment_commit', 'main'),
                               ('reviewer', 'Bearer private-value'), ('unknown', True)):
            value = copy.deepcopy(self.receipt); value[field] = invalid
            with self.subTest(field=field, invalid=invalid), self.assertRaises(ConfigError):
                validate_binding(value, self.profile)

    def test_target_mismatches_are_rejected_and_no_remote_local_mode_confusion(self):
        for field in self.receipt['target']:
            value = copy.deepcopy(self.receipt); value['target'][field] = 'other'
            with self.subTest(field=field), self.assertRaises(ConfigError): validate_binding(value, self.profile)
        with self.assertRaises(ConfigError):
            validate_binding(self.receipt, load(ROOT / 'profiles/local.mock.json'))
        profile = copy.deepcopy(self.profile); profile['observer']['environment_binding_confirmed'] = False
        with self.assertRaises(ConfigError): validate_binding(self.receipt, profile)

    def test_evidence_sources_and_time_bounds_are_required_not_guessed(self):
        for field, invalid in (('reviewed_at', True), ('reviewed_at', -1),
                               ('reviewed_at', self.receipt['expires_at'] + 1), ('expires_at', 1)):
            value = copy.deepcopy(self.receipt); value[field] = invalid
            with self.subTest(field=field), self.assertRaises(ConfigError): validate_binding(value, self.profile)
        with patch('bcs_e2e.remote_observer.time.time', return_value=self.receipt['reviewed_at'] - 1), self.assertRaises(ConfigError):
            validate_binding(self.receipt, self.profile)
        for mutation in ('same-ref', 'bad-sha', 'missing', 'url-ref'):
            value = copy.deepcopy(self.receipt)
            if mutation == 'same-ref': value['source_reviews']['mcp_datasource']['reference'] = 'private-deployment-ref'
            if mutation == 'bad-sha': value['source_reviews']['deployment_config']['sha256'] = 'unknown'
            if mutation == 'missing': value['source_reviews'].pop('mcp_datasource')
            if mutation == 'url-ref': value['source_reviews']['deployment_config']['reference'] = 'https://example.test/?token=test'
            with self.subTest(mutation=mutation), self.assertRaises(ConfigError): validate_binding(value, self.profile)

    def test_invalid_prepare_does_not_acquire_pool_or_create_run(self):
        self.receipt['query_authorized'] = False; write(self.receipt_path, self.receipt)
        with patch('bcs_e2e.prepare.PoolLease.acquire') as acquire:
            rc, result = self.command(['prepare', '--profile', str(self.profile_path), '--run-id', self.run_id,
                '--state-dir', str(self.state), '--observer-binding', str(self.receipt_path)])
        self.assertEqual(rc, 2, result); acquire.assert_not_called(); self.assertFalse(self.state.exists())

    def test_changed_original_review_does_not_change_the_frozen_run(self):
        self.prepare()
        binding = ReviewedBinding(self.profile, self.state, self.run_id)
        self.receipt['expires_at'] += 100; write(self.receipt_path, self.receipt)
        self.assertNotEqual(read(self.state / FILENAME), self.receipt)
        self.assertEqual(binding('testdb', 'pre')['status'], 'BOUND')
        self.assertEqual(binding('testdb', 'pre')['verification_mode'], 'OPERATOR_ATTESTED')
        self.assertEqual(binding('testdb', 'pre')['live_verification'], 'NOT_PERFORMED')

    def test_frozen_review_change_or_deletion_blocks_execution_and_report_integrity(self):
        self.prepare()
        binding = ReviewedBinding(self.profile, self.state, self.run_id)
        modified = copy.deepcopy(self.receipt); modified['reviewer'] = 'other-reviewer'
        write(self.state / FILENAME, modified)
        with self.assertRaises(ConfigError): binding.validate()
        self.assertEqual(build_report(self.profile, self.state)['integrity']['status'], 'BLOCKED')
        (self.state / FILENAME).unlink()
        with self.assertRaises(RunStateError): binding.validate()
        self.assertEqual(build_report(self.profile, self.state)['observer_binding']['verification_mode'], 'INVALID')

    def test_no_silent_receipt_replacement_in_existing_or_partial_run(self):
        self.state.mkdir(); write(self.state / FILENAME, self.receipt)
        before = (self.state / FILENAME).read_bytes()
        with self.assertRaises(ConfigError):
            prepare_run(self.profile, self.state, self.run_id, observer_binding=self.receipt_path)
        self.assertEqual((self.state / FILENAME).read_bytes(), before)
        self.assertFalse((self.state / 'run.json').exists())

    def test_nonregular_duplicate_json_and_symlink_receipts_are_rejected(self):
        self.receipt_path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(RunStateError): read(self.receipt_path)
        self.receipt_path.unlink(); self.receipt_path.symlink_to(self.profile_path)
        with self.assertRaises(RunStateError):
            prepare_run(self.profile, self.state, self.run_id, observer_binding=self.receipt_path)
        self.assertFalse(self.state.exists())

    def test_each_mcp_operation_rechecks_expiry_without_invoking_raw_client(self):
        self.prepare(); binding = ReviewedBinding(self.profile, self.state, self.run_id)
        raw = Mock(); client = ReviewedMcpClient(raw, binding)
        plan = _make_plan('group.persisted', 'owned', 'testdb', 'pre')
        calls = [client.db_list, lambda: client.table_list('testdb'),
                 lambda: client.table_describe('testdb', 'bcs_groups'), lambda: client.db_query(plan)]
        with patch('bcs_e2e.remote_observer.time.time', return_value=self.receipt['expires_at']):
            for call in calls:
                with self.assertRaises(ConfigError): call()
        self.assertEqual(raw.mock_calls, [])

    def test_inflight_expiry_rejects_every_successful_mcp_response(self):
        self.prepare()
        plan = _make_plan('group.persisted', 'owned', 'testdb', 'pre')
        for method, args in (('db_list', ()), ('table_list', ('testdb',)),
                             ('table_describe', ('testdb', 'bcs_groups')), ('db_query', (plan,))):
            with self.subTest(method=method):
                binding = ReviewedBinding(self.profile, self.state, self.run_id)
                raw = Mock()
                client = ReviewedMcpClient(raw, binding)
                with patch('bcs_e2e.remote_observer.time.time', return_value=self.receipt['reviewed_at'] + 1) as clock:
                    def expire(*_args):
                        clock.return_value = self.receipt['expires_at']
                        return {'success': True, 'data': []}
                    getattr(raw, method).side_effect = expire
                    with self.assertRaises(ConfigError): getattr(client, method)(*args)
                self.assertEqual(len(raw.mock_calls), 1)

    def test_inflight_run_rebinding_rejects_resource_response(self):
        self.prepare()
        binding = ReviewedBinding(self.profile, self.state, self.run_id)
        raw = Mock()
        client = ReviewedMcpClient(raw, binding)
        def rebind(_plan):
            state = read(self.state / 'run.json')
            state['run_id'] = 'different-run'
            write(self.state / 'run.json', state)
            return {'success': True, 'data': []}
        raw.db_query.side_effect = rebind
        with self.assertRaises(ConfigError):
            client.db_query(_make_plan('group.persisted', 'owned', 'testdb', 'pre'))
        raw.db_query.assert_called_once()

    def test_inflight_resource_expiry_preserves_api_result_but_blocks_qualification(self):
        self.prepare()
        with patch('bcs_e2e.remote_observer.time.time', return_value=self.receipt['reviewed_at'] + 1) as clock:
            def expire(command, **kwargs):
                result = self.process(command, **kwargs)
                if command[2] == 'test-mcp.db_query':
                    clock.return_value = self.receipt['expires_at']
                return result
            with self.composed(runner=expire):
                rc, report = self.command(self.run_args())
        self.assertEqual(rc, 2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['functional'], 'PASS')
        self.assertEqual(report['cleanup'], 'PASS')
        self.assertEqual(report['evidence_status'], 'BLOCKED')
        self.assertEqual(sum(call[2] == 'test-mcp.db_query' for call in self.calls), 1)
        self.assertTrue(Path(read(self.state / 'run.json')['lock_path']).exists())

    def test_query_database_environment_and_run_rebinding_are_refused(self):
        self.prepare(); binding = ReviewedBinding(self.profile, self.state, self.run_id)
        raw = Mock(); client = ReviewedMcpClient(raw, binding)
        with self.assertRaises(ConfigError): client.table_list('otherdb')
        with self.assertRaises(ConfigError): client.db_query(_make_plan('group.persisted', 'owned', 'testdb', 'prod'))
        with self.assertRaises(ConfigError): binding('otherdb', 'pre')
        state = read(self.state / 'run.json'); state['run_id'] = 'another-run'; write(self.state / 'run.json', state)
        with self.assertRaises(ConfigError): client.db_list()
        self.assertEqual(raw.mock_calls, [])

    def test_executable_is_explicit_and_validated_before_client_creation(self):
        self.prepare()
        for path in (Path('mcporter'), self.root / 'missing', self.root, self.profile_path):
            with self.subTest(path=path), patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client:
                with self.assertRaises(ConfigError):
                    remote_dependencies(self.profile, self.state, self.run_id, executable=path, allow_read=True)
                client.assert_not_called()

    def test_bad_mcp_admission_blocks_before_intents_and_never_attempts_http(self):
        self.prepare()
        def fail(command, **kwargs):
            self.calls.append(command)
            return subprocess.CompletedProcess(command, 1, '', 'private-mcp-error')
        with self.composed(runner=fail):
            rc, result = self.command(self.run_args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertNotIn('private-mcp-error', json.dumps(result))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.adapter.groups, {})
        with Ledger(self.state / 'run.sqlite', self.run_id, fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual(ledger.resource_snapshot(), [])

    def test_case_report_must_preserve_frozen_binding_provenance(self):
        self.prepare()
        with self.composed(): self.assertEqual(self.command(self.run_args())[0], 0)
        report = read(self.state / 'report-PRE-01.json'); report.pop('observer_binding')
        write(self.state / 'report-PRE-01.json', report)
        summary = build_report(self.profile, self.state)
        self.assertEqual(summary['integrity']['status'], 'BLOCKED')
        self.assertEqual(summary['cases'][0]['status'], 'BLOCKED')

    def test_receipt_expiry_after_admission_blocks_rows_but_preserves_api_cleanup(self):
        self.prepare()
        describes = 0
        now = self.receipt['reviewed_at'] + 1
        with patch('bcs_e2e.remote_observer.time.time', return_value=now) as clock:
            def expire(command, **kwargs):
                nonlocal describes
                result = self.process(command, **kwargs)
                if command[2] == 'test-mcp.table_describe':
                    describes += 1
                    # Admission describes once; observation describes again.
                    if describes == 2:
                        clock.return_value = self.receipt['expires_at']
                return result
            with self.composed(runner=expire):
                rc, report = self.command(self.run_args())
        self.assertEqual(rc, 2, report)
        self.assertEqual(report['functional'], 'PASS')
        self.assertEqual(report['cleanup'], 'PASS')
        self.assertEqual(report['evidence_status'], 'BLOCKED')
        self.assertFalse(any(call[2] == 'test-mcp.db_query' for call in self.calls))
        self.assertTrue(Path(read(self.state / 'run.json')['lock_path']).exists())
        with Ledger(self.state / 'run.sqlite', self.run_id, fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual({r['state'] for r in ledger.resource_snapshot()}, {'deleted'})

    def test_mcp_cancellation_preserves_quarantine_without_retry(self):
        self.prepare()
        def cancel(command, **kwargs):
            self.calls.append(command)
            try:
                raise KeyboardInterrupt()
            finally:
                raise OSError('private-mcp-secondary')
        with self.composed(runner=cancel):
            rc, result = self.command(self.run_args())
        self.assertEqual(rc, 130, result)
        self.assertNotIn('private-mcp-secondary', json.dumps(result))
        self.assertEqual(len(self.calls), 1)
        state = read(self.state / 'run.json')
        self.assertEqual(state['status'], 'QUARANTINED')
        self.assertTrue(Path(state['lock_path']).exists())
        self.assertEqual(self.adapter.groups, {})

    def test_frozen_integrity_is_rechecked_after_client_construction(self):
        self.prepare(); binding = ReviewedBinding(self.profile, self.state, self.run_id)
        raw = Mock(); client = ReviewedMcpClient(raw, binding)
        changed = copy.deepcopy(self.receipt); changed['expires_at'] += 1000
        write(self.state / FILENAME, changed)
        state = read(self.state / 'run.json'); state['observer_binding'] = binding_summary(changed)
        write(self.state / 'run.json', state)
        with self.assertRaises(ConfigError): client.db_list()
        raw.db_list.assert_not_called()


    def test_mcp_budget_below_client_minimum_is_structured_blocker_before_prepare(self):
        self.profile['policy']['max_response_bytes'] = 512
        write(self.profile_path, self.profile)
        self.receipt['profile_digest'] = fingerprint(self.profile)
        write(self.receipt_path, self.receipt)
        with patch('bcs_e2e.prepare.PoolLease.acquire') as acquire, \
             patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client:
            rc, result = self.command(['prepare', '--profile', str(self.profile_path), '--run-id', self.run_id,
                '--state-dir', str(self.state), '--observer-binding', str(self.receipt_path)])
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('max_response_bytes', result['reason'])
        acquire.assert_not_called(); client.assert_not_called()
        self.assertFalse(self.state.exists())
