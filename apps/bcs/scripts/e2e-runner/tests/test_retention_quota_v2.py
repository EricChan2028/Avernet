"""Quota-v2 accounting tests; all fixtures, journals, and failures are local."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.pool import PoolBusyError, PoolLease, pool_path, pool_scope
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.retained_reporting import _quota, retained_markdown
from bcs_e2e.retention_quota import COSTS as V1_COSTS, reserve, validate_quota
from bcs_e2e.retention_quota_v2 import (CONTRACT, COSTS, DIMENSIONS,
    SOURCE_BINDING_CONTRACT, validate_journal)
from bcs_e2e.run_state import read, write
import test_retained_remote as fixtures


class FrozenReview:
    def __init__(self, value):
        self.value = copy.deepcopy(value)

    def validate(self):
        return copy.deepcopy(self.value)


class QuotaV2Tests(unittest.TestCase):
    setUp = fixtures.RetainedRemoteTests.setUp

    def journal(self):
        return pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json')

    def lease(self, run_id=None, profile=None):
        run_id, profile = run_id or self.run_id, profile or self.profile
        lease = PoolLease.acquire(pool_path(pool_scope(profile)), run_id, fingerprint(profile))
        self.addCleanup(lease.close)
        return lease

    def quota(self, **changes):
        value = {'contract': CONTRACT, 'registry_id': 'test-inventory',
            'mode': 'initialize', 'expected_digest': 'absent',
            'max_groups': 10, 'max_sessions': 20, 'max_files': 10,
            'max_uploaded_bytes': 40960, 'max_share_mints': 20,
            'baseline_groups': 0, 'baseline_sessions': 0, 'baseline_files': 0,
            'baseline_uploaded_bytes': 0, 'baseline_share_mints': 0,
            'inventory_reference': 'test-inventory-review', 'inventory_sha256': 'e' * 64,
            'file_inventory_reference': 'test-file-inventory-review',
            'file_inventory_sha256': 'f' * 64}
        value.update(changes)
        return value

    def permission(self, case_id='RETAIN-FILE-01', run_id=None, quota=None, profile=None):
        profile = profile or self.profile
        return {'schema_version': 1, 'contract': 'synthetic-retained-review-v1',
            'profile_digest': fingerprint(profile), 'run_id': run_id or self.run_id,
            'case_id': case_id, 'retention_quota': quota or self.quota()}

    def reserve(self, lease, case_id='RETAIN-FILE-01', run_id=None, quota=None, profile=None):
        permission = self.permission(case_id, run_id, quota, profile)
        return reserve(profile or self.profile, FrozenReview(permission), lease, case_id)

    def next_lease(self, lease, run_id, profile=None):
        lease.release()
        return self.lease(run_id, profile)

    def install_v1(self, lease, case_id='RETAIN-GROUP-01'):
        quota = copy.deepcopy(self.review['retention_quota'])
        permission = self.permission(case_id, quota=quota)
        receipt = reserve(self.profile, FrozenReview(permission), lease, case_id)
        return receipt, copy.deepcopy(read(self.journal()))

    def test_strict_configuration_and_dispatch(self):
        self.assertEqual(validate_quota(self.quota())['contract'], CONTRACT)
        invalid = ({'max_files': True}, {'max_uploaded_bytes': 0}, {'max_share_mints': 10001},
            {'baseline_files': -1}, {'baseline_uploaded_bytes': 40961},
            {'baseline_share_mints': False}, {'file_inventory_reference': ''},
            {'file_inventory_sha256': 'bad'}, {'mode': 'reset'},
            {'mode': 'existing', 'expected_digest': 'absent'}, {'extra': 1})
        for edit in invalid:
            with self.subTest(edit=edit), self.assertRaises(ConfigError):
                validate_quota({**self.quota(), **edit})

    def test_fresh_file_reserves_exact_admission_maxima(self):
        result = self.reserve(self.lease())
        self.assertEqual(result['contract'], CONTRACT)
        self.assertEqual(result['cost'], {'groups': 1, 'sessions': 2, 'files': 1,
            'uploaded_bytes': 4096, 'share_mints': 2})
        self.assertEqual(result['accounted_before'], {key: 0 for key in DIMENSIONS})
        self.assertEqual(result['accounted_after'], result['cost'])
        self.assertEqual(result['source_binding']['contract'], SOURCE_BINDING_CONTRACT)
        self.assertEqual(result['physical_storage_usage'], 'NOT_MEASURED')
        self.assertEqual(read(self.journal())['reservations'], [result['reservation']])

    def test_existing_v2_accumulates_and_group_session_costs_keep_zero_file_dimensions(self):
        lease = self.lease(); first = self.reserve(lease, 'RETAIN-GROUP-01')
        quota = self.quota(mode='existing', expected_digest=first['journal_digest'])
        lease = self.next_lease(lease, 'second-run')
        second = self.reserve(lease, 'RETAIN-SESSION-01', 'second-run', quota)
        self.assertEqual(first['cost'], COSTS['RETAIN-GROUP-01'])
        self.assertEqual(second['cost'], COSTS['RETAIN-SESSION-01'])
        self.assertEqual(second['accounted_after'], {'groups': 2, 'sessions': 3,
            'files': 0, 'uploaded_bytes': 0, 'share_mints': 0})

    def test_explicit_upgrade_preserves_complete_v1_journal_and_old_run_artifacts(self):
        lease = self.lease(); _, legacy = self.install_v1(lease)
        old_report = self.root / 'historical-report.json'
        old_report.write_bytes(b'{"frozen":"v1"}\n'); before = old_report.read_bytes()
        quota = self.quota(mode='upgrade-v1', expected_digest=fingerprint(legacy))
        lease = self.next_lease(lease, 'file-run')
        result = self.reserve(lease, run_id='file-run', quota=quota)
        journal = read(self.journal())
        self.assertEqual((journal['schema_version'], result['schema_version']), (1, 1))
        self.assertEqual(journal['legacy_v1'], legacy)
        self.assertEqual(journal['predecessor'], {'contract': 'retained-resource-quota-v1',
                                                  'digest': fingerprint(legacy)})
        self.assertEqual(journal['legacy_v1']['reservations'][0]['cost'], V1_COSTS['RETAIN-GROUP-01'])
        self.assertEqual(result['accounted_before'], {'groups': 1, 'sessions': 1,
            'files': 0, 'uploaded_bytes': 0, 'share_mints': 0})
        self.assertEqual(result['previous_journal_digest'], fingerprint(legacy))
        view = _quota({'retention_quota': result},
                      {'run_id': 'file-run', 'retention_quota': result},
                      {'retention_quota': result}, self.profile, 'RETAIN-FILE-01')
        self.assertEqual(view['accounted_at_reservation'], result['accounted_after'])
        self.assertEqual(old_report.read_bytes(), before)

    def test_upgrade_requires_exact_valid_v1_predecessor_and_never_auto_migrates(self):
        lease = self.lease(); _, legacy = self.install_v1(lease); lease.release()
        for mode, digest in (('existing', fingerprint(legacy)), ('upgrade-v1', '0' * 64)):
            lease = self.lease()
            try:
                with self.subTest(mode=mode), self.assertRaises(ConfigError):
                    self.reserve(lease, quota=self.quota(mode=mode, expected_digest=digest))
            finally:
                lease.release()
        self.assertEqual(read(self.journal()), legacy)

    def test_upgrade_rejects_changed_target_registry_limits_baseline_and_inventory(self):
        lease = self.lease(); _, legacy = self.install_v1(lease); lease.release()
        base = self.quota(mode='upgrade-v1', expected_digest=fingerprint(legacy))
        edits = ({'registry_id': 'other'}, {'max_groups': 21}, {'baseline_sessions': 1},
                 {'inventory_reference': 'other-review'}, {'inventory_sha256': 'a' * 64})
        for index, edit in enumerate(edits):
            run_id = f'changed-{index}'; lease = self.lease(run_id)
            try:
                with self.subTest(edit=edit), self.assertRaisesRegex(ConfigError, 'rebasing'):
                    self.reserve(lease, run_id=run_id, quota={**base, **edit})
            finally:
                lease.release()
        changed = copy.deepcopy(self.profile)
        changed['actors']['member']['bot_id'] = 'different-member'
        lease = self.lease('changed-target', changed)
        try:
            with self.assertRaisesRegex(ConfigError, 'rebasing'):
                self.reserve(lease, run_id='changed-target', quota=base, profile=changed)
        finally:
            lease.release()
        self.assertEqual(read(self.journal()), legacy)

    def test_v1_cannot_open_or_downgrade_a_v2_journal(self):
        lease = self.lease(); first = self.reserve(lease); lease = self.next_lease(lease, 'v1-run')
        quota = copy.deepcopy(self.review['retention_quota'])
        quota.update(mode='existing', expected_digest=first['journal_digest'])
        with self.assertRaisesRegex(ConfigError, 'rebasing'):
            self.reserve(lease, 'RETAIN-GROUP-01', 'v1-run', quota)

    def test_each_dimension_exhausts_independently(self):
        settings = {'groups': {'max_groups': 1, 'baseline_groups': 1},
            'sessions': {'max_sessions': 1}, 'files': {'max_files': 1, 'baseline_files': 1},
            'uploaded_bytes': {'max_uploaded_bytes': 4095},
            'share_mints': {'max_share_mints': 1}}
        for dimension, edit in settings.items():
            with self.subTest(dimension=dimension):
                path = self.journal()
                if path.exists(): path.unlink()
                lease = self.lease('run-' + dimension)
                with self.assertRaisesRegex(ConfigError, 'exhausted'):
                    self.reserve(lease, run_id='run-' + dimension, quota=self.quota(**edit))
                lease.release()
                self.assertFalse(path.exists())

    def test_duplicate_run_is_rejected_across_legacy_and_new_rows(self):
        lease = self.lease(); _, legacy = self.install_v1(lease); lease.release()
        quota = self.quota(mode='upgrade-v1', expected_digest=fingerprint(legacy))
        with self.assertRaisesRegex(ConfigError, 'already consumed'):
            self.reserve(self.lease(), quota=quota)
        self.assertEqual(read(self.journal()), legacy)

    def test_pool_guard_serializes_v2_reservation(self):
        lease = self.lease()
        with self.assertRaises(PoolBusyError):
            PoolLease.from_file(lease.path, self.run_id, fingerprint(self.profile))
        self.reserve(lease)

    def test_malformed_predecessor_rows_and_v2_costs_fail_closed(self):
        lease = self.lease(); _, legacy = self.install_v1(lease); lease.release()
        original = copy.deepcopy(legacy)
        for cost in ({'groups': True, 'sessions': 1}, {'groups': 1, 'sessions': 1, 'files': 0}):
            broken = copy.deepcopy(original); broken['reservations'][0]['cost'] = cost
            write(self.journal(), broken)
            quota = self.quota(mode='upgrade-v1', expected_digest=fingerprint(broken))
            run_id = 'bad-' + str(len(cost)); lease = self.lease(run_id)
            try:
                with self.subTest(cost=cost), self.assertRaises(ConfigError):
                    self.reserve(lease, run_id=run_id, quota=quota)
            finally:
                lease.release()
        write(self.journal(), original)
        quota = self.quota(mode='upgrade-v1', expected_digest=fingerprint(original))
        lease = self.lease('good'); result = self.reserve(lease, run_id='good', quota=quota)
        journal = read(self.journal()); lease.close()
        for cost in ({**COSTS['RETAIN-FILE-01'], 'files': True},
                     {**COSTS['RETAIN-FILE-01'], 'share_mints': 1}):
            broken = copy.deepcopy(journal); broken['reservations'][0]['cost'] = cost
            with self.subTest(new_cost=cost), self.assertRaises(ConfigError):
                validate_journal(broken, self.profile,
                    self.quota(mode='existing', expected_digest=result['journal_digest']))

    def test_committed_write_uncertainty_keeps_charge_and_blocks_retry(self):
        permission = self.permission()
        original_write = write
        def committed_then_error(path, value):
            original_write(path, value)
            raise OSError('synthetic directory fsync uncertainty')
        lease = self.lease()
        with patch('bcs_e2e.retention_quota_v2.write', side_effect=committed_then_error), self.assertRaises(OSError):
            reserve(self.profile, FrozenReview(permission), lease, 'RETAIN-FILE-01')
        committed = read(self.journal())
        self.assertEqual(len(committed['reservations']), 1)
        quota = self.quota(mode='existing', expected_digest=fingerprint(committed))
        with self.assertRaisesRegex(ConfigError, 'already consumed'):
            self.reserve(lease, quota=quota)

    def test_existing_after_upgrade_keeps_all_historical_file_and_session_costs(self):
        lease = self.lease(); _, legacy = self.install_v1(lease)
        quota = self.quota(mode='upgrade-v1', expected_digest=fingerprint(legacy),
                           baseline_files=2, baseline_uploaded_bytes=1000, baseline_share_mints=3)
        lease = self.next_lease(lease, 'file-run')
        file_receipt = self.reserve(lease, run_id='file-run', quota=quota)
        quota.update(mode='existing', expected_digest=file_receipt['journal_digest'])
        lease = self.next_lease(lease, 'later-group')
        group_receipt = self.reserve(lease, 'RETAIN-GROUP-01', 'later-group', quota)
        self.assertEqual(read(self.journal())['legacy_v1'], legacy)
        self.assertEqual(group_receipt['accounted_after'], {'groups': 3, 'sessions': 4,
            'files': 3, 'uploaded_bytes': 5096, 'share_mints': 5})
        view = _quota({'retention_quota': group_receipt},
                      {'run_id': 'later-group', 'retention_quota': group_receipt},
                      {'retention_quota': group_receipt}, self.profile, 'RETAIN-GROUP-01')
        self.assertEqual(view['accounted_at_reservation'], group_receipt['accounted_after'])

    def test_v2_receipt_validation_recomputes_totals_and_source_binding(self):
        receipt = self.reserve(self.lease())
        state = {'run_id': self.run_id, 'retention_quota': receipt}
        report = {'retention_quota': receipt}
        value = {'retention_quota': receipt}
        view = _quota(value, state, report, self.profile, 'RETAIN-FILE-01')
        self.assertEqual(view['accounted_at_reservation'], receipt['accounted_after'])
        self.assertEqual(tuple(view['accounted_at_reservation']), DIMENSIONS)
        for mutation in ('after', 'binding', 'cost'):
            broken = copy.deepcopy(receipt)
            if mutation == 'after': broken['accounted_after']['files'] += 1
            elif mutation == 'binding': broken['source_binding']['policy']['registry_id'] = 'other'
            else: broken['cost']['uploaded_bytes'] = 1
            with self.subTest(mutation=mutation), self.assertRaises(ConfigError):
                _quota({'retention_quota': broken}, {'run_id': self.run_id, 'retention_quota': broken},
                       {'retention_quota': broken}, self.profile, 'RETAIN-FILE-01')

    def test_receipt_rejects_changed_predecessor_digest_and_nonfinal_bound_row(self):
        lease = self.lease(); first = self.reserve(lease, 'RETAIN-GROUP-01')
        quota = self.quota(mode='existing', expected_digest=first['journal_digest'])
        lease = self.next_lease(lease, 'second-run')
        receipt = self.reserve(lease, run_id='second-run', quota=quota)
        def check(value):
            return _quota({'retention_quota': value}, {'run_id': 'second-run', 'retention_quota': value},
                          {'retention_quota': value}, self.profile, 'RETAIN-FILE-01')
        check(receipt)
        wrong_previous = copy.deepcopy(receipt)
        wrong_previous['previous_journal_digest'] = '0' * 64
        with self.assertRaises(ConfigError):
            check(wrong_previous)
        reordered = copy.deepcopy(receipt)
        reordered['journal_snapshot']['reservations'].reverse()
        reordered['journal_digest'] = fingerprint(reordered['journal_snapshot'])
        reordered['source_binding']['reservations_digest'] = fingerprint(
            reordered['journal_snapshot']['reservations'])
        with self.assertRaises(ConfigError):
            check(reordered)

    def test_markdown_renders_all_v2_dimensions_and_v1_receipts_remain_compatible(self):
        receipt = self.reserve(self.lease())
        quota = _quota({'retention_quota': receipt}, {'run_id': self.run_id, 'retention_quota': receipt},
                       {'retention_quota': receipt}, self.profile, 'RETAIN-FILE-01')
        summary = {'cases': [], 'retention': {'status': 'VERIFIED_LOCAL_INVENTORY', 'resources': [],
            'handoff': {'owner': 'owner', 'status': 'REQUIRED', 'review_state': 'PENDING', 'actions': []},
            'quota': quota}}
        rendered = '\n'.join(retained_markdown(summary))
        for dimension in DIMENSIONS:
            self.assertIn(dimension + ' at reservation:', rendered)
        old = {'contract': 'retained-resource-quota-v1', 'status': 'RESERVED_MAXIMUM',
            'automatic_refund': False,
            'reservation': {'run_id': self.run_id, 'case_id': 'RETAIN-GROUP-01',
                'profile_digest': fingerprint(self.profile), 'cost': V1_COSTS['RETAIN-GROUP-01']},
            'cost': V1_COSTS['RETAIN-GROUP-01'],
            'accounted_after': {'groups': 2, 'sessions': 3}, 'limits': {'groups': 4, 'sessions': 8}}
        old_view = _quota({'retention_quota': old}, {'run_id': self.run_id, 'retention_quota': old},
                          {'retention_quota': old}, self.profile, 'RETAIN-GROUP-01')
        self.assertEqual(old_view['contract'], 'retained-resource-quota-v1')
        self.assertEqual(old_view['accounted_at_reservation'], {'groups': 2, 'sessions': 3})


class QuotaV2RemoteCompositionTests(unittest.TestCase):
    setUp = fixtures.RetainedRemoteTests.setUp
    command = fixtures.RetainedRemoteTests.command
    process = fixtures.RetainedRemoteTests.process
    boundaries = fixtures.RetainedRemoteTests.boundaries
    args = fixtures.RetainedRemoteTests.args
    quota = QuotaV2Tests.quota

    def test_reviewed_group_uses_v2_without_changing_its_case_or_remote_operations(self):
        self.review['retention_quota'] = self.quota()
        write(self.review_path, self.review)
        with self.boundaries():
            rc, result = self.command(self.args() + ['--serial-preflight'])
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['canonical_summary_status'], 'RETAINED_CASE_PASS')
        self.assertEqual(result['retention_quota']['cost'], COSTS['RETAIN-GROUP-01'])
        summary = read(self.state/'reports/summary.json')
        self.assertEqual(summary['retention']['quota']['contract'], CONTRACT)
        self.assertEqual(summary['scope']['suite'], 'preprod-retained-v1')
        self.assertFalse(any('file' in operation for _, operation in self.http))
        self.assertEqual(self.adapter.deleted, set())

    def test_lost_group_response_keeps_v2_charge_and_returns_failure(self):
        self.review['retention_quota'] = self.quota()
        write(self.review_path, self.review)
        with self.boundaries(lose_create=True):
            rc, result = self.command(self.args() + ['--serial-preflight'])
        self.assertEqual(rc, 2)
        self.assertEqual(result['functional'], 'FAIL')
        journal = read(pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json'))
        self.assertEqual(journal['reservations'][0]['cost'], COSTS['RETAIN-GROUP-01'])
        self.assertEqual(result['retention_quota']['status'], 'RESERVED_MAXIMUM')
        self.assertEqual(self.adapter.deleted, set())


if __name__ == '__main__':
    unittest.main()
