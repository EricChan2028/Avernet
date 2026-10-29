"""Durable conservative admission; all targets/transports are synthetic and local."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.pool import PoolLease, PoolBusyError, PoolLeaseError, pool_path, pool_scope
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.retention_quota import COSTS, reserve, validate_quota
from bcs_e2e.retained_remote import RetainedReview
from bcs_e2e.run_state import RunStateError, read, write
import test_retained_remote as fixtures


class QuotaTests(unittest.TestCase):
    # Reuse setup/boundaries, not inherited test methods.
    setUp = fixtures.RetainedRemoteTests.setUp
    command = fixtures.RetainedRemoteTests.command
    process = fixtures.RetainedRemoteTests.process
    boundaries = fixtures.RetainedRemoteTests.boundaries
    args = fixtures.RetainedRemoteTests.args

    def journal(self):
        return pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json')

    def lease(self):
        lease = PoolLease.acquire(pool_path(pool_scope(self.profile)), self.run_id, fingerprint(self.profile))
        self.addCleanup(lease.close)
        return lease

    def reserve(self, lease):
        write(self.review_path, self.review)
        review = RetainedReview(self.profile, self.run_id, self.review['case_id'], self.review_path)
        return reserve(self.profile, review, lease, self.review['case_id'])

    def existing(self):
        self.review['retention_quota'].update(mode='existing', expected_digest=fingerprint(read(self.journal())))

    def next_run(self, lease):
        # Test-only explicit lease release models a separately admitted future
        # reuse; production retained commands must never do this automatically.
        lease.release()
        self.run_id = 'second-run'
        self.review['run_id'] = self.run_id
        return self.lease()

    def test_required_config_rejected_before_http(self):
        del self.review['retention_quota']; write(self.review_path, self.review)
        with self.boundaries(): rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual(self.http, [])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.journal().exists())

    def test_strict_config(self):
        base = self.review['retention_quota']
        invalid = ({'max_groups': True}, {'max_sessions': 0}, {'max_groups': 10001},
                   {'baseline_groups': -1}, {'baseline_groups': 11}, {'baseline_sessions': False},
                   {'mode': 'reset'}, {'expected_digest': '0'*64},
                   {'mode': 'existing'}, {'inventory_sha256': 'bad'}, {'extra': 1})
        for edit in invalid:
            with self.subTest(edit=edit), self.assertRaises(ConfigError):
                validate_quota({**base, **edit})

    def test_initialization_and_exact_session_maximum(self):
        self.review['case_id'] = 'RETAIN-SESSION-01'
        self.review['retention_quota'].update(baseline_groups=2, baseline_sessions=3)
        result = self.reserve(self.lease())
        self.assertEqual(result['cost'], {'groups': 1, 'sessions': 2})
        self.assertEqual(result['accounted_after'], {'groups': 3, 'sessions': 5})
        self.assertEqual(result['previous_journal_digest'], 'absent')
        journal = read(self.journal())
        self.assertEqual(journal['reservations'], [result['reservation']])
        self.assertEqual(result['journal_digest'], fingerprint(journal))
        self.assertEqual(self.journal().stat().st_mode & 0o777, 0o600)
        self.assertEqual(result['physical_storage_usage'], 'NOT_MEASURED')

    def test_existing_accumulates_across_runs_and_profile_credentials(self):
        lease = self.lease(); first = self.reserve(lease)
        self.existing(); lease.release()
        self.run_id = 'second-run'; self.review['run_id'] = self.run_id
        # Profile changes cannot reset the pool-local quota. Frozen target is unchanged.
        self.profile['policy']['timeout_seconds'] += 1
        self.review['profile_digest'] = fingerprint(self.profile)
        second = self.reserve(self.lease())
        self.assertEqual(second['previous_journal_digest'], first['journal_digest'])
        self.assertEqual(second['accounted_after'], {'groups': 2, 'sessions': 2})
        self.assertEqual(len(read(self.journal())['reservations']), 2)

    def test_initialization_cannot_reset_existing_registry(self):
        lease = self.lease(); self.reserve(lease); original = self.journal().read_bytes()
        with self.assertRaises(ConfigError): self.reserve(lease)
        self.assertEqual(self.journal().read_bytes(), original)

    def test_duplicate_run_and_stale_digest_rejected(self):
        lease = self.lease(); self.reserve(lease); self.existing()
        original = self.journal().read_bytes()
        with self.assertRaisesRegex(ConfigError, 'already consumed'): self.reserve(lease)
        self.review['retention_quota']['expected_digest'] = '0'*64
        with self.assertRaisesRegex(ConfigError, 'snapshot changed'): self.reserve(lease)
        self.assertEqual(self.journal().read_bytes(), original)

    def test_baseline_and_policy_cannot_be_rebased(self):
        lease = self.lease(); self.reserve(lease); self.existing()
        quota = copy.deepcopy(self.review['retention_quota'])
        for edit in ({'max_groups': 11}, {'baseline_sessions': 1}, {'registry_id': 'new'},
                     {'inventory_sha256': 'f'*64}):
            self.review['retention_quota'] = {**quota, **edit}
            with self.subTest(edit=edit), self.assertRaisesRegex(ConfigError, 'rebasing'):
                self.reserve(lease)

    def test_changed_target_rejected_even_with_fresh_digest_and_lease(self):
        lease = self.lease(); self.reserve(lease); self.existing(); lease.release()
        self.profile['actors']['member']['bot_id'] = 'another-bot'
        self.review['profile_digest'] = fingerprint(self.profile)
        with self.assertRaisesRegex(ConfigError, 'target/baseline/limits'): self.reserve(self.lease())

    def test_missing_existing_registry_does_not_initialize(self):
        self.review['retention_quota'].update(mode='existing', expected_digest='a'*64)
        with self.assertRaisesRegex(ConfigError, 'missing'): self.reserve(self.lease())
        self.assertFalse(self.journal().exists())

    def test_symlink_and_corrupt_journals_fail_closed(self):
        lease = self.lease(); target = self.root/'missing.json'
        self.journal().symlink_to(target)
        with self.assertRaises(ConfigError): self.reserve(lease)
        self.review['retention_quota'].update(mode='existing', expected_digest='a'*64)
        with self.assertRaises(RunStateError): self.reserve(lease)
        self.assertFalse(target.exists())
        self.journal().unlink(); self.journal().write_text('not JSON')
        with self.assertRaises(RunStateError): self.reserve(lease)
        self.assertEqual(self.journal().read_text(), 'not JSON')

    def test_journal_rows_require_strict_costs_and_valid_identity(self):
        lease = self.lease(); self.reserve(lease)
        base = read(self.journal())
        for edit in ({'cost': {'groups': True, 'sessions': 1}}, {'cost': {'groups': 1.0, 'sessions': 1}},
                     {'cost': {}}, {'case_id': []}, {'review_digest': 'bad'}):
            journal = copy.deepcopy(base); journal['reservations'][0].update(edit)
            write(self.journal(), journal); self.existing()
            with self.subTest(edit=edit), self.assertRaises(ConfigError): self.reserve(lease)

    def test_exhaustion_blocks_creation_before_resource_intent(self):
        self.review['retention_quota'].update(max_groups=1, baseline_groups=1)
        write(self.review_path, self.review)
        with self.boundaries(): rc, result = self.command(self.args())
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertFalse(any(kind == 'write' for kind, _ in self.http))
        self.assertFalse(self.journal().exists())
        self.assertEqual(read(self.state/'run.json')['status'], 'QUARANTINED')
        self.assertEqual(self.adapter.groups, {})

    def test_session_quota_independently_exhausts(self):
        self.review['case_id'] = 'RETAIN-SESSION-01'
        self.review['retention_quota']['max_sessions'] = 1
        with self.assertRaisesRegex(ConfigError, 'exhausted'): self.reserve(self.lease())
        self.assertFalse(self.journal().exists())

    def test_exact_limit_last_admission_then_refusal(self):
        self.review['retention_quota'].update(max_groups=1, max_sessions=1)
        lease = self.lease(); self.reserve(lease); self.existing()
        lease = self.next_run(lease)
        with self.assertRaisesRegex(ConfigError, 'exhausted'): self.reserve(lease)
        self.assertEqual(len(read(self.journal())['reservations']), 1)

    def test_pool_guard_serializes_reservation(self):
        lease = self.lease()
        with self.assertRaises(PoolBusyError):
            PoolLease.from_file(lease.path, self.run_id, fingerprint(self.profile))
        self.reserve(lease); lease.close()
        with PoolLease.from_file(lease.path, self.run_id, fingerprint(self.profile)) as second:
            self.existing()
            with self.assertRaisesRegex(ConfigError, 'already consumed'): self.reserve(second)

    def test_closed_or_wrong_pool_lease_cannot_reserve(self):
        lease = self.lease(); lease.close()
        with self.assertRaises(PoolLeaseError): self.reserve(lease)
        other = PoolLease.acquire(self.root/'other.lock', self.run_id, fingerprint(self.profile))
        self.addCleanup(other.close)
        with self.assertRaises(ConfigError): self.reserve(other)
        self.assertFalse(self.journal().exists())

    def test_lost_response_permanently_consumes_maximum(self):
        with self.boundaries(lose_create=True): rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'FAIL')
        journal = read(self.journal())
        self.assertEqual(journal['reservations'][0]['cost'], COSTS['RETAIN-GROUP-01'])
        self.assertEqual(result['retention_quota']['status'], 'RESERVED_MAXIMUM')
        self.assertEqual(read(self.state/'retention.json')['retention_quota'], result['retention_quota'])
        self.assertFalse(self.adapter.deleted)

    def test_journal_write_uncertainty_preserves_reservation_and_no_dispatch(self):
        def committed_then_error(path, value):
            write(path, value)
            raise OSError('synthetic directory fsync failure')
        with self.boundaries(), patch('bcs_e2e.retention_quota.write', side_effect=committed_then_error):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertEqual(len(read(self.journal())['reservations']), 1)
        self.assertEqual(result['retention_quota']['status'], 'UNCONFIRMED')
        self.assertFalse(result['retention_quota']['automatic_refund'])
        self.assertFalse(any(kind == 'write' for kind, _ in self.http))
        self.assertEqual(read(self.state/'run.json')['status'], 'QUARANTINED')

    def test_run_checkpoint_failure_does_not_undo_committed_quota(self):
        def fail(path, value):
            if path.name == 'run.json' and value.get('retention_quota', {}).get('status') == 'RESERVED_MAXIMUM':
                raise OSError('synthetic Run receipt failure')
            write(path, value)
        with self.boundaries(), patch('bcs_e2e.retained.write', side_effect=fail):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertEqual(len(read(self.journal())['reservations']), 1)
        self.assertEqual(result['retention_quota']['status'], 'UNCONFIRMED')
        self.assertFalse(any(kind == 'write' for kind, _ in self.http))

    def test_quota_write_failure_before_commit_never_creates_resources(self):
        with self.boundaries(), patch('bcs_e2e.retention_quota.write', side_effect=OSError('synthetic disk full')):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertFalse(self.journal().exists())
        self.assertEqual(result['retention_quota']['status'], 'UNCONFIRMED')
        self.assertFalse(any(kind == 'write' for kind, _ in self.http))


if __name__ == '__main__': unittest.main()
