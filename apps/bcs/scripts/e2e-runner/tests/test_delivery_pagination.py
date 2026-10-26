"""Keyset traversal uses real SQLite queries and reviewed CLI composition."""
import copy
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.delivery_pagination import CONTRACT, collect, query_arguments, validate_limits
from bcs_e2e.delivery_observation import DeliveryReadPlan, FIELDS, WAIT_CONTRACT
from bcs_e2e.evidence import EvidenceError
from bcs_e2e.profile import ConfigError
from bcs_e2e.retained_remote import validate_review, validate_observation_permission, RetainedMcpClient
from bcs_e2e.run_state import write
import test_delivery_observation as delivery
import test_retained_recheck as recheck

LIMITS = {'contract': CONTRACT, 'page_size': 100, 'max_pages': 4, 'max_rows': 300}


class KeysetTests(unittest.TestCase):
    def setUp(self):
        self.plan = DeliveryReadPlan('testdb', 'pre', 'g-1', 's-1', ('bot-1', 'bot-2'))
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE bcs_message_deliveries (id INTEGER PRIMARY KEY, ' +
                        ','.join(FIELDS) + ')')
        self.progress, self.calls = {}, []

    def seed(self, count, **extra):
        for index in range(count):
            row = delivery.row(self.plan, delivery_id=f'd-{index:04}', **extra)
            self.db.execute('INSERT INTO bcs_message_deliveries (' + ','.join(FIELDS) +
                            ') VALUES (' + ','.join('?' for _ in FIELDS) + ')', tuple(row[k] for k in FIELDS))

    def fetch(self, cursor, limit):
        args = query_arguments(self.plan, after_id=cursor, limit=limit)
        self.calls.append(args)
        rows = self.db.execute('SELECT ' + args['select'] + ' FROM ' + args['table'] +
                              ' WHERE ' + args['where'] + ' ORDER BY ' + args['orderby'] +
                              ' LIMIT ' + args['limit']).fetchall()
        return {'success': True, 'data': [dict(row) for row in rows]}

    def scan(self, limits=None, fetch=None):
        return collect(self.plan, limits or LIMITS, fetch or self.fetch,
                       deadline=time.monotonic() + 10, progress=self.progress)

    def test_real_sqlite_three_pages_complete_without_offset_or_foreign_rows(self):
        self.seed(205)
        self.seed(1, env='prod')
        self.seed(1, group_id='foreign')
        self.seed(1, session_id='foreign')
        self.seed(1, target_bot_id='foreign')
        rows = self.scan()
        self.assertEqual(len(rows), 205)
        self.assertEqual(self.progress['pages'], 3)
        self.assertEqual(self.progress['status'], 'COMPLETE')
        self.assertEqual(self.progress['consistency'], 'NON_ATOMIC_TRAVERSAL')
        self.assertEqual(self.progress['scope_status'], 'UNCONFIRMED')
        self.assertEqual([r['delivery_id'] for r in rows], [f'd-{n:04}' for n in range(205)])
        self.assertTrue(all('id' not in row for row in rows))
        self.assertTrue(all('offset' not in args and args['orderby'] == 'id asc' for args in self.calls))
        self.assertTrue(self.calls[-1]['where'].endswith('id > 200'))

    def test_exact_full_page_needs_terminal_probe_even_at_aggregate_cap(self):
        self.seed(100)
        self.assertEqual(len(self.scan({**LIMITS, 'max_rows': 100})), 100)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[-1]['limit'], '1')
        self.assertEqual(self.progress['status'], 'COMPLETE')

    def test_page_and_row_exhaustion_never_return_partial_collection(self):
        self.seed(101)
        for limits, reason in (({**LIMITS, 'max_pages': 1}, 'PAGE_LIMIT'),
                               ({**LIMITS, 'max_rows': 100}, 'ROW_LIMIT')):
            self.calls.clear()
            with self.assertRaises(EvidenceError): self.scan(limits)
            self.assertEqual(self.progress['status'], 'INCOMPLETE')
            self.assertEqual(self.progress['reason'], reason)
            self.assertEqual(self.progress['rows'], 100)
            self.assertLessEqual(len(self.calls), 2)

    def test_falsey_truncation_bad_ids_duplicates_foreign_and_oversized_pages_rejected(self):
        good = {'id': 1, **delivery.row(self.plan)}
        invalid = [{'success': False, 'data': []},
                   *({'success': True, 'data': [good], 'truncated': flag}
                     for flag in (True, 1, 0, None, 'false', [])),
                   *({'success': True, 'data': [{**good, 'id': value}]}
                     for value in (True, 0, -1, '1', 1.0, 1 << 64)),
                   {'success': True, 'data': [good, good]},
                   {'success': True, 'data': [good, {**good, 'id': 2}]},
                   {'success': True, 'data': [{**good, 'env': 'prod'}]},
                   {'success': True, 'data': [good] * 101}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises((EvidenceError, ConfigError)):
                self.scan(fetch=lambda *_: payload)
            self.assertEqual(self.progress['status'], 'INCOMPLETE')

    def test_cross_page_repetition_or_cursor_regression_is_rejected(self):
        good = {'id': 1, **delivery.row(self.plan)}
        for second in (good, {**good, 'id': 2}):
            fetch = Mock(side_effect=[{'success': True, 'data': [good]}, {'success': True, 'data': [second]}])
            with self.assertRaises(EvidenceError): self.scan({**LIMITS, 'page_size': 1}, fetch)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(self.progress['rows'], 1)

    def test_late_page_and_cancellation_do_not_return_rows(self):
        with patch('bcs_e2e.delivery_pagination.time.monotonic', side_effect=[0, 11]):
            fetch = Mock(return_value={'success': True, 'data': []})
            with self.assertRaises(TimeoutError):
                collect(self.plan, LIMITS, fetch, deadline=10, progress=self.progress)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.progress['status'], 'INCOMPLETE')
        with self.assertRaises(KeyboardInterrupt): self.scan(fetch=Mock(side_effect=KeyboardInterrupt()))
        self.assertEqual(self.progress['status'], 'INCOMPLETE')

    def test_strict_configuration_and_sql_cursor_boundaries(self):
        for field, values in (('page_size', (0, 101, True, 1.0)), ('max_pages', (0, 9, None)),
                              ('max_rows', (0, 501, '100'))):
            for value in values:
                with self.assertRaises(ConfigError): validate_limits({**LIMITS, field: value})
        for cursor in (-1, True, '0 OR 1=1', 1 << 64):
            with self.assertRaises(EvidenceError): query_arguments(self.plan, after_id=cursor, limit=100)
        for limit in (0, 101, True):
            with self.assertRaises(EvidenceError): query_arguments(self.plan, after_id=0, limit=limit)
        for value in (None, {}, {**LIMITS, 'extra': 1}, {**LIMITS, 'contract': 'unknown'}):
            with self.assertRaises(ConfigError): validate_limits(value)


