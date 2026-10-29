"""Offline checks that runtime observer admission precedes quota reservation."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.alisddbquery import AlisdDbQueryError, AlisdDbQueryObserver
from bcs_e2e.observation_gate import AdmittedObserver
from bcs_e2e.pool import pool_path, pool_scope
from bcs_e2e.retention_quota_v2 import COSTS
from bcs_e2e.run_state import read
import test_retained_file_remote as fixtures


class RetainedFileAdmissionQuotaOrderTests(fixtures.RetainedFileRemoteTests):
    command = fixtures.RetainedFileRemoteTests.command
    process = fixtures.RetainedFileRemoteTests.process

    def setUp(self):
        super().setUp()

    @property
    def quota_path(self):
        return pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json')

    def assert_no_business_or_intents(self):
        self.assertEqual(self.file_calls, [])
        self.assertEqual(self.adapter.groups, {})
        self.assertEqual(self.adapter.sessions, {})
        self.assertEqual(self.adapter.files, {})
        self.assertFalse(any(kind == 'write' for kind, _operation in self.http))

    def test_runtime_admission_failure_happens_before_quota_reservation(self):
        original = AlisdDbQueryObserver.admit
        admissions = []

        def fail_second_admission(observer, assertion_ids):
            admissions.append(tuple(assertion_ids))
            if len(admissions) == 2:
                raise AlisdDbQueryError('private synthetic runtime failure')
            return original(observer, assertion_ids)

        with self.boundaries(), patch.object(
                AlisdDbQueryObserver, 'admit', fail_second_admission):
            rc, result = self.command(self.args())

        self.assertEqual(rc, 2, result)
        self.assertEqual(len(admissions), 2)
        self.assertEqual(admissions[0], ('file.persisted',))
        self.assertEqual(admissions[1], ('file.persisted',))
        self.assertEqual((result['functional'], result['status']), ('NOT_RUN', 'BLOCKED'))
        self.assertNotIn('retention_quota', result)
        self.assertEqual(result['failure'].get('error_type'), 'ObserverAdmissionError')
        self.assertEqual(result['failure'].get('phase'), 'pre-effect-admission')
        self.assertEqual(result['failure'].get('diagnostic'), {
                'stage': 'observer_admission',
                'operation': 'unknown',
                'category': 'tool_failure',
        })
        self.assertFalse(self.quota_path.exists())
        state = read(self.state / 'run.json')
        # Observer admission is read-only and precedes execution. The failed Run
        # must remain a pre-execution handoff candidate, unlike a started Case.
        self.assertNotIn('attempted_cases', state)
        self.assertEqual(read(self.state / 'retention.json')['resources'], [])
        self.assert_no_business_or_intents()

    def test_success_admits_before_one_reservation_and_first_file_prepare(self):
        from bcs_e2e import retained as retained_module
        events = []
        original_observer_init = AdmittedObserver.__init__
        original_reserve = retained_module.reserve_retention_quota
        original_prepare = self.adapter.file_prepare

        def admitted(observer, *args, **kwargs):
            original_observer_init(observer, *args, **kwargs)
            events.append('observer-admit')

        def reserve(*args, **kwargs):
            events.append('reserve')
            return original_reserve(*args, **kwargs)

        def file_prepare(session_id, body):
            events.append('file.prepare')
            return original_prepare(session_id, body)

        with self.boundaries(), \
             patch.object(AdmittedObserver, '__init__', admitted), \
             patch.object(retained_module, 'reserve_retention_quota', side_effect=reserve), \
             patch.object(self.adapter, 'file_prepare', side_effect=file_prepare):
            rc, result = self.command(self.args())

        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(events[:3], ['observer-admit', 'reserve', 'file.prepare'])
        journal = read(self.quota_path)
        self.assertEqual(len(journal['reservations']), 1)
        self.assertEqual(journal['reservations'][0]['cost'], COSTS['RETAIN-FILE-01'])
        self.assertEqual(result['retention_quota']['cost'], COSTS['RETAIN-FILE-01'])
        self.assertFalse(result['retention_quota']['automatic_refund'])


if __name__ == '__main__':
    unittest.main()
