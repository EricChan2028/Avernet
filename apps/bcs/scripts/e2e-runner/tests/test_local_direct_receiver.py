"""Direct Run transport cannot borrow Group-frame semantics or fabricate ACKs."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_direct_receiver import OwnedLocalDirectReceiver, validate_direct_frame
from bcs_e2e.local_bot_receiver import LocalBotReceiverError


def frame():
    return {'type':'req','id':'run-1','method':'chat.send','params':{'session_key':'chat:session-1',
        'bcs_group_id':'chat:session-1','bcs_session_id':None,'tags':['e2e','release'],'attachments':[],
        'message':{'role':'user','content':[{'type':'text','text':'hello'}]},
        'channel':{'actor_id':'ceo','thread_id':None},'session_context':{'session_id':'chat:session-1',
        'recipient':'pm','participants':['pm'],'from_bot_id':'ceo','message':'hello'}}}


def expected():
    return dict(run_id='run-1',session_id='chat:session-1',caller_id='ceo',target_id='pm',text='hello',tags=['e2e','release'])


class DirectReceiverTests(unittest.TestCase):
    def test_exact_direct_receipt_is_transport_only_and_not_group(self):
        result=validate_direct_frame(frame(),**expected())
        self.assertEqual(result['request_id'],'run-1')
        self.assertFalse(result['success_ack_sent'])
        self.assertEqual(result['scope'],'DIRECT_RUN_NOT_GROUP_SESSION')
        self.assertEqual(result['engine'],'NOT_STARTED')
        self.assertNotIn('hello',json.dumps(result))

    def test_every_identity_and_message_dimension_is_checked(self):
        edits=[lambda f:f.update(id='foreign'),lambda f:f.update(method='chat.inject'),
            lambda f:f['params'].update(bcs_group_id='foreign'),lambda f:f['params'].update(bcs_session_id='group:sid'),
            lambda f:f['params']['channel'].update(actor_id='qa'),lambda f:f['params']['channel'].update(thread_id='foreign'),
            lambda f:f['params']['session_context'].update(recipient='qa'),
            lambda f:f['params']['session_context'].update(participants=['pm','qa']),
            lambda f:f['params']['session_context'].update(from_bot_id='qa'),
            lambda f:f['params']['session_context'].update(message='foreign'),
            lambda f:f['params']['message'].update(content=[{'type':'text','text':'foreign'}]),
            lambda f:f['params'].update(tags=[])]
        for edit in edits:
            value=frame();edit(value)
            with self.subTest(value=value),self.assertRaises(LocalBotReceiverError):validate_direct_frame(value,**expected())

    def test_receiver_does_not_ack_delivery_and_rejects_repeat(self):
        receiver=object.__new__(OwnedLocalDirectReceiver)
        connection=Mock();connection.receive_frame.return_value=(1,json.dumps(frame()).encode())
        raw=SimpleNamespace(_state='OPEN',_connection=connection,bot_id='pm',_decode_frame=lambda data,_:json.loads(data),close=Mock())
        receiver.receiver=raw;receiver.deliveries=[]
        receiver.identities=SimpleNamespace(_require_roles=Mock(),_entries={'CEO':('ceo','token')})
        args=expected();args.pop('target_id')
        receiver.receive_run(**args)
        connection._send_frame.assert_not_called()
        with self.assertRaises(LocalBotReceiverError):receiver.receive_run(**args)
        raw.close.assert_called_once()

    def test_unexpected_request_closes_instead_of_discarding_foreign_run(self):
        receiver=object.__new__(OwnedLocalDirectReceiver);bad=frame();bad['id']='foreign'
        connection=Mock();connection.receive_frame.return_value=(1,json.dumps(bad).encode())
        raw=SimpleNamespace(_state='OPEN',_connection=connection,bot_id='pm',_decode_frame=lambda data,_:json.loads(data),close=Mock())
        receiver.receiver=raw;receiver.deliveries=[]
        receiver.identities=SimpleNamespace(_require_roles=Mock(),_entries={'CEO':('ceo','token')})
        args=expected();args.pop('target_id')
        with self.assertRaises(LocalBotReceiverError):receiver.receive_run(**args)
        self.assertEqual(receiver.deliveries,[]);raw.close.assert_called_once();connection._send_frame.assert_not_called()


class DirectFixtureSemanticTests(unittest.TestCase):
    def test_only_default_none_bot_info_fields_are_equivalent(self):
        from bcs_e2e.local_direct_chat_storage import bot_info_semantic_digest
        original = {'summary':'fixture', 'skills':[], 'domains':[], 'scopes':[], 'hidden':False}
        expanded = dict(original, binding_channels=None, agent_code=None, agent_token=None)
        self.assertEqual(bot_info_semantic_digest(json.dumps(original)), bot_info_semantic_digest(json.dumps(expanded)))
        for key in ('binding_channels','agent_code','agent_token','summary','skills','unknown'):
            changed = dict(expanded); changed[key] = 'changed'
            with self.subTest(key=key):
                self.assertNotEqual(bot_info_semantic_digest(json.dumps(original)), bot_info_semantic_digest(json.dumps(changed)))
