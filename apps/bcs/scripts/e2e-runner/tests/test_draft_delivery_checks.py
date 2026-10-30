"""Good and reverse-failure cases for every frozen Draft delivery obligation."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.draft_delivery.checks import (DraftDeliveryViolation, OBLIGATION_CHECKS,
                                           REDACTION_TOKENS, scan_redaction,
                                           verify_all_obligations)
from bcs_e2e.draft_delivery.contract import REQUIREMENTS, STORY_CHECKS
from bcs_e2e.run_state import RunStateError

DRAFT_ID = '750c52c6-60e2-4249-8ee5-1fb8cb9402ab'
CONTENT_HASH = '761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7'
UPDATED_HASH = 'a' * 64


def obligation_records() -> dict:
    reference = {'draft_id': DRAFT_ID, 'revision': 1, 'schemaVersion': 2,
                 'card_token_field_present': True, 'card_token_field_length': 43,
                 'http_draft_id': DRAFT_ID, 'http_revision': 1}
    return {
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
             'reference': copy.deepcopy(reference)},
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


def valid_evidence(*, identity_mode: str = 'register') -> dict:
    return {
        'schema_version': 2,
        'draft_id': DRAFT_ID,
        'identity_mode': identity_mode,
        'fixture': {'content_hash': CONTENT_HASH, 'updated_content_hash': UPDATED_HASH,
                    'size_bytes': 2174},
        'author': {'uuid': DRAFT_ID, 'resolved': True},
        'obligations': obligation_records(),
        'responses': [],
        'public_v1_face': None,
    }


def mutated(obligation: str, path: tuple, bad: object) -> dict:
    value = valid_evidence()
    target = value['obligations'][obligation]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    return value


NEGATIVES = (
    ('DRAFT-DELIVERY.offline-preview-schema1-snapshot', ('transport',), 'product-http'),
    ('DRAFT-DELIVERY.offline-preview-schema1-snapshot', ('exit_code',), 1),
    ('DRAFT-DELIVERY.offline-preview-schema1-snapshot', ('snapshot_present',), False),
    ('DRAFT-DELIVERY.offline-preview-retains-schema1', ('schema_version',), 2),
    ('DRAFT-DELIVERY.offline-preview-retains-schema1', ('transport',), 'offline-http'),
    ('DRAFT-DELIVERY.offline-preview-preserves-bytes', ('definition_yaml_bytes',), 2173),
    ('DRAFT-DELIVERY.offline-preview-preserves-bytes', ('previewed_bytes_equal',), False),
    ('DRAFT-DELIVERY.author-upload-incident-file', ('transport',), 'product-http'),
    ('DRAFT-DELIVERY.author-upload-incident-file', ('exit_code',), 1),
    ('DRAFT-DELIVERY.author-upload-incident-file', ('saved',), False),
    ('DRAFT-DELIVERY.author-upload-incident-file', ('phase',), 'conflict'),
    ('DRAFT-DELIVERY.author-upload-incident-file', ('draft_id',), DRAFT_ID.upper()),
    ('DRAFT-DELIVERY.author-upload-incident-file', ('create_stdout_token_field_present',), True),
    ('DRAFT-DELIVERY.saved-source-2174-bytes', ('size_bytes',), 2173),
    ('DRAFT-DELIVERY.saved-source-validates', ('valid',), False),
    ('DRAFT-DELIVERY.first-revision-immutable', ('create_revision',), 2),
    ('DRAFT-DELIVERY.first-revision-immutable', ('replay_exit_code',), 1),
    ('DRAFT-DELIVERY.first-revision-immutable', ('replay_revision',), 2),
    ('DRAFT-DELIVERY.first-revision-immutable', ('replay_matched_existing',), True),
    ('DRAFT-DELIVERY.first-revision-immutable', ('replay_saved',), False),
    ('DRAFT-DELIVERY.first-revision-immutable', ('replay_content_hash',), UPDATED_HASH),
    ('DRAFT-DELIVERY.first-revision-immutable', ('conflict_exit_code',), 0),
    ('DRAFT-DELIVERY.first-revision-immutable', ('conflict_http_status',), 200),
    ('DRAFT-DELIVERY.first-revision-immutable', ('history_revision',), 2),
    ('DRAFT-DELIVERY.first-revision-immutable', ('history_size_bytes',), 2173),
    ('DRAFT-DELIVERY.first-revision-immutable', ('history_content_hash',), UPDATED_HASH),
    ('DRAFT-DELIVERY.author-reads-metadata', ('status',), 404),
    ('DRAFT-DELIVERY.metadata-head-revision-one', ('head_revision',), 2),
    ('DRAFT-DELIVERY.metadata-head-revision-one', ('deleted',), True),
    ('DRAFT-DELIVERY.card-holder-tokenless-read', ('status',), 404),
    ('DRAFT-DELIVERY.card-holder-tokenless-read', ('transport',), 'product-cli'),
    ('DRAFT-DELIVERY.shared-read-preserves-hash', ('content_hash',), UPDATED_HASH),
    ('DRAFT-DELIVERY.shared-read-preserves-hash', ('size_bytes',), 1),
    ('DRAFT-DELIVERY.shared-source-no-store', ('cache_control',), 'max-age=3600'),
    ('DRAFT-DELIVERY.card-holder-validates-source', ('status',), 404),
    ('DRAFT-DELIVERY.shared-validation-binds-rev1', ('revision',), 2),
    ('DRAFT-DELIVERY.shared-validation-binds-rev1', ('valid',), False),
    ('DRAFT-DELIVERY.public-cannot-delete', ('public_delete_status',), 204),
    ('DRAFT-DELIVERY.public-cannot-delete', ('inert_delete_status',), 200),
    ('DRAFT-DELIVERY.public-cannot-delete', ('inert_update_status',), 200),
    ('DRAFT-DELIVERY.public-cannot-delete', ('head_after',), 2),
    ('DRAFT-DELIVERY.public-cannot-delete', ('deleted_after',), True),
    ('DRAFT-DELIVERY.author-download-fixed-bytes', ('exit_code',), 1),
    ('DRAFT-DELIVERY.author-download-fixed-bytes', ('output_path_in_receipt',), False),
    ('DRAFT-DELIVERY.download-matches-incident-bytes', ('bytes_equal',), False),
    ('DRAFT-DELIVERY.download-matches-incident-bytes', ('size_bytes',), 2173),
    ('DRAFT-DELIVERY.author-validates-fixed-revision', ('exit_code',), 1),
    ('DRAFT-DELIVERY.author-validates-fixed-revision', ('valid',), False),
    ('DRAFT-DELIVERY.author-appends-revision', ('exit_code',), 1),
    ('DRAFT-DELIVERY.author-appends-revision', ('saved',), False),
    ('DRAFT-DELIVERY.author-appends-revision', ('phase',), 'conflict'),
    ('DRAFT-DELIVERY.update-advances-exactly-once', ('revision',), 1),
    ('DRAFT-DELIVERY.update-advances-exactly-once', ('head_after',), 1),
    ('DRAFT-DELIVERY.update-advances-exactly-once', ('latest_content_hash',), CONTENT_HASH),
    ('DRAFT-DELIVERY.author-reissues-historical-card', ('exit_code',), 1),
    ('DRAFT-DELIVERY.author-reissues-historical-card', ('phase',), 'conflict'),
    ('DRAFT-DELIVERY.author-reissues-historical-card', ('reference', 'revision'), 2),
    ('DRAFT-DELIVERY.author-reissues-historical-card', ('reference', 'http_revision'), 2),
    ('DRAFT-DELIVERY.author-reissues-historical-card', ('reference', 'draft_id'),
     '00000000-0000-4000-8000-000000000000'),
    ('DRAFT-DELIVERY.author-reissues-historical-card', ('reference', 'card_token_field_present'), False),
    ('DRAFT-DELIVERY.author-reissues-historical-card', ('reference', 'card_token_field_length'), 42),
    ('DRAFT-DELIVERY.historical-preview-original-bytes', ('size_bytes',), 1),
    ('DRAFT-DELIVERY.reissued-card-fixed-version', ('reference_revision',), 2),
    ('DRAFT-DELIVERY.reissued-card-fixed-version', ('reference_schema_version',), 1),
    ('DRAFT-DELIVERY.reissued-card-fixed-version', ('other_revision_card_revision',), 1),
    ('DRAFT-DELIVERY.stale-write-rejected', ('status',), 200),
    ('DRAFT-DELIVERY.stale-write-rejected', ('error',), 'DRAFT_REFERENCE_UNAVAILABLE'),
    ('DRAFT-DELIVERY.stale-write-rejected', ('head_after',), 1),
    ('DRAFT-DELIVERY.stale-write-rejected', ('latest_content_hash',), CONTENT_HASH),
    ('DRAFT-DELIVERY.revoke-second-revision', ('exit_code',), 1),
    ('DRAFT-DELIVERY.revoke-second-revision', ('revoked',), False),
    ('DRAFT-DELIVERY.revoke-idempotent', ('revoked',), False),
    ('DRAFT-DELIVERY.revoke-idempotent', ('transport',), 'product-http'),
    ('DRAFT-DELIVERY.revoked-card-unavailable', ('status',), 200),
    ('DRAFT-DELIVERY.revoked-card-unavailable', ('error',), 'DRAFT_CONFLICT'),
    ('DRAFT-DELIVERY.old-shared-revision-readable', ('status',), 404),
    ('DRAFT-DELIVERY.old-shared-revision-readable', ('content_hash',), UPDATED_HASH),
    ('DRAFT-DELIVERY.old-shared-revision-readable', ('size_bytes',), 2173),
    ('DRAFT-DELIVERY.old-shared-revision-readable', ('cache_control',), 'max-age=3600'),
    ('DRAFT-DELIVERY.author-deletes-draft', ('exit_code',), 1),
    ('DRAFT-DELIVERY.author-deletes-draft', ('deleted',), False),
    ('DRAFT-DELIVERY.deletion-revokes-all-cards', ('revision_one_status',), 200),
    ('DRAFT-DELIVERY.deletion-revokes-all-cards', ('revision_two_status',), 200),
    ('DRAFT-DELIVERY.deletion-revokes-all-cards', ('metadata_status',), 500),
    ('DRAFT-DELIVERY.deletion-revokes-all-cards', ('deleted_flag',), False),
    ('DRAFT-DELIVERY.late-create-cannot-revive', ('method',), 'POST'),
    ('DRAFT-DELIVERY.late-create-cannot-revive', ('status',), 409),
    ('DRAFT-DELIVERY.late-create-cannot-revive', ('error',), 'DRAFT_CONFLICT'),
    ('DRAFT-DELIVERY.journey-completes', ('responses_no_store',), False),
    ('DRAFT-DELIVERY.journey-completes', ('status_matrix',), [200, 401]),
    ('DRAFT-DELIVERY.journey-completes', ('token_canary_free',), False),
    ('DRAFT-DELIVERY.journey-completes', ('transport',), 'product-cli'),
)


class RegistryTests(unittest.TestCase):
    def test_registry_covers_the_frozen_32_obligations_in_journey_order(self):
        registered = [(obligation, function)
                      for obligation, function in OBLIGATION_CHECKS]
        self.assertEqual([obligation for obligation, _ in registered], list(REQUIREMENTS))
        self.assertEqual(len(registered), 32)
        functions = [function for _, function in registered]
        self.assertEqual(len(set(functions)), 32)
        for function in functions:
            self.assertTrue(callable(function))

    def test_good_evidence_passes_all_32_obligations(self):
        result = verify_all_obligations(valid_evidence())
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['checked'], list(REQUIREMENTS))

    def test_bearer_identity_mode_evidence_is_equally_admitted(self):
        result = verify_all_obligations(valid_evidence(identity_mode='bearer'))
        self.assertEqual(result['status'], 'PASS')


class EnvelopeTests(unittest.TestCase):
    def test_envelope_rejects_schema_one_and_bad_envelopes(self):
        cases = (
            ('schema_version', lambda value: value.__setitem__('schema_version', 1)),
            ('draft_id', lambda value: value.__setitem__('draft_id', DRAFT_ID.upper())),
            ('identity_mode', lambda value: value.__setitem__('identity_mode', 'cookie')),
            ('fixture_hash', lambda value: value['fixture'].__setitem__('content_hash', 'zz')),
            ('fixture_equality', lambda value: value['fixture'].__setitem__(
                'updated_content_hash', CONTENT_HASH)),
            ('fixture_size', lambda value: value['fixture'].__setitem__('size_bytes', 2173)),
            ('responses', lambda value: value.__setitem__('responses', {})),
            ('obligation_drop', lambda value: value['obligations'].pop(REQUIREMENTS[0])),
            ('obligation_extra', lambda value: value['obligations'].__setitem__(
                'DRAFT-DELIVERY.extra-obligation', {})),
            ('obligations_shape', lambda value: value.__setitem__('obligations', [])),
        )
        for name, mutate in cases:
            with self.subTest(case=name), self.assertRaises(DraftDeliveryViolation):
                value = valid_evidence()
                mutate(value)
                verify_all_obligations(value)

    def test_envelope_rejects_non_objects(self):
        for payload in (None, [], 'evidence', 32):
            with self.subTest(payload=payload), self.assertRaises(DraftDeliveryViolation):
                verify_all_obligations(payload)


class ObligationMatrixTests(unittest.TestCase):
    def test_every_obligation_rejects_its_violating_observation(self):
        self.assertEqual(len(NEGATIVES), 92)
        seen = {obligation for obligation, _, _ in NEGATIVES}
        self.assertEqual(len(seen), 32)
        for obligation, path, bad in NEGATIVES:
            with self.subTest(obligation=obligation, field='.'.join(path)):
                value = mutated(obligation, path, bad)
                with self.assertRaisesRegex(DraftDeliveryViolation, obligation.replace('.', r'\.')):
                    verify_all_obligations(value)

    def test_malformed_record_is_wrapped_as_obligation_violation(self):
        value = valid_evidence()
        value['obligations']['DRAFT-DELIVERY.author-reads-metadata'] = None
        with self.assertRaises(DraftDeliveryViolation) as ctx:
            verify_all_obligations(value)
        self.assertIn('DRAFT-DELIVERY.author-reads-metadata', str(ctx.exception))


class RedactionTests(unittest.TestCase):
    def test_scan_redaction_observes_without_retaining(self):
        payload = b'clean observation'
        clean = scan_redaction('story.stdout', payload)
        self.assertEqual(clean, {'name': 'story.stdout', 'clean': True,
                                 'bytes': len(payload)})
        dirty = scan_redaction('story.stderr', b'leaked Bearer secret')
        self.assertFalse(dirty['clean'])
        self.assertTrue(scan_redaction(
            'evidence', json.dumps(valid_evidence(), sort_keys=True).encode())['clean'])

    def test_scan_redaction_rejects_malformed_calls(self):
        with self.assertRaises(RunStateError):
            scan_redaction('', b'x')
        with self.assertRaises(RunStateError):
            scan_redaction('story.stdout', 'text')

    def test_json_canaries_never_appear_in_a_passing_receipt(self):
        payload = json.dumps(valid_evidence(), sort_keys=True)
        for token in REDACTION_TOKENS:
            self.assertNotIn(token, payload)
            self.assertNotIn(token.lower(), payload.lower())


if __name__ == '__main__':
    unittest.main()
