"""Native group slice never relaxes the V1 pilot or uses session-list GET."""
import copy
import json
import os
import time
import unittest
from unittest.mock import patch

import test_runner as helpers
from bcs_e2e.identity_binding import NATIVE
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.remote_effects import require_group_effect_admission, RemoteBootstrapAdmissionError
from bcs_e2e.sessionless_native import NativeSessionlessClient, CONTRACT, validate_review
from bcs_e2e.write_transport import WriteTransportError


def profile():
    p = helpers.profile()
    p['entrypoint'].update(kind='bcs-direct', manifest_face='legacy')
    p['identity_binding'] = dict(NATIVE)
    return p


def review(p):
    now = int(time.time())
    return {'schema_version': 1, 'contract': CONTRACT, 'profile_digest': fingerprint(p),
            'health_version': '0.1.0 (ocb HEAD/abcdef0; avernet dev/1234567; 2026-10-10)',
            'ocb_commit': 'abcdef0'+'0'*33, 'avernet_commit': '1234567'+'0'*33,
            'source_review_sha256': 'a'*64, 'reviewed_at': now-1, 'expires_at': now+60}


def bot(p, role):
    return {'bot_uuid': p['actors'][role]['bot_id'], 'actor_kind': 'bot',
            'created_by': p['human']['user_id'], 'env': 'pre', 'status': 'online',
            'capabilities': {'visibility': 'protected'}}


class NativeTests(unittest.TestCase):
    def make_client(self, bodies):
        p = profile(); r = review(p)
        env = patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'fake-cookie'}); env.start(); self.addCleanup(env.stop)
        opener = patch('bcs_e2e.write_transport.build_opener'); mock = opener.start(); self.addCleanup(opener.stop)
        mock.return_value.open.side_effect = [helpers.Reply(body, status) for body, status in bodies]
        return NativeSessionlessClient(p, r), mock.return_value.open, p, r

    def test_review_must_match_source_and_profile_and_expire(self):
        p = profile(); r = review(p)
        self.assertEqual(validate_review(r, p), r)
        for update in ({'profile_digest': '0'*64}, {'ocb_commit': 'f'*40},
                       {'avernet_commit': 'f'*40}, {'expires_at': 1},
                       {'expires_at': int(time.time())+7200}, {'reviewed_at': True},
                       {'health_version': '0.1.0'}, {'contract': 'public-v1'}):
            with self.subTest(update=update), self.assertRaises(ConfigError):
                validate_review({**r, **update}, p)

    def test_native_preflight_and_projected_metadata_no_secret(self):
        p = profile(); r = review(p)
        client, send, _, _ = self.make_client([
            ({'user_id': 'owner-1'}, 200), ({'env': 'pre'}, 200),
            ({'service': 'bcs', 'status': 'ok', 'version': r['health_version']}, 200),
            (bot(p, 'driver'), 200), (bot(p, 'member'), 200)])
        client.preflight()
        self.assertEqual(send.call_count, 5)
        self.assertTrue(all(c.args[0].method == 'GET' for c in send.call_args_list))
        self.assertNotIn('fake-cookie', json.dumps(client.evidence))
        self.assertNotIn('capabilities', json.dumps(client.evidence))

    def test_preflight_identity_or_version_mismatch_stops_before_bots(self):
        for bodies, count in [([({'user_id': 'wrong'}, 200)], 1),
                               ([({'user_id':'owner-1'},200), ({'env':'prod'},200)],2),
                               ([({'user_id':'owner-1'},200), ({'env':'pre'},200),
                                 ({'status':'ok','service':'bcs','version':'unknown'},200)],3)]:
            with self.subTest(count=count):
                client, send, _, _ = self.make_client(bodies)
                with self.assertRaises(ConfigError): client.preflight()
                self.assertEqual(send.call_count, count)

    def test_public_or_wrong_owner_bot_blocks(self):
        for field in ('visibility', 'owner', 'env', 'kind'):
            p = profile(); b = bot(p, 'driver')
            if field == 'visibility': b['capabilities']['visibility'] = 'public'
            elif field == 'owner': b['created_by'] = 'someone-else'
            elif field == 'env': b['env'] = 'prod'
            else: b['actor_kind'] = 'human'
            client, send, _, _ = self.make_client([(b,200)])
            with self.subTest(field=field), self.assertRaises(ConfigError): client.verify_actors()
            self.assertEqual(send.call_count, 1)

    def test_create_fixes_suppression_and_rechecks_build_and_bots(self):
        p = profile(); r = review(p)
        client, send, _, _ = self.make_client([
            ({'service':'bcs','status':'ok','version':r['health_version']},200),
            (bot(p,'driver'),200), (bot(p,'member'),200), ({'id':'created'},200)])
        self.assertEqual(client.create('test-group', 'journaled-request-id'), {'id':'created'})
        req = send.call_args_list[-1].args[0]; body = json.loads(req.data)
        self.assertEqual(req.method, 'POST'); self.assertTrue(req.full_url.endswith('/groups'))
        self.assertIs(body['create_initial_session'], False)
        self.assertEqual(req.get_header('X-request-id'), 'journaled-request-id')
        self.assertEqual(body['participants'], [{'bot_uuid':'bot-a','role':'driver'},
                                               {'bot_uuid':'bot-b','role':'consultant'}])
        self.assertEqual(body['group_strategy'],'chat'); self.assertEqual(body['visibility'],'private')
        self.assertEqual(body['originator'],'human_owner-1')
        self.assertNotIn('event_subscriptions',body)
        self.assertFalse(any('/sessions' in c.args[0].full_url for c in send.call_args_list))

    def test_denial_does_not_fallback_or_retry(self):
        client, send, _, _ = self.make_client([({'private':'body'},403)])
        with self.assertRaises(WriteTransportError): client.preflight()
        self.assertEqual(send.call_count,1)

    def test_public_pilot_guard_stays_blocked(self):
        with self.assertRaises(RemoteBootstrapAdmissionError): require_group_effect_admission(profile())
        p=profile(); p['entrypoint']['kind']='gateway'
        with self.assertRaises(ConfigError): NativeSessionlessClient(p,review(p))

    def test_native_transport_does_not_mutate_profile(self):
        p=profile(); original=copy.deepcopy(p)
        with patch.dict(os.environ,{'BCS_E2E_HUMAN_COOKIE':'fake-cookie'}):
            NativeSessionlessClient(p,review(p))
        self.assertEqual(original,p)
