"""Good cases and reverse-failure cases for every Draft delivery obligation."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.draft_delivery.checks import (DraftDeliveryViolation,
                                           check_client_selected_idempotent_create)
from bcs_e2e.run_state import RunStateError

DRAFT_ID = '750c52c6-60e2-4249-8ee5-1fb8cb9402ab'
CONTENT_HASH = '761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7'


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


if __name__ == '__main__':
    unittest.main()
