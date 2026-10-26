"""Read-only trust preconditions never repair fixtures or count Story passes."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity, LocalRoleIdentityError
from bcs_e2e.trust_baseline import (ROLES, PAIRS, TrustBaselineError, require_empty,
                                    visibility, result)
import test_local_fixture_sets as fixtures


class TrustBaselineTests(unittest.TestCase):
    response = fixtures.LocalFixtureSetTests.response

    def setUp(self):
        fixtures.LocalFixtureSetTests.setUp(self)
        self.bound = OwnedLocalFixtures(self.runtime, fixture_set='trusted-team')

    def provision(self, identity):
        with patch.object(self.bound, '_request', side_effect=self.response):
            self.bound.apply(allow_write=True, role_identity=identity)
        with patch.object(identity, '_request', side_effect=self.read_response):
            for role in identity.roles: identity.verify(role)

    @staticmethod
    def read_response(operation, bot_id, _secret):
        return {'success': True, 'data': {'bot_uuid': bot_id, 'visibility': 'protected'}
                if operation == 'visibility' else []}

    def test_five_roles_exact_pairs_and_observed_visibility_need_no_business_writes(self):
        with OwnedLocalRoleIdentity(self.bound, roles=ROLES) as identity:
            self.provision(identity)
            before = self.bound.path.read_bytes()
            with patch.object(identity, '_request', side_effect=self.read_response) as requests, \
                 patch('bcs_e2e.local_trust_store.observe_legacy_baseline', return_value={'status': 'OBSERVED'}):
                baseline = identity.trust_baseline()
            self.assertEqual(requests.call_count, 25)
            for index, role in enumerate(ROLES):
                calls = requests.call_args_list[index*5:(index+1)*5]
                self.assertEqual([call.args[0] for call in calls],
                                 ['visibility', 'friends', 'pending-sent', 'pending-received', 'visibility'])
                self.assertTrue(all(call.args[1] == 'bot-' + role for call in calls))
            self.assertEqual(baseline['status'], 'BASELINE_OBSERVED')
            self.assertEqual(len(baseline['roles']), 5)
            self.assertEqual(len(baseline['required_absent_pairs']), 4)
            self.assertEqual(baseline['visibility_changes_required'],
                             [{'role': 'ENG', 'before': 'protected', 'required': 'public'}])
            self.assertFalse(baseline['ready_to_execute'])
            self.assertEqual(baseline['business_writes'], 0)
            self.assertEqual(baseline['storage_completeness'], 'LEGACY_INCIDENT_ROWS_VERIFIED')
            self.assertIn('separate read-transaction snapshot', baseline['note'])
            self.assertNotIn('API baseline only', baseline['note'])
            self.assertEqual(baseline['full_story_status'], 'NOT_MIGRATED')
            self.assertEqual(self.bound.path.read_bytes(), before)
            self.assertNotIn('test-token-', json.dumps(baseline))
            with patch.object(identity, '_request') as requests, self.assertRaises(LocalRoleIdentityError):
                identity.trust_baseline()
            requests.assert_not_called()
            self.assertEqual(identity.summary()['credential_memory'], 'CLEARED')

    def test_existing_friend_pending_sent_or_received_stop_without_repairs(self):
        for operation in ('friends', 'pending-sent', 'pending-received'):
            with self.subTest(operation=operation), OwnedLocalRoleIdentity(self.bound, roles=ROLES) as identity:
                self.provision(identity)
                def conflict(current, bot_id, value):
                    response = self.read_response(current, bot_id, value)
                    if current == operation:
                        response['data'] = ([{'bot_uuid': 'bot-PM', 'summary': 'private-not-retained'}]
                            if operation == 'friends' else [{'id': 'pending-1', 'status': 'pending',
                                'from_bot': bot_id if operation == 'pending-sent' else 'bot-PM',
                                'to_bot': bot_id if operation == 'pending-received' else 'bot-PM'}])
                    return response
                with patch.object(identity, '_request', side_effect=conflict) as request, \
                     self.assertRaises(TrustBaselineError) as caught:
                    identity.trust_baseline()
                self.assertLessEqual(request.call_count, 4)
                self.assertNotIn('private-not-retained', str(caught.exception))
                self.assertEqual(identity.summary()['credential_memory'], 'CLEARED')
            self.bound.path.unlink()

    def test_visibility_drift_cancellation_or_receipt_change_never_returns_a_baseline(self):
        for mode in ('visibility', 'cancel', 'receipt'):
            with self.subTest(mode=mode), OwnedLocalRoleIdentity(self.bound, roles=ROLES) as identity:
                self.provision(identity)
                count = 0
                def unstable(operation, bot_id, value):
                    nonlocal count
                    count += 1
                    if mode == 'cancel' and count == 3: raise KeyboardInterrupt()
                    if mode == 'receipt' and count == 3:
                        state = json.loads(self.bound.path.read_text()); state['status'] = 'BLOCKED'
                        self.bound.path.write_text(json.dumps(state))
                    response = self.read_response(operation, bot_id, value)
                    if mode == 'visibility' and count == 5: response['data']['visibility'] = 'public'
                    return response
                with patch.object(identity, '_request', side_effect=unstable) as request, \
                     self.assertRaises((TrustBaselineError, LocalRoleIdentityError, KeyboardInterrupt)):
                    identity.trust_baseline()
                self.assertLessEqual(request.call_count, 5)
                self.assertEqual(identity.summary()['credential_memory'], 'CLEARED')
            self.bound.path.unlink()

    def test_baseline_requires_every_verified_role_before_http(self):
        for mode in ('unverified', 'partial'):
            roles = ROLES if mode == 'unverified' else ('CEO', 'PM')
            with OwnedLocalRoleIdentity(self.bound, roles=roles) as identity:
                if mode == 'partial': self.provision(identity)
                with patch.object(identity, '_request') as request, self.assertRaises(LocalRoleIdentityError):
                    identity.trust_baseline()
                request.assert_not_called()
            if self.bound.path.exists(): self.bound.path.unlink()

    def test_malformed_falsey_truncation_full_pages_and_foreign_direction_fail_closed(self):
        with self.assertRaises(TrustBaselineError):
            require_empty({'success': True, 'data': []}, operation='unreviewed', bot_id='bot-A')
        for flag in (True, 1, 0, None, 'false', [], {}):
            with self.subTest(flag=flag), self.assertRaises(TrustBaselineError):
                require_empty({'success': True, 'data': [], 'truncated': flag}, operation='friends', bot_id='bot-A')
        payloads = [{'success': False, 'data': []}, {'success': True, 'data': {}},
                    {'success': True, 'data': [{}]*100},
                    {'success': True, 'data': [{'id':'p-1','from_bot':'foreign','to_bot':'bot-B','status':'pending'}]},
                    {'success': True, 'data': [{'id':'p-1','from_bot':'bot-A','to_bot':'bot-B','status':'accepted'}]}]
        for payload in payloads:
            with self.assertRaises(TrustBaselineError): require_empty(payload, operation='pending-sent', bot_id='bot-A')
        for data in ({}, {'bot_uuid':'foreign','visibility':'public'}, {'bot_uuid':'bot-A','visibility':'unknown'}):
            with self.assertRaises(TrustBaselineError): visibility({'success':True,'data':data}, 'bot-A')

    def test_pending_route_has_no_human_fallback_selector_and_never_resolves_cookies(self):
        identity = OwnedLocalRoleIdentity(self.bound, roles=ROLES)
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _): return b'{"success":true,"data":[]}'
        with patch.object(identity.opener, 'open', return_value=Response()) as request:
            for operation, direction in (('pending-sent','sent'), ('pending-received','received')):
                identity._request(operation, 'bot-A', 'test-token')
                sent = request.call_args.args[0]
                self.assertEqual(sent.full_url, self.inputs.origin + '/friends/requests?direction=' + direction + '&status=pending')
                self.assertEqual(sent.get_method(), 'GET')
                self.assertEqual(set(dict(sent.header_items())), {'Accept','Authorization'})
                self.assertNotIn('bot_uuid', sent.full_url)

    def test_cli_requires_explicit_identity_flag_and_trusted_team_before_start(self):
        for selected, flags in (('workspace', ['--verify-bot-identities']), ('trusted-team', [])):
            with patch('bcs_e2e.cli.local_runtime_smoke') as smoke, contextlib.redirect_stdout(io.StringIO()):
                rc = main(['runtime','fixture-smoke','--profile',str(ROOT/'profiles/local.singlebox.example.json'),
                           '--state-dir',str(self.inputs.root/'unused'),'--fixture-set',selected,
                           '--check-trust-baseline','--allow-local-processes','--allow-fixture-write',*flags])
            self.assertEqual(rc,2); smoke.assert_not_called()
