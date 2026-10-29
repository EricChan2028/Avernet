"""Admission, complete real-network Story and offline evidence invariants."""
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
from bcs_e2e.local_network_contract import network_source_binding, CASE_ID, REQUIREMENTS
from bcs_e2e.local_network_run import local_network_run
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.inventory import current_manifest

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
ASSET = os.environ.get('BCS_E2E_TEST_PANEL_ASSET', '')
ASSET_SHA = os.environ.get('BCS_E2E_TEST_PANEL_SHA256', '')


def arguments(root, **overrides):
    args = dict(binary=Path(BINARY), binary_sha256=DIGEST,
                panel_asset=Path(ASSET), panel_sha256=ASSET_SHA, state_dir=root,
                run_id='network-acceptance', confirm_run_id='network-acceptance',
                allow_local_processes=True, allow_fixture_write=True, allow_write=True)
    return dict(args, **overrides)


class NetworkAdmissionTests(unittest.TestCase):
    def test_permissions_and_confirmation_are_required_before_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_network_run.OwnedBcsProcess') as runtime:
            for key, value in (('allow_local_processes', False), ('allow_fixture_write', False),
                               ('allow_write', False), ('confirm_run_id', 'other')):
                root = Path(tmp)/key
                with self.assertRaises(RunStateError):
                    local_network_run(**arguments(root, **{key: value}))
                self.assertFalse(root.exists())
            runtime.assert_not_called()

    def test_existing_directory_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_network_run.OwnedBcsProcess') as runtime:
            with self.assertRaises(RunStateError):
                local_network_run(**arguments(Path(tmp)))
            runtime.assert_not_called()

    def test_complete_source_mapping_is_ordered_and_drift_is_rejected(self):
        original = network_source_binding()
        self.assertEqual([r['id'] for r in original['requirements']], list(REQUIREMENTS))
        for kind in ('missing', 'duplicate', 'reorder', 'digest'):
            value = copy.deepcopy(original)
            if kind == 'missing': value['requirements'].pop()
            elif kind == 'duplicate': value['requirements'][-1] = value['requirements'][0]
            elif kind == 'reorder': value['requirements'].reverse()
            else: value['sources']['actor.sh'] = '0'*64
            with self.subTest(kind=kind), patch('bcs_e2e.local_network_contract.read', return_value=value):
                with self.assertRaises(RunStateError): network_source_binding()

    def test_network_inventory_adds_no_release_credit_or_historical_rewrite(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        row = next(r for r in plan['local_registered_executors'] if r['case_id'] == CASE_ID)
        self.assertEqual(row['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(row['canonical_reporting'], 'INTEGRATED_LOCAL')
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['mapped_story_count'], 3)  # Original frozen three-parent map.
        self.assertEqual(sum(row['parent'] == 'story_user_prepares_agent_network'
                             for row in plan['additional_source_maps']), 1)
        self.assertEqual(plan['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(plan['migrated_story_count'], 0)

    def test_cli_needs_no_remote_profile_and_blocks_missing_permission(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(['local-network-run', '--binary', '/unused', '--binary-sha256', '0'*64,
                       '--panel-asset', '/unused.js', '--panel-sha256', '1'*64,
                       '--state-dir', str(Path(tmp)/'run'), '--run-id', 'network-no-write',
                       '--confirm-run-id', 'network-no-write'])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')


@unittest.skipUnless(BINARY and DIGEST and ASSET and ASSET_SHA and shutil.which('lsof'),
                     'approved actual BCS and real panel artifact required')
class NetworkLifecycleLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)/'run'
        cls.result = local_network_run(**arguments(cls.root))
        if cls.result.get('status') != 'LOCAL_NETWORK_PASS':
            raise AssertionError(json.dumps(cls.result, indent=2))

    def test_all_original_obligations_and_three_reports(self):
        result = self.result
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        summary = result['canonical_summary']
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['requirements'], [{'id': r, 'status': 'PASS'} for r in REQUIREMENTS])
        self.assertEqual(summary['scope']['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(set(result['outputs']['outputs']), {'json', 'junit', 'markdown'})
        junit = ET.parse(self.root/'reports/junit.xml').getroot()
        self.assertEqual([junit.attrib[k] for k in ('tests', 'failures', 'errors', 'skipped')], ['1', '0', '0', '0'])
        self.assertEqual(junit.attrib['name'], 'bcs-e2e-network')
        for key in REQUIREMENTS:
            self.assertIn(key, (self.root/'reports/summary.md').read_text())

    def test_offline_reader_is_identical_without_credentials_or_network(self):
        from bcs_e2e.local_network_reporting import build_network_report
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('offline')):
            self.assertEqual(build_network_report(self.root), self.result['canonical_summary'])

    def test_all_bound_sources_and_report_outputs_reject_drift(self):
        from bcs_e2e.local_network_reporting import build_network_report
        completion = read(self.root/'network-completion.json')
        for name in [*completion['sources'], *completion['outputs']]:
            path = self.root/name
            original = path.read_bytes()
            try:
                path.write_bytes(original+b' ')
                with self.subTest(name=name):
                    try:
                        observed = build_network_report(self.root)
                    except (RunStateError, ValueError):
                        continue
                    self.assertNotEqual(observed['status'], 'LOCAL_SUITE_PASS')
            finally:
                path.write_bytes(original)
        self.assertEqual(build_network_report(self.root)['status'], 'LOCAL_SUITE_PASS')

    def test_projection_rejects_false_business_success_even_without_hash_checks(self):
        from bcs_e2e.local_network_reporting import _business_evidence
        binding = read(self.root/'network-binding.json')
        case = read(self.root/'local-network-api.json')
        self.assertTrue(_business_evidence(self.root, binding, case))
        variants = []
        value = copy.deepcopy(case); value['operations'].pop(); variants.append(value)
        value = copy.deepcopy(case); value['operations'][0]['status'] = 'UNKNOWN'; variants.append(value)
        value = copy.deepcopy(case); value['operations'][0]['transitions'] = []; variants.append(value)
        value = copy.deepcopy(case); value['non_target_after'] = {}; variants.append(value)
        value = copy.deepcopy(case); value['credentials'] = {}; variants.append(value)
        value = copy.deepcopy(case); value['platform']['panel']['sha256'] = '0'*64; variants.append(value)
        value = copy.deepcopy(case)
        next(op for op in value['operations'] if op['name'] == 'delete-owned-bot')['path'] = '/bots/foreign'
        variants.append(value)
        for value in variants:
            self.assertFalse(_business_evidence(self.root, binding, value))
        ledger = read(self.root/'local-network-ledger.json')
        for kind in ('unknown', 'foreign', 'request', 'transition'):
            mutated = copy.deepcopy(ledger)
            if kind == 'unknown': mutated['resource']['state'] = 'UNKNOWN'
            elif kind == 'foreign': mutated['resource']['resource_id'] = 'foreign'
            elif kind == 'request': mutated['resource']['request_id'] = 'unrelated'
            else: mutated['transitions'].pop()
            from bcs_e2e.local_network_reporting import read as real_read
            def altered(path):
                return mutated if path.name == 'local-network-ledger.json' else real_read(path)
            with patch('bcs_e2e.local_network_reporting.read', side_effect=altered):
                self.assertFalse(_business_evidence(self.root, binding, case))

    def test_pending_publication_and_source_symlink_never_pass(self):
        from bcs_e2e.local_network_reporting import build_network_report
        pending = self.root/'network-publication-pending.json'
        pending.write_text('{}')
        try:
            self.assertNotEqual(build_network_report(self.root)['status'], 'LOCAL_SUITE_PASS')
        finally:
            pending.unlink()
        original = self.root/'local-network-api.json'
        held = self.root/'saved-journal.json'
        original.rename(held)
        original.symlink_to(held)
        try:
            self.assertNotEqual(build_network_report(self.root)['status'], 'LOCAL_SUITE_PASS')
        finally:
            original.unlink(); held.rename(original)

    def test_cli_offline_does_not_overwrite_original_reports(self):
        before = hashlib.sha256((self.root/'reports/summary.json').read_bytes()).hexdigest()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(['local-network-report', '--state-dir', str(self.root),
                       '--output-dir', str(self.root/'reports')])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out.getvalue())['status'], 'BLOCKED')
        self.assertEqual(hashlib.sha256((self.root/'reports/summary.json').read_bytes()).hexdigest(), before)
