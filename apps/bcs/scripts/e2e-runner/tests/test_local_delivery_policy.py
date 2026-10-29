"""Delivery-policy CAS parent: source map, guards and offline evidence."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_delivery_policy_contract import (
    CASE_ID,
    PATH,
    PARENT,
    REQUIREMENTS,
    SUITE,
    delivery_policy_source_binding,
)
from bcs_e2e.local_delivery_policy import OwnedLocalDeliveryPolicy
from bcs_e2e.local_delivery_policy_reporting import (
    _business_evidence,
    build_delivery_policy_report,
)
from bcs_e2e.local_delivery_policy_run import local_delivery_policy_run
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.inventory import current_manifest

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


def args(root, **changes):
    return dict(dict(binary=Path(BINARY), binary_sha256=SHA, state_dir=root,
                     run_id='delivery-test', confirm_run_id='delivery-test',
                     allow_local_processes=True, allow_fixture_write=True,
                     allow_write=True), **changes)


class DeliveryGuards(unittest.TestCase):
    def test_original_complete_source_map_and_drift_rejection(self):
        original = delivery_policy_source_binding()
        self.assertEqual(original['parent'], PARENT)
        self.assertEqual([x['id'] for x in original['requirements']], list(REQUIREMENTS))
        self.assertEqual([x['method'] for x in original['requirements']],
                         ['GET', 'GET', 'PUT', 'GET', 'PUT', 'GET', 'PUT', 'GET', 'PUT', 'GET'])
        for mutation in ('missing', 'reverse', 'method', 'path', 'hash'):
            value = copy.deepcopy(original)
            if mutation == 'missing':
                value['requirements'].pop()
            elif mutation == 'reverse':
                value['requirements'].reverse()
            elif mutation == 'method':
                value['requirements'][3]['method'] = 'PUT'
            elif mutation == 'path':
                value['requirements'][3]['path'] = '/admin/message-delivery/other'
            else:
                value['sources']['stories.sh'] = '0'*64
            with self.subTest(mutation=mutation), patch('bcs_e2e.local_delivery_policy_contract.read', return_value=value):
                with self.assertRaises(RunStateError):
                    delivery_policy_source_binding()

    def test_registry_keeps_delivery_parent_separate_and_no_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        row = next(r for r in plan['local_registered_executors'] if r['case_id'] == CASE_ID)
        self.assertEqual(row['requirement_ids'], list(REQUIREMENTS))
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(row['legacy_id'], PARENT)
        self.assertEqual(plan['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(plan['migrated_story_count'], 0)
        self.assertTrue(any(m.get('parent') == PARENT for m in plan['additional_source_maps']))

    def test_permissions_and_fresh_directory_precede_process_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_delivery_policy_run.OwnedBcsProcess') as proc:
            for key, value in (('allow_write', False), ('allow_fixture_write', False),
                               ('allow_local_processes', False), ('confirm_run_id', 'other')):
                with self.subTest(key=key), self.assertRaises(RunStateError):
                    local_delivery_policy_run(**args(Path(tmp)/'run', **{key: value}))
            with self.assertRaises(RunStateError):
                local_delivery_policy_run(**args(Path(tmp)))
            proc.assert_not_called()

    def test_unmanaged_identity_and_missing_cli_permissions_refused(self):
        with self.assertRaises(RunStateError):
            OwnedLocalDeliveryPolicy(object())
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(['local-delivery-policy-run', '--binary', '/unused', '--binary-sha256', '0'*64,
                       '--state-dir', str(Path(tmp)/'run'), '--run-id', 'denied', '--confirm-run-id', 'denied'])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out.getvalue())['status'], 'BLOCKED')

    def test_wire_refuses_foreign_path_and_duplicate_without_dispatch(self):
        case = object.__new__(OwnedLocalDeliveryPolicy)
        case._active = lambda: None
        case.state = {'operations': [{'operation': name, 'submitted_request_id': 'req-'+name}
                                     for name in REQUIREMENTS[:3]]}

        class _FakeIdentities:
            _entries = {'CEO': ('bot-id', 'token')}
        case.identities = _FakeIdentities()
        case._actor = lambda index: 'actor'
        row = {'operation': REQUIREMENTS[3], 'method': 'GET', 'path': '/admin/message-delivery/foreign',
               'actor': 'actor', 'submitted_request_id': 'request', 'status': 'INTENDED'}
        case.state['operations'].append(row)
        with patch('bcs_e2e.local_delivery_policy.HTTPConnection') as conn:
            with self.assertRaises(RunStateError):
                case._wire(3, row, None)
            row['path'] = PATH
            row['submitted_request_id'] = case.state['operations'][0]['submitted_request_id']
            with self.assertRaises(RunStateError):
                case._wire(3, row, None)
            conn.assert_not_called()


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'), 'approved real BCS required')
class DeliveryLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)/'run'
        cls.result = local_delivery_policy_run(**args(cls.root))
        if cls.result['status'] != 'LOCAL_DELIVERY_POLICY_PASS':
            raise AssertionError(json.dumps(cls.result, indent=2))

    def test_all10_steps_policy_restored_and_actual_stop(self):
        result, summary = self.result, self.result['canonical_summary']
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['requirements'], [{'id': x, 'status': 'PASS'} for x in REQUIREMENTS])
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(result['case']['resources'], {'created': [], 'deleted': [], 'unknown': []})
        self.assertEqual(result['case']['resource_disposition'],
                         'NO_BOT_CREATED_NO_GROUP_DELETED_POLICY_RESTORED')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        case = result['case']
        self.assertEqual(case['policy_version'], case['operations'][0]['observed']['version'])
        self.assertEqual(case['original_policy'], case['operations'][9]['observed']['policy'])
        self.assertEqual(case['policy_change']['field'], 'max_context_messages')
        self.assertNotEqual(case['policy_change']['from'], case['policy_change']['to'])
        junit = ET.parse(self.root/'reports/junit.xml').getroot()
        self.assertEqual([junit.attrib[k] for k in ('tests', 'failures', 'errors', 'skipped')], ['1', '0', '0', '0'])
        for name in REQUIREMENTS:
            self.assertIn(name, (self.root/'reports/summary.md').read_text())

    def test_offline_read_only_and_source_corruption_rejected(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob('*') if p.is_file()}
        with patch('http.client.HTTPConnection.request', side_effect=AssertionError('offline')):
            self.assertEqual(build_delivery_policy_report(self.root), self.result['canonical_summary'])
        receipt = read(self.root/'delivery-policy-completion.json')
        for name in [*receipt['sources'], *receipt['outputs']]:
            p = self.root/name
            raw = p.read_bytes()
            try:
                p.write_bytes(raw+b' ')
                try:
                    value = build_delivery_policy_report(self.root)
                except (RunStateError, ValueError):
                    continue
                self.assertNotEqual(value['status'], 'LOCAL_SUITE_PASS')
            finally:
                p.write_bytes(raw)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in self.root.rglob('*') if p.is_file()})
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['local-delivery-policy-report', '--state-dir', str(self.root),
                       '--output-dir', str(self.root/'reports')])
        self.assertEqual(rc, 2)

    def test_semantic_projection_rejects_unknown_foreign_or_duplicate(self):
        binding = read(self.root/'delivery-policy-binding.json')
        case = self.result['case']
        self.assertTrue(_business_evidence(self.root, binding, case))
        for edit in ('unknown', 'foreign-path', 'duplicate-request', 'created-resource',
                     'version-mismatch', 'actor-mismatch'):
            value = copy.deepcopy(case)
            if edit == 'unknown':
                value['operations'][0]['status'] = 'UNKNOWN'
            elif edit == 'foreign-path':
                value['operations'][5]['path'] = '/admin/message-delivery/foreign'
            elif edit == 'duplicate-request':
                value['operations'][1]['submitted_request_id'] = value['operations'][0]['submitted_request_id']
            elif edit == 'created-resource':
                value['resources']['created'] = ['unexpected']
            elif edit == 'version-mismatch':
                value['policy_version'] = value['policy_version'] + 5
            else:
                value['human_actor'] = 'human_intruder'
            with self.subTest(edit=edit):
                self.assertFalse(_business_evidence(self.root, binding, value))
