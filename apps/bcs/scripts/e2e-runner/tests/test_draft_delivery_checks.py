"""Good cases and reverse-failure cases for every Draft delivery obligation."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.draft_delivery.checks import (DraftDeliveryViolation,
                                           check_client_selected_idempotent_create,
                                           check_immutable_revision_append,
                                           check_stale_update_conflict)
from bcs_e2e.run_state import RunStateError

DRAFT_ID = '750c52c6-60e2-4249-8ee5-1fb8cb9402ab'
CONTENT_HASH = '761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7'
UPDATED_HASH = 'a' * 64


def create_evidence():
    return {
        'create': {
            'draft_id': DRAFT_ID,
            'content_hash': CONTENT_HASH,
            'attempts': [
                {'status': 201, 'draft_id': DRAFT_ID, 'revision': 1,
                 'size_bytes': 2174, 'content_hash': CONTENT_HASH, 'valid': True},
                {'status': 200, 'draft_id': DRAFT_ID, 'revision': 1,
                 'size_bytes': 2174, 'content_hash': CONTENT_HASH, 'valid': True},
            ],
            'different_content': {'status': 409},
        },
    }


class ClientSelectedIdempotentCreateTests(unittest.TestCase):
    def test_accepts_client_selected_uuid_and_idempotent_replay(self):
        check_client_selected_idempotent_create(create_evidence())

    def test_rejects_noncanonical_id(self):
        value = create_evidence()
        value['create']['draft_id'] = DRAFT_ID.upper()
        with self.assertRaisesRegex(RunStateError, 'canonical lowercase UUIDv4'):
            check_client_selected_idempotent_create(value)

    def test_rejects_replay_that_advances_or_changes_content(self):
        for field, replacement in (('status', 201), ('revision', 2), ('size_bytes', 2175),
                                   ('content_hash', '0' * 64), ('valid', False)):
            with self.subTest(field=field):
                value = create_evidence()
                value['create']['attempts'][1][field] = replacement
                with self.assertRaises(DraftDeliveryViolation):
                    check_client_selected_idempotent_create(value)

    def test_rejects_nonconflicting_different_content(self):
        value = create_evidence()
        value['create']['different_content']['status'] = 200
        with self.assertRaisesRegex(RunStateError, 'different content'):
            check_client_selected_idempotent_create(value)


def append_evidence():
    return {
        'fixture': {'content_hash': CONTENT_HASH},
        'append': {
            'head_before': 1,
            'expected_revision': 1,
            'status': 200,
            'revision': 2,
            'head_after': 2,
            'new_content_hash': UPDATED_HASH,
            'original_after': {'status': 200, 'revision': 1,
                               'size_bytes': 2174, 'content_hash': CONTENT_HASH},
            'updated_after': {'status': 200, 'revision': 2,
                              'content_hash': UPDATED_HASH},
        },
    }


class ImmutableRevisionAppendTests(unittest.TestCase):
    def test_accepts_exactly_one_append_and_immutable_history(self):
        check_immutable_revision_append(append_evidence())

    def test_rejects_non_append_or_renumbering(self):
        for field, replacement in (('expected_revision', 2), ('status', 201),
                                   ('revision', 3), ('head_after', 1)):
            with self.subTest(field=field):
                value = append_evidence()
                value['append'][field] = replacement
                with self.assertRaises(DraftDeliveryViolation):
                    check_immutable_revision_append(value)

    def test_rejects_mutated_historical_revision(self):
        for field, replacement in (('status', 404), ('revision', 2),
                                   ('size_bytes', 2175), ('content_hash', '0' * 64)):
            with self.subTest(field=field):
                value = append_evidence()
                value['append']['original_after'][field] = replacement
                with self.assertRaises(DraftDeliveryViolation):
                    check_immutable_revision_append(value)

    def test_rejects_updated_revision_that_reuses_old_content(self):
        value = append_evidence()
        value['append']['new_content_hash'] = CONTENT_HASH
        with self.assertRaisesRegex(RunStateError, 'did not change content'):
            check_immutable_revision_append(value)


def stale_evidence():
    return {
        'stale': {
            'status': 409,
            'expected_revision': 1,
            'head_after': 2,
            'new_content_hash': UPDATED_HASH,
            'original_content_hash': CONTENT_HASH,
            'latest_after': {'status': 200, 'revision': 2, 'content_hash': UPDATED_HASH},
        },
    }


class StaleUpdateConflictTests(unittest.TestCase):
    def test_accepts_409_without_head_or_content_change(self):
        check_stale_update_conflict(stale_evidence())

    def test_rejects_nonconflicting_stale_write(self):
        value = stale_evidence()
        value['stale']['status'] = 200
        with self.assertRaisesRegex(RunStateError, 'HTTP 409'):
            check_stale_update_conflict(value)

    def test_rejects_head_advance_or_replacement(self):
        for mutation in ('head_after', 'latest_after_content', 'latest_after_status',
                         'latest_after_revision', 'latest_after_hash'):
            with self.subTest(mutation=mutation):
                value = stale_evidence()
                if mutation == 'head_after':
                    value['stale']['head_after'] = 3
                elif mutation == 'latest_after_content':
                    value['stale']['original_content_hash'] = UPDATED_HASH
                elif mutation == 'latest_after_status':
                    value['stale']['latest_after']['status'] = 404
                elif mutation == 'latest_after_revision':
                    value['stale']['latest_after']['revision'] = 3
                else:
                    value['stale']['latest_after']['content_hash'] = CONTENT_HASH
                with self.assertRaises(DraftDeliveryViolation):
                    check_stale_update_conflict(value)


if __name__ == '__main__':
    unittest.main()
