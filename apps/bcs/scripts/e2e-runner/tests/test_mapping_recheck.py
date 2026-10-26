"""Persistent target facts and one-shot read plans are separate contracts."""
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.mapping_confirmation import CONTRACT, EnvironmentMapping, target, validate_confirmation
from bcs_e2e.mapping_recheck import ScopedMappingClient, mapping_dependencies
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.recheck_review import PLAN_CONTRACT, RetainedRecheckReview, recheck_plan
from bcs_e2e.remote_observer import ReviewedMcpClient, validate_binding, remote_check_dependencies
from bcs_e2e.run_state import read, write
import test_retained_recheck as fixtures


def confirmation(profile):
    return {'schema_version': 1, 'contract': CONTRACT, 'mapping_id': 'test-environment-mapping',
            'revision': 1, 'status': 'ACTIVE', 'confirmed_at': int(time.time())-86400,
            'confirmed_by': 'test-environment-owner', 'target': target(profile),
            'database_sharing': 'preprod-and-production',
            'evidence': {'reference': 'actual-test-mapping-statement', 'sha256': 'a'*64}}


class MappingRecheckTests(unittest.TestCase):
    command = fixtures.RetainedRecheckTests.command
    boundaries = fixtures.RetainedRecheckTests.boundaries
    process = fixtures.RetainedRecheckTests.process
    execute = fixtures.RetainedRecheckTests.execute
    assert_original_unchanged = fixtures.RetainedRecheckTests.assert_original_unchanged

    def setUp(self):
        fixtures.RetainedRecheckTests.setUp(self)
        self.mapping_path = self.root/'environment-mapping.json'
        self.mapping = confirmation(self.profile); write(self.mapping_path, self.mapping)
        self.old_review = copy.deepcopy(self.recheck)
        self.recheck = recheck_plan(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01',
                                   mapping_confirmation=self.mapping_path)['read_plan']
        self.recheck['observation_id'] = 'read-plan-1'
        self.recheck['execution_observation_budget'] = {'timeout_seconds': 30, 'max_rounds': 2, 'poll_interval_seconds': 1}
        write(self.recheck_path, self.recheck)

    def args(self):
        return ['retained-recheck', '--profile', str(self.profile_path), '--state-dir', str(self.state),
                '--run-id', self.run_id, '--case', 'RETAIN-GROUP-01', '--read-plan', str(self.recheck_path),
                '--mapping-confirmation', str(self.mapping_path), '--mcporter-binary', str(self.executable),
                '--allow-observer-read', '--confirm-run-id', self.run_id]

    def test_old_mapping_facts_and_source_plan_do_not_expire_after_an_hour(self):
        future = int(time.time()) + 10*86400
        with patch('time.time', return_value=future): rc, report = self.execute()
        self.assertEqual(rc, 2, report)  # observation success still is not qualification
        self.assertEqual(report['execution_observation']['status'], 'OBSERVED', report)
        self.assertEqual(report['read_authorization_basis'], 'EXPLICIT_COMMAND_FLAGS_AND_SOURCE_PLAN')
        self.assertEqual(report['observer_binding']['query_authorization'], 'NONE')
        self.assertFalse(report['pool_release_authorized'])
        self.assertIn('read_plan_digest', report)
        self.assertNotIn('recheck_review_digest', report)
        self.assertTrue((self.state/'observations/read-plan-1/read-plan.json').is_file())
        self.assertTrue((self.state/'observations/read-plan-1/mapping-confirmation.json').is_file())
        self.assertFalse((self.state/'observations/read-plan-1/observer-binding.json').exists())
        self.assert_original_unchanged()

    def test_missing_read_flag_or_wrong_run_confirmation_never_queries(self):
        for args in (self.args()[:-1]+['wrong-run'], [a for a in self.args() if a != '--allow-observer-read']):
            with self.boundaries(): rc, report = self.command(args)
            self.assertEqual(rc, 2); self.assertEqual(self.calls, [])
            self.assertFalse((self.state/'observations').exists())
            self.assert_original_unchanged()

    def test_replayed_observation_id_cannot_reset_budget(self):
        self.execute(); self.calls.clear()
        saved = (self.state/'observations/read-plan-1/report.json').read_bytes()
        rc, _ = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(self.calls, [])
        self.assertEqual((self.state/'observations/read-plan-1/report.json').read_bytes(), saved)
        self.assert_original_unchanged()

    def test_read_plan_drift_before_call_is_rejected(self):
        for edit in ({'profile_digest': '0'*64}, {'run_id': 'foreign'},
                     {'mapping_confirmation_digest': '0'*64}, {'source': {}},
                     {'reviewed_at': 1}, {'expires_at': 9999999999}, {'reviewer': 'test-owner'}):
            write(self.recheck_path, {**self.recheck, **edit})
            with self.subTest(edit=edit):
                rc, _ = self.execute(); self.assertEqual(rc, 2); self.assertEqual(self.calls, [])
        self.assert_original_unchanged()

    def test_new_actors_reuse_mapping_facts_but_not_an_old_run_read_plan(self):
        changed = copy.deepcopy(self.profile)
        changed['actors']['driver']['bot_id'] = 'another-driver'
        self.assertEqual(validate_confirmation(self.mapping, changed), self.mapping)
        write(self.profile_path, changed)
        rc, _ = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(self.calls, [])
        self.assert_original_unchanged()

    def test_mapping_change_after_planning_requires_explicit_replan(self):
        self.mapping['revision'] = 2; write(self.mapping_path, self.mapping)
        rc, _ = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(self.calls, [])
        self.assertFalse((self.state/'observations').exists())

    def test_revoked_mapping_is_not_an_expired_platform_credential(self):
        self.mapping['status'] = 'REVOKED'; write(self.mapping_path, self.mapping)
        rc, _ = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(self.calls, [])
        self.assert_original_unchanged()

    def test_revocation_during_query_rejects_late_result(self):
        original = self.process
        def revoke(command, **kwargs):
            result = original(command, **kwargs)
            if command[2].endswith('.db_query'):
                value = read(self.mapping_path); value['status'] = 'REVOKED'; write(self.mapping_path, value)
            return result
        with patch.object(self, 'process', side_effect=revoke): rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(report['execution_observation']['status'], 'BLOCKED')
        self.assertEqual(report['execution_observation']['sessions'], [])
        self.assertEqual(len(self.delivery_queries), 1)
        self.assert_original_unchanged()

    def test_mapping_and_expiring_observer_receipt_cannot_be_mixed(self):
        with self.boundaries(): rc, _ = self.command(self.args()+['--observer-binding', str(self.fresh_binding)])
        self.assertEqual(rc, 2); self.assertEqual(self.calls, [])
        self.assertFalse((self.state/'observations').exists())

    def test_legacy_read_review_is_not_implicitly_upgraded_by_mapping(self):
        write(self.recheck_path, self.old_review)
        rc, _ = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(self.calls, [])

    def test_legacy_generic_factories_reject_persistent_mapping(self):
        with self.assertRaises(ConfigError): validate_binding(self.mapping, self.profile)
        with self.assertRaises(ConfigError):
            remote_check_dependencies(self.profile, self.mapping_path, executable=self.executable, allow_read=True)
        with self.assertRaises(ConfigError):
            ReviewedMcpClient(Mock(), EnvironmentMapping(self.profile, self.mapping_path))
        self.assertEqual(self.calls, [])

    def test_stable_mapping_has_no_generic_sql_or_catalogue_authority(self):
        review = RetainedRecheckReview(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01', self.recheck_path)
        raw = Mock()
        client = ScopedMappingClient(raw, EnvironmentMapping(self.profile, self.mapping_path), review)
        for call in (lambda: client.db_list(), lambda: client.table_list('testdb'),
                     lambda: client.db_query(object()), lambda: client.table_describe('testdb', 'bcs_session_files')):
            with self.assertRaises(ConfigError): call()
        self.assertEqual(raw.mock_calls, [])
        with self.assertRaises(ConfigError):
            mapping_dependencies(self.profile, self.mapping_path, review, executable=self.executable, allow_read=False)

    def test_dry_run_builds_plan_offline_without_renewal_or_credentials(self):
        original = self.mapping_path.read_bytes()
        with self.boundaries(): rc, result = self.command(self.args()+['--dry-run'])
        self.assertEqual(rc, 0)
        self.assertEqual(result['read_plan']['contract'], PLAN_CONTRACT)
        self.assertNotIn('expires_at', result['read_plan'])
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(self.calls, []); self.assertEqual(self.http, [])
        self.assertEqual(self.mapping_path.read_bytes(), original)
        self.assertFalse((self.state/'observations').exists())
        self.assert_original_unchanged()

    def test_actual_runtime_deadline_remains_enforced_without_approval_expiry(self):
        clock = [0.0]; original = self.process
        def slow(command, **kwargs):
            result = original(command, **kwargs)
            if command[2].endswith('.db_query'): clock[0] = 31
            return result
        with patch.object(self, 'process', side_effect=slow), patch('time.monotonic', side_effect=lambda: clock[0]):
            rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(report['execution_observation']['status'], 'TIMED_OUT')
        self.assertEqual(report['execution_observation']['sessions'], [])
        self.assert_original_unchanged()

    def test_cancelled_query_preserves_original_run_and_single_use_slot(self):
        original = self.process
        def cancel(command, **kwargs):
            if command[2].endswith('.db_query'): raise KeyboardInterrupt()
            return original(command, **kwargs)
        with patch.object(self, 'process', side_effect=cancel): rc, report = self.execute()
        self.assertEqual(rc, 130); self.assertEqual(report['status'], 'CANCELLED')
        self.assertTrue((self.state/'observations/read-plan-1').is_dir())
        self.assert_original_unchanged()


class MappedProducerTests(unittest.TestCase):
    import test_producer_inventory as fixtures
    command = fixtures.ProducerRecheckTests.command
    boundaries = fixtures.ProducerRecheckTests.boundaries
    process = fixtures.ProducerRecheckTests.process
    execute = fixtures.ProducerRecheckTests.execute
    assert_original_unchanged = fixtures.ProducerRecheckTests.assert_original_unchanged
    args = MappingRecheckTests.args

    def setUp(self):
        self.fixtures.ProducerRecheckTests.setUp(self)
        self.mapping_path = self.root/'mapping.json'
        self.mapping = confirmation(self.profile); write(self.mapping_path, self.mapping)
        prior = self.recheck
        self.recheck = recheck_plan(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01',
            mapping_confirmation=self.mapping_path)['read_plan']
        self.recheck.update(observation_id='mapped-producers', scope_observation=prior['scope_observation'],
                            execution_observation_budget=prior['execution_observation_budget'])
        write(self.recheck_path, self.recheck)

    def test_new_mapping_path_preserves_precise_producer_projection_and_call_budget(self):
        rc, report = self.execute()
        self.assertEqual(rc, 2, report)
        facts = report['execution_observation']['scope_observation']
        self.assertEqual(facts['status'], 'OBSERVED', report)
        self.assertEqual(len(facts['state_machine_inventories']), 1)
        self.assertEqual(len(self.calls), 8)  # no extra discovery or refresh query
        self.assertEqual(self.http, [])
        self.assert_original_unchanged()


class MappingFactTests(unittest.TestCase):
    def setUp(self):
        import test_runner
        self.profile = test_runner.profile()
        self.value = confirmation(self.profile)

    def test_mapping_is_target_bound_not_actor_or_credential_bound(self):
        changed = copy.deepcopy(self.profile)
        changed['actors']['driver']['bot_id'] = 'another-driver'
        changed['auth']['header_env']['Cookie'] = 'OTHER_COOKIE_ENV'
        changed['policy']['timeout_seconds'] += 1
        self.assertEqual(validate_confirmation(self.value, changed), self.value)
        for key, value in (('database', 'foreign'), ('server', 'another-mcp')):
            changed = copy.deepcopy(self.profile); changed['observer'][key] = value
            with self.assertRaises(ConfigError): validate_confirmation(self.value, changed)

    def test_target_change_and_unsupported_status_fail_closed(self):
        for edit in ({'status': 'REVOKED'}, {'status': 'PROPOSED'}, {'revision': True},
                     {'revision': 0}, {'confirmed_at': 0}, {'confirmed_at': int(time.time())+100},
                     {'target': {}}, {'database_sharing': 'unknown'}, {'query_authorized': True},
                     {'expires_at': int(time.time())+3600}, {'profile_digest': fingerprint(self.profile)}):
            with self.subTest(edit=edit), self.assertRaises(ConfigError):
                validate_confirmation({**self.value, **edit}, self.profile)

    def test_factual_confirmation_returns_detached_data(self):
        result = validate_confirmation(self.value, self.profile)
        result['target']['env'] = 'foreign'
        self.assertEqual(self.value['target']['env'], 'pre')
        self.assertNotIn('expires_at', self.value)
        self.assertNotIn('query_authorized', self.value)


if __name__ == '__main__': unittest.main()
