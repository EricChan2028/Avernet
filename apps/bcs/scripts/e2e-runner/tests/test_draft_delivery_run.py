"""Lifecycle, drift and redaction tests for the Draft delivery owned-local run."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.draft_delivery import run as draft_run
from bcs_e2e.draft_delivery.checks import OBLIGATION_CHECKS
from bcs_e2e.draft_delivery.contract import (CASE_ID, REQUIREMENTS, STATUS, STORY_CHECKS,
                                             FIXTURE_SOURCE)
from bcs_e2e.draft_delivery.run import (BINDING_NAME, EVIDENCE_NAME, FLOW_NAME, STORY_SOURCE,
                                        local_draft_delivery_run, validate_draft_delivery_binary,
                                        _parse_story_marker, _redaction_entries)
from bcs_e2e.run_state import RunStateError, read

DRAFT_ID = '750c52c6-60e2-4249-8ee5-1fb8cb9402ab'
CONTENT_HASH = '761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7'
UPDATED_HASH = 'a' * 64
GOOD_MARKER = ('Draft delivery story: ' + str(STORY_CHECKS)
               + ' live HTTP assertions passed').encode()


def obligation_records() -> dict:
    records = {
        'DRAFT-DELIVERY.offline-preview-schema1-snapshot':
            {'transport': 'offline-cli', 'exit_code': 0, 'snapshot_present': True},
        'DRAFT-DELIVERY.offline-preview-retains-schema1':
            {'transport': 'offline-cli', 'schema_version': 1},
        'DRAFT-DELIVERY.offline-preview-preserves-bytes':
            {'transport': 'offline-cli', 'definition_yaml_bytes': 2174,
             'previewed_bytes_equal': True},
        'DRAFT-DELIVERY.author-upload-incident-file':
            {'transport': 'product-cli', 'exit_code': 0, 'saved': True, 'phase': 'ready',
             'draft_id': DRAFT_ID, 'create_stdout_token_field_present': False},
        'DRAFT-DELIVERY.saved-source-2174-bytes':
            {'transport': 'product-cli', 'size_bytes': 2174},
        'DRAFT-DELIVERY.saved-source-validates':
            {'transport': 'product-cli', 'valid': True},
        'DRAFT-DELIVERY.first-revision-immutable':
            {'transport': 'product-cli', 'create_revision': 1, 'replay_exit_code': 0,
             'replay_revision': 1, 'replay_saved': True, 'replay_matched_existing': False,
             'replay_content_hash': CONTENT_HASH, 'conflict_exit_code': 1,
             'conflict_http_status': 409, 'history_revision': 1, 'history_size_bytes': 2174,
             'history_content_hash': CONTENT_HASH},
        'DRAFT-DELIVERY.author-reads-metadata':
            {'transport': 'product-http', 'status': 200},
        'DRAFT-DELIVERY.metadata-head-revision-one':
            {'transport': 'product-http', 'head_revision': 1, 'deleted': False},
        'DRAFT-DELIVERY.card-holder-tokenless-read':
            {'transport': 'product-http', 'status': 200},
        'DRAFT-DELIVERY.shared-read-preserves-hash':
            {'transport': 'product-http', 'content_hash': CONTENT_HASH, 'size_bytes': 2174},
        'DRAFT-DELIVERY.shared-source-no-store':
            {'transport': 'product-http', 'cache_control': 'no-store'},
        'DRAFT-DELIVERY.card-holder-validates-source':
            {'transport': 'product-http', 'status': 200},
        'DRAFT-DELIVERY.shared-validation-binds-rev1':
            {'transport': 'product-http', 'revision': 1, 'valid': True},
        'DRAFT-DELIVERY.public-cannot-delete':
            {'transport': 'product-http', 'public_delete_status': 401,
             'inert_delete_status': 401, 'inert_update_status': 401,
             'head_after': 1, 'deleted_after': False},
        'DRAFT-DELIVERY.author-download-fixed-bytes':
            {'transport': 'product-cli', 'exit_code': 0, 'output_path_in_receipt': True},
        'DRAFT-DELIVERY.download-matches-incident-bytes':
            {'transport': 'product-cli', 'bytes_equal': True, 'size_bytes': 2174},
        'DRAFT-DELIVERY.author-validates-fixed-revision':
            {'transport': 'product-cli', 'exit_code': 0, 'valid': True},
        'DRAFT-DELIVERY.author-appends-revision':
            {'transport': 'product-cli', 'exit_code': 0, 'saved': True, 'phase': 'ready'},
        'DRAFT-DELIVERY.update-advances-exactly-once':
            {'transport': 'product-cli', 'revision': 2, 'head_after': 2,
             'latest_content_hash': UPDATED_HASH},
        'DRAFT-DELIVERY.author-reissues-historical-card':
            {'transport': 'product-cli', 'exit_code': 0, 'phase': 'ready',
             'reference': {'draft_id': DRAFT_ID, 'revision': 1, 'schemaVersion': 2,
                           'card_token_field_present': True, 'card_token_field_length': 43,
                           'http_draft_id': DRAFT_ID, 'http_revision': 1}},
        'DRAFT-DELIVERY.historical-preview-original-bytes':
            {'transport': 'product-cli', 'size_bytes': 2174},
        'DRAFT-DELIVERY.reissued-card-fixed-version':
            {'transport': 'product-cli', 'reference_revision': 1,
             'reference_schema_version': 2, 'other_revision_card_revision': 2},
        'DRAFT-DELIVERY.stale-write-rejected':
            {'transport': 'product-http', 'status': 409, 'error': 'DRAFT_CONFLICT',
             'head_after': 2, 'latest_content_hash': UPDATED_HASH},
        'DRAFT-DELIVERY.revoke-second-revision':
            {'transport': 'product-cli', 'exit_code': 0, 'revoked': True},
        'DRAFT-DELIVERY.revoke-idempotent':
            {'transport': 'product-cli', 'exit_code': 0, 'revoked': True},
        'DRAFT-DELIVERY.revoked-card-unavailable':
            {'transport': 'product-http', 'status': 404,
             'error': 'DRAFT_REFERENCE_UNAVAILABLE'},
        'DRAFT-DELIVERY.old-shared-revision-readable':
            {'transport': 'product-http', 'status': 200, 'content_hash': CONTENT_HASH,
             'size_bytes': 2174, 'cache_control': 'no-store'},
        'DRAFT-DELIVERY.author-deletes-draft':
            {'transport': 'product-cli', 'exit_code': 0, 'deleted': True},
        'DRAFT-DELIVERY.deletion-revokes-all-cards':
            {'transport': 'product-http', 'revision_one_status': 404,
             'revision_two_status': 404, 'metadata_status': 200, 'deleted_flag': True},
        'DRAFT-DELIVERY.late-create-cannot-revive':
            {'transport': 'product-http', 'method': 'PUT', 'status': 404,
             'error': 'DRAFT_REFERENCE_UNAVAILABLE'},
        'DRAFT-DELIVERY.journey-completes':
            {'transport': 'product-http', 'responses_no_store': True,
             'status_matrix': [200, 401, 404, 409], 'token_canary_free': True},
    }
    assert set(records) == {obligation for obligation, _ in OBLIGATION_CHECKS}
    return records


def valid_evidence() -> dict:
    return {
        'schema_version': 2,
        'draft_id': DRAFT_ID,
        'identity_mode': 'register',
        'fixture': {'content_hash': CONTENT_HASH, 'updated_content_hash': UPDATED_HASH,
                    'size_bytes': 2174},
        'author': {'uuid': DRAFT_ID, 'resolved': True},
        'obligations': obligation_records(),
        'responses': [{'method': 'GET', 'path': '/collaboration/drafts/' + DRAFT_ID,
                       'status': 200, 'cache_control': 'no-store'}],
        'public_v1_face': None,
    }


class FakeOwnedBcs:
    instances = []

    def __init__(self, *, binary, binary_sha256, root, run_id, allow_ambient_local_identity,
                 ready=True):
        self.root = Path(root)
        self.binary = Path(binary)
        self.binary_sha256 = binary_sha256
        self.run_id = run_id
        self.ready = ready
        self.inputs = SimpleNamespace(human_id='e2ehumanabcdef0123456789',
                                      origin='http://127.0.0.1:12345')
        FakeOwnedBcs.instances.append(self)

    def __enter__(self):
        self.root.mkdir(parents=True)
        self.root.joinpath('console.log').write_text('owned runtime console clean\n',
                                                     encoding='utf-8')
        (self.root / 'owned-bcs.json').write_text(json.dumps(
            {'schema_version': 1, 'run_id': self.run_id, 'status': 'RUNNING',
             'returncode': None, 'ready': self.ready, 'forced_stop': False}),
            encoding='utf-8')
        return self

    def __exit__(self, *_args):
        (self.root / 'owned-bcs.json').write_text(json.dumps(
            {'schema_version': 1, 'run_id': self.run_id, 'status': 'STOPPED',
             'returncode': 0, 'ready': False, 'forced_stop': False}), encoding='utf-8')
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
        self.cli_binary = self.base / 'bcs-cli'
        self.cli_binary.write_bytes(b'owned-bcs-cli-binary')
        self.cli_binary.chmod(0o700)
        self.cli_sha = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()
        self.state = self.base / 'state'
        self.kwargs = dict(binary=self.binary, binary_sha256=self.sha,
                           cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha,
                           state_dir=self.state,
                           run_id='draft-delivery-run', confirm_run_id='draft-delivery-run',
                           allow_local_processes=True, allow_fixture_write=True,
                           allow_write=True)
        FakeOwnedBcs.instances = []

    def tearDown(self):
        self.temp.cleanup()

    def run_story(self, launch, **overrides):
        kwargs = dict(self.kwargs)
        kwargs.update(overrides)
        with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess', FakeOwnedBcs), \
                patch('bcs_e2e.draft_delivery.run._launch_story', side_effect=launch):
            return local_draft_delivery_run(**kwargs)

    def test_success_publishes_the_dual_local_receipt_without_release_credit(self):
        result = self.run_story(fake_story())
        self.assertEqual(result['status'], STATUS)
        self.assertEqual(result['canonical_summary']['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(result['canonical_summary']['verification_boundary']['preprod'],
                         'DUAL_PREPROD_LEG_OUTSIDE_THIS_LOCAL_RECEIPT')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertFalse(result['canonical_summary']['verification_boundary']['grants_release_credit'])
        argv = result['child_receipt']['argv']
        self.assertIn('--cli-binary', argv)
        self.assertIn(str(self.cli_binary), argv)
        self.assertNotIn(str(self.binary), argv)
        self.assertIn('--identity-mode', argv)
        self.assertIn('register', argv)
        self.assertIn('--fixture', argv)
        self.assertIn(str(FIXTURE_SOURCE), argv)
        self.assertIn('--base-url', argv)
        self.assertIn('http://127.0.0.1:12345', argv)
        self.assertEqual(result['child_receipt']['observed_checks'], STORY_CHECKS)
        case = read(self.state / CASE_ID.lower().replace('-', '-') if False else
                    self._case_path())
        self.assertEqual(len(case['requirements']), 32)
        self.assertEqual(case['checks']['status'], 'PASS')
        self.assertEqual(case['checks']['checked'], list(REQUIREMENTS))
        self.assertEqual(case['story_markers']['live_http_assertions'], STORY_CHECKS)
        self.assertEqual(len(case['story_lines']), 32)
        self.assertEqual(result['redaction']['status'], 'PASS')
        self.assertEqual(result['runtime_shutdown']['status'], 'STOPPED')
        self.assertFalse(result['runtime_shutdown']['ready'])
        for name in ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md'):
            self.assertTrue((self.state / name).is_file())
        summary = (self.state / 'reports' / 'summary.json').read_text(encoding='utf-8')
        for canary in ('Bearer ', 'bot_token', 'readToken', 'read_token'):
            self.assertNotIn(canary, summary)
        binding = read(self.state / BINDING_NAME)
        self.assertEqual(binding['source_binding']['story_checks'], 65)
        self.assertEqual(len(binding['selected_requirement_ids']), 32)
        self.assertEqual(binding['bcs_binary_sha256'], self.sha)
        self.assertEqual(binding['cli_binary_sha256'], self.cli_sha)
        self.assertEqual(result['bcs_binary_sha256'], self.sha)
        self.assertEqual(result['cli_binary_sha256'], self.cli_sha)
        self.assertEqual(result['case']['cli_binary_sha256'], self.cli_sha)
        self.assertEqual(result['canonical_summary']['runner']['required_arguments'],
                         ['--binary', '--binary-sha256', '--cli-binary', '--cli-binary-sha256'])

    def _case_path(self):
        return next(self.state.glob('draft-delivery.json'))

    def test_bad_marker_or_nonzero_exit_is_blocked(self):
        cases = (
            fake_story(marker=b'Draft delivery story: 57 live HTTP assertions passed'),
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

    def test_obligation_violation_in_child_evidence_blocks_the_case(self):
        evidence = valid_evidence()
        evidence['obligations']['DRAFT-DELIVERY.shared-source-no-store']['cache_control'] = \
            'max-age=3600'
        result = self.run_story(fake_story(evidence=evidence))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['checks']['status'], 'BLOCKED')
        self.assertEqual(result['case'], {})

    def test_token_leak_blocks_and_never_writes_child_logs(self):
        result = self.run_story(fake_story(stderr=b'leaked Bearer secret-value'))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RedactionGateError')
        self.assertFalse((self.state / 'draft-delivery-story.err.log').exists())
        self.assertNotIn('secret-value', json.dumps(result))

    def test_binding_or_runtime_drift_is_blocked(self):
        result = self.run_story(fake_story(mutate_binding=True))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'DriftFenceError')
        self.assertEqual(result['canonical_summary']['status'], 'BLOCKED')

    def test_unready_owned_runtime_blocks(self):
        with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess',
                   lambda **kwargs: FakeOwnedBcs(ready=False, **kwargs)), \
                patch('bcs_e2e.draft_delivery.run._launch_story', side_effect=fake_story()):
            result = local_draft_delivery_run(**self.kwargs)
        self.assertEqual(result['status'], 'BLOCKED')

    def test_story_deadline_is_enforced_and_reported(self):
        def hanging(argv, cwd, deadline):
            raise draft_run.RunStateError('draft-delivery story exceeded its 900-second deadline')
        with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess', FakeOwnedBcs), \
                patch('bcs_e2e.draft_delivery.run._launch_story', side_effect=hanging):
            result = local_draft_delivery_run(**self.kwargs)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RunStateError')

    def test_binary_validation_and_marker_seams(self):
        self.assertEqual(_parse_story_marker(GOOD_MARKER.decode()), (True, STORY_CHECKS))
        self.assertEqual(
            _parse_story_marker(b'Draft delivery story: 8 live HTTP assertions passed'),
            (True, 8))
        self.assertEqual(_parse_story_marker(b'nothing here'), (False, 0))
        executable = self.base / 'exec'
        executable.write_bytes(b'#!/bin/sh\n')
        executable.chmod(0o700)
        self.assertEqual(validate_draft_delivery_binary(executable,
                                                        hashlib.sha256(b'#!/bin/sh\n').hexdigest()),
                         executable)
        with self.assertRaises(RunStateError):
            validate_draft_delivery_binary(executable, '0' * 64)
        executable.chmod(0o600)
        with self.assertRaises(RunStateError):
            validate_draft_delivery_binary(executable, hashlib.sha256(b'#!/bin/sh\n').hexdigest())
        link = self.base / 'link'
        link.symlink_to(executable)
        with self.assertRaises(RunStateError):
            validate_draft_delivery_binary(link, hashlib.sha256(b'#!/bin/sh\n').hexdigest())
        self.assertIsNotNone(STORY_SOURCE)
        self.assertTrue(STORY_SOURCE.is_file())

    def test_story_argv_shapes_the_dual_child_contract(self):
        captured = {}

        def launch(argv, cwd, deadline):
            captured['argv'] = list(argv)
            evidence_path = Path(argv[argv.index('--evidence-file') + 1])
            evidence_path.write_text(json.dumps(valid_evidence()), encoding='utf-8')
            return {'exit_code': 0, 'timed_out': False, 'duration_seconds': 0.0,
                    'stdout': GOOD_MARKER, 'stderr': b'', 'process': None}

        self.run_story(launch)
        argv = captured['argv']
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(Path(argv[1]).name == 'story.py')
        self.assertEqual(argv[argv.index('--identity-mode') + 1], 'register')
        self.assertEqual(Path(argv[argv.index('--cli-binary') + 1]), self.cli_binary)
        self.assertEqual(Path(argv[argv.index('--fixture') + 1]), FIXTURE_SOURCE)
        self.assertEqual(argv[argv.index('--human') + 1], 'e2ehumanabcdef0123456789')

    def test_redaction_entries_gate_streams_and_artifacts(self):
        streams, artifacts, ok = _redaction_entries(
            [('story.stdout', b'clean'), ('story.stderr', b'leaked readToken value')],
            [('child.receipt', b'{}')])
        self.assertTrue(streams[0]['clean'])
        self.assertFalse(streams[1]['clean'])
        self.assertTrue(artifacts[0]['clean'])
        self.assertFalse(ok)

    def test_permissions_fresh_state_and_exact_confirmation_are_required(self):
        for field, value in (('allow_local_processes', False),
                             ('allow_fixture_write', False), ('allow_write', False),
                             ('confirm_run_id', 'different')):
            with self.subTest(field=field):
                kwargs = dict(self.kwargs)
                kwargs[field] = value
                with self.assertRaises(RunStateError):
                    with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess', FakeOwnedBcs), \
                            patch('bcs_e2e.draft_delivery.run._launch_story',
                                  side_effect=fake_story()):
                        local_draft_delivery_run(**kwargs)
        self.state.mkdir()
        with self.assertRaisesRegex(RunStateError, 'fresh state directory'):
            with patch('bcs_e2e.draft_delivery.run.OwnedBcsProcess', FakeOwnedBcs), \
                    patch('bcs_e2e.draft_delivery.run._launch_story', side_effect=fake_story()):
                local_draft_delivery_run(**self.kwargs)


if __name__ == '__main__':
    unittest.main()
