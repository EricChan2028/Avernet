"""Good cases and reverse-failure cases for every Draft delivery obligation."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.draft_delivery.checks import (DraftDeliveryViolation,
                                           check_client_selected_idempotent_create,
                                           check_immutable_revision_append,
                                           check_read_token_single_revision,
                                           check_no_store_every_response,
                                           check_read_token_read_only,
                                           check_revoked_or_deleted_unavailable,
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


def read_token_evidence():
    return {
        'create': {'draft_id': DRAFT_ID},
        'cards': {
            'head_revision': 2,
            'historical': {
                'draft_id': DRAFT_ID,
                'revision': 1,
                'content_hash': CONTENT_HASH,
                'read_token_present': True,
                'read_token_revision': 1,
                'read_with_token_as_header': False,
                'read_without_token': {'status': 200, 'revision': 1,
                                       'size_bytes': 2174, 'content_hash': CONTENT_HASH},
            },
            'other_revision': {'revision': 2, 'read_token_revision': 2},
        },
    }


class ReadTokenSingleRevisionTests(unittest.TestCase):
    def test_accepts_revision_pinned_inert_token(self):
        check_read_token_single_revision(read_token_evidence())

    def test_rejects_card_that_follows_head(self):
        value = read_token_evidence()
        value['cards']['historical']['revision'] = 2
        with self.assertRaisesRegex(RunStateError, 'staying on revision 1'):
            check_read_token_single_revision(value)

    def test_rejects_token_cross_revision_or_authorization_use(self):
        for mutation in ('token_revision', 'token_header', 'other_revision',
                         'read_status', 'read_hash'):
            with self.subTest(mutation=mutation):
                value = read_token_evidence()
                if mutation == 'token_revision':
                    value['cards']['historical']['read_token_revision'] = 2
                elif mutation == 'token_header':
                    value['cards']['historical']['read_with_token_as_header'] = True
                elif mutation == 'other_revision':
                    value['cards']['other_revision']['read_token_revision'] = 1
                elif mutation == 'read_status':
                    value['cards']['historical']['read_without_token']['status'] = 401
                else:
                    value['cards']['historical']['read_without_token']['content_hash'] = UPDATED_HASH
                with self.assertRaises(DraftDeliveryViolation):
                    check_read_token_single_revision(value)


def availability_evidence():
    return {
        'fixture': {'content_hash': CONTENT_HASH},
        'availability': {
            'revoked': {'status': 404, 'error': 'DRAFT_REFERENCE_UNAVAILABLE'},
            'historical_sibling': {'status': 200, 'content_hash': CONTENT_HASH},
            'deleted': {'status': 404, 'error': 'DRAFT_REFERENCE_UNAVAILABLE'},
            'late_create': {'method': 'PUT', 'status': 404},
        },
    }


class RevokedOrDeletedUnavailableTests(unittest.TestCase):
    def test_accepts_revocation_scoped_to_one_revision_and_delete_tombstone(self):
        check_revoked_or_deleted_unavailable(availability_evidence())

    def test_rejects_revoked_revision_that_remains_readable(self):
        for field, replacement in (('status', 200),
                                   ('error', 'DRAFT_INPUT')):
            with self.subTest(field=field):
                value = availability_evidence()
                value['availability']['revoked'][field] = replacement
                with self.assertRaises(DraftDeliveryViolation):
                    check_revoked_or_deleted_unavailable(value)

    def test_rejects_sibling_deleted_by_revoke(self):
        value = availability_evidence()
        value['availability']['historical_sibling']['status'] = 404
        with self.assertRaisesRegex(RunStateError, 'sibling'):
            check_revoked_or_deleted_unavailable(value)

    def test_rejects_deleted_draft_revival(self):
        for mutation in ('deleted_status', 'deleted_error', 'late_status', 'late_method'):
            with self.subTest(mutation=mutation):
                value = availability_evidence()
                if mutation == 'deleted_status':
                    value['availability']['deleted']['status'] = 200
                elif mutation == 'deleted_error':
                    value['availability']['deleted']['error'] = 'DRAFT_INPUT'
                elif mutation == 'late_status':
                    value['availability']['late_create']['status'] = 200
                else:
                    value['availability']['late_create']['method'] = 'POST'
                with self.assertRaises(DraftDeliveryViolation):
                    check_revoked_or_deleted_unavailable(value)


def read_only_evidence():
    return {
        'management': {
            'public_delete': {'status': 401},
            'token_delete': {'status': 401},
            'token_update': {'status': 401},
            'head_after': 2,
            'deleted_after': False,
        },
    }


class ReadTokenReadOnlyTests(unittest.TestCase):
    def test_accepts_all_unauthorized_management_attempts(self):
        check_read_token_read_only(read_only_evidence())

    def test_accepts_forbidden_status_from_scope_guard(self):
        value = read_only_evidence()
        value['management']['token_delete']['status'] = 403
        check_read_token_read_only(value)

    def test_rejects_any_management_success(self):
        for field in ('public_delete', 'token_delete', 'token_update'):
            with self.subTest(field=field):
                value = read_only_evidence()
                value['management'][field]['status'] = 200
                with self.assertRaisesRegex(RunStateError, 'rejected as unauthorized'):
                    check_read_token_read_only(value)

    def test_rejects_state_mutation_after_rejection(self):
        for field, replacement in (('head_after', 3), ('deleted_after', True)):
            with self.subTest(field=field):
                value = read_only_evidence()
                value['management'][field] = replacement
                with self.assertRaises(DraftDeliveryViolation):
                    check_read_token_read_only(value)


def response_rows():
    rows = []
    for method, suffix, status in (
            ('PUT', '', 201), ('PUT', '', 200), ('GET', '/revisions/1', 200),
            ('POST', '/revisions/1/validate', 200), ('POST', '/revisions', 409),
            ('DELETE', '/revisions/2/share', 204), ('DELETE', '', 401),
            ('GET', '/revisions/2', 404)):
        rows.append({'method': method, 'path': '/collaboration/drafts/' + DRAFT_ID + suffix,
                     'status': status, 'cache_control': 'no-store'})
    return rows


def no_store_evidence():
    return {'responses': response_rows()}


class NoStoreEveryResponseTests(unittest.TestCase):
    def test_accepts_complete_no_store_matrix(self):
        check_no_store_every_response(no_store_evidence())

    def test_rejects_any_cacheable_success_or_error(self):
        for index in (0, 3, 7):
            with self.subTest(index=index):
                value = no_store_evidence()
                value['responses'][index]['cache_control'] = 'private'
                with self.assertRaisesRegex(RunStateError, 'did not carry no-store'):
                    check_no_store_every_response(value)

    def test_rejects_empty_or_incomplete_response_matrix(self):
        for mutation in ('empty', 'missing_status', 'outside_draft_surface'):
            with self.subTest(mutation=mutation):
                value = no_store_evidence()
                if mutation == 'empty':
                    value['responses'] = []
                elif mutation == 'missing_status':
                    value['responses'] = [row for row in value['responses']
                                          if row['status'] != 404]
                else:
                    value['responses'][0]['path'] = '/health'
                with self.assertRaises(DraftDeliveryViolation):
                    check_no_store_every_response(value)


if __name__ == '__main__':
    unittest.main()
