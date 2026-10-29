"""Real BCS downlink admission proof, NOT the32-operation Provider parent suite."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_provider_sink import OwnedLocalProviderSink
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.run_state import write
from bcs_e2e.transport import NoRedirect

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'), 'approved real BCS binary required')
class ProviderSinkLiveTests(unittest.TestCase):
    def test_actual_organization_admin_dispatch_is_rejected_by_owned_receiver(self):
        run_id = 'provider-sink-'+uuid.uuid4().hex[:12]
        credentials = {}
        journal = {'run_id': run_id, 'scope': 'DOWNLINK_PREREQUISITE_ONLY',
                   'full_parent_status': 'NOT_RUN', 'operations': []}
        with tempfile.TemporaryDirectory() as tmp:
            with OwnedLocalProviderSink(run_id) as sink:
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=Path(tmp)/'run', run_id=run_id, provider_sink=sink) as runtime:
                    origin = runtime.inputs.origin
                    opener = build_opener(ProxyHandler({}), NoRedirect())
                    def request(method, path, body=None, token='', provider=''):
                        event = {'method': method, 'path': path, 'request_id': str(uuid.uuid4()), 'status': 'INTENDED'}
                        journal['operations'].append(event)
                        write(runtime.root/'provider-sink-probe.json', journal)
                        headers = {'Content-Type': 'application/json', 'X-Request-ID': event['request_id']}
                        if token: headers['Authorization'] = 'Bearer '+token
                        if provider: headers['X-BCN-Provider-Id'] = provider
                        req = Request(origin+path, method=method, headers=headers,
                                      data=json.dumps(body).encode() if body is not None else None)
                        try: response = opener.open(req, timeout=15)
                        except HTTPError as error: response = error
                        with response:
                            raw = response.read(65537); status = response.status
                        self.assertLessEqual(len(raw), 65536)
                        self.assertEqual(status, 200, 'unexpected HTTP status; body intentionally omitted')
                        value = json.loads(raw)
                        event.update(status='ACKNOWLEDGED', http_status=status)
                        write(runtime.root/'provider-sink-probe.json', journal)
                        return value
                    try:
                        created = request('POST', '/providers', {'name': 'Owned negative provider fixture',
                            'webhook_url': sink.url, 'auth': {'mode': 'static_bearer'},
                            'protocol_version': '2.0', 'coordination': {'mode': 'native_tool'}})
                        provider = created['provider_id']
                        credentials.update(admin=created['provider_admin_token'], downlink=created['bcs_to_provider_token'])
                        del created
                        bot = request('POST', '/providers/'+provider+'/bots',
                            {'name': 'Owned provider test bot', 'summary': 'No engine',
                             'owners': [runtime.inputs.human_id], 'provider_bot_ref': run_id,
                             'skills': ['review'], 'domains': ['release'], 'scopes': ['local']}, credentials['admin'])
                        bot_id = bot['bot_uuid']; credentials['runtime'] = bot['bot_runtime_token']; del bot
                        sink.arm(provider, bot_id, credentials['downlink'])
                        org_code = 'org-'+run_id
                        org = request('POST', '/providers/'+provider+'/organizations',
                            {'organization_code': org_code, 'name': 'Owned probe organization', 'description': 'Downlink prerequisite'}, credentials['admin'])
                        self.assertEqual(org['organization_code'], org_code)
                        request('PUT', '/organizations/'+org_code+'/members/'+bot_id, {'role': 'reviewer'}, credentials['admin'])
                        result = request('POST', '/organizations/'+org_code+'/admin-runs',
                            {'target_bot_uuid': bot_id, 'message': {'role': 'user', 'content': [
                                {'type': 'text', 'text': 'Review the E2E release plan'}]}, 'detach': True},
                            credentials['admin'], provider)['data']
                        self.assertEqual(result['target_bot_uuid'], bot_id)
                        self.assertEqual(result['organization_code'], org_code)
                        self.assertEqual(result['status'], 'delivery_failed')
                        observed = request('GET', '/organizations/'+org_code+'/admin-runs/'+result['run_id'],
                                           token=credentials['admin'], provider=provider)['data']
                        self.assertEqual(observed['run_id'], result['run_id'])
                        self.assertEqual(observed['target_bot_uuid'], bot_id)
                        self.assertTrue(observed['status'])
                        journal.update(provider_id=provider, bot_id=bot_id, organization_code=org_code,
                                       admin_run_id=result['run_id'], downstream=sink.summary(),
                                       retained=['provider', 'provider_credentials', 'bot', 'organization', 'membership', 'admin_run'])
                        evidence = journal['downstream']
                        self.assertEqual((evidence['received'], evidence['rejected']), (1, 1))
                        self.assertFalse(evidence['failed'])
                        self.assertEqual(evidence['engine'], 'NOT_STARTED')
                        record = evidence['records'][0]
                        self.assertEqual(record['provider_run_id'], result['run_id'])
                        self.assertEqual(record['provider_id'], provider)
                        self.assertEqual(record['bot_id'], bot_id)
                        self.assertEqual(record['owner_run_id'], run_id)
                        self.assertEqual(record['status'], 'REJECTED_NO_ENGINE')
                        serialized=json.dumps(journal)
                        self.assertFalse(any(secret in serialized for secret in credentials.values()))
                        write(runtime.root/'provider-sink-probe.json', journal)
                    finally:
                        credentials.clear()
                self.assertEqual(runtime.state, 'STOPPED')
            summary = sink.summary()
            self.assertEqual(summary['state'], 'CLOSED')
            self.assertEqual(journal['full_parent_status'], 'NOT_RUN')
            self.assertEqual(len(journal['operations']), 6)
            self.assertEqual(credentials, {})
