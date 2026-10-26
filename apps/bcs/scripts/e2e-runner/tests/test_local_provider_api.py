"""Focused Provider unit tests: real local journals/SQLite, synthetic HTTP/ownership.

No product process, external credentials, or real BCS startup is used here.
The full live lifecycle is covered by main's separate opt-in Provider run tests.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, PropertyMock, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.inventory import current_manifest, executor_digest
from bcs_e2e.local_config import LocalBcsInputs
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_provider_api import OwnedLocalProviderApi, ROLES
from bcs_e2e.local_provider_contract import CASE_ID, SUITE, REQUIREMENTS, provider_source_binding
from bcs_e2e.local_provider_sink import OwnedLocalProviderSink
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import RunStateError, read, write

RUN = 'unit-provider-owner'
PROVIDER = 'unit-new-provider'
BOT = 'unit-new-provider-bot'
ADMIN_RUN = 'run-unit-admin-result'
HUMAN = 'unit-local-human'
ADMIN = 'synthetic-unit-provider-admin'
DOWNLINK = 'synthetic-unit-provider-downlink'
BOT_TOKEN = 'synthetic-unit-provider-runtime'
SINK_URL = 'http://127.0.0.1:54322/provider/unit-private-capability'
SLUG = 'e2e-provider-' + hashlib.sha256(RUN.encode()).hexdigest()[:24]


class Response:
    def __init__(self, status, value, raw=None):
        self.status = status
        self.raw = raw if raw is not None else (json.dumps(value).encode() if value else b'')
        self.length = len(self.raw)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read1(self, size):
        result, self.raw = self.raw[:size], self.raw[size:]
        self.length -= len(result)
        return result


class Product:
    """Synthetic product boundary; actual API validators and ledger execute unchanged."""
    def __init__(self, test):
        self.test = test
        self.calls = []
        self.fail_on = None
        self.mutate_on = None
        self.mutation = None
        self.corrupt_delete = False
        self.wrong_run = False
        self.raw_response = None

    def connect(self, host, port, timeout):
        self.test.assertEqual((host, port, timeout), ('127.0.0.1', 54321, 5))
        product = self
        class Connection:
            sock = Mock()
            def request(self, method, path, body, headers):
                self.response = product.respond(method, path, json.loads(body) if body else None, headers)
            def getresponse(self):
                return self.response
            def close(self):
                pass
        return Connection()

    def respond(self, method, path, body, headers):
        t = self.test
        self.calls.append((method, path, body, dict(headers)))
        if path == '/auth/user':
            t.assertNotIn('Authorization', headers)
            return Response(200, {'user_id': HUMAN})
        index = len(self.calls) - 2
        req = provider_source_binding()['requirements'][index]
        journal = read(t.root/'local-provider-api.json')
        row = journal['operations'][-1]
        t.assertEqual(row['operation'], req['id'])
        t.assertEqual(row['status'], 'INTENDED')
        t.assertEqual((row['method'], row['path']), (method, path))
        t.assertEqual(row['submitted_request_id'], headers['X-Request-ID'])
        suffix = req['id'].removeprefix('PROVIDER.')
        replacements = {'provider_id': PROVIDER, 'provider_bot_ref': RUN, 'provider_bot_uuid': BOT,
                        'organization_code': t.org, 'admin_run_id': ADMIN_RUN}
        expected_path = req['path']
        for key, value in replacements.items():
            expected_path = expected_path.replace('${'+key+'}', value)
        t.assertEqual((method, path), (req['method'], expected_path))
        if suffix in ('register', 'duplicate-slug-conflict'):
            t.assertNotIn('Authorization', headers)
        else:
            token = BOT_TOKEN if suffix in ('agentpass-negative', 'unknown-run-callback') else ADMIN
            t.assertEqual(headers['Authorization'], 'Bearer '+token)
        if suffix in ('admin-start', 'admin-read', 'agentpass-negative', 'unknown-run-callback'):
            t.assertEqual(headers['X-BCN-Provider-Id'], PROVIDER)
        else:
            t.assertNotIn('X-BCN-Provider-Id', headers)
        handle = {'register': 'provider', 'bot-publish': 'bot', 'org-create': 'organization',
                  'member-add': 'member', 'admin-start': 'admin_run',
                  'member-remove': 'member', 'bot-retire': 'bot'}.get(suffix)
        if handle:
            resource = read(t.root/'local-provider-ledger.json')['resources'][handle]
            expected = 'DELETE_INTENDED' if suffix in ('member-remove', 'bot-retire') else 'INTENDED'
            t.assertEqual(resource['state'], expected)
            t.assertEqual(resource['history'][-1]['request_id'], headers['X-Request-ID'])
        with sqlite3.connect(t.root/'bcs.sqlite') as db:
            value, status = self.dispatch(db, suffix, body), 200
            if suffix.endswith('-denied'):
                status = 403
            elif suffix == 'unknown-run-callback':
                status = 404
            elif suffix == 'member-remove':
                status = 204
            elif suffix == 'duplicate-slug-conflict':
                status = 409
        if self.mutate_on == req['id']:
            self.mutation()
        if self.fail_on == req['id']:
            raise OSError('private-response-' + ADMIN)
        return Response(status, value, raw=self.raw_response)

    def dispatch(self, db, suffix, body):
        t = self.test
        if suffix == 'register':
            t.assertEqual(body, {'name': 'E2E Provider', 'slug': SLUG, 'webhook_url': SINK_URL,
                'auth': {'mode': 'static_bearer'}, 'protocol_version': '2.0', 'coordination': {'mode': 'native_tool'}})
            config = {'downlink': {'enabled': True, 'webhook_url': SINK_URL,
                                  'auth_mode': 'static_bearer', 'protocol_version': '2.0'},
                      'coordination': {'mode': 'native_tool'}}
            db.execute('INSERT INTO bcs_providers VALUES (?,?,?,?,?,?,?,?)',
                       (PROVIDER, 'local', body['name'], HUMAN, json.dumps([HUMAN]), 0, json.dumps(config), SLUG))
            db.executemany('INSERT INTO bcs_provider_credentials VALUES (?,?,?,?)',
                [(PROVIDER, 'local', k, 0) for k in ('provider_admin', 'downlink_bcs_to_provider')])
            return {'provider_id': PROVIDER, 'provider_admin_token': ADMIN, 'bcs_to_provider_token': DOWNLINK}
        if suffix == 'duplicate-slug-conflict':
            t.assertEqual(body, {'name': 'Duplicate E2E Provider', 'slug': SLUG,
                                 'auth': {'mode': 'static_bearer'}})
            t.assertEqual(db.execute('SELECT COUNT(*) FROM bcs_providers').fetchone()[0], 1)
            return {'error': 'conflict'}
        if suffix in ('read', 'update', 'disable', 'enable'):
            if suffix == 'update':
                t.assertEqual(body, {'name': 'E2E Provider Updated', 'protocol_version': '2.0'})
                db.execute('UPDATE bcs_providers SET name=?', (body['name'],))
            if suffix in ('disable', 'enable'):
                db.execute('UPDATE bcs_providers SET disabled=?', (int(suffix == 'disable'),))
            name, disabled = db.execute('SELECT name, disabled FROM bcs_providers').fetchone()
            return {'provider_id': PROVIDER, 'name': name, 'disabled': bool(disabled),
                    'auth_mode': 'static_bearer', 'webhook_url': SINK_URL}
        if suffix == 'bot-publish':
            t.assertEqual(body, {'name': 'Provider Review Agent', 'summary': 'Reviews release plans',
                'owners': [HUMAN], 'provider_bot_ref': RUN, 'domains': ['release'], 'skills': ['review'], 'scopes': ['local']})
            t.insert_bot(db, BOT, body['name'], BOT_TOKEN)
            db.execute('INSERT INTO bcs_provider_bot_bindings VALUES (?,?,?,?,?)', (BOT, PROVIDER, RUN, 'local', 0))
            return {'provider_id': PROVIDER, 'provider_bot_ref': RUN, 'bot_uuid': BOT,
                    'bot_runtime_token': BOT_TOKEN, 'message': 'new owned Bot'}
        if suffix == 'bot-update':
            t.assertEqual(body, {'name': 'Provider Review Agent (Senior)', 'domains': ['release', 'audit'], 'scopes': []})
            db.execute('UPDATE bcs_bots SET name=? WHERE bot_uuid=?', (body['name'], BOT))
            return dict(body, bot_uuid=BOT, provider_bot_ref=RUN)
        if suffix.startswith('attributes-'):
            if suffix == 'attributes-write-denied':
                t.assertEqual(body, {'user_visibility': 'public'})
            return {'error': 'not allowed'}
        if suffix in ('org-create', 'org-read', 'org-update'):
            if suffix == 'org-create':
                t.assertEqual(body, {'organization_code': t.org, 'name': 'E2E release organization',
                                    'description': 'provider-managed release team'})
                db.execute('INSERT INTO bcs_organizations VALUES (?,?,?,?,?)',
                           (t.org, 'local', body['name'], PROVIDER, 0))
            if suffix == 'org-update':
                t.assertEqual(body, {'name': 'E2E release organization updated'})
                db.execute('UPDATE bcs_organizations SET name=?', (body['name'],))
            name = db.execute('SELECT name FROM bcs_organizations').fetchone()[0]
            return {'organization_code': t.org, 'name': name}
        if suffix == 'org-list':
            return {'organizations': [{'organization_code': t.org}]}
        if suffix.startswith('candidate-list'):
            return {'bots': [{'bot_uuid': BOT}] if suffix.endswith('before') else []}
        if suffix.startswith('candidate-'):
            return {'bot_uuid': BOT, 'is_member': suffix.endswith('after')}
        if suffix == 'member-add':
            t.assertEqual(body, {'role': 'reviewer'})
            db.execute('INSERT INTO bcs_organization_members VALUES (?,?,?,?,?)', (t.org, BOT, 'local', 'reviewer', 0))
            return {'bot_uuid': BOT, 'role': 'reviewer'}
        if suffix == 'member-profile':
            t.assertEqual(body, {'name': 'E2E organization bot'})
            db.execute('UPDATE bcs_bots SET name=? WHERE bot_uuid=?', (body['name'], BOT))
            return {'profile': {'name': body['name']}}
        if suffix == 'member-read':
            return {'bot_uuid': BOT, 'role': 'reviewer'}
        if suffix == 'member-list':
            return {'members': [{'bot_uuid': BOT}]}
        if suffix in ('admin-start', 'admin-read'):
            session = 'admin-' + ADMIN_RUN
            if suffix == 'admin-start':
                t.assertEqual(body, {'target_bot_uuid': BOT, 'message': {'role': 'user', 'content': [
                    {'type': 'text', 'text': 'Review the E2E release plan'}]}, 'detach': True})
                t.assertEqual(t.sink._bearer_token, DOWNLINK)
                t.sink._received = t.sink._rejected = 1
                t.sink._records = [{'status': 'REJECTED_NO_ENGINE', 'http_status': 503,
                    'owner_run_id': RUN, 'provider_run_id': 'foreign-run' if self.wrong_run else ADMIN_RUN,
                    'provider_id': PROVIDER, 'bot_id': BOT, 'provider_bot_ref': RUN,
                    'request_id': 'unit-http-downlink-id', 'session_id': session, 'bcn_group_id': session,
                    'method': 'chat.send', 'protocol_version': '2.0', 'body_sha256': 'a'*64,
                    'engine': 'NOT_STARTED'}]
            return {'code': 20000, 'message': 'accepted' if suffix == 'admin-start' else 'ok',
                    'data': {'run_id': ADMIN_RUN, 'provider_id': PROVIDER, 'organization_code': t.org,
                             'target_bot_uuid': BOT, 'session_id': session, 'status': 'delivery_failed'}}
        if suffix == 'member-remove':
            if not self.corrupt_delete:
                db.execute('UPDATE bcs_organization_members SET disabled=1')
            return {}
        if suffix == 'bot-list':
            return {'items': [{'bot_uuid': BOT}]}
        if suffix == 'roster-denied':
            return {'error': 'not allowed to access the task-mode roster'}
        if suffix == 'agentpass-negative':
            t.assertEqual(body, {})
            return {'agent_code': None, 'provider_bot_binding': None, 'jwt': BOT_TOKEN}
        if suffix == 'unknown-run-callback':
            t.assertEqual(body, {'run_id': 'missing-provider-run-' + hashlib.sha256(RUN.encode()).hexdigest()[:24],
                                'state': 'final', 'message': {'text': 'late provider result'}})
            return {'error': 'run_not_found'}
        if suffix == 'takeover-denied':
            t.assertEqual(body, {'bot_id': 'fixture-QA', 'provider_bot_ref': RUN})
            return {'error': 'not allowed to switch bot delivery'}
        if suffix == 'bot-retire':
            db.execute('UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid=?', (BOT,))
            db.execute('UPDATE bcs_provider_bot_bindings SET disabled=1')
            return {'deleted': True, 'bot_uuid': BOT, 'provider_id': PROVIDER, 'provider_bot_ref': RUN}
        if suffix == 'bot-list-empty':
            return {'items': []}
        raise AssertionError('unimplemented synthetic response')


class ProviderApiUnitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.org = 'e2e-org-' + hashlib.sha256(RUN.encode()).hexdigest()[:24]
        self.sink = OwnedLocalProviderSink(RUN)
        self.sink._state = 'LIVE'
        self.addCleanup(self.sink.close)
        def live(sink, run_id):
            if sink.run_id != run_id or sink._state != 'LIVE':
                raise RunStateError('unit sink binding differs')
        self.start_patch(patch.object(OwnedLocalProviderSink, 'validate_live', live))
        self.start_patch(patch.object(OwnedLocalProviderSink, '_require_live_locked', lambda sink: live(sink, RUN)))
        self.start_patch(patch.object(OwnedLocalProviderSink, 'url', new_callable=PropertyMock, return_value=SINK_URL))
        config = {'database': {'type': 'sqlite', 'sqlite': {'path': str(self.root/'bcs.sqlite')}},
                  'store_messages': False, 'allowed_switch_provider_ids': [], 'llm': {'type': 'none'},
                  'bcsfuse': {'enabled': False}, 'eventing': {'enabled': False, 'dispatcher_enabled': False},
                  'security': {'outbound_url': {'block_private_networks': True, 'allow_loopback': True}}}
        (self.root/'bcs-config.json').write_text(json.dumps(config))
        self.runtime = object.__new__(OwnedBcsProcess)
        self.runtime.root, self.runtime.run_id = self.root, RUN
        self.runtime.state, self.runtime.ready = 'RUNNING', True
        self.runtime.provider_sink, self.runtime.store_messages = self.sink, False
        self.runtime.binary_sha256 = 'b'*64
        self.runtime.child = Mock(poll=lambda: None)
        self.runtime._owns_listener = lambda _timeout: True
        self.runtime.inputs = LocalBcsInputs(self.root, self.root/'bcs-config.json',
            'http://127.0.0.1:54321', HUMAN, {'SERVER_ENV': 'local'})
        self.binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
            'run_id': RUN, 'executor_digest': executor_digest(), 'bcs_binary_sha256': 'b'*64,
            'source_binding': provider_source_binding(), 'selected_requirement_ids': list(REQUIREMENTS),
            'legacy_inventory': current_manifest()['legacy_inventory'], 'runtime_dir': str(self.root),
            'store_messages': False, 'sink_url_sha256': hashlib.sha256(SINK_URL.encode()).hexdigest()}
        write(self.root/'provider-binding.json', self.binding)
        self.fixtures = OwnedLocalFixtures(self.runtime, fixture_set='workspace')
        receipt = {'schema_version': 1, 'fixture_set': 'workspace', 'status': 'APPLIED',
                   'roles': {role: {'bot_id': 'fixture-'+role} for role in ROLES}}
        write(self.fixtures.path, receipt)
        self.identities = OwnedLocalRoleIdentity(self.fixtures, roles=ROLES)
        self.identities._state = 'READY'
        self.identities._verified = list(ROLES)
        self.identities._entries = {role: ('fixture-'+role, 'synthetic-fixture-token-'+role) for role in ROLES}
        self.identities._receipt_digest = fingerprint(receipt)
        self.create_db()
        metadata = (self.root/'bcs.sqlite').stat()
        self.runtime.database_identity = (metadata.st_dev, metadata.st_ino)
        self.product = Product(self)
        self.start_patch(patch('bcs_e2e.local_provider_api.HTTPConnection', side_effect=self.product.connect))

    def start_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def create_db(self):
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            db.executescript('''
                CREATE TABLE bcs_bots (bot_uuid TEXT, env TEXT, name TEXT, created_by TEXT,
                    actor_kind TEXT, status TEXT, is_deleted INTEGER, visibility TEXT, user_visibility TEXT,
                    session_token TEXT, task_claim_mode INTEGER, task_dream_mode INTEGER,
                    friend_ext TEXT, friend_check_in_strategy TEXT, bot_info TEXT);
                CREATE TABLE bcs_providers (provider_id TEXT, env TEXT, name TEXT, created_by TEXT,
                    owners TEXT, disabled INTEGER, config TEXT, slug TEXT);
                CREATE TABLE bcs_provider_credentials (provider_id TEXT, env TEXT, credential_kind TEXT, disabled INTEGER);
                CREATE TABLE bcs_provider_bot_bindings (bot_uuid TEXT, provider_id TEXT, provider_bot_ref TEXT,
                    env TEXT, disabled INTEGER);
                CREATE TABLE bcs_organizations (code TEXT, env TEXT, name TEXT, managing_provider_id TEXT, disabled INTEGER);
                CREATE TABLE bcs_organization_members (organization_code TEXT, bot_uuid TEXT, env TEXT, role TEXT, disabled INTEGER);
            ''')
            for role, (bot, token) in self.identities._entries.items():
                self.insert_bot(db, bot, role, token)

    def insert_bot(self, db, bot, name, token):
        db.execute('INSERT INTO bcs_bots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (bot, 'local', name, HUMAN, 'bot', 'online', 0, 'protected', 'protected', token,
             0, 0, None, 'APPROVAL', json.dumps({'name': name})))

    def api(self):
        return OwnedLocalProviderApi(self.identities, self.sink)

    def assert_no_secrets(self):
        for name in ('local-provider-api.json', 'local-provider-ledger.json'):
            text = (self.root/name).read_text()
            for secret in (ADMIN, DOWNLINK, BOT_TOKEN, SINK_URL, urlsplit(SINK_URL).path,
                           *[v[1] for v in self.identities._entries.values()]):
                self.assertNotIn(secret, text)

    def test_full32_order_wire_intents_sql_tombstones_and_redacted_journals(self):
        api = self.api()
        result = api.run()
        self.assertEqual(result['status'], 'LOCAL_PROVIDER_PASS')
        self.assertEqual(result['assertions'], [{'id': req, 'status': 'PASS'} for req in REQUIREMENTS])
        self.assertEqual([r['operation'] for r in result['operations']], list(REQUIREMENTS))
        self.assertEqual(len(self.product.calls), 33)  # 32 business plus Human admission.
        ids = [r['submitted_request_id'] for r in result['operations']]
        self.assertEqual(len(set(ids)), 32)
        for row in result['operations']:
            self.assertEqual(row['transitions'], ['INTENDED', 'ACKNOWLEDGED', 'VERIFIED'])
        self.assertEqual(result['fixture_state']['before'], result['fixture_state']['after'])
        self.assertEqual(result['downstream']['records'][0]['provider_run_id'], ADMIN_RUN)
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        self.assertEqual(api._credentials, {})
        ledger = read(self.root/'local-provider-ledger.json')
        self.assertEqual(result['ledger'], ledger)
        self.assertEqual(result['ledger_digest'], fingerprint(ledger))
        self.assertEqual({k: r['state'] for k, r in ledger['resources'].items()},
            {'provider': 'RETAINED', 'bot': 'DELETED', 'organization': 'RETAINED',
             'member': 'DELETED', 'admin_run': 'RETAINED'})
        for kind in ('member', 'bot'):
            self.assertEqual([r['state'] for r in ledger['resources'][kind]['history']],
                             ['INTENDED', 'LIVE', 'DELETE_INTENDED', 'DELETE_ACKNOWLEDGED', 'DELETED'])
        self.assertEqual(read(self.root/'local-provider-api.json'), result)
        self.assert_no_secrets()
        self.assertEqual((self.root/'local-provider-ledger.json').stat().st_mode & 0o777, 0o600)

    def test_unmanaged_and_unverified_identity_rejected_before_wire(self):
        with self.assertRaises(RunStateError):
            OwnedLocalProviderApi(object(), self.sink)
        self.identities._verified.remove('QA')
        with self.assertRaises(RunStateError):
            self.api()
        self.assertEqual(self.product.calls, [])

    def test_borrowed_sink_and_wrong_run_rejected_before_wire(self):
        self.runtime.provider_sink = OwnedLocalProviderSink(RUN)
        with self.assertRaises(RunStateError):
            self.api()
        self.runtime.provider_sink = self.sink
        self.sink.run_id = 'foreign'
        with self.assertRaises(RunStateError):
            self.api()
        self.assertEqual(self.product.calls, [])

    def test_missing_requirement_or_extra_binding_field_rejected(self):
        for edit in ({'selected_requirement_ids': list(REQUIREMENTS[:-1])},
                     {'unexpected': True}, {'sink_url_sha256': '0'*64}, {'store_messages': True}):
            with self.subTest(edit=edit):
                write(self.root/'provider-binding.json', self.binding | edit)
                with self.assertRaises(RunStateError):
                    self.api()
        self.assertEqual(self.product.calls, [])

    def test_frozen_binding_replaced_after_constructor_blocks_without_business_call(self):
        api = self.api()
        write(self.root/'provider-binding.json', self.binding | {'run_id': 'foreign'})
        with self.assertRaises(RunStateError):
            api.run()
        self.assertEqual(self.product.calls, [])
        self.assertFalse(api.path.exists())

    def test_lost_provider_response_is_unknown_no_repeat_adopt_or_cleanup(self):
        self.product.fail_on = 'PROVIDER.register'
        api = self.api()
        with self.assertRaises(RunStateError) as error:
            api.run()
        self.assertNotIn(ADMIN, str(error.exception))
        self.assertEqual(len(self.product.calls), 2)
        ledger = read(api.ledger_path)
        resource = ledger['resources']['provider']
        self.assertEqual((resource['state'], resource['resource_id']), ('UNKNOWN', None))
        self.assertEqual(read(api.path)['operations'][0]['status'], 'UNKNOWN')
        with self.assertRaises(RunStateError):
            api.run()
        with self.assertRaises(RunStateError):
            self.api()
        self.assertEqual(len(self.product.calls), 2)
        self.assert_no_secrets()

    def test_lost_bot_creation_retains_parent_and_unknown_child_without_delete(self):
        self.product.fail_on = 'PROVIDER.bot-publish'
        api = self.api()
        with self.assertRaises(RunStateError):
            api.run()
        resources = read(api.ledger_path)['resources']
        self.assertEqual(resources['provider']['state'], 'RETAINED')
        self.assertEqual(resources['bot']['state'], 'UNKNOWN')
        self.assertFalse(any(call[0] == 'DELETE' for call in self.product.calls))
        self.assertEqual(api._credentials, {})
        self.assert_no_secrets()

    def test_lost_member_delete_response_cannot_mark_deleted_or_retry(self):
        self.product.fail_on = 'PROVIDER.member-remove'
        api = self.api()
        with self.assertRaises(RunStateError):
            api.run()
        self.assertEqual(read(api.ledger_path)['resources']['member']['state'], 'UNKNOWN')
        self.assertEqual(sum(c[0] == 'DELETE' for c in self.product.calls), 1)
        self.assertEqual(len(self.product.calls), 24)

    def test_delete_ack_without_independent_tombstone_is_unconfirmed(self):
        self.product.corrupt_delete = True
        api = self.api()
        with self.assertRaises(RunStateError):
            api.run()
        self.assertEqual(read(api.ledger_path)['resources']['member']['state'], 'DELETE_ACKNOWLEDGED')
        self.assertEqual(read(api.path)['operations'][-1]['status'], 'UNCONFIRMED')
        self.assertEqual(sum(c[0] == 'DELETE' for c in self.product.calls), 1)

    def test_mismatched_downstream_run_never_advances_to_admin_read(self):
        self.product.wrong_run = True
        api = self.api()
        with self.assertRaises(RunStateError):
            api.run()
        self.assertEqual(read(api.path)['operations'][-1]['operation'], 'PROVIDER.admin-start')
        self.assertEqual(read(api.ledger_path)['resources']['admin_run']['state'], 'UNKNOWN')
        self.assertEqual(sum(c[0] == 'DELETE' for c in self.product.calls), 0)

    def test_fixture_drift_after_response_stops_all_further_dispatch(self):
        self.product.mutate_on = 'PROVIDER.update'
        def mutate():
            receipt = read(self.fixtures.path)
            receipt['roles']['QA']['bot_id'] = 'borrowed-qa'
            write(self.fixtures.path, receipt)
        self.product.mutation = mutate
        api = self.api()
        with self.assertRaises(RunStateError):
            api.run()
        self.assertEqual(len(self.product.calls), 5)
        self.assertFalse(any(call[0] == 'DELETE' for call in self.product.calls))

    def test_unknown_or_out_of_order_step_cannot_dispatch(self):
        api = self.api()
        with self.assertRaises(RunStateError):
            api._step('PROVIDER.enable')
        self.assertEqual(self.product.calls, [])

    def test_wire_rejects_arbitrary_path_body_and_missing_intent(self):
        api = self.api()
        with self.assertRaises(RunStateError):
            api._wire('DELETE', '/providers/borrowed', None, 'human', 'arbitrary')
        api.state = {'operations': [{'operation': 'PROVIDER.register', 'status': 'INTENDED',
                     'submitted_request_id': 'intended', 'provider_header': False}], 'checks': []}
        for method, path, body in [('POST', '/providers/borrowed', {}),
                                   ('DELETE', '/providers', {}), ('POST', '/providers', {'webhook_url': 'foreign'})]:
            with self.subTest(method=method, path=path), self.assertRaises(RunStateError):
                api._wire(method, path, body, 'human', 'intended')
        self.assertEqual(self.product.calls, [])

    def test_existing_borrowed_journal_never_overwritten(self):
        path = self.root/'local-provider-ledger.json'
        write(path, {'schema_version': 1, 'run_id': 'foreign', 'resources': {}})
        original = path.read_bytes()
        with self.assertRaises(RunStateError):
            self.api()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.product.calls, [])

    def test_unexpected_response_and_oversize_are_unknown_and_sanitized(self):
        self.product.raw_response = b'x' * 65_537
        api = self.api()
        with self.assertRaises(RunStateError):
            api.run()
        self.assertEqual(read(api.ledger_path)['resources']['provider']['state'], 'UNKNOWN')
        self.assertEqual(len(self.product.calls), 2)
        self.assert_no_secrets()

    def test_borrowed_resource_provenance_cannot_authorize_delete(self):
        api = self.api()
        api.ledger = {'resources': {'bot': {'handle': 'bot', 'kind': 'bot', 'case_id': CASE_ID,
            'attempt': 1, 'origin': 'borrowed', 'parent': 'provider', 'state': 'LIVE', 'resource_id': 'fixture-QA'}}}
        with self.assertRaises(RunStateError):
            api._resource('bot')
        self.assertEqual(self.product.calls, [])

    def test_source_artifact_drift_fails_before_new_intent(self):
        api = self.api()
        with patch('bcs_e2e.local_provider_api.executor_digest', return_value='0'*64):
            with self.assertRaises(RunStateError):
                api.run()
        self.assertEqual(self.product.calls, [])
        self.assertFalse(api.path.exists())


if __name__ == '__main__':
    unittest.main()
