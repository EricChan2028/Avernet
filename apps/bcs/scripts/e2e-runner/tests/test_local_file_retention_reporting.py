"""Owned-local CLI file retention reporting boundaries."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_file_reporting import CONTRACT, LEGACY_CONTRACT, _project, _summary
from bcs_e2e.local_file_setup import REQUIRED as SETUP_ASSERTIONS
from bcs_e2e.local_file_story import CASE_ID, LEAVES, REQUIREMENTS
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import write
from bcs_e2e.reporting import render_junit, render_markdown
import xml.etree.ElementTree as ET


class _Ledger:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def resource_snapshot(self):
        return self.rows


class LocalFileRetentionReportingTests(unittest.TestCase):
    def binding(self, root):
        return {'contract': CONTRACT, 'run_id': 'retention-run', 'runtime_dir': str(root),
                'executor_digest': 'executor', 'bcs_binary_sha256': 'bcs',
                'required_assertions': ['CLI-FILE.' + name for name in REQUIREMENTS]}

    def setup(self, status='LOCAL_CLI_FILE_SETUP_PASS'):
        return {'schema_version': 1, 'case_id': CASE_ID, 'run_id': 'retention-run',
                'status': status, 'binding_digest': 'setup-binding',
                'executor_digest': 'executor',
                'participant_admission': {'operation': 'participant-visibility-public',
                    'status': 'ACKNOWLEDGED', 'resource_binding': 'BOUND_OWNED_ENG'},
                'assertions': [{'id': CASE_ID + '.' + name, 'status': 'PASS'}
                               for name in SETUP_ASSERTIONS],
                'operations': [{'operation': name, 'status': 'ACKNOWLEDGED',
                                'credential_cleanup': 'CLEARED'}
                               for name in ('group-create', 'session-create')],
                'resources': {'session': {'handle': 'session-handle'}}}

    def case(self, binding, setup):
        operations = []
        for operation in LEAVES:
            operations.append({'operation': operation, 'status': 'ACKNOWLEDGED',
                'credential_cleanup': 'CLEARED',
                'wire': [{'dispatch_status': 'RESPONSE_RECEIVED', 'http_status': 200}
                         for _ in range(3 if operation == 'upload' else 1)]})
        return {'schema_version': 1, 'case_id': CASE_ID, 'run_id': binding['run_id'],
                'binding_digest': fingerprint(binding), 'setup': setup, 'status': 'PASS',
                'assertions': [{'id': 'CLI-FILE.' + name, 'status': 'PASS'}
                               for name in REQUIREMENTS],
                'http_observations': [{'operation': op, 'http_status': 404,
                    'anonymous': anonymous, 'resource_id': 'file-1'}
                    for op, anonymous in [('deleted-metadata', False),
                        ('shared-deleted-metadata', True), ('shared-deleted-content', True)]],
                'operations': operations, 'file_id': 'file-1', 'file_handle': 'file-handle',
                'file_delete': 'PASS'}

    def test_success_projection_reports_only_recorded_owned_fixture_visibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binding = self.binding(root)
            setup = self.setup()
            write(root / 'owned-bcs.json', {'schema_version': 1, 'run_id': binding['run_id'],
                'binary_sha256': 'bcs', 'status': 'STOPPED', 'returncode': 0})
            write(root / 'cli-file-setup.json', setup)
            write(root / 'local-file-story.json', self.case(binding, setup))
            (root / 'cli-file-resources.sqlite').touch()
            rows = [
                {'kind': 'group', 'case_id': CASE_ID, 'attempt': 1, 'origin': 'created',
                 'state': 'live', 'handle': 'group', 'resource_id': 'group-1', 'parent': None},
                {'kind': 'session', 'case_id': CASE_ID, 'attempt': 1, 'origin': 'created',
                 'state': 'live', 'handle': 'initial', 'resource_id': 'session-1', 'parent': 'group'},
                {'kind': 'session', 'case_id': CASE_ID, 'attempt': 1, 'origin': 'created',
                 'state': 'live', 'handle': 'session-handle', 'resource_id': 'session-2', 'parent': 'group'},
                {'kind': 'file', 'case_id': CASE_ID, 'attempt': 1, 'origin': 'created',
                 'state': 'deleted', 'handle': 'file-handle', 'resource_id': 'file-1',
                 'parent': 'session-handle'},
            ]
            with patch('bcs_e2e.local_file_reporting.Ledger', return_value=_Ledger(rows)):
                case = _project(root, binding)
            self.assertEqual(case['status'], 'PASS')
            self.assertEqual(case['retained_visibility'], {
                'scope': 'OWNED_LOCAL_FIXTURE', 'role': 'ENG',
                'setup_visibility': 'public', 'restoration': 'NOT_PERFORMED',
                'current_state_verification': 'NOT_PERFORMED',
                'evidence_status': 'SETUP_ACKNOWLEDGED'})
            summary = _summary(binding, case, [])
            self.assertEqual(summary['cleanup']['retained_visibility'], case['retained_visibility'])
            self.assertEqual(summary['cases'][0]['retained_visibility'], case['retained_visibility'])
            xml = ET.fromstring(render_junit(summary))
            properties = {item.attrib['name']: item.attrib['value'] for item in xml.findall('.//property')}
            self.assertEqual(properties['retained_visibility.setup_visibility'], 'public')
            self.assertEqual(properties['retained_visibility.restoration'], 'NOT_PERFORMED')
            self.assertIn('setup_visibility=public', render_markdown(summary))
            self.assertIn('current_state_verification=NOT_PERFORMED', render_markdown(summary))
            # Missing new evidence must block v2, not silently inherit v1 coverage.
            raw = self.case(binding, setup)
            raw['http_observations'].pop(1)
            write(root / 'local-file-story.json', raw)
            with patch('bcs_e2e.local_file_reporting.Ledger', return_value=_Ledger(rows)):
                self.assertEqual(_project(root, binding)['status'], 'BLOCKED')
            # Archived v1 projections keep their original shape and denominator.
            binding['contract'] = LEGACY_CONTRACT
            raw['binding_digest'] = fingerprint(binding)
            write(root / 'local-file-story.json', raw)
            with patch('bcs_e2e.local_file_reporting.Ledger', return_value=_Ledger(rows)):
                legacy = _project(root, binding)
            self.assertEqual(legacy['status'], 'PASS')
            self.assertNotIn('retained_visibility', legacy)

    def test_failed_setup_visibility_is_not_verified(self):
        setup = self.setup(status='BLOCKED')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binding = self.binding(root)
            write(root / 'owned-bcs.json', {'schema_version': 1, 'run_id': binding['run_id'],
                'binary_sha256': 'bcs', 'status': 'STOPPED', 'returncode': 0})
            write(root / 'cli-file-setup.json', setup)
            case = _project(root, binding, failed=True)
        self.assertEqual(case['retained_visibility']['evidence_status'], 'FAILED_SETUP_NOT_VERIFIED')
        self.assertEqual(case['retained_visibility']['setup_visibility'], 'NOT_VERIFIED')
        self.assertEqual(case['retained_visibility']['restoration'], 'NOT_VERIFIED')

    def test_missing_setup_visibility_evidence_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binding = self.binding(root)
            write(root / 'owned-bcs.json', {'schema_version': 1, 'run_id': binding['run_id'],
                'binary_sha256': 'bcs', 'status': 'STOPPED', 'returncode': 0})
            case = _project(root, binding, failed=True)
        self.assertEqual(case['retained_visibility']['evidence_status'], 'SETUP_EVIDENCE_MISSING')
        self.assertEqual(case['retained_visibility']['setup_visibility'], 'NOT_VERIFIED')
        self.assertEqual(case['retained_visibility']['current_state_verification'], 'NOT_PERFORMED')


if __name__ == '__main__':
    unittest.main()
