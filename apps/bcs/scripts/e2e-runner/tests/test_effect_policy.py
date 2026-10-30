"""Reject undeclared cleanup capabilities before a new Run acquires resources."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import plan
from bcs_e2e.effect_policy import policy_summary, validate_effect_policy
from bcs_e2e.inventory import current_manifest, freeze_manifest, validate_manifest
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load
from bcs_e2e.run_state import RunStateError

ROOT = Path(__file__).resolve().parents[1]


class EffectPolicyTests(unittest.TestCase):
    def manifest(self):
        return json.loads((ROOT / 'manifest.json').read_text())

    def test_all_existing_shared_cases_keep_identical_effects_and_versions(self):
        manifest = self.manifest(); before = copy.deepcopy(manifest)
        validate_effect_policy(validate_manifest(manifest))
        self.assertEqual(manifest, before)
        self.assertEqual(current_manifest(), before)
        self.assertEqual([case['version'] for case in manifest['cases']], [3, 3, 3])
        self.assertEqual(len(manifest['legacy_inventory']), 41)

    def test_unknown_retained_or_broad_cleanup_effects_are_not_implicitly_admitted(self):
        for effect in ('create:dm', 'delete:provider', 'disable:organization', 'purge:message_history',
                       'delete:bucket', 'delete:all_groups', 'retain:definition', 'update:borrowed_bot'):
            manifest = self.manifest(); manifest['cases'][0]['effects'].append(effect)
            with self.subTest(effect=effect), self.assertRaises(RunStateError):
                validate_effect_policy(manifest)

    def test_implicit_session_and_each_created_resource_need_cleanup_declarations(self):
        for index, effect in ((0, 'create:initial_session'), (0, 'delete:owned_initial_session'),
                              (0, 'delete:owned_group'), (1, 'delete:owned_session'),
                              (2, 'delete:owned_file'), (2, 'create:session'), (2, 'upload:file')):
            manifest = self.manifest(); manifest['cases'][index]['effects'].remove(effect)
            with self.subTest(effect=effect), self.assertRaises(RunStateError):
                validate_effect_policy(manifest)

    def test_borrowed_only_update_or_delete_cannot_omit_current_run_creation(self):
        for effect in ('update:owned_group', 'update:owned_session', 'delete:owned_file',
                       'share:owned_file', 'mint:session_token'):
            manifest = self.manifest(); manifest['cases'][0]['effects'] = [effect]
            with self.subTest(effect=effect), self.assertRaises(RunStateError):
                validate_effect_policy(manifest)

    def test_file_effects_do_not_expand_the_original_retained_suite(self):
        manifest = current_manifest('preprod-retained-v1')
        manifest['cases'][1]['effects'].extend(['upload:file', 'share:owned_file', 'retain:owned_file'])
        with self.assertRaises(RunStateError):
            validate_effect_policy(manifest)

    def test_retained_file_dependencies_and_no_delete_contract(self):
        manifest = current_manifest('preprod-retained-file-v1')
        validate_effect_policy(manifest)
        for missing in ('create:session', 'upload:file', 'retain:owned_file'):
            changed = copy.deepcopy(manifest)
            changed['cases'][0]['effects'].remove(missing)
            with self.subTest(missing=missing), self.assertRaises(RunStateError):
                validate_effect_policy(changed)
        manifest['cases'][0]['effects'].append('delete:owned_file')
        with self.assertRaises(RunStateError):
            validate_effect_policy(manifest)

    def test_failed_freeze_does_not_write_or_acquire_lease(self):
        manifest = self.manifest(); manifest['cases'][0]['effects'].append('delete:bucket')
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            profile = load(ROOT / 'profiles/local.mock.json')
            with patch('bcs_e2e.prepare.PoolLease.acquire') as lease, patch('bcs_e2e.prepare.Ledger') as ledger:
                with self.assertRaises(RunStateError):
                    prepare_run(profile, state, 'unreviewed-effects', manifest=manifest)
                lease.assert_not_called(); ledger.assert_not_called()
            self.assertFalse((state / 'manifest.json').exists())
            self.assertFalse((state / 'run.sqlite').exists())

    def test_historical_schema_parser_does_not_apply_current_execution_policy(self):
        historical = self.manifest(); historical['cases'][0]['effects'].append('historical:effect')
        # Rendering an old snapshot must not silently rewrite its declarations.
        # This does not authorize executing it or grant release qualification.
        self.assertEqual(validate_manifest(historical), historical)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RunStateError):
            freeze_manifest(Path(tmp), historical)

    def test_plan_exposes_policy_without_claiming_authority_or_erasure(self):
        profile = load(ROOT / 'profiles/local.mock.json')
        result = plan(profile)
        self.assertEqual(result['effect_policy'], policy_summary())
        self.assertFalse(result['ready_to_execute'])
        self.assertFalse(result['effect_policy']['grants_delete_authority'])
        self.assertFalse(result['effect_policy']['proves_physical_erasure'])
        self.assertIn('retained-data ownership and reclamation procedures', result['remaining_before_qualification'])

    def test_current_manifest_loader_rejects_unapproved_effects_before_execution(self):
        manifest = self.manifest(); manifest['cases'][0]['effects'].append('delete:dm')
        with patch('bcs_e2e.inventory.read', return_value=manifest), self.assertRaises(RunStateError):
            current_manifest()
