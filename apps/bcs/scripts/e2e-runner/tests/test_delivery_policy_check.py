"""Native policy check: fake HTTP only, never live hosts or credentials."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.delivery_policy_check import CONTRACT, PATH, PolicyReadReview
from bcs_e2e.delivery_policy_projection import project_policy
from bcs_e2e.identity_binding import NATIVE, HUMAN_VIEW
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import write
from bcs_e2e.transport import ObservationError, ReadClient
import test_runner as helpers


def record():
    return {'version': 7, 'updated_by': 'unrelated-operator', 'updated_at_ms': 100,
            'policy': {'flow_enabled': {'group': True, 'system': True, 'task': False,
                        'direct_a2a': False, 'state_machine': False},
                       'defaults': {'mode': 'off', 'max_running': 1, 'max_queued': 20, 'min_send_interval_ms': 1000},
                       'bots': {'bot-a': {'mode': 'enforce'}, 'bot-b': {'mode': 'enforce', 'max_queued': 5},
                                'unrelated-bot': {'max_running': 3}},
                       'queue_ttl_ms': None, 'safe_retry': None, 'pause_dispatch': False,
                       'max_context_messages': 24, 'max_context_bytes': 131072}}


class PolicyProjectionTests(unittest.TestCase):
    def test_exact_actors_defaults_and_overrides_without_unrelated_information(self):
        value = project_policy(record(), helpers.profile())
        self.assertEqual(value['version'], 7)
        self.assertEqual(value['actors']['driver']['configuration']['max_queued'], 20)
        self.assertEqual(value['actors']['member']['configuration']['max_queued'], 5)
        self.assertTrue(value['both_actors_group_system_managed'])
        self.assertNotIn('unrelated-bot', json.dumps(value)); self.assertNotIn('unrelated-operator', json.dumps(value))
        self.assertEqual(value['whole_scope_completion'], 'NOT_PROVEN')
        self.assertEqual(value['scheduler_health'], 'NOT_PROVEN')
        self.assertEqual(value['record_digest'], fingerprint(record()))

    def test_default_and_nullable_override_match_source_resolution(self):
        value = record(); value['policy']['defaults']['mode'] = 'enforce'
        value['policy']['bots'] = {'bot-a': {'mode': None, 'max_running': None}, 'bot-b': {'mode': 'off'}}
        projected = project_policy(value, helpers.profile())
        self.assertTrue(projected['actors']['driver']['managed_new_flows']['group'])
        self.assertFalse(projected['actors']['member']['managed_new_flows']['group'])
        self.assertFalse(projected['both_actors_group_system_managed'])

    def test_disabled_zero_record_is_not_a_missing_response_or_end_evidence(self):
        value = record(); value.update(version=0, updated_by='', updated_at_ms=0)
        value['policy']['bots'] = {}; value['policy']['flow_enabled'] = dict.fromkeys(value['policy']['flow_enabled'], False)
        projected = project_policy(value, helpers.profile())
        self.assertTrue(projected['synthetic_disabled_version'])
        self.assertFalse(projected['both_actors_group_system_managed'])
        value['policy']['flow_enabled'].update(group=True, system=True)
        with self.assertRaises(ConfigError): project_policy(value, helpers.profile())

    def test_strict_numeric_and_source_switch_validation(self):
        changes = [('version', True), ('updated_at_ms', -1), ('policy.pause_dispatch', 1),
                   ('policy.flow_enabled.group', False), ('policy.flow_enabled.state_machine', True),
                   ('policy.defaults.max_running', 0), ('policy.defaults.min_send_interval_ms', -1),
                   ('policy.defaults.max_queued', True), ('policy.defaults.mode', 'observe'),
                   ('policy.max_context_messages', 0), ('policy.max_context_bytes', 1),
                   ('policy.queue_ttl_ms', 0), ('policy.safe_retry', {'max_retries': 11, 'backoff_ms': 1}),
                   ('policy.safe_retry', {'max_retries': 1, 'backoff_ms': True})]
        for path, replacement in changes:
            value = record(); node = value; parts = path.split('.')
            for key in parts[:-1]: node = node[key]
            node[parts[-1]] = replacement
            with self.subTest(path=path), self.assertRaises(ConfigError): project_policy(value, helpers.profile())

    def test_unknown_fields_unrelated_invalid_override_and_map_bound_rejected(self):
        for edit in (lambda v: v.update(extra=1), lambda v: v['policy'].update(secret='private'),
                     lambda v: v['policy']['bots']['unrelated-bot'].update(max_running=False),
                     lambda v: v['policy']['bots']['unrelated-bot'].update(unknown=True),
                     lambda v: v['policy'].update(bots={str(n): {} for n in range(4097)})):
            value = record(); edit(value)
            with self.assertRaises(ConfigError): project_policy(value, helpers.profile())

    def test_pause_dispatch_and_retry_do_not_mean_running_work_stopped(self):
        value = record(); value['policy'].update(pause_dispatch=True, safe_retry={'max_retries': 2, 'backoff_ms': 5})
        projected = project_policy(value, helpers.profile())
        self.assertTrue(projected['pause_dispatch'])
        self.assertEqual(projected['safe_retry'], {'max_retries': 2, 'backoff_ms': 5})
        self.assertEqual(projected['whole_scope_completion'], 'NOT_PROVEN')


class PolicyCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = helpers.profile()
        self.profile['entrypoint'].update(kind='bcs-direct', origin='https://bcn.example.com', manifest_face='legacy')
        self.profile['identity_binding'] = dict(NATIVE)
        self.profile['auth'] = {'header_env': {'Cookie': 'E2E_POLICY_TEST_COOKIE'}}
        self.profile_path = self.root/'profile.json'; write(self.profile_path, self.profile)
        now = int(time.time())
        self.review = {'schema_version': 1, 'contract': CONTRACT, 'profile_digest': fingerprint(self.profile),
                       'reviewer': self.profile['human']['user_id'], 'reviewed_at': now-1, 'expires_at': now+1800,
                       'path': PATH, 'source_review': {'reference': 'synthetic-source-review', 'sha256': 'a'*64,
                                                       'deployment_commit': 'b'*40}}
        self.review_path = self.root/'review.json'; write(self.review_path, self.review)
        self.environment = {'env': 'pre'}
        self.identity = {'user_id': 'owner-1', 'provider': 'OAuth2', 'name': 'private-name', 'avatar': None}
        self.policy = record(); self.policy_code = 200
        self.requests = []; self.after_request = lambda operation: None

    def respond(self, request, **kwargs):
        self.requests.append(request)
        index = len(self.requests)
        self.assertEqual(request.method, 'GET'); self.assertIsNone(request.data)
        self.assertEqual(request.get_header('Cookie'), 'synthetic-policy-cookie')
        self.assertEqual(request.full_url, self.profile['entrypoint']['origin']+
                         ('/manifest', '/auth/user', PATH)[index-1])
        self.assertEqual(kwargs['timeout'], self.profile['policy']['timeout_seconds'])
        self.after_request(index)
        return helpers.Reply((self.environment, self.identity, self.policy)[index-1],
                             code=self.policy_code if index == 3 else 200)

    def execute(self, allow=True):
        args = ['policy-check', '--profile', str(self.profile_path), '--review', str(self.review_path)]
        if allow: args.append('--allow-read')
        with patch.dict(os.environ, {'E2E_POLICY_TEST_COOKIE': 'synthetic-policy-cookie'}), \
             patch('bcs_e2e.transport.build_opener') as opener, contextlib.redirect_stdout(io.StringIO()) as output:
            opener.return_value.open.side_effect = self.respond
            rc = main(args)
        return rc, json.loads(output.getvalue())

    def test_three_explicit_native_gets_no_state_creation_or_gate_release(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        rc, result = self.execute()
        self.assertEqual(rc, 0, result); self.assertEqual(result['status'], 'POLICY_OBSERVED')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len(result['requests']), 3)
        self.assertFalse(result['ready_to_execute']); self.assertFalse(result['pool_release_authorized'])
        self.assertEqual(result['execution_status'], 'UNCONFIRMED')
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)
        for private in ('private-name', 'synthetic-policy-cookie', 'unrelated-operator', 'unrelated-bot'):
            self.assertNotIn(private, json.dumps(result))

    def test_missing_explicit_permission_never_resolves_credentials_or_http(self):
        rc, result = self.execute(allow=False)
        self.assertEqual(rc, 2); self.assertEqual(self.requests, [])

    def test_bad_review_refuses_before_http(self):
        for edit in ({'profile_digest': '0'*64}, {'reviewer': 'other'}, {'expires_at': 1},
                     {'path': '/openapi/v1/admin/message-delivery/policy'}, {'path': 'https://foreign.example.com'},
                     {'contract': 'other'}, {'unexpected': True}):
            write(self.review_path, {**self.review, **edit})
            with self.subTest(edit=edit):
                rc, result = self.execute(); self.assertEqual(rc, 2); self.assertEqual(self.requests, [])

    def test_gateway_profile_is_not_rewritten_or_used_as_fallback(self):
        self.profile['entrypoint']['kind'] = 'gateway'; self.profile['identity_binding'] = dict(HUMAN_VIEW)
        write(self.profile_path, self.profile)
        self.review['profile_digest'] = fingerprint(self.profile); write(self.review_path, self.review)
        rc, result = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(self.requests, [])
        self.assertEqual(json.loads(self.profile_path.read_text()), self.profile)

    def test_machine_auth_or_legacy_identity_binding_does_not_admit_policy(self):
        for auth in ({'header_env': {'Authorization': 'E2E_POLICY_TEST_COOKIE'}},
                     {'header_env': {'Authorization': 'E2E_POLICY_TEST_COOKIE', 'Cookie': 'E2E_POLICY_TEST_COOKIE'}}):
            self.profile['auth'] = auth; write(self.profile_path, self.profile)
            self.review['profile_digest'] = fingerprint(self.profile); write(self.review_path, self.review)
            rc, _ = self.execute(); self.assertEqual(rc, 2); self.assertEqual(self.requests, [])

    def test_wrong_environment_stops_before_identity(self):
        self.environment = {'env': 'prod'}
        rc, result = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(len(self.requests), 1)
        self.assertIsNone(result['policy'])

    def test_wrong_human_stops_before_policy(self):
        self.identity['user_id'] = 'foreign'
        rc, result = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(len(self.requests), 2)
        self.assertIsNone(result['policy'])

    def test_local_identity_is_not_remote_cookie_authentication(self):
        for provider in ('Local', 'local', '', None):
            self.requests = []; self.identity['provider'] = provider
            rc, result = self.execute()
            self.assertEqual(rc, 2); self.assertEqual(len(self.requests), 2)
            self.assertIsNone(result['policy'])

    def test_http_failure_no_json_body_required_and_no_route_retry(self):
        for code in (301, 302, 401, 403, 404, 503):
            self.requests = []; self.policy_code = code; self.policy = b''
            rc, result = self.execute()
            self.assertEqual(rc, 2); self.assertEqual(len(self.requests), 3)
            self.assertEqual(result['requests'][-1]['http_status'], code)
            self.assertIsNone(result['policy'])

    def test_envelope_html_duplicate_keys_and_oversize_are_not_policy_records(self):
        bad = (helpers.envelope(record()), b'<html>login</html>',
               b'{"version":7,"version":8}', b'x'*(self.profile['policy']['max_response_bytes']+1), {})
        for value in bad:
            self.requests = []; self.policy = value
            rc, result = self.execute()
            self.assertEqual(rc, 2); self.assertEqual(len(self.requests), 3)
            self.assertIsNone(result['policy'])

    def test_review_expiry_between_reads_stops_next_get(self):
        def expire(index):
            if index == 1: write(self.review_path, {**self.review, 'expires_at': 1})
        self.after_request = expire
        rc, result = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(len(self.requests), 1)
        self.assertIsNone(result['policy'])

    def test_review_change_after_policy_return_rejects_projection(self):
        def change(index):
            if index == 3: write(self.review_path, {**self.review, 'expires_at': self.review['expires_at']+1})
        self.after_request = change
        rc, result = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(len(self.requests), 3)
        self.assertIsNone(result['policy'])

    def test_transport_requires_real_matching_authority(self):
        with patch.dict(os.environ, {'E2E_POLICY_TEST_COOKIE': 'synthetic-policy-cookie'}), \
             patch('bcs_e2e.transport.build_opener') as opener:
            client = ReadClient(self.profile)
            for authority in (None, object()):
                with self.assertRaises(ObservationError): client.read('delivery.policy.read', policy_authority=authority)
            authority = PolicyReadReview(self.profile, self.review_path)
            client.profile = copy.deepcopy(self.profile); client.profile['human']['user_id'] = 'different'
            with self.assertRaises(ObservationError): client.read('delivery.policy.read', policy_authority=authority)
            opener.return_value.open.assert_not_called()

    def test_cancelled_policy_read_never_publishes_success(self):
        def cancel(index):
            if index == 3: raise KeyboardInterrupt()
        self.after_request = cancel
        rc, result = self.execute()
        self.assertEqual(rc, 130); self.assertEqual(result['status'], 'CANCELLED')
        self.assertIsNone(result['policy'])

    def test_unrelated_observer_placeholders_do_not_require_db_access(self):
        self.profile['observer']['database'] = 'REPLACE_WITH_DATABASE'
        write(self.profile_path, self.profile)
        self.review['profile_digest'] = fingerprint(self.profile); write(self.review_path, self.review)
        rc, result = self.execute()
        self.assertEqual(rc, 0, result); self.assertEqual(len(self.requests), 3)


if __name__ == '__main__': unittest.main()
