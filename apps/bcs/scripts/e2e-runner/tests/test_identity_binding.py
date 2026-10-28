"""BCS native identity is explicit, plain JSON, not an automatic V1 fallback."""
import copy
import contextlib
import io
import tempfile
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main, plan
from bcs_e2e.identity_binding import NATIVE, pilot_execution_blocker, resolve_identity_binding
from bcs_e2e.prepare import prepare_run
from bcs_e2e.pilot import run_case
from bcs_e2e.remote_run import remote_run
from bcs_e2e.preflight import check
from bcs_e2e.probe import http_probe
from bcs_e2e.profile import ConfigError, fingerprint, load, validate
from bcs_e2e.transport import ObservationError, ReadClient
import test_runner as helpers


class IdentityBindingTests(unittest.TestCase):
    def profile(self):
        p = helpers.profile()
        p['entrypoint']['kind'] = 'bcs-direct'
        p['identity_binding'] = dict(NATIVE)
        return validate(p)

    def client(self, payload, code=200):
        opener = patch('bcs_e2e.transport.build_opener').start()
        self.addCleanup(patch.stopall)
        opener.return_value.open.return_value = helpers.Reply(payload, code=code)
        env = patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'})
        env.start(); self.addCleanup(env.stop)
        return ReadClient(self.profile()), opener

    def test_plain_native_identity_uses_exact_path_and_cookie_without_v1_projection(self):
        client, opener = self.client({'user_id': 'owner-1', 'provider': 'cookie', 'name': 'private-name'})
        result = client.read('identity.read')
        self.assertEqual(result.data['user_id'], 'owner-1')
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, self.profile()['entrypoint']['origin'] + '/auth/user')
        self.assertEqual(request.method, 'GET'); self.assertIsNone(request.data)
        self.assertEqual(request.get_header('Cookie'), 'test-token')
        self.assertNotIn('X-avernet-principal', request.headers)
        self.assertNotIn('private-name', json.dumps(result.evidence))
        self.assertNotIn('test-token', json.dumps(result.evidence))
        opener.return_value.open.assert_called_once()

    def test_profile_rejects_arbitrary_routes_envelopes_methods_and_origin_confusion(self):
        for field, values in (('path', ('/openapi/v1/auth/user', '/auth/user/other', '/me',
                                      'https://other.test/auth/user', '//other.test/auth/user')),
                              ('response', ('public-envelope', 'auto')), ('method', ('POST', 'get'))):
            for value in values:
                p = self.profile(); p['identity_binding'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ConfigError): validate(p)
        for kind in ('gateway', 'mock', 'singlebox'):
            p = self.profile(); p['entrypoint']['kind'] = kind
            with self.subTest(kind=kind), self.assertRaises(ConfigError): validate(p)
        p = self.profile(); p['identity_binding']['unknown'] = True
        with self.assertRaises(ConfigError): validate(p)
        for value in (None, 'native', {}, True):
            p = self.profile(); p['identity_binding'] = value
            with self.assertRaises(ConfigError): validate(p)

    def test_existing_profiles_and_digest_are_not_silently_rewritten(self):
        for name in ('preprod.example.json', 'local.singlebox.example.json', 'local.mock.json'):
            raw = json.loads((ROOT / 'profiles' / name).read_text())
            before = fingerprint(raw)
            current = load(ROOT / 'profiles' / name)
            self.assertNotIn('identity_binding', current)
            self.assertEqual(fingerprint(current), before)
        p = self.profile(); before = fingerprint(p)
        del p['identity_binding']
        self.assertNotEqual(fingerprint(p), before)
        # A bcs-direct profile without an explicit binding defaults to the
        # reviewed station native discovery; the gateway face keeps the V1
        # default. Neither fingerprint nor stored profile changes.
        self.assertEqual(resolve_identity_binding(p)['path'], '/auth/user')
        gateway = helpers.profile()
        gateway.pop('identity_binding', None)
        self.assertEqual(resolve_identity_binding(validate(gateway))['path'], '/openapi/v1/auth/user')
        self.assertEqual(resolve_identity_binding(p, owned_local=True), NATIVE)

    def test_plan_declares_native_identity_without_changing_business_routes(self):
        p = self.profile(); before = copy.deepcopy(p['route_bindings'])
        with patch.dict(os.environ, {}, clear=True), patch('bcs_e2e.transport.build_opener') as opener:
            result = plan(p)
        opener.assert_not_called()
        identity = next(r for r in result['preflight_routes'] if r['operation'] == 'identity.read')
        self.assertEqual(identity, {'operation': 'identity.read', **NATIVE})
        self.assertEqual(p['route_bindings'], before)
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(result['execution_admission']['status'], 'BLOCKED')

    def test_native_reader_never_unwraps_v1_or_accepts_html_or_invalid_user_id(self):
        for payload in (helpers.envelope({'user_id': 'owner-1'}), b'<html>login</html>', {},
                        {'user_id': None}, {'user_id': 39464}, {'user_id': ''}, {'user_id': ' '},
                        {'user_id': 'x' * 129}, {'user_id': 'abc\n'}):
            with self.subTest(payload=payload):
                client, opener = self.client(payload)
                with self.assertRaises(ObservationError): client.read('identity.read')
                opener.return_value.open.assert_called_once()
                patch.stopall()

    def test_auth_failure_does_not_try_alternate_path_or_identity(self):
        for status in (401, 403, 302, 404):
            with self.subTest(status=status):
                client, opener = self.client({'error': 'private-body'}, code=status)
                with self.assertRaises(ObservationError): client.read('identity.read')
                opener.return_value.open.assert_called_once()
                self.assertEqual(opener.return_value.open.call_args.args[0].get_header('Cookie'), 'test-token')
                patch.stopall()

    def test_preflight_verifies_identity_but_blocks_v1_bot_checks(self):
        for user in ('owner-1', 'other-owner'):
            bodies = [helpers.responses()[0], {'user_id': user}, *helpers.responses()[2:]]
            with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
                 patch('bcs_e2e.transport.build_opener') as opener:
                opener.return_value.open.side_effect = [helpers.Reply(b) for b in bodies]
                report = check(self.profile(), ReadClient(self.profile()))
            self.assertEqual(opener.return_value.open.call_count, 2)
            self.assertEqual(report['status'], 'BLOCKED')
            checks = {c['id']: c['status'] for c in report['checks']}
            self.assertEqual(checks['identity.human'], 'PASS' if user == 'owner-1' else 'BLOCKED')
            if user == 'owner-1':
                self.assertEqual(checks['route.business_adapter'], 'BLOCKED')
            else:
                self.assertNotIn('route.business_adapter', checks)
            self.assertFalse(report['ready_to_execute'])

    def test_probe_checks_expected_user_and_never_emits_identity_payload(self):
        for user in ('owner-1', 'other-owner'):
            client, opener = self.client({'user_id': user, 'provider': 'cookie', 'name': 'private-name'})
            with patch('bcs_e2e.probe.ReadClient', return_value=client):
                result = http_probe(self.profile(), 'identity.read')
            self.assertEqual(result['status'], 'PROBE_PASSED' if user == 'owner-1' else 'BLOCKED')
            self.assertNotIn('private-name', json.dumps(result))
            self.assertNotIn('other-owner', json.dumps(result))
            self.assertNotIn('test-token', json.dumps(result))
            patch.stopall()


    def test_bot_probe_refuses_native_identity_profile_before_network(self):
        client, opener = self.client(helpers.responses()[2])
        with self.assertRaisesRegex(ObservationError, 'discovery-only'):
            client.read('bot.read', 'bot-a')
        opener.return_value.open.assert_not_called()
        self.assertEqual(client.evidence, [])

    def test_prepare_blocks_before_directory_receipt_ledger_and_pool(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('bcs_e2e.prepare.PoolLease.acquire') as lease, \
             patch('bcs_e2e.prepare.Ledger') as ledger, \
             patch('bcs_e2e.prepare.validate_binding') as receipt:
            state = Path(tmp) / 'state'
            with self.assertRaisesRegex(ConfigError, 'discovery-only'):
                prepare_run(self.profile(), state, 'run-native', observer_binding=Path(tmp) / 'missing.json')
            self.assertFalse(state.exists())
            lease.assert_not_called(); ledger.assert_not_called(); receipt.assert_not_called()

    def test_all_pilot_cases_block_before_state_lease_and_adapter(self):
        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp, \
                 patch('bcs_e2e.pilot.read_state') as state, \
                 patch('bcs_e2e.pilot._case_lease') as lease, \
                 patch('bcs_e2e.pilot.adapter_for') as adapter:
                with self.assertRaisesRegex(ConfigError, 'discovery-only'):
                    run_case(self.profile(), Path(tmp), 'run-native', case)
                state.assert_not_called(); lease.assert_not_called(); adapter.assert_not_called()
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_remote_run_blocks_before_network_observer_or_directory(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('bcs_e2e.remote_run.remote_check_dependencies') as dependencies, \
             patch('bcs_e2e.remote_run.check_remote') as check_remote:
            state = Path(tmp) / 'state'
            with self.assertRaisesRegex(ConfigError, 'discovery-only'):
                remote_run(self.profile(), state_dir=state, run_id='run-native',
                           receipt=Path(tmp) / 'receipt', executable=Path(tmp) / 'mcporter',
                           allow_http_read=True, allow_observer_read=True, allow_write=True,
                           confirm_run_id='run-native')
            dependencies.assert_not_called(); check_remote.assert_not_called()
            self.assertFalse(state.exists())

    def test_cli_prepare_and_run_report_blocker_without_credentials_or_run_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            path = Path(tmp) / 'profile.json'
            path.write_text(json.dumps(self.profile()))
            state = Path(tmp) / 'state'
            for command in ('prepare', 'run'):
                args = [command, '--profile', str(path), '--state-dir', str(state), '--run-id', 'run-native']
                if command == 'run':
                    args += ['--case', 'PRE-01', '--allow-write', '--confirm-run-id', 'run-native']
                with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = main(args)
                report = json.loads(output.getvalue())
                self.assertEqual(rc, 2)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertIn('discovery-only', report['reason'])
                self.assertFalse(state.exists())

    def test_reviewed_check_does_not_query_mcp_after_native_identity_success(self):
        from bcs_e2e.remote_check import check_remote
        from unittest.mock import Mock
        binding = Mock(); binding.summary = {'source': 'test-review'}
        client = Mock()
        with patch('bcs_e2e.remote_check.remote_check_dependencies', return_value=(client, binding)), \
             patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
             patch('bcs_e2e.transport.build_opener') as opener:
            opener.return_value.open.side_effect = [helpers.Reply(helpers.responses()[0]),
                                                   helpers.Reply({'user_id': 'owner-1'})]
            report = check_remote(self.profile(), receipt=Path('unused'), executable=Path('unused'),
                                  allow_http_read=True, allow_observer_read=True)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['observer_preflight']['status'], 'NOT_RUN')
        self.assertEqual(client.mock_calls, [])
        self.assertEqual(opener.return_value.open.call_count, 2)

    def test_station_direct_defaults_native_discovery_without_explicit_binding(self):
        # A bcs-direct profile that never selects a binding keeps the reviewed
        # station defaults: native /auth/user discovery and no native
        # business-adapter declaration. The explicit NATIVE opt-in above stays
        # blocked end-to-end; only the binding-less default is admitted here.
        p = helpers.profile()
        p['entrypoint']['kind'] = 'bcs-direct'
        p.pop('identity_binding', None)
        p = validate(p)
        self.assertEqual(resolve_identity_binding(p), NATIVE)
        self.assertEqual(pilot_execution_blocker(p), '')

    def test_station_direct_reads_native_identity_and_legacy_bot_detail(self):
        p = helpers.profile()
        p['entrypoint']['kind'] = 'bcs-direct'
        p.pop('identity_binding', None)
        p = validate(p)
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
                patch('bcs_e2e.transport.build_opener') as opener:
            opener.return_value.open.side_effect = [
                helpers.Reply({'user_id': 'owner-1'}),
                helpers.Reply({'actor_kind': 'bot', 'bot_uuid': 'bot-a',
                               'created_by': 'owner-1', 'env': 'pre', 'status': 'online'}),
            ]
            client = ReadClient(p)
            identity = client.read('identity.read')
            self.assertEqual(identity.data, {'user_id': 'owner-1'})
            first_url = opener.return_value.open.call_args_list[0].args[0].full_url
            self.assertTrue(first_url.endswith('/auth/user'), first_url)
            bot = client.read('bot.read', 'bot-a')
            self.assertEqual(bot.data['bot_uuid'], 'bot-a')
            self.assertEqual(bot.data['actor_kind'], 'bot')
            second_url = opener.return_value.open.call_args_list[1].args[0].full_url
            self.assertTrue(second_url.endswith('/bots/bot-a'), second_url)
            self.assertNotIn('/openapi/v1/collaboration/bots/', second_url)
            opener.return_value.open.assert_called_once  # network never retried
            patch.stopall()
