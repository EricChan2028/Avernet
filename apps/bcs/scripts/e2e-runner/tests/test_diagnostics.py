from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.diagnostics import collect_log_diagnostics
from bcs_e2e.ledger import Ledger


class DiagnosticCollectorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ledger = Ledger(Path(tmp.name) / 'run.sqlite', 'run', 'a' * 64, create=True)
        self.addCleanup(self.ledger.close)
        for number in (1, 2):
            self.ledger.record_correlation('PRE-01', 1, {
                'operation': 'group.read', 'submitted_request_id': f'client-{number}',
                'response_request_id': f'server-{number}',
            })
        self.observer = Mock()
        self.observer.observe.side_effect = lambda query, **_: {
            'status': 'OBSERVED', 'source': 'logs', 'query': query.public(),
            'record_count': 1, 'truncated': False,
        }

    def collect(self, **kwargs):
        return collect_log_diagnostics(self.ledger, self.observer, case_id='PRE-01', attempt=1,
                                       environment='pre', start_epoch=100, end_epoch=120, **kwargs)

    def test_prefers_actual_response_id_and_keeps_case_scope(self):
        self.ledger.record_correlation('PRE-02', 1, {
            'operation': 'group.read', 'submitted_request_id': 'another-case',
        })
        result = self.collect()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual([c.args[0].request_id for c in self.observer.observe.call_args_list],
                         ['server-1', 'server-2'])
        self.assertTrue(all(c.kwargs['timeout_seconds'] <= 5
                            for c in self.observer.observe.call_args_list))

    def test_invalid_or_foreign_result_is_blocked_and_never_stored_as_observed(self):
        for reply in (None, {}, {'status': 'OBSERVED', 'source': 'logs', 'query': {'environment': 'prod'}}):
            self.observer.observe.side_effect = None
            self.observer.observe.return_value = reply
            result = self.collect()
            self.assertEqual(result['status'], 'BLOCKED')
        for row in self.ledger.diagnostics():
            self.assertEqual(row['environment'], 'pre')
            self.assertEqual(row['status'], 'BLOCKED')

    def test_backend_exception_is_sanitized(self):
        self.observer.observe.side_effect = RuntimeError('private-backend-credential')
        result = self.collect()
        self.assertEqual(result['blocked'], 2)
        self.assertNotIn('private-backend-credential', str(result) + str(self.ledger.diagnostics()))

    def test_query_and_time_budgets_are_explicit(self):
        result = self.collect(max_queries=1)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertTrue(result['budget_exhausted'])
        self.observer.reset_mock()
        with patch('bcs_e2e.diagnostics.time.monotonic', side_effect=[0, 0, 31, 31]):
            result = self.collect(timeout_seconds=30)
        self.assertEqual(result['queried'], 1)
        self.assertTrue(result['time_exhausted'])
        self.assertEqual(self.observer.observe.call_count, 1)

    def test_interrupt_is_not_converted_to_diagnostic_warning(self):
        self.observer.observe.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