class KeysetRecheckTests(unittest.TestCase):
    setUp = recheck.RetainedRecheckTests.setUp
    command = recheck.RetainedRecheckTests.command
    boundaries = recheck.RetainedRecheckTests.boundaries
    args = recheck.RetainedRecheckTests.args
    execute = recheck.RetainedRecheckTests.execute
    assert_original_unchanged = recheck.RetainedRecheckTests.assert_original_unchanged

    def process(self, command, **kwargs):
        args = json.loads(command[4]) if '--args' in command else {}
        if getattr(self, 'pagination_enabled', False) and args.get('table') == delivery.TABLE:
            if command[2].endswith('.table_describe'):
                self.calls.append(command)
                fields = FIELDS if getattr(self, 'missing_cursor_schema', False) else ('id', *FIELDS)
                ddl = 'CREATE TABLE `'+delivery.TABLE+'` ('+','.join('`'+f+'` bigint' for f in fields)+')'
                return subprocess.CompletedProcess(command, 0, json.dumps({'success': True, 'data': [
                    {'Table': delivery.TABLE, 'Create Table': ddl}]}), '')
            if command[2].endswith('.db_query') and args.get('select', '').startswith('id,'):
                self.calls.append(command)
                match = re.fullmatch(r"env = 'pre' AND group_id = '([A-Za-z0-9_.:@-]+)' AND session_id = '([A-Za-z0-9_.:@-]+)' AND target_bot_id IN \('test-driver','test-member'\) AND id > ([0-9]+)", args['where'])
                self.assertIsNotNone(match)
                self.assertEqual(args['orderby'], 'id asc')
                self.assertEqual(args['select'], 'id,' + ','.join(FIELDS))
                plan = DeliveryReadPlan('testdb', 'pre', match[1], match[2], ('test-driver', 'test-member'))
                cursor, limit = int(match[3]), int(args['limit'])
                self.page_calls.append((cursor, limit))
                if getattr(self, 'cancel_page', None) == len(self.page_calls): raise KeyboardInterrupt()
                if getattr(self, 'change_authority', False):
                    changed = copy.deepcopy(self.recheck); changed['delivery_pagination']['max_rows'] += 1
                    write(self.recheck_path, changed)
                rows = [{'id': n, **delivery.row(plan, target_bot_id='test-driver', delivery_id=f'd-{n:04}')}
                        for n in range(cursor + 1, min(self.row_count, cursor + limit) + 1)]
                return subprocess.CompletedProcess(command, 0, json.dumps({'success': True, 'data': rows}), '')
        return recheck.RetainedRecheckTests.process(self, command, **kwargs)

    def enable(self, *, count=205, limits=None):
        self.pagination_enabled = True
        self.row_count = count
        self.page_calls = []
        self.recheck['delivery_pagination'] = limits or dict(LIMITS)
        write(self.recheck_path, self.recheck)

    def test_cli_recheck_traverses_three_pages_without_original_mutation_or_release(self):
        self.enable()
        rc, result = self.execute()
        self.assertEqual(rc, 2)
        observation = result['execution_observation']
        self.assertEqual(observation['status'], 'OBSERVED', result)
        self.assertEqual(observation['sessions'][0]['row_count'], 205)
        self.assertEqual(observation['queries'], 3)
        self.assertEqual(len(self.page_calls), 3)
        self.assertEqual(observation['delivery_pagination']['traversals'][0]['status'], 'COMPLETE')
        self.assertFalse(result['pool_release_authorized'])
        self.assertEqual(result['execution_status'], 'UNCONFIRMED')
        self.assert_original_unchanged()

    def test_full_last_page_and_shared_call_budget_exhaustion_are_not_complete(self):
        self.enable(limits={**LIMITS, 'max_pages': 1})
        _, result = self.execute()
        observation = result['execution_observation']
        self.assertEqual(observation['status'], 'BLOCKED')
        self.assertEqual(observation['sessions'], [])
        self.assertEqual(observation['delivery_pagination']['traversals'][0]['reason'], 'PAGE_LIMIT')
        self.assertEqual(len(self.page_calls), 1)
        self.assert_original_unchanged()

    def test_shared_phase_budget_debits_every_page(self):
        self.enable()
        original = RetainedMcpClient.__init__
        def limited(client, *args):
            original(client, *args)
            client._remaining = 3  # One describe, then only two page calls.
        with patch.object(RetainedMcpClient, '__init__', limited):
            _, result = self.execute()
        observation = result['execution_observation']
        self.assertEqual(observation['status'], 'BLOCKED')
        self.assertEqual(len(self.page_calls), 2)
        self.assertEqual(observation['queries'], 2)
        self.assertEqual(observation['sessions'], [])
        self.assert_original_unchanged()

    def test_midpage_authority_change_discards_result_and_does_not_continue(self):
        self.enable(); self.change_authority = True
        _, result = self.execute()
        self.assertEqual(result['execution_observation']['status'], 'BLOCKED')
        self.assertEqual(len(self.page_calls), 1)
        self.assertEqual(result['execution_observation']['sessions'], [])
        self.assert_original_unchanged()

    def test_cancellation_preserves_partial_counts_but_not_alias_authority(self):
        self.enable(); self.cancel_page = 2
        _, result = self.execute()
        self.assertEqual(result['status'], 'CANCELLED')
        observation = result['execution_observation']
        self.assertEqual(observation['sessions'], [])
        self.assertEqual(observation['delivery_pagination']['traversals'][0]['rows'], 100)
        self.assertFalse(result['pool_release_authorized'])
        self.assert_original_unchanged()

    def test_final_authority_rejection_invalidates_complete_traversal_and_alias_cache(self):
        from bcs_e2e.remote_observer import ReviewedMcpClient
        self.enable(count=1)
        original_scan = ReviewedMcpClient.delivery_scan
        cache_after = []
        def scan(client, plan, **kwargs):
            client._delivery_evidence[plan] = ('stale', [])
            try:
                return original_scan(client, plan, **kwargs)
            finally:
                cache_after.append(plan in client._delivery_evidence)
        def revoke_after_collection(*args, **kwargs):
            rows = collect(*args, **kwargs)
            changed = copy.deepcopy(self.recheck)
            changed['delivery_pagination']['max_rows'] += 1
            write(self.recheck_path, changed)
            return rows
        with patch.object(ReviewedMcpClient, 'delivery_scan', scan), \
             patch('bcs_e2e.delivery_pagination.collect', side_effect=revoke_after_collection):
            _, result = self.execute()
        observation = result['execution_observation']
        self.assertEqual(observation['status'], 'BLOCKED')
        traversal = observation['delivery_pagination']['traversals'][0]
        self.assertEqual(traversal['status'], 'INCOMPLETE')
        self.assertEqual(traversal['reason'], 'POST_SCAN_VALIDATION_FAILED')
        self.assertEqual(cache_after, [False])
        self.assertEqual(observation['sessions'], [])
        self.assert_original_unchanged()

    def test_only_complete_authorized_scan_publishes_alias_evidence(self):
        from bcs_e2e.remote_observer import ReviewedMcpClient
        self.enable(count=101, limits={**LIMITS, 'max_pages': 1})
        original = ReviewedMcpClient.delivery_scan
        caches = []
        def scan(client, plan, **kwargs):
            client._delivery_evidence[plan] = ('stale', [])
            try:
                return original(client, plan, **kwargs)
            finally:
                caches.append(client._delivery_evidence.get(plan))
        with patch.object(ReviewedMcpClient, 'delivery_scan', scan):
            _, result = self.execute()
        self.assertEqual(caches, [None])
        self.assertEqual(result['execution_observation']['status'], 'BLOCKED')
        self.assert_original_unchanged()

    def test_missing_integer_cursor_column_blocks_before_any_page(self):
        self.enable(); self.missing_cursor_schema = True
        _, result = self.execute()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(self.page_calls, [])
        self.assert_original_unchanged()

    def test_persistent_mapping_read_plan_uses_same_page_scope_and_budget(self):
        from bcs_e2e.recheck_review import recheck_plan
        from test_mapping_recheck import confirmation
        mapping = self.root / 'mapping.json'
        write(mapping, confirmation(self.profile))
        self.recheck = recheck_plan(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01',
                                   mapping_confirmation=mapping)['read_plan']
        self.recheck['observation_id'] = 'keyset-mapped'
        self.enable()
        args = ['retained-recheck', '--profile', str(self.profile_path), '--state-dir', str(self.state),
                '--run-id', self.run_id, '--case', 'RETAIN-GROUP-01', '--read-plan', str(self.recheck_path),
                '--mapping-confirmation', str(mapping), '--mcporter-binary', str(self.executable),
                '--allow-observer-read', '--confirm-run-id', self.run_id]
        with patch.object(self, 'args', return_value=args):
            _, result = self.execute()
        self.assertEqual(result['execution_observation']['status'], 'OBSERVED', result)
        self.assertEqual(result['execution_observation']['queries'], 3)
        self.assertEqual(result['read_authorization_basis'], 'EXPLICIT_COMMAND_FLAGS_AND_SOURCE_PLAN')
        self.assert_original_unchanged()

    def test_old_business_review_cannot_implicitly_enable_pagination(self):
        changed = {**self.review, 'delivery_pagination': LIMITS}
        with self.assertRaises(ConfigError):
            validate_review(changed, self.profile, self.run_id, 'RETAIN-GROUP-01')
        for contract in (delivery.CONTRACT, WAIT_CONTRACT):
            with self.assertRaises(ConfigError):
                validate_observation_permission({'execution_observation': contract, 'delivery_pagination': LIMITS}, self.profile)
