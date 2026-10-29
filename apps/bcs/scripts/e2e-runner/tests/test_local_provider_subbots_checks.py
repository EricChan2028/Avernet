"""Good cases and reverse-failure cases for all Provider subbots obligations."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.provider_subbots import checks
from bcs_e2e.run_state import RunStateError

PARENT = 'poolab:e2e:0123456789ab'
ADMIN_ID = 'bot_11111111'
SCOPED_ID = 'bot_22222222'


def valid_evidence() -> dict:
    success = {
        'status': 200,
        'cache_control': 'no-store',
        'actor_kind': 'none',
        'registered': True,
        'parent_matches': True,
        'bot_id_matches': True,
        'ref_matches': True,
        'token_absent': True,
    }
    return {
        'schema_version': 1,
        'parent': PARENT,
        'provider': {
            'registration_status': 200,
            'provider_id_present': True,
            'provider_slug': 'e2e-subbots-0123456789ab',
            'admin_credential_present': True,
            'discovery_status': 200,
            'auth_mode': 'agentpass-subbots',
        },
        'admin_ref_validation': {
            'malformed_count': 6,
            'statuses': [400] * 6,
        },
        'admin_registration': {
            'status': 200,
            'agent_code': PARENT,
            'bot_slug': 'reviewer',
            'auth_mode': 'agentpass-subbots',
            'provider_bot_ref': PARENT + ':reviewer',
            'bot_id': ADMIN_ID,
            'token_present': True,
        },
        'scoped_ref_validation': {
            'removed_slug_status': 400,
            'malformed_statuses': [400] * 4,
        },
        'scoped_registration': {
            'status': 200,
            'agent_code': PARENT,
            'bot_slug': 'coder',
            'auth_mode': 'agentpass-subbots',
            'provider_bot_ref': PARENT + ':coder',
            'bot_id': SCOPED_ID,
            'token_present': True,
            'distinct_from_admin_uuid': True,
            'distinct_from_admin_token': True,
        },
        'duplicates': {
            'admin_status': 409,
            'scoped_status': 409,
        },
        'self_lookup': {
            'successes': [dict(success) for _ in range(9)],
            'rejections': [{'status': 401, 'cache_control': 'no-store'}
                           for _ in range(6)],
            'matching_actor_successes': 2,
            'actor_only_status': 401,
            'malformed_actor_status': 401,
        },
        'legacy_api': {
            'matching_statuses': [200, 200],
            'sibling_statuses': [401, 401],
        },
        'metadata_update': {
            'status': 200,
            'requested_bot_id': ADMIN_ID,
            'returned_bot_id': ADMIN_ID,
            'name': 'E2E Senior Reviewer',
            'authorization_scopes': [],
            'readback_parent_matches': True,
            'readback_ref_matches': True,
        },
        'onboarding': {
            'status': 200,
            'readback_status': 200,
            'readback_parent_matches': True,
            'readback_ref_matches': True,
        },
        'parent_discovery': {
            'unauthenticated_status': 401,
            'invalid_query_statuses': [400] * 4,
            'unavailable_status': 502,
            'error_code': 'agent_identity_unavailable',
        },
        'callback': {
            'authenticated_status': 404,
            'authenticated_error': 'run_not_found',
            'sibling_status': 401,
            'token_only_status': 404,
        },
        'signed_principal': {
            'accepted_status': 200,
            'conflicting_actor_sent': True,
            'disabled_status': 401,
            'enabled_status': 200,
            'deleted_status': 401,
        },
        'disable_enable': {
            'disable_status': 200,
            'disabled_view_statuses': [401, 401],
            'disabled_callback_status': 401,
            'disabled_signed_status': 401,
            'enable_status': 200,
            'enabled_view_statuses': [200, 200],
            'enabled_signed_status': 200,
        },
        'deletion': {
            'delete_reviewer_status': 200,
            'reviewer_view_status': 401,
            'sibling_view_status': 200,
            'deleted_signed_status': 401,
            'admin_reclaim_status': 409,
            'scoped_reclaim_status': 409,
            'delete_coder_status': 200,
            'coder_view_status': 401,
            'retire_status': 200,
        },
    }


OBLIGATION_CASES = {
    checks.OBLIGATION_PROVIDER:
        (checks.check_provider_registration_discovery,
         lambda value: value['provider'].__setitem__('auth_mode', 'static_bearer')),
    checks.OBLIGATION_ADMIN_REFS:
        (checks.check_admin_complete_ref_validation,
         lambda value: value['admin_ref_validation'].__setitem__('statuses', [400] * 5 + [200])),
    checks.OBLIGATION_ADMIN_REGISTRATION:
        (checks.check_admin_sibling_registration,
         lambda value: value['admin_registration'].__setitem__(
             'provider_bot_ref', PARENT + ':wrong')),
    checks.OBLIGATION_SCOPED_REFS:
        (checks.check_scoped_complete_ref_validation,
         lambda value: value['scoped_ref_validation'].__setitem__('removed_slug_status', 200)),
    checks.OBLIGATION_SCOPED_IDENTITY:
        (checks.check_scoped_sibling_distinct_identity,
         lambda value: value['scoped_registration'].__setitem__(
             'distinct_from_admin_uuid', False)),
    checks.OBLIGATION_DUPLICATES:
        (checks.check_duplicate_reference_conflicts,
         lambda value: value['duplicates'].__setitem__('admin_status', 200)),
    checks.OBLIGATION_SELF_LOOKUP:
        (checks.check_self_lookup_actor_binding,
         lambda value: value['self_lookup']['successes'][0].__setitem__(
             'bot_id_matches', False)),
    checks.OBLIGATION_LEGACY_API:
        (checks.check_legacy_api_actor_binding,
         lambda value: value['legacy_api'].__setitem__(
             'sibling_statuses', [401, 200])),
    checks.OBLIGATION_METADATA:
        (checks.check_metadata_update_parent_immutability,
         lambda value: value['metadata_update'].__setitem__(
             'returned_bot_id', SCOPED_ID)),
    checks.OBLIGATION_ONBOARD:
        (checks.check_onboard_parent_immutability,
         lambda value: value['onboarding'].__setitem__(
             'readback_ref_matches', False)),
    checks.OBLIGATION_PARENT_DISCOVERY:
        (checks.check_parent_discovery_fail_closed,
         lambda value: value['parent_discovery'].__setitem__('unavailable_status', 500)),
    checks.OBLIGATION_CALLBACK:
        (checks.check_callback_actor_binding,
         lambda value: value['callback'].__setitem__(
             'authenticated_error', 'run_found')),
    checks.OBLIGATION_SIGNED:
        (checks.check_signed_principal_live_state,
         lambda value: value['signed_principal'].__setitem__('disabled_status', 200)),
    checks.OBLIGATION_DISABLE_ENABLE:
        (checks.check_disable_enable_state,
         lambda value: value['disable_enable'].__setitem__(
             'enabled_view_statuses', [200, 401])),
    checks.OBLIGATION_DELETION:
        (checks.check_deletion_tombstone_retirement,
         lambda value: value['deletion'].__setitem__('sibling_view_status', 401)),
}


class ProviderSubbotsCheckTests(unittest.TestCase):
    def test_all_fifteen_obligations_accept_the_complete_observation(self):
        result = checks.verify_all_obligations(valid_evidence())
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['checked'], list(OBLIGATION_CASES))

    def test_every_obligation_has_a_reverse_failure_case(self):
        self.assertEqual(len(OBLIGATION_CASES), 15)
        for obligation, (check, mutate) in OBLIGATION_CASES.items():
            with self.subTest(obligation=obligation):
                value = copy.deepcopy(valid_evidence())
                mutate(value)
                with self.assertRaises(checks.ProviderSubbotsViolation):
                    check(value)

    def test_malformed_observation_is_reported_as_a_violation(self):
        value = valid_evidence()
        value['callback'] = []
        with self.assertRaises(RunStateError):
            checks.verify_all_obligations(value)

    def test_self_lookup_requires_all_success_and_rejection_counts(self):
        for mutation in ('successes', 'rejections', 'cache', 'matching_actor',
                         'actor_only', 'malformed_actor'):
            with self.subTest(mutation=mutation):
                value = valid_evidence()
                if mutation == 'successes':
                    value['self_lookup']['successes'].pop()
                elif mutation == 'rejections':
                    value['self_lookup']['rejections'].pop()
                elif mutation == 'cache':
                    value['self_lookup']['rejections'][0]['cache_control'] = 'public'
                elif mutation == 'matching_actor':
                    value['self_lookup']['matching_actor_successes'] = 1
                elif mutation == 'actor_only':
                    value['self_lookup']['actor_only_status'] = 200
                else:
                    value['self_lookup']['malformed_actor_status'] = 200
                with self.assertRaises(checks.ProviderSubbotsViolation):
                    checks.check_self_lookup_actor_binding(value)

    def test_redaction_scan_detects_each_credential_canary(self):
        clean = checks.scan_redaction('story.stdout', b'Provider subbots story: 131 passed')
        self.assertTrue(clean['clean'])
        for canary in ('bot' + '_token', 'provider_admin' + '_token',
                       'X-Avernet-Principal', 'Bear' + 'er secret',
                       'coo' + 'kie=value'):
            with self.subTest(canary=canary):
                result = checks.scan_redaction('story.stdout', canary.encode())
                self.assertFalse(result['clean'])


if __name__ == '__main__':
    unittest.main()
