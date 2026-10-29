"""Quota amendment through the existing guarded File composition; synthetic only."""
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.profile import fingerprint, ConfigError
from bcs_e2e.pool import pool_path, pool_scope
from bcs_e2e.run_state import read, write
from bcs_e2e.reporting import build_report
from bcs_e2e.retained_reporting import _quota_v2
import test_retained_file_remote as fixtures


class GroupIncreaseReportTests(unittest.TestCase):
    setUp = fixtures.RetainedFileRemoteTests.setUp
    command = fixtures.RetainedFileRemoteTests.command
    process = fixtures.RetainedFileRemoteTests.process
    args = fixtures.RetainedFileRemoteTests.args
    quota = fixtures.RetainedFileRemoteTests.quota
    signed_target = fixtures.RetainedFileRemoteTests.signed_target
    boundaries = fixtures.RetainedFileRemoteTests.boundaries

    def first_run(self):
        self.review['retention_quota']['baseline_groups'] = 9
        write(self.review_path, self.review)
        with self.boundaries():
            rc, result = self.command(self.args())
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['retention_quota']['accounted_after']['groups'], 10)
        self.previous = self.state
        self.prior_files = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        self.journal = pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json')
        self.prior = read(self.journal)
        self.prior_pool = pool_path(pool_scope(self.profile)).read_bytes()
        self.state = self.root / 'increased'
        self.run_id = 'increased'
        self.file_calls.clear()
        self.http.clear()
        now = int(time.time())
        amendment = {'contract': 'retained-group-limit-increase-v1',
            'run_id': self.run_id, 'profile_digest': fingerprint(self.profile),
            'supervisor': self.profile['human']['user_id'],
            'reviewed_at': now - 1, 'expires_at': now + 1800,
            'reference': 'approved-one-group', 'evidence_sha256': 'c' * 64,
            'previous_max_groups': 10, 'new_max_groups': 11}
        self.review['run_id'] = self.run_id
        self.review['retention_quota'].update(mode='raise-group-limit', max_groups=11,
            expected_digest=fingerprint(self.prior), group_limit_change=amendment)
        write(self.review_path, self.review)

    def second(self):
        with self.boundaries():
            return self.command(self.args() + ['--previous-state-dir', str(self.previous)])

    def assert_prior_unchanged(self):
        self.assertEqual(self.prior_files, {p: p.read_bytes() for p in self.previous.rglob('*') if p.is_file()})

    def test_guarded_second_run_and_offline_reporting_preserve_prior_limits(self):
        self.first_run()
        rc, result = self.second()
        self.assertEqual(rc, 0, result)
        q = result['retention_quota']
        self.assertEqual(q['limits']['groups'], 11)
        self.assertEqual(q['accounted_before']['groups'], 10)
        self.assertEqual(q['accounted_after']['groups'], 11)
        self.assertEqual(q['journal_snapshot']['previous_v2'], self.prior)
        self.assertEqual(q['previous_journal_digest'], fingerprint(self.prior))
        self.assertEqual(q['journal_snapshot']['previous_v2']['policy']['max_groups'], 10)
        self.assert_prior_unchanged()
        before = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        with patch('time.time', return_value=self.review['expires_at'] + 100):
            self.assertEqual(build_report(self.profile, self.state)['status'], 'RETAINED_CASE_PASS')
            self.assertEqual(build_report(self.profile, self.previous)['status'], 'RETAINED_CASE_PASS')
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()})
        self.assert_prior_unchanged()

    def test_missing_or_substituted_transition_bindings_cannot_qualify(self):
        self.first_run()
        rc, result = self.second()
        self.assertEqual(rc, 0, result)
        original = result['retention_quota']
        for edit in ('missing-history', 'missing-change-binding', 'changed-old-max',
                     'changed-receipt', 'wrong-previous-digest'):
            q = copy.deepcopy(original)
            if edit == 'missing-history': q['journal_snapshot'].pop('previous_v2')
            elif edit == 'missing-change-binding': q['source_binding'].pop('group_limit_change_digest')
            elif edit == 'changed-old-max': q['journal_snapshot']['previous_v2']['policy']['max_groups'] = 11
            elif edit == 'changed-receipt': q['journal_snapshot']['group_limit_change']['new_max_groups'] = 12
            else: q['previous_journal_digest'] = '0' * 64
            with self.subTest(edit=edit), self.assertRaises(ConfigError):
                _quota_v2(q, {'run_id': self.run_id}, self.profile, 'RETAIN-FILE-01')

    def test_failed_preflight_does_not_apply_approved_amendment(self):
        self.first_run()
        self.anonymous_route_blocked = True
        rc, result = self.second()
        self.assertEqual(rc, 2)
        self.assertFalse(result['prepared'])
        self.assertEqual(read(self.journal), self.prior)
        self.assertEqual(pool_path(pool_scope(self.profile)).read_bytes(), self.prior_pool)
        self.assertFalse(any(kind == 'write' for kind, _ in self.http))
        self.assert_prior_unchanged()

    def test_failure_after_creation_keeps_new_limit_reservation_and_no_deletion(self):
        self.first_run()
        self.lose_operation = 'prepare'
        rc, result = self.second()
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['functional'], 'FAIL')
        self.assertEqual(result['cleanup'], 'RETAINED')
        self.assertEqual(result['retention_quota']['accounted_after']['groups'], 11)
        self.assertEqual(read(self.journal)['previous_v2'], self.prior)
        self.assertFalse(any('delete' in name for name in self.file_calls))
        self.assert_prior_unchanged()

    def test_invalid_amendment_identity_or_expiry_fails_before_guard_and_http(self):
        self.first_run()
        valid = copy.deepcopy(self.review)
        now = int(time.time())
        edits = ({'run_id': 'another-run'}, {'supervisor': 'another-owner'},
                 {'profile_digest': '0' * 64},
                 {'reviewed_at': now - 100, 'expires_at': now - 1})
        for edit in edits:
            review = copy.deepcopy(valid)
            review['retention_quota']['group_limit_change'].update(edit)
            write(self.review_path, review)
            with self.subTest(edit=edit), \
                 patch('bcs_e2e.retained_serial.PoolLease._guard') as guard, \
                 patch('bcs_e2e.transport.ReadClient.read') as http:
                rc, _ = self.command(self.args() + ['--previous-state-dir', str(self.previous)])
            self.assertEqual(rc, 2)
            guard.assert_not_called()
            http.assert_not_called()
            self.assertEqual(read(self.journal), self.prior)
            self.assertEqual(pool_path(pool_scope(self.profile)).read_bytes(), self.prior_pool)
        self.assert_prior_unchanged()
