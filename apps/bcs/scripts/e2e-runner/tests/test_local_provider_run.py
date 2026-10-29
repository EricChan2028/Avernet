"""Whole Provider parent lifecycle, immutable reports and failure attribution."""
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
from bcs_e2e.local_provider_contract import CASE_ID, REQUIREMENTS
from bcs_e2e.local_provider_run import local_provider_run
from bcs_e2e.run_state import RunStateError, read
BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


def arguments(root, **overrides):
    return dict(dict(binary=Path(BINARY), binary_sha256=DIGEST, state_dir=root,
                     run_id='provider-flow-test', confirm_run_id='provider-flow-test',
                     allow_local_processes=True, allow_fixture_write=True, allow_write=True), **overrides)


class ProviderAdmissionTests(unittest.TestCase):
    def test_permissions_and_confirmation_precede_sink_or_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_provider_run.OwnedLocalProviderSink') as sink:
            for key, value in (('allow_local_processes', False), ('allow_fixture_write', False),
                               ('allow_write', False), ('confirm_run_id', 'foreign')):
                with self.subTest(key=key), self.assertRaises(RunStateError):
                    local_provider_run(**arguments(Path(tmp)/'run', **{key: value}))
            with self.assertRaises(RunStateError): local_provider_run(**arguments(Path(tmp)))
            sink.assert_not_called()

    def test_cli_requires_explicit_permissions_without_remote_profile(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(['local-provider-run', '--binary', '/unused', '--binary-sha256', '0'*64,
                       '--state-dir', str(Path(tmp)/'run'), '--run-id', 'denied', '--confirm-run-id', 'denied'])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'), 'approved real BCS binary required')
class ProviderFlowLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)/'run'
        cls.result = local_provider_run(**arguments(cls.root))
        if cls.result['status'] != 'LOCAL_PROVIDER_PASS':
            raise AssertionError(json.dumps(cls.result, indent=2))

    def test_full32_parent_is_local_only_and_retention_is_explicit(self):
        result = self.result; summary = result['canonical_summary']
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['requirements'], [{'id': x, 'status': 'PASS'} for x in REQUIREMENTS])
        self.assertEqual(summary['reports'], {'expected_assertion_count': 32, 'observed_assertion_count': 32})
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['scope']['legacy_inventory_count'], 34)
        self.assertEqual(summary['cases'][0]['cleanup'], 'RETAINED')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['downstream']['state'], 'CLOSED')
        self.assertFalse(result['downstream']['failed'])
        self.assertEqual(result['downstream']['rejected'], 1)
        junit = ET.parse(self.root/'reports/junit.xml').getroot()
        self.assertEqual(junit.attrib['name'], 'bcs-e2e-provider')
        self.assertEqual([junit.attrib[k] for k in ('tests','failures','errors','skipped')], ['1','0','0','0'])
        for requirement in REQUIREMENTS: self.assertIn(requirement, (self.root/'reports/summary.md').read_text())

    def test_offline_report_is_identical_without_requests_or_file_changes(self):
        from bcs_e2e.local_provider_reporting import build_provider_report
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('offline')):
            self.assertEqual(build_provider_report(self.root), self.result['canonical_summary'])
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_source_and_output_drift_blocks_without_overwriting_original(self):
        from bcs_e2e.local_provider_reporting import build_provider_report
        receipt = read(self.root/'provider-completion.json')
        for name in [*receipt['sources'], *receipt['outputs']]:
            path = self.root/name; original = path.read_bytes()
            try:
                path.write_bytes(original+b' ')
                with self.subTest(name=name):
                    try: result = build_provider_report(self.root)
                    except (RunStateError, ValueError): continue
                    self.assertNotEqual(result['status'], 'LOCAL_SUITE_PASS')
            finally: path.write_bytes(original)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['local-provider-report', '--state-dir', str(self.root), '--output-dir', str(self.root/'reports')])
        self.assertEqual(rc, 2)
        self.assertEqual(build_provider_report(self.root)['status'], 'LOCAL_SUITE_PASS')

    def test_failure_of_semantic_evidence_cannot_be_projected_as_pass(self):
        from bcs_e2e.local_provider_reporting import _business_evidence
        binding = read(self.root/'provider-binding.json')
        case = read(self.root/'local-provider-api.json')
        self.assertTrue(_business_evidence(self.root, binding, case))
        for change in ('missing', 'duplicate', 'unknown'):
            mutated = copy.deepcopy(case)
            if change == 'missing': mutated['operations'].pop()
            elif change == 'duplicate': mutated['operations'][1] = mutated['operations'][0]
            else: mutated['operations'][0]['status'] = 'UNKNOWN'
            with self.subTest(change=change): self.assertFalse(_business_evidence(self.root, binding, mutated))


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'), 'approved real BCS binary required')
class ProviderFailureLiveTests(unittest.TestCase):
    def test_cancellation_stops_owned_handles_without_claiming_parent_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            with patch('bcs_e2e.local_provider_api.OwnedLocalProviderApi.run', side_effect=KeyboardInterrupt):
                result = local_provider_run(**arguments(root))
            self.assertEqual(result['status'], 'CANCELLED')
            self.assertEqual(result['case_id'], CASE_ID)
            self.assertEqual(result['shutdown']['status'], 'STOPPED')
            self.assertEqual(result['downstream']['state'], 'CLOSED')
            self.assertEqual(result['canonical_summary']['status'], 'CANCELLED')
