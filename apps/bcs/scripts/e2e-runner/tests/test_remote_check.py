"""Reviewed preflight is metadata-only, does not prepare or mutate a Run."""
import contextlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.alisddbquery import McporterAlisdDbQueryClient
from bcs_e2e.evidence import EvidenceError, _make_plan
from bcs_e2e.remote_check import MetadataOnlyClient
from bcs_e2e.run_state import write
import test_remote_observer_cli as remote_fixture


class RemoteCheckTests(unittest.TestCase):
    # Reuse synthetic inputs/wire fixture, not the base TestCase's test methods.
    setUp = remote_fixture.RemoteObserverCliTests.setUp
    command = remote_fixture.RemoteObserverCliTests.command
    process = remote_fixture.RemoteObserverCliTests.process

    def args(self):
        return ['check', '--profile', str(self.profile_path), '--allow-read',
                '--observer-binding', str(self.receipt_path), '--mcporter-binary', str(self.executable),
                '--allow-observer-read']

    @contextlib.contextmanager
    def dependencies(self, runner=None, failed_http=None):
        self.http_calls = []
        http = Mock(); http.evidence = []
        def read(operation, bot_id=None):
            self.http_calls.append(operation)
            if operation == 'environment.read': data = {'env': 'pre'}
            elif operation == 'identity.read': data = {'user_id': 'test-owner'}
            else: data = {'bot_id': bot_id, 'kind': 'bot', 'created_by': 'test-owner', 'env': 'pre', 'status': 'online'}
            if operation == failed_http: data = {}
            return SimpleNamespace(data=data)
        http.read.side_effect = read
        def client(**kwargs):
            return McporterAlisdDbQueryClient(**kwargs, runner=runner or self.process)
        with patch('bcs_e2e.remote_check.ReadClient', return_value=http), \
             patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient', side_effect=client), \
             patch('bcs_e2e.pool.PoolLease.acquire', side_effect=AssertionError('preflight must not acquire pool')), \
             patch('bcs_e2e.pilot.adapter_for', side_effect=AssertionError('preflight must not execute Stories')), \
             patch('socket.create_connection', side_effect=AssertionError('no network in synthetic tests')):
            yield

    def test_combined_check_runs_four_http_reads_and_five_metadata_calls_without_run(self):
        before = sorted(p.relative_to(self.root) for p in self.root.rglob('*'))
        with self.dependencies():
            rc, result = self.command(self.args())
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'DISCOVERY_PASSED')
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(result['observer_preflight']['status'], 'METADATA_ADMITTED')
        self.assertEqual(result['observer_preflight']['business_queries'], 0)
        self.assertEqual(result['observer_preflight']['assertion_ids'],
                         ['group.persisted', 'session.persisted', 'file.persisted'])
        self.assertEqual(result['observer_binding']['verification_mode'], 'OPERATOR_ATTESTED')
        self.assertEqual(result['observer_binding']['live_verification'], 'NOT_PERFORMED')
        self.assertEqual(self.http_calls, ['environment.read', 'identity.read', 'bot.read', 'bot.read'])
        self.assertEqual([c[2] for c in self.calls], ['test-mcp.db_list', 'test-mcp.table_list',
            'test-mcp.table_describe', 'test-mcp.table_describe', 'test-mcp.table_describe'])
        self.assertEqual(sorted(p.relative_to(self.root) for p in self.root.rglob('*')), before)
        self.assertIn('OB business-row access and values', result['not_verified'])
        self.assertIn('write authorization', result['not_verified'])
        self.assertIn('BaaS and signed URL reachability', result['not_verified'])
        self.assertNotIn('private-deployment-ref', json.dumps(result))
        self.assertNotIn('private-reviewer', json.dumps(result))

    def test_bad_api_identity_environment_or_bot_never_triggers_metadata(self):
        for failed in ('environment.read', 'identity.read', 'bot.read'):
            with self.subTest(failed=failed), self.dependencies(failed_http=failed):
                rc, result = self.command(self.args())
            self.assertEqual(rc, 2)
            self.assertEqual(result['observer_preflight']['status'], 'NOT_RUN')
            self.assertEqual(result['observer_preflight']['business_queries'], 0)
            self.assertEqual(self.calls, [])

    def test_all_explicit_flags_required_before_http_and_mcp(self):
        for flag in ('--allow-read', '--allow-observer-read', '--observer-binding', '--mcporter-binary'):
            args = self.args(); i = args.index(flag)
            del args[i:i + (2 if flag in ('--observer-binding', '--mcporter-binary') else 1)]
            with self.subTest(flag=flag), patch('bcs_e2e.remote_check.ReadClient') as http, \
                 patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client:
                rc, _ = self.command(args)
            self.assertEqual(rc, 2); http.assert_not_called(); client.assert_not_called()

    def test_expired_review_is_rejected_before_any_http_or_mcp(self):
        with patch('bcs_e2e.remote_observer.time.time', return_value=self.receipt['expires_at']), \
             patch('bcs_e2e.remote_check.ReadClient') as http, \
             patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient') as client:
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['status'], 'BLOCKED')
        http.assert_not_called(); client.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_schema_failure_is_sanitized_and_cannot_pass(self):
        def response(command, **kwargs):
            result = self.process(command, **kwargs)
            if command[2] == 'test-mcp.table_describe':
                result.stdout = json.dumps({'success': False, 'message': 'private-schema-error'})
            return result
        with self.dependencies(runner=response):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual(result['observer_preflight']['status'], 'BLOCKED')
        self.assertEqual(result['observer_preflight']['business_queries'], 0)
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn('private-schema-error', json.dumps(result))
        self.assertEqual(result['checks'][-1]['status'], 'BLOCKED')

    def test_review_change_during_metadata_is_refused_before_next_call(self):
        def response(command, **kwargs):
            result = self.process(command, **kwargs)
            self.receipt['reviewer'] = 'changed-reviewer'
            write(self.receipt_path, self.receipt)
            return result
        with self.dependencies(runner=response):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual(result['observer_preflight']['status'], 'BLOCKED')
        self.assertEqual(len(self.calls), 1)

    def test_expiry_during_final_metadata_call_does_not_become_pass(self):
        with patch('bcs_e2e.remote_observer.time.time', return_value=self.receipt['reviewed_at'] + 1) as clock:
            def response(command, **kwargs):
                result = self.process(command, **kwargs)
                if len(self.calls) == 5: clock.return_value = self.receipt['expires_at']
                return result
            with self.dependencies(runner=response):
                rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual(result['observer_preflight']['status'], 'BLOCKED')
        self.assertEqual(len(self.calls), 5)

    def test_metadata_client_cannot_delegate_any_business_query(self):
        raw = Mock(); client = MetadataOnlyClient(raw)
        with self.assertRaises(EvidenceError):
            client.db_query(_make_plan('group.persisted', 'owned', 'testdb', 'pre'))
        self.assertEqual(raw.mock_calls, [])

    def test_provider_cannot_accidentally_query_rows_during_admission(self):
        from bcs_e2e.alisddbquery import AlisdDbQueryObserver
        def bad_admission(observer, assertions):
            return observer.client.db_query(_make_plan('group.persisted', 'operational', 'testdb', 'pre'))
        with self.dependencies(), patch.object(AlisdDbQueryObserver, 'admit', bad_admission):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['observer_preflight']['business_queries'], 0)
        self.assertEqual(self.calls, [])

    def test_chained_cancellation_is_not_converted_to_a_normal_metadata_failure(self):
        def response(command, **kwargs):
            self.calls.append(command)
            try: raise KeyboardInterrupt()
            finally: raise OSError('private-secondary-error')
        with self.dependencies(runner=response):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 130); self.assertEqual(result['status'], 'CANCELLED')
        self.assertNotIn('private-secondary-error', json.dumps(result))
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.state.exists())
