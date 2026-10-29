"""Synthetic-only contract tests for reviewed individually bounded quota-v2 group increases."""
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.pool import PoolLease, pool_path, pool_scope
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.retention_quota import reserve as reserve_dispatch
from bcs_e2e.retention_quota_v2 import (
    CONTRACT, GROUP_LIMIT_CHANGE_CONTRACT, validate_journal, validate_quota,
)
from bcs_e2e.retention_quota_v2 import reserve as reserve_v2
from bcs_e2e.run_state import read, write
import test_retained_remote as fixtures


class FrozenReview:
    def __init__(self, value):
        self.value = copy.deepcopy(value)

    def validate(self):
        return copy.deepcopy(self.value)


class ChangedReview(FrozenReview):
    def validate(self):
        value = super().validate()
        value['run_id'] = 'changed-review-run'
        return value


class QuotaGroupIncreaseTests(unittest.TestCase):
    setUp = fixtures.RetainedRemoteTests.setUp

    def journal_path(self):
        return pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json')

    def lease(self, run_id):
        lease = PoolLease.acquire(pool_path(pool_scope(self.profile)), run_id,
                                  fingerprint(self.profile))
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

    def permission(self, run_id, quota, case_id='RETAIN-GROUP-01'):
        return {'schema_version': 1, 'contract': 'synthetic-retained-review-v1',
            'profile_digest': fingerprint(self.profile), 'run_id': run_id,
            'case_id': case_id, 'retention_quota': copy.deepcopy(quota)}

    def reserve(self, run_id, quota, case_id='RETAIN-GROUP-01', review=None):
        permission = self.permission(run_id, quota, case_id)
        review = review or FrozenReview(permission)
        lease = self.lease(run_id)
        try:
            return reserve_v2(self.profile, permission, review, lease, case_id)
        finally:
            if lease._guard_fd >= 0:
                lease.release()

    def seed_v2(self, run_id='old-run'):
        receipt = self.reserve(run_id, self.quota())
        return receipt, copy.deepcopy(read(self.journal_path()))

    def change_receipt(self, run_id, previous=10, new=11, **changes):
        now = int(time.time())
        value = {'contract': GROUP_LIMIT_CHANGE_CONTRACT, 'run_id': run_id,
            'profile_digest': fingerprint(self.profile),
            'supervisor': self.profile['human']['user_id'],
            'reviewed_at': now - 1, 'expires_at': now + 1800,
            'reference': 'group-limit-review', 'evidence_sha256': 'a' * 64,
            'previous_max_groups': previous, 'new_max_groups': new}
        value.update(changes)
        return value

    def increase_quota(self, old, run_id='increase-run', **changes):
        value = {**copy.deepcopy(old['policy']), 'mode': 'raise-group-limit',
            'expected_digest': fingerprint(old),
            'max_groups': old['policy']['max_groups'] + 1,
            'group_limit_change': self.change_receipt(
                run_id, old['policy']['max_groups'], old['policy']['max_groups'] + 1)}
        value.update(changes)
        return value

    def existing_quota(self, journal):
        return {**copy.deepcopy(journal['policy']), 'mode': 'existing',
                'expected_digest': fingerprint(journal)}

    def test_increase_preserves_entire_v2_with_nested_v1_and_binds_first_row(self):
        v1_permission = self.permission('legacy-run', copy.deepcopy(self.review['retention_quota']))
        legacy_lease = self.lease('legacy-run')
        try:
            v1_receipt = reserve_dispatch(self.profile, FrozenReview(v1_permission),
                                          legacy_lease, 'RETAIN-GROUP-01')
        finally:
            if legacy_lease._guard_fd >= 0:
                legacy_lease.release()
        legacy = copy.deepcopy(read(self.journal_path()))
        upgrade = self.quota(mode='upgrade-v1', expected_digest=fingerprint(legacy))
        upgraded = self.reserve('old-v2-run', upgrade)
        old = copy.deepcopy(read(self.journal_path()))

        result = self.reserve('increase-run', self.increase_quota(old))
        journal = read(self.journal_path())
        self.assertEqual(journal['previous_v2'], old)
        self.assertEqual(result['journal_snapshot']['previous_v2'], old)
        self.assertEqual(journal['predecessor'], {'contract': CONTRACT, 'digest': fingerprint(old)})
        self.assertEqual(journal['reservations'][0]['run_id'], 'increase-run')
        self.assertEqual(result['accounted_before']['groups'], 2)
        self.assertEqual(result['accounted_after']['groups'], 3)
        self.assertNotIn('legacy_v1', journal)
        self.assertEqual(journal['previous_v2']['legacy_v1'], legacy)
        self.assertEqual(v1_receipt['reservation']['run_id'], 'legacy-run')
        self.assertEqual(upgraded['reservation']['run_id'], 'old-v2-run')
        self.assertEqual(result['source_binding']['previous_v2_digest'], fingerprint(old))
        self.assertEqual(result['source_binding']['group_limit_change_digest'],
                         fingerprint(journal['group_limit_change']))

    def test_existing_mode_appends_after_the_bound_increase_row(self):
        _, old = self.seed_v2()
        increased = self.reserve('increase-run', self.increase_quota(old))
        journal = copy.deepcopy(read(self.journal_path()))
        later = self.reserve('later-run', self.existing_quota(journal))
        current = read(self.journal_path())
        self.assertEqual([row['run_id'] for row in current['reservations']],
                         ['increase-run', 'later-run'])
        self.assertEqual(later['accounted_before'], increased['accounted_after'])
        self.assertEqual(current['previous_v2'], old)

    def test_receipt_schema_supports_any_bounded_exact_plus_one(self):
        quota = self.quota(mode='raise-group-limit', expected_digest='a' * 64,
                           max_groups=10,
                           group_limit_change=self.change_receipt(
                               'generic-run', previous=9, new=10))
        self.assertEqual(validate_quota(quota)['group_limit_change']['new_max_groups'], 10)
        for previous, new in ((0, 1), (9, 11), (10000, 10001)):
            broken = copy.deepcopy(quota)
            broken['max_groups'] = new
            broken['group_limit_change'].update(
                previous_max_groups=previous, new_max_groups=new)
            with self.subTest(previous=previous, new=new), self.assertRaises(ConfigError):
                validate_quota(broken)

    def test_receipt_is_required_only_by_raise_mode(self):
        _, old = self.seed_v2()
        increase = self.increase_quota(old)
        missing = copy.deepcopy(increase); missing.pop('group_limit_change')
        with self.assertRaises(ConfigError):
            validate_quota(missing)
        for mode in ('initialize', 'existing', 'upgrade-v1'):
            quota = self.quota(mode=mode,
                expected_digest='absent' if mode == 'initialize' else fingerprint(old),
                group_limit_change=self.change_receipt('run'))
            with self.subTest(mode=mode), self.assertRaises(ConfigError):
                validate_quota(quota)

    def test_missing_expired_wrong_owner_run_profile_and_digest_receipts_do_not_mutate(self):
        _, old = self.seed_v2()
        base = self.increase_quota(old)
        now = int(time.time())
        mutations = []
        missing = copy.deepcopy(base); missing['group_limit_change'].pop('reference'); mutations.append(missing)
        expired = copy.deepcopy(base); expired['group_limit_change'].update(
            reviewed_at=now - 100, expires_at=now - 1); mutations.append(expired)
        for edit in ({'supervisor': 'other-owner'}, {'run_id': 'other-run'},
                     {'profile_digest': 'b' * 64}, {'evidence_sha256': 'bad'}):
            changed = copy.deepcopy(base); changed['group_limit_change'].update(edit); mutations.append(changed)
        for index, quota in enumerate(mutations):
            run_id = f'increase-run-{index}'
            quota['group_limit_change']['run_id'] = (quota['group_limit_change'].get('run_id')
                if index == 3 else run_id)
            with self.subTest(index=index), self.assertRaises(ConfigError):
                self.reserve(run_id, quota)
            self.assertEqual(read(self.journal_path()), old)

    def test_only_one_group_and_no_other_policy_baseline_or_inventory_change_is_allowed(self):
        _, old = self.seed_v2()
        edits = ({'max_groups': 12}, {'max_groups': 10}, {'max_sessions': 21}, {'baseline_groups': 1},
                 {'baseline_files': 1}, {'inventory_reference': 'other-review'},
                 {'inventory_sha256': 'b' * 64}, {'file_inventory_reference': 'other-file-review'},
                 {'file_inventory_sha256': 'c' * 64})
        for index, edit in enumerate(edits):
            run_id = f'change-{index}'
            quota = self.increase_quota(old, run_id)
            quota.update(edit)
            if 'max_groups' in edit:
                quota['group_limit_change'].update(
                    previous_max_groups=edit['max_groups'] - 1, new_max_groups=edit['max_groups'])
            with self.subTest(edit=edit), self.assertRaises(ConfigError):
                self.reserve(run_id, quota)
            self.assertEqual(read(self.journal_path()), old)

    def test_predecessor_shapes_first_row_and_replay_are_rejected(self):
        _, old = self.seed_v2()
        result = self.reserve('increase-run', self.increase_quota(old))
        journal = copy.deepcopy(read(self.journal_path()))
        quota = self.existing_quota(journal)
        variants = []
        for missing in ('predecessor', 'previous_v2', 'group_limit_change'):
            broken = copy.deepcopy(journal); broken.pop(missing); variants.append(broken)
        mixed = copy.deepcopy(journal); mixed['legacy_v1'] = copy.deepcopy(old); variants.append(mixed)
        wrong_first = copy.deepcopy(journal); wrong_first['reservations'][0]['run_id'] = 'other-run'; variants.append(wrong_first)
        for broken in variants:
            with self.assertRaises(ConfigError):
                validate_journal(broken, self.profile, quota)
        with self.assertRaisesRegex(ConfigError, 'already consumed'):
            self.reserve('increase-run', quota)
        second = self.reserve('second-increase', self.increase_quota(journal, 'second-increase'))
        chained = read(self.journal_path())
        self.assertEqual(second['previous_journal_digest'], fingerprint(journal))
        self.assertEqual(chained['previous_v2'], journal)
        self.assertEqual(chained['group_limit_change']['previous_max_groups'], 11)
        self.assertEqual(chained['group_limit_change']['new_max_groups'], 12)
        self.assertEqual([row['run_id'] for row in chained['reservations']], ['second-increase'])
        self.assertEqual(journal['group_limit_change']['new_max_groups'], 11)
        self.assertEqual(result['reservation']['run_id'], 'increase-run')
        third = self.increase_quota(chained, 'third-increase')
        third['group_limit_change'].update(previous_max_groups=11, new_max_groups=13)
        with self.assertRaises(ConfigError):
            self.reserve('third-increase', third)
        self.assertEqual(read(self.journal_path()), chained)

    def test_historical_expired_receipt_validates_but_tampering_is_rejected(self):
        _, old = self.seed_v2()
        quota = self.increase_quota(old)
        historical = int(time.time()) - 7200
        quota['group_limit_change'].update(reviewed_at=historical, expires_at=historical + 1800)
        # Build the historical snapshot without exercising live admission.
        journal = {'schema_version': 1, 'contract': CONTRACT, 'target': old['target'],
            'policy': {key: value for key, value in quota.items()
                       if key not in {'mode', 'expected_digest', 'group_limit_change'}},
            'reservations': [], 'predecessor': {'contract': CONTRACT, 'digest': fingerprint(old)},
            'previous_v2': copy.deepcopy(old),
            'group_limit_change': copy.deepcopy(quota['group_limit_change'])}
        validate_journal(journal, self.profile,
                         {**journal['policy'], 'mode': 'existing',
                          'expected_digest': fingerprint(journal)})
        for kind in ('receipt', 'policy'):
            broken = copy.deepcopy(journal)
            if kind == 'receipt':
                broken['group_limit_change']['contract'] = 'tampered-contract'
            else:
                broken['previous_v2']['policy']['max_sessions'] += 1
            with self.subTest(kind=kind), self.assertRaises(ConfigError):
                validate_journal(broken, self.profile,
                    {**journal['policy'], 'mode': 'existing',
                     'expected_digest': fingerprint(journal)})

    def test_failed_review_or_uncommitted_write_keeps_original_journal(self):
        _, old = self.seed_v2()
        quota = self.increase_quota(old)
        permission = self.permission('increase-run', quota)
        with self.assertRaisesRegex(ConfigError, 'authorization changed'):
            self.reserve('increase-run', quota, review=ChangedReview(permission))
        self.assertEqual(read(self.journal_path()), old)
        with patch('bcs_e2e.retention_quota_v2.write', side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):
                self.reserve('increase-run', quota, review=FrozenReview(permission))
        self.assertEqual(read(self.journal_path()), old)


if __name__ == '__main__':
    unittest.main()
