"""Offline review of retained Session/WS closure evidence."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.profile import ConfigError, fingerprint, load
from bcs_e2e.retained import retained_run
from bcs_e2e.retained_session_ws import (
    full_pre02_pair_review,
    retained_session_ws_pair_review,
)
from bcs_e2e.run_state import RunStateError, read, write


class RetainedSessionWsReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = load(ROOT / 'profiles/local.mock.json')
        patcher = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'registry')
        patcher.start()
        self.addCleanup(patcher.stop)

    def pair(self, first='first', second='second'):
        retained_run(
            self.profile, state_dir=self.root / first, run_id=first,
            case_id='RETAIN-SESSION-01', allow_write=True, confirm_run_id=first,
            serial_preflight=True,
        )
        retained_run(
            self.profile, state_dir=self.root / second, run_id=second,
            case_id='RETAIN-SESSION-01', allow_write=True, confirm_run_id=second,
            serial_preflight=True, previous_state_dir=self.root / first,
        )
        return self.root / first, self.root / second

    def report(self, path):
        case = 'RETAIN-SESSION-01'
        report_path = path / f'report-{case}.json'
        return report_path, read(report_path)

    def test_passing_retained_pair_projects_closure_not_full_pre02(self):
        first, second = self.pair()
        result = retained_session_ws_pair_review(self.profile, first, second)
        self.assertEqual(result['status'], 'RETAINED_SESSION_WS_PAIR_PASS')
        self.assertEqual(result['session_ws_binding_and_closure'], 'PASS')
        self.assertEqual(result['execution_closure'], 'UNCONFIRMED')
        self.assertEqual(result['cleanup'], 'RETAINED')
        self.assertEqual(result['full_pre02']['status'], 'NOT_QUALIFIED')
        self.assertFalse(result['grants_release_credit'])
        self.assertIn('full PRE-02 execution-effect convergence proof',
                      result['full_pre02']['missing'])
        self.assertIn('exact initial/explicit Session and Group cleanup',
                      result['full_pre02']['missing'])

    def test_repeated_run_directory_is_refused(self):
        first, _ = self.pair('first', 'first-copy')
        with self.assertRaisesRegex(ConfigError, 'distinct state directories'):
            retained_session_ws_pair_review(self.profile, first, first)

    def test_bad_close_evidence_is_refused(self):
        first, second = self.pair()
        path, report = self.report(second)
        close = next(row for row in report['evidence']
                     if row.get('operation') == 'session.ws.close')
        close['http_status'] = 101
        write(path, report)
        with self.assertRaisesRegex(ConfigError, 'protocol close frame'):
            retained_session_ws_pair_review(self.profile, first, second)

    def test_semantic_parent_binding_is_required(self):
        first, second = self.pair()
        path, report = self.report(second)
        session = next(row for row in report['semantic_evidence']
                       if row['plan']['assertion_id'] == 'session.persisted')
        session['rows'][0]['group_id'] = 'different-owned-group'
        write(path, report)
        with self.assertRaisesRegex(ConfigError, 'Session semantic parent differs'):
            retained_session_ws_pair_review(self.profile, first, second)

    def test_unredacted_token_query_is_refused(self):
        first, second = self.pair()
        path, report = self.report(second)
        connect = next(row for row in report['evidence']
                       if row.get('operation') == 'session.ws.connect')
        connect['path'] = '/mock/sessions/ws?token=synthetic-review-only'
        write(path, report)
        with self.assertRaisesRegex(ConfigError, 'raw WebSocket token query'):
            retained_session_ws_pair_review(self.profile, first, second)

    def test_serial_receipt_must_match_the_frozen_state(self):
        first, second = self.pair()
        receipt = read(second / 'serial-admission.json')
        receipt['review_note'] = 'changed-without-freeze'
        write(second / 'serial-admission.json', receipt)
        with self.assertRaisesRegex(ConfigError, 'serial receipt differs'):
            retained_session_ws_pair_review(self.profile, first, second)

    def test_retained_pair_cannot_prompt_full_pre02_review(self):
        first, second = self.pair()
        with self.assertRaises((ConfigError, RunStateError)):
            full_pre02_pair_review(self.profile, first, second)


if __name__ == '__main__':
    unittest.main()
