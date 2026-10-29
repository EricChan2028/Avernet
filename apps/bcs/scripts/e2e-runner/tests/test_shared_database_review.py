"""Shared-database owner confirmation is explicit, scoped and honestly labelled."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.evidence import _make_plan
from bcs_e2e.profile import ConfigError
from bcs_e2e.remote_observer import (FILENAME, InputReviewedBinding, ReviewedBinding, ReviewedMcpClient,
                                    binding_summary, validate_binding)
from bcs_e2e.reporting import build_report, render_junit, render_markdown
from bcs_e2e.run_state import read, write
import test_remote_observer_cli as harness


class SharedDatabaseReviewTests(unittest.TestCase):
    command = harness.RemoteObserverCliTests.command
    prepare = harness.RemoteObserverCliTests.prepare
    run_args = harness.RemoteObserverCliTests.run_args
    process = harness.RemoteObserverCliTests.process
    composed = harness.RemoteObserverCliTests.composed

    def setUp(self):
        harness.RemoteObserverCliTests.setUp(self)
        self.legacy = copy.deepcopy(self.receipt)
        self.receipt['verification_mode'] = 'OWNER_CONFIRMED_SHARED_DATABASE'
        self.receipt['deployment_commit'] = 'unknown'
        self.receipt.pop('source_reviews')
        self.receipt['owner_confirmation'] = {
            'reference': 'private-owner-confirmation', 'sha256': 'e' * 64,
            'database_sharing': 'preprod-and-production', 'mapping_confirmed': True,
            'tables_without_env_excluded': True, 'read_scope': 'current-case-resource-id-and-env',
            'allowed_assertions': ['group.persisted', 'session.persisted', 'file.persisted'],
        }
        self.receipt['expires_at'] = self.receipt['reviewed_at'] + 600
        write(self.receipt_path, self.receipt)

    def test_explicit_prepare_freezes_owner_basis_without_fabricating_sources_or_commit(self):
        self.prepare()
        frozen = read(self.state / FILENAME)
        self.assertEqual(frozen, self.receipt)
        self.assertNotIn('source_reviews', frozen)
        self.assertEqual(frozen['deployment_commit'], 'unknown')
        summary = read(self.state / 'run.json')['observer_binding']
        self.assertEqual(summary['review_basis'], 'OWNER_CONFIRMED_SHARED_DATABASE')
        self.assertEqual(summary['independent_config_review'], 'NOT_PERFORMED')
        self.assertEqual(summary['live_verification'], 'NOT_PERFORMED')
        self.assertEqual(summary['deployment_version_source'], 'UNVERIFIED')

    def test_only_owner_explicit_shared_scope_is_accepted(self):
        for key, bad in (('mapping_confirmed', False), ('tables_without_env_excluded', False),
                         ('database_sharing', 'preprod-only'), ('read_scope', 'all-database-rows'),
                         ('sha256', 'unknown'), ('reference', 'https://private.test'),
                         ('allowed_assertions', []), ('allowed_assertions', ['other-table']),
                         ('allowed_assertions', ['group.persisted', 'group.persisted'])):
            value = copy.deepcopy(self.receipt); value['owner_confirmation'][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(ConfigError):
                validate_binding(value, self.profile)
        for key, bad in (('query_authorized', False), ('verification_mode', 'LIVE_VERIFIED'),
                         ('deployment_commit', 'a' * 40)):
            value = copy.deepcopy(self.receipt); value[key] = bad
            with self.subTest(key=key), self.assertRaises(ConfigError): validate_binding(value, self.profile)

    def test_boolean_or_legacy_receipt_does_not_silently_convert_to_shared_mode(self):
        p = copy.deepcopy(self.profile)
        self.assertTrue(p['observer']['environment_binding_confirmed'])
        for value in ({}, {'schema_version': True}, {'schema_version': 3}):
            with self.assertRaises(ConfigError): validate_binding(value, p)
        value = copy.deepcopy(self.legacy); value['deployment_commit'] = 'unknown'
        with self.assertRaises(ConfigError): validate_binding(value, p)
        value = copy.deepcopy(self.receipt); value['source_reviews'] = self.legacy['source_reviews']
        with self.assertRaises(ConfigError): validate_binding(value, p)
        self.assertEqual(validate_binding(self.legacy, p), self.legacy)
        self.assertNotIn('review_basis', binding_summary(self.legacy))

    def test_shared_review_is_short_lived_and_historical_reporting_is_not_reauthorization(self):
        value = copy.deepcopy(self.receipt); value['expires_at'] = value['reviewed_at'] + 3601
        with self.assertRaises(ConfigError): validate_binding(value, self.profile)
        value = copy.deepcopy(self.receipt); value['reviewed_at'] = int(time.time()) - 60
        value['expires_at'] = int(time.time()) - 1
        with self.assertRaises(ConfigError): validate_binding(value, self.profile)
        self.assertEqual(validate_binding(value, self.profile, execution=False), value)

    def test_unknown_fields_wrong_target_and_profile_drift_block(self):
        for mutation in ('unknown', 'wrong-db', 'wrong-env', 'profile-digest', 'missing-confirmation'):
            value = copy.deepcopy(self.receipt)
            if mutation == 'unknown': value['owner_confirmation']['extra'] = True
            if mutation == 'wrong-db': value['target']['database'] = 'otherdb'
            if mutation == 'wrong-env': value['target']['env'] = 'prod'
            if mutation == 'profile-digest': value['profile_digest'] = 'b' * 64
            if mutation == 'missing-confirmation': value.pop('owner_confirmation')
            with self.subTest(mutation=mutation), self.assertRaises(ConfigError): validate_binding(value, self.profile)

    def test_shared_query_rejects_broad_foreign_or_modified_plan_before_client(self):
        binding = InputReviewedBinding(self.profile, self.receipt_path)
        raw = Mock(); client = ReviewedMcpClient(raw, binding)
        plan = _make_plan('group.persisted', 'owned-group', 'testdb', 'pre')
        bad = [replace(plan, table='unscoped_table'), replace(plan, database='otherdb'),
               replace(plan, select=('group_id',)), replace(plan, limit=100),
               replace(plan, filters={'group_id': 'owned-group'}),
               replace(plan, filters={'env': 'prod', 'group_id': 'owned-group'}),
               replace(plan, filters={'env': 'pre', 'group_id': "x' OR 1=1"}),
               replace(plan, filters={'env': 'pre', 'group_id': 'owned-group', 'owner': 'any'})]
        for candidate in bad:
            with self.subTest(candidate=candidate), self.assertRaises(ConfigError): client.db_query(candidate)
        raw.db_query.assert_not_called()
        client.db_query(plan); raw.db_query.assert_called_once_with(plan)

    def test_unapproved_assertion_cannot_expand_read_scope(self):
        self.receipt['owner_confirmation']['allowed_assertions'] = ['group.persisted']
        write(self.receipt_path, self.receipt)
        raw = Mock(); client = ReviewedMcpClient(raw, InputReviewedBinding(self.profile, self.receipt_path))
        with self.assertRaises(ConfigError): client.db_query(_make_plan('file.persisted', 'file-1', 'testdb', 'pre'))
        raw.db_query.assert_not_called()

    def test_confirmation_changes_after_prepare_stop_mcp(self):
        self.prepare(); binding = ReviewedBinding(self.profile, self.state, self.run_id)
        value = read(self.state / FILENAME); value['owner_confirmation']['sha256'] = 'f' * 64
        write(self.state / FILENAME, value)
        raw = Mock(); client = ReviewedMcpClient(raw, binding)
        with self.assertRaises(ConfigError): client.db_list()
        raw.db_list.assert_not_called()

    def test_complete_pilot_uses_same_scoped_query_contract_and_reports_unknown_deployment(self):
        self.prepare()
        with self.composed():
            for case in ('PRE-01', 'PRE-02', 'PRE-03'):
                rc, report = self.command(self.run_args(case))
                self.assertEqual(rc, 0, report)
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['cleanup'], 'PASS')
                self.assertEqual(report['observer_binding']['review_basis'], 'OWNER_CONFIRMED_SHARED_DATABASE')
        summary = build_report(self.profile, self.state)
        self.assertEqual(summary['status'], 'PILOT_PASS')
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['target']['deployment_version'], 'unknown')
        self.assertEqual(summary['observer_binding']['declared_deployment_commit'], 'unknown')
        text = json.dumps(summary) + render_markdown(summary) + render_junit(summary)
        self.assertIn('OWNER_CONFIRMED_SHARED_DATABASE', text)
        for secret in ('private-owner-confirmation', 'private-reviewer'):
            self.assertNotIn(secret, text)
        self.assertEqual(len([call for call in self.calls if call[2].endswith('.db_query')]), 4)


    def test_malformed_mode_is_structured_for_input_and_historical_report(self):
        for malformed in ([], {}, None, True):
            value = copy.deepcopy(self.receipt); value['verification_mode'] = malformed
            with self.subTest(mode=malformed), self.assertRaises(ConfigError): validate_binding(value, self.profile)
        self.prepare()
        value = read(self.state / FILENAME); value['verification_mode'] = []
        write(self.state / FILENAME, value)
        self.assertEqual(build_report(self.profile, self.state)['observer_binding']['verification_mode'], 'INVALID')

    def test_case_missing_required_read_permission_blocks_before_any_resource_intent(self):
        self.receipt['owner_confirmation']['allowed_assertions'] = ['file.persisted']
        write(self.receipt_path, self.receipt); self.prepare()
        with self.composed():
            rc, result = self.command(self.run_args('PRE-01'))
        self.assertEqual(rc, 2)
        self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['cleanup'], 'NOT_ATTEMPTED')
        from bcs_e2e.ledger import Ledger
        from bcs_e2e.profile import fingerprint
        with Ledger(self.state / 'run.sqlite', self.run_id, fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual(ledger.resource_snapshot(), [])
        self.assertEqual([call for call in self.calls if call[2].endswith('.db_query')], [])
