"""Lifecycle, drift and redaction tests for the Draft delivery owned-local run."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.draft_delivery.contract import CASE_ID, STATUS, STORY_CHECKS
from bcs_e2e.draft_delivery.run import (BINDING_NAME, EVIDENCE_NAME, FLOW_NAME,
                                        local_draft_delivery_run)
from bcs_e2e.run_state import RunStateError, read

CONTENT_HASH = '761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7'
UPDATED_HASH = 'a' * 64
DRAFT_ID = '750c52c6-60e2-4249-8ee5-1fb8cb9402ab'
GOOD_MARKER = ('Draft delivery story: ' + str(STORY_CHECKS)
               + ' live HTTP assertions passed').encode()


def valid_evidence() -> dict:
    responses = [
        {'method': method, 'path': '/collaboration/drafts/' + DRAFT_ID + suffix,
         'status': status, 'cache_control': 'no-store'}
        for method, suffix, status in (
            ('PUT', '', 201), ('PUT', '', 200), ('GET', '/revisions/1', 200),
            ('POST', '/revisions/1/validate', 200), ('POST', '/revisions', 409),
            ('DELETE', '/revisions/2/share', 204), ('DELETE', '', 401),
            ('GET', '/revisions/2', 404))
    ]
    return {
        'fixture': {'content_hash': CONTENT_HASH},
        'create': {
            'draft_id': DRAFT_ID, 'content_hash': CONTENT_HASH,
            'attempts': [
                {'status': 201, 'draft_id': DRAFT_ID, 'revision': 1,
                 'size_bytes': 2174, 'content_hash': CONTENT_HASH, 'valid': True},
                {'status': 200, 'draft_id': DRAFT_ID, 'revision': 1,
                 'size_bytes': 2174, 'content_hash': CONTENT_HASH, 'valid': True},
            ],
            'different_content': {'status': 409},
        },
        'append': {
            'head_before': 1, 'expected_revision': 1, 'status': 200, 'revision': 2,
            'head_after': 2, 'new_content_hash': UPDATED_HASH,
            'original_after': {'status': 200, 'revision': 1, 'size_bytes': 2174,
                               'content_hash': CONTENT_HASH},
            'updated_after': {'status': 200, 'revision': 2, 'content_hash': UPDATED_HASH},
        },
        'stale': {
            'status': 409, 'expected_revision': 1, 'head_after': 2,
            'new_content_hash': UPDATED_HASH, 'original_content_hash': CONTENT_HASH,
            'latest_after': {'status': 200, 'revision': 2, 'content_hash': UPDATED_HASH},
        },
        'cards': {
            'head_revision': 2,
            'historical': {'draft_id': DRAFT_ID, 'revision': 1, 'content_hash': CONTENT_HASH,
                           'compat_field_present': True, 'compat_revision': 1,
                           'read_with_token_as_header': False,
                           'read_without_token': {'status': 200, 'revision': 1,
                                                  'size_bytes': 2174,
                                                  'content_hash': CONTENT_HASH}},
            'other_revision': {'revision': 2, 'compat_revision': 2},
        },
        'availability': {
            'revoked': {'status': 404, 'error': 'DRAFT_REFERENCE_UNAVAILABLE'},
            'historical_sibling': {'status': 200, 'content_hash': CONTENT_HASH},
            'deleted': {'status': 404, 'error': 'DRAFT_REFERENCE_UNAVAILABLE'},
            'late_create': {'method': 'PUT', 'status': 404},
        },
        'management': {
            'public_delete': {'status': 401}, 'token_delete': {'status': 401},
            'token_update': {'status': 401}, 'head_after': 2, 'deleted_after': False,
        },
        'responses': responses,
    }


class FakeOwnedBcs:
    instances = []

    def __init__(self, *, binary, binary_sha256, root, run_id, allow_ambient_local_identity):
        self.root = Path(root)
        self.binary = Path(binary)
        self.binary_sha256 = binary_sha256
        self.run_id = run_id
        self.inputs = SimpleNamespace(human_id='e2ehumanabcdef0123456789',
                                      origin='http://127.0.0.1:12345')
        self.state = 'NEW'
        self.returncode = 0
        FakeOwnedBcs.instances.append(self)

    def __enter__(self):
        self.root.mkdir(parents=True)
        self.root.joinpath('console.log').write_text('owned runtime console clean\n', encoding='utf-8')
        self.state = 'RUNNING'
        return self

    def __exit__(self, *_args):
        self.state = 'STOPPED'
        (self.root / 'owned-bcs.json').write_text(json.dumps({
            'schema_version': 1, 'run_id': self.run_id, 'status': 'STOPPED',
            'returncode': 0, 'ready': True, 'forced_stop': False,
        }), encoding='utf-8')
        return False


def fake_story(evidence=None, *, marker=GOOD_MARKER, exit_code=0, stderr=b'',
               mutate_binding=False):
    def launch(argv, cwd, deadline):
        evidence_path = Path(argv[argv.index('--evidence-file') + 1])
        payload = valid_evidence() if evidence is None else evidence
        evidence_path.write_text(json.dumps(payload), encoding='utf-8')
        if mutate_binding:
            binding_path = Path(cwd) / BINDING_NAME
            value = read(binding_path)
            value['run_id'] = 'changed-after-binding'
            binding_path.write_text(json.dumps(value), encoding='utf-8')
        return {'exit_code': exit_code, 'timed_out': False, 'duration_seconds': 0.01,
                'stdout': marker, 'stderr': stderr, 'process': None}
    return launch


class DraftDeliveryRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.binary = self.base / 'bcs'
        self.binary.write_bytes(b'owned-bcs-binary')
        self.binary.chmod(0o700)
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.base / 'state'
        self.kwargs = dict(binary=self.binary, binary_sha256=self.sha, state_dir=self.state,
                           run_id='draft-delivery-run', confirm_run_id='draft-delivery-run',
                           allow_local_processes=True, allow_fixture_write=True,
                           allow_write=True)
        FakeOwnedBcs.instances = []

    def tearDown(self):
        self.temp.cleanup()

    def run_story(self, launch):
        with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess', FakeOwnedBcs), \
                patch('bcs_e2e.draft_delivery.run._launch_story', side_effect=launch):
            return local_draft_delivery_run(**self.kwargs)

    def test_success_publishes_local_suite_pass_without_release_credit(self):
        result = self.run_story(fake_story())
        self.assertEqual(result['status'], STATUS)
        self.assertEqual(result['canonical_summary']['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertFalse(result['canonical_summary']['verification_boundary']['grants_release_credit'])
        self.assertEqual(read(self.state / 'owned-bcs.json')['status'], 'STOPPED')
        self.assertEqual(result['child_receipt']['observed_checks'], STORY_CHECKS)
        for name in ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md'):
            self.assertTrue((self.state / name).is_file())
        summary = (self.state / 'reports/summary.json').read_text(encoding='utf-8')
        self.assertNotIn('Bearer ', summary)
        self.assertNotIn('bot_token', summary)

    def test_bad_marker_or_nonzero_exit_is_blocked(self):
        cases = (
            fake_story(marker=b'Draft delivery story: 1 live HTTP assertions passed'),
            fake_story(exit_code=1),
        )
        for launch in cases:
            with self.subTest(launch=launch):
                state = self.base / ('state-' + str(len(list(self.base.iterdir()))))
                kwargs = dict(self.kwargs)
                kwargs['state_dir'] = state
                with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess', FakeOwnedBcs), \
                        patch('bcs_e2e.draft_delivery.run._launch_story', side_effect=launch):
                    result = local_draft_delivery_run(**kwargs)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['canonical_summary']['status'], 'BLOCKED')

    def test_token_leak_blocks_and_never_writes_child_logs(self):
        result = self.run_story(fake_story(stderr=b'leaked Bearer secret-value'))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RedactionGateError')
        self.assertFalse((self.state / 'draft-delivery-story.err.log').exists())
        self.assertNotIn('secret-value', json.dumps(result))

    def test_binding_or_runtime_drift_is_blocked(self):
        result = self.run_story(fake_story(mutate_binding=True))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['canonical_summary']['status'], 'BLOCKED')

    def test_permissions_fresh_state_and_exact_confirmation_are_required(self):
        for field, value in (('allow_local_processes', False),
                             ('allow_fixture_write', False), ('allow_write', False),
                             ('confirm_run_id', 'different')):
            with self.subTest(field=field):
                kwargs = dict(self.kwargs)
                kwargs[field] = value
                with self.assertRaises(RunStateError):
                    with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess', FakeOwnedBcs), \
                            patch('bcs_e2e.draft_delivery.run._launch_story', side_effect=fake_story()):
                        local_draft_delivery_run(**kwargs)
        self.state.mkdir()
        with self.assertRaisesRegex(RunStateError, 'fresh state directory'):
            with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess', FakeOwnedBcs), \
                    patch('bcs_e2e.draft_delivery.run._launch_story', side_effect=fake_story()):
                local_draft_delivery_run(**self.kwargs)


if __name__ == '__main__':
    unittest.main()
