"""Cookie gateway Upgrade authentication never permits redirects or header injection."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.adapters import RemotePilotAdapter
from bcs_e2e.profile import load
from bcs_e2e.ws_transport import SessionWebSocketClient, WebSocketTransportError
from bcs_e2e.retained_serial import rejected_before_ws_connect
from test_ws_evidence import ReplySocket


class GatewayHandshakeTests(unittest.TestCase):
    def test_remote_adapter_uses_same_fixed_gateway_cookie_and_browser_context(self):
        profile=load(ROOT/'profiles/preprod.example.json')
        profile['browser_origin']='https://workbench.example.test'
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE':'session=synthetic-cookie'}):
            adapter=RemotePilotAdapter(profile)
        self.assertEqual(adapter.websocket._request_headers, {
            'Cookie':'session=synthetic-cookie','Origin':profile['browser_origin'],
            'Referer':profile['browser_origin']+'/'})
        self.assertEqual(adapter.websocket.host,adapter.write.base.split('://',1)[1])

    def test_header_values_are_copied_and_redirect_is_never_followed(self):
        headers={'Cookie':'session=synthetic-cookie','Origin':'https://workbench.example.test'}
        client=SessionWebSocketClient('http://example.test',request_headers=headers)
        headers['Cookie']='changed-after-construction'
        sock=ReplySocket(302)
        with patch('bcs_e2e.ws_transport.socket.create_connection',return_value=sock) as connect:
            with self.assertRaises(WebSocketTransportError) as caught:
                client.connect('synthetic-session-token','g1','s1')
        self.assertEqual(connect.call_count,1)
        self.assertEqual(connect.call_args.args[0],('example.test',80))
        self.assertEqual(len(sock.sent),1)
        self.assertIn(b'Cookie: session=synthetic-cookie\r\n',sock.sent[0])
        self.assertNotIn(b'changed-after-construction',sock.sent[0])
        record=caught.exception.request_records[0]
        self.assertEqual(record['http_status'],302)
        self.assertEqual(record['socket_close_status'],'CLOSED')
        self.assertTrue(sock.closed)
        for secret in ('synthetic-cookie','synthetic-session-token','workbench.example.test','private-location'):
            self.assertNotIn(secret,str(caught.exception)+json.dumps(record))

    def test_invalid_header_keys_values_and_origins_fail_before_network(self):
        bad=[{'Authorization':'value'},{'Host':'foreign.test'},{'X-Avernet-Principal':'value'},
             {'cookie':'value'},{'Cookie':''},{'Cookie':'private\r\nHost: foreign.test'},
             {'Cookie':'私有'}, {'Cookie':'x'*16384}, {'Origin':None}]
        with patch('bcs_e2e.ws_transport.socket.create_connection') as connect:
            for headers in bad:
                with self.subTest(headers=list(headers)), self.assertRaises(WebSocketTransportError):
                    SessionWebSocketClient('https://example.test',request_headers=headers)
            for origin in ('https://user:password@example.test','https://example.test/path',
                           'https://example.test?redirect=foreign','https://example.test/#fragment',
                           'https://example.test\r\nHost: foreign'):
                with self.subTest(origin=origin),self.assertRaises(WebSocketTransportError):
                    SessionWebSocketClient(origin,request_headers={'Cookie':'synthetic'})
            connect.assert_not_called()

    def test_close_failure_remains_unconfirmed(self):
        sock=ReplySocket(302)
        def fail(): raise OSError('synthetic failure')
        sock.close=fail
        with patch('bcs_e2e.ws_transport.socket.create_connection',return_value=sock):
            with self.assertRaises(WebSocketTransportError) as caught:
                SessionWebSocketClient('http://example.test',request_headers={'Cookie':'synthetic'}).connect('t','g','s')
        self.assertEqual(caught.exception.request_records[0]['socket_close_status'],'UNCONFIRMED')

    def test_old_rejection_is_not_an_open_websocket_but_unknown_or_connected_is(self):
        report={'evidence':[{'operation':'session.ws.connect','outcome':'ERROR','phase':'upgrade',
                            'http_status':302,'connected':False,'connect_request_id':None}]}
        self.assertTrue(rejected_before_ws_connect(report))
        for change in ({'phase':'connect'}, {'http_status':101}, {'http_status':None},
                       {'connected':True},{'connect_request_id':'already-sent'}, {'outcome':'PASS'}):
            changed=copy.deepcopy(report);changed['evidence'][0].update(change)
            self.assertFalse(rejected_before_ws_connect(changed))
        changed=copy.deepcopy(report);changed['evidence']*=2
        self.assertFalse(rejected_before_ws_connect(changed))
        self.assertFalse(rejected_before_ws_connect({'evidence':[]}))

    def test_failure_projection_only_preserves_known_close_status_enum(self):
        from unittest.mock import Mock
        from bcs_e2e.correlation import CorrelatedEvidence
        for value in ('CLOSED','NOT_OPENED','UNCONFIRMED','private-cookie',{},None):
            evidence=CorrelatedEvidence(Mock(),'case',1)
            evidence.record_failure(WebSocketTransportError('synthetic').with_record({
                'operation':'session.ws.connect','socket_close_status':value}))
            if isinstance(value,str) and value in ('CLOSED','NOT_OPENED','UNCONFIRMED'):
                self.assertEqual(evidence[0]['socket_close_status'],value)
            else:
                self.assertNotIn('socket_close_status',evidence[0])
            self.assertNotIn('private-cookie',json.dumps(evidence))
