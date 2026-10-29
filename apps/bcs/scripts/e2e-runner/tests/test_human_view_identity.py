"""Post-bootstrap identity admission must not re-run ensure_human_actor."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import plan
from bcs_e2e.identity_binding import HUMAN_VIEW, NATIVE, identity_request, pilot_execution_blocker
from bcs_e2e.preflight import check
from bcs_e2e.probe import http_probe
from bcs_e2e.profile import ConfigError, fingerprint, validate
from bcs_e2e.transport import ObservationError, ReadClient
import test_runner as helpers


class HumanViewIdentityTests(unittest.TestCase):
    def profile(self):
        p = helpers.profile()
        p['identity_binding'] = dict(HUMAN_VIEW)
        return validate(p)

    def page(self, items=None):
        return {'items': [] if items is None else items, 'total': 0 if items is None else len(items),
                'offset': 0, 'limit': 1}

    def human(self):
        return {'bot_id': 'human_owner-1', 'kind': 'human', 'created_by': 'owner-1', 'env': 'pre'}

    def client(self, body, code=200):
        env = patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'})
        env.start(); self.addCleanup(env.stop)
        opener = patch('bcs_e2e.transport.build_opener')
        mock = opener.start(); self.addCleanup(opener.stop)
        mock.return_value.open.side_effect = [helpers.Reply(helpers.envelope(self.human())), helpers.Reply(body, code=code)]
        return ReadClient(self.profile()), mock

    def test_profile_accepts_only_finite_gateway_self_view_binding(self):
        p = self.profile()
        self.assertEqual(pilot_execution_blocker(p), '')
        for kind in ('bcs-direct', 'mock', 'singlebox'):
            candidate = copy.deepcopy(p); candidate['entrypoint']['kind'] = kind
            with self.subTest(kind=kind), self.assertRaises(ConfigError): validate(candidate)
        for field, value in (('method', 'POST'), ('path', '/openapi/v1/collaboration/bots/mine'),
                              ('response', 'public-envelope'), ('unknown', True)):
            candidate = copy.deepcopy(p); candidate['identity_binding'][field] = value
            with self.subTest(field=field), self.assertRaises(ConfigError): validate(candidate)

    def test_explicit_binding_changes_digest_not_legacy_or_native_behavior(self):
        old = validate(helpers.profile()); digest = fingerprint(old)
        self.assertEqual(identity_request(old)['path'], '/openapi/v1/auth/user')
        self.assertEqual(fingerprint(old), digest)
        self.assertNotEqual(fingerprint(self.profile()), digest)
        p = self.profile(); p['entrypoint']['kind'] = 'bcs-direct'; p['identity_binding'] = dict(NATIVE)
        self.assertIn('discovery-only', pilot_execution_blocker(validate(p)))

    def test_plan_resolves_exact_expected_human_without_network(self):
        with patch('bcs_e2e.transport.build_opener') as opener:
            report = plan(self.profile())
        opener.assert_not_called()
        row = next(x for x in report['preflight_routes'] if x['operation'] == 'identity.read')
        self.assertEqual(row['path'], '/openapi/v1/collaboration/bots/human_owner-1/candidates?offset=0&limit=1')
        self.assertEqual(row['response'], 'authorized-human-view')
        self.assertFalse(report['ready_to_execute'])
        self.assertEqual(report['execution_admission']['status'], 'NOT_EVALUATED')

    def test_authorization_proof_uses_no_mine_or_synthetic_identity_headers(self):
        client, opener = self.client(helpers.envelope(self.page()))
        result = client.read('identity.read')
        self.assertEqual(result.data, {'user_id': 'owner-1', 'identity_basis': 'server-authorized-human-view'})
        self.assertFalse(result.evidence['identity_returned_directly'])
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, self.profile()['entrypoint']['origin'] + identity_request(self.profile())['path'])
        self.assertEqual(request.method, 'GET'); self.assertIsNone(request.data)
        for header in ('X-user-id', 'X-mock-user-id', 'X-avernet-principal'):
            self.assertNotIn(header, request.headers)
        self.assertEqual(opener.return_value.open.call_count, 2)

    def test_candidates_are_not_exposed_as_identity_or_saved_evidence(self):
        client, _ = self.client(helpers.envelope(self.page([{'bot': {'name': 'private-candidate'}, 'is_friend': False}])))
        result = client.read('identity.read')
        self.assertNotIn('private-candidate', json.dumps({'data': result.data, 'evidence': result.evidence}))
        self.assertNotIn('test-token', json.dumps(result.evidence))

    def test_denial_missing_fixture_or_error_never_triggers_bootstrap_or_fallback(self):
        for status in (401, 403, 404, 302, 500):
            client, opener = self.client({'error': 'not admitted'}, status)
            with self.subTest(status=status), self.assertRaises(ObservationError): client.read('identity.read')
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_wrong_body_and_pagination_do_not_become_identity_proof(self):
        invalid = [{}, {'user_id': 'owner-1'}, {'items': []}, self.page([{}, {}])]
        for field, value in (('offset', 1), ('offset', False), ('limit', 50), ('total', -1),
                              ('total', True), ('items', None), ('items', ['bad'])):
            page = self.page(); page[field] = value; invalid.append(page)
        for page in invalid:
            client, opener = self.client(helpers.envelope(page))
            with self.subTest(page=page), self.assertRaises(ObservationError): client.read('identity.read')
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_server_user_field_cannot_override_authorized_perspective(self):
        page = self.page(); page['user_id'] = 'unexpected-value'
        client, _ = self.client(helpers.envelope(page))
        self.assertEqual(client.read('identity.read').data['user_id'], 'owner-1')

    def test_full_read_only_preflight_and_probe_reuse_declared_contract(self):
        bodies = [helpers.responses()[0], helpers.envelope(self.human()), helpers.envelope(self.page()), *helpers.responses()[2:]]
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
             patch('bcs_e2e.transport.build_opener') as opener:
            opener.return_value.open.side_effect = [helpers.Reply(x) for x in bodies]
            report = check(self.profile(), ReadClient(self.profile()))
        self.assertEqual(report['status'], 'DISCOVERY_PASSED')
        self.assertEqual(opener.return_value.open.call_count, 5)
        client, opener = self.client(helpers.envelope(self.page()))
        with patch('bcs_e2e.probe.ReadClient', return_value=client):
            probe = http_probe(self.profile(), 'identity.read')
        self.assertEqual(probe['status'], 'PROBE_PASSED')
        self.assertFalse(probe['evidence']['identity_returned_directly'])
        self.assertEqual(len(probe['prerequisite_requests']), 1)
        self.assertEqual(probe['prerequisite_requests'][0]['operation'], 'identity.actor.read')


    def test_viewpoint_kind_owner_id_and_environment_are_checked_before_candidates(self):
        for field, wrong in (('kind', 'bot'), ('created_by', 'other-user'), ('env', 'prod'),
                             ('bot_id', 'human_other-user')):
            human = self.human(); human[field] = wrong
            client, opener = self.client(helpers.envelope(self.page()))
            opener.return_value.open.side_effect = [helpers.Reply(helpers.envelope(human))]
            with self.subTest(field=field), self.assertRaisesRegex(ObservationError, 'exact expected Human'):
                client.read('identity.read')
            opener.return_value.open.assert_called_once()
            self.assertNotIn('/candidates', opener.return_value.open.call_args.args[0].full_url)

    def test_missing_human_blocks_without_initialization(self):
        client, opener = self.client(helpers.envelope(self.page()))
        opener.return_value.open.side_effect = [helpers.Reply({}, code=404)]
        with self.assertRaises(ObservationError): client.read('identity.read')
        opener.return_value.open.assert_called_once()
        self.assertNotIn('/mine', opener.return_value.open.call_args.args[0].full_url)

    def test_unresolved_environment_is_not_used_for_human_view(self):
        client, opener = self.client(helpers.envelope(self.page()))
        client.profile['environment']['expected_bcs_env'] = 'REPLACE_WITH_ENV'
        with self.assertRaises(ConfigError): client.read('identity.read')
        opener.return_value.open.assert_not_called()

    def test_human_record_projection_drops_profile_and_description_fields(self):
        client, opener = self.client(helpers.envelope(self.page()))
        human = {**self.human(), 'name': 'private-human-name', 'description': 'private-description'}
        opener.return_value.open.side_effect = [helpers.Reply(helpers.envelope(human))]
        observed = client.read('identity.actor.read')
        self.assertEqual(observed.data, self.human())
        self.assertNotIn('private-', json.dumps({'data': observed.data, 'evidence': observed.evidence}))


    def test_prerequisite_credential_echo_is_rejected_before_probe_evidence(self):
        client, opener = self.client(helpers.envelope(self.page()))
        envelope = helpers.envelope(self.human()); envelope['request_id'] = 'test-token'
        opener.return_value.open.side_effect = [helpers.Reply(envelope), helpers.Reply(helpers.envelope(self.page()))]
        with patch('bcs_e2e.probe.ReadClient', return_value=client):
            probe = http_probe(self.profile(), 'identity.read')
        self.assertEqual(probe['status'], 'BLOCKED')
        self.assertEqual(probe['auth_leak_check'], 'PASS')  # No secret reached probe evidence.
        self.assertTrue(client.evidence[0]['response_envelope_request_id_rejected'])
        opener.return_value.open.assert_called_once()
        self.assertNotIn('test-token', json.dumps(client.evidence))
        self.assertNotIn('test-token', json.dumps(probe))

    def test_probe_defense_in_depth_checks_prerequisite_metadata(self):
        from bcs_e2e.transport import Observation
        client = Mock()
        prerequisite = {'operation': 'identity.actor.read', 'response_request_id': 'test-token'}
        evidence = {'operation': 'identity.read', 'identity_basis': 'server-authorized-human-view'}
        client.evidence = [prerequisite, evidence]
        client.read.return_value = Observation({'user_id': self.profile()['human']['user_id']}, evidence)
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
             patch('bcs_e2e.probe.ReadClient', return_value=client):
            probe = http_probe(self.profile(), 'identity.read')
        self.assertEqual(probe['status'], 'BLOCKED')
        self.assertEqual(probe['auth_leak_check'], 'FAIL')
        self.assertNotIn('test-token', json.dumps(probe))
