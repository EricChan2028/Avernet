import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.case_scope import CaseScopedAdapter
from bcs_e2e.execution_ledger import ExecutionLedger
from bcs_e2e.ledger import Ledger
from bcs_e2e.case_scope import CaseScopeError
from bcs_e2e.profile import fingerprint


def profile():
    return {
        'human': {'user_id':'owner-1'},
        'actors': {'driver': {'bot_id':'bot-a','owner_id':'owner-1','dedicated':True},
                   'member': {'bot_id':'bot-b','owner_id':'owner-1','dedicated':True}},
    }


class LocalExecutionEffectsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.profile=profile();self.path=Path(self.tmp.name)/'run.sqlite'
        self.ledger=Ledger(self.path,'run-1',fingerprint({'profile':'test'}),create=True)
        self.effects=ExecutionLedger(self.ledger)
        self.adapter=LocalMockAdapter(self.profile)
        self.adapter.bind_execution_ledger(self.effects,'PRE-02',1)
        self.case=CaseScopedAdapter(self.adapter,self.ledger,fingerprint({'profile':'test'}),
                                    {'id':'PRE-02','effects':{'delete:owned_initial_session','delete:owned_session'}},attempt=1)

    def tearDown(self): self.ledger.close()

    def test_group_and_explicit_session_create_effects_are_exactly_bound(self):
        group_handle, session_handle = self.ledger.group_creation_intent('PRE-02',1,'group-req')
        group,record=self.adapter.group_create({'group_kind':'normal','driver_bot_uuid':'bot-a',
            'participants':[{'actor_id':'bot-a'},{'actor_id':'bot-b'}]})
        self.ledger.observed(group_handle,group['group_id'],origin='created',actual_request_id=record['submitted_request_id'])
        self.ledger.observed(session_handle,group['initial_session_id'],origin='created',actual_request_id=record['submitted_request_id'])
        explicit_handle=self.ledger.intent('PRE-02',1,'session','session-req',group_handle)
        session,record=self.adapter.session_create(group['group_id'],{'title':'x'})
        self.ledger.observed(explicit_handle,session['session_id'],origin='created',actual_request_id=record['submitted_request_id'])
        effects=self.effects.snapshot(case_id='PRE-02',attempt=1)
        self.assertEqual(len(effects),8) # two contexts + two runs + four Bot deliveries
        self.assertEqual({e['state'] for e in effects},{'QUEUED'})
        self.assertEqual({e['actor_id'] for e in effects if e['kind']=='delivery'},{'bot-a','bot-b'})
        self.assertEqual({e['kind'] for e in effects}, {'context', 'run', 'delivery'})
        self.assertTrue(session['session_id'])

    def test_case_scoped_session_delete_quiesces_before_delete(self):
        group_handle,session_handle=self.ledger.group_creation_intent('PRE-02',1,'group-req')
        group,_=self.adapter.group_create({'group_kind':'normal','driver_bot_uuid':'bot-a',
            'participants':[{'actor_id':'bot-a'},{'actor_id':'bot-b'}]})
        self.ledger.observed(group_handle,group['group_id'],origin='created')
        self.ledger.observed(session_handle,group['initial_session_id'],origin='created')
        self.case._initial_sessions.add(session_handle)
        self.case.session_delete(group['initial_session_id'])
        self.effects.require_quiescent(case_id='PRE-02',attempt=1)
        self.assertTrue(all(e['state']=='COMPLETED' for e in self.effects.snapshot()))
        self.assertEqual(self.case.termination_snapshots()[0]['phase'], 'CLEANUP_READY')
        self.case.session_verify_deleted(group['initial_session_id'])
        self.assertEqual(self.case.termination_snapshots()[0]['phase'], 'CLEANED')
        with self.assertRaises(Exception):
            self.case.session_read(group['initial_session_id'])

    def test_termination_and_effect_binding_survive_ledger_reopen(self):
        group_handle, session_handle = self.ledger.group_creation_intent('PRE-02', 1, 'group-req')
        group, _ = self.adapter.group_create({'group_kind': 'normal', 'driver_bot_uuid': 'bot-a',
            'participants': [{'actor_id': 'bot-a'}]})
        self.ledger.observed(group_handle, group['group_id'], origin='created')
        self.ledger.observed(session_handle, group['initial_session_id'], origin='created')
        self.case._initial_sessions.add(session_handle)
        self.case.session_delete(group['initial_session_id'])
        saved = self.ledger.termination_state(session_handle)
        self.assertEqual(saved['phase'], 'CLEANUP_READY')
        effects = self.ledger.execution_effects(session_id=group['initial_session_id'])
        self.assertTrue(effects)
        self.ledger.close()
        self.ledger = Ledger(self.path, 'run-1', fingerprint({'profile': 'test'}))
        self.assertEqual(self.ledger.termination_state(session_handle)['phase'], 'CLEANUP_READY')
        self.assertTrue(all(row['state'] == 'COMPLETED'
                            for row in self.ledger.execution_effects(session_id=group['initial_session_id'])))

    def test_missing_termination_capability_is_retained(self):
        group_handle, session_handle = self.ledger.group_creation_intent('PRE-02', 1, 'group-req')
        group, _ = self.adapter.group_create({'group_kind': 'normal', 'driver_bot_uuid': 'bot-a',
            'participants': [{'actor_id': 'bot-a'}]})
        self.ledger.observed(group_handle, group['group_id'], origin='created')
        self.ledger.observed(session_handle, group['initial_session_id'], origin='created')
        self.case._initial_sessions.add(session_handle)
        self.adapter.termination_mode = 'REMOTE_UNSUPPORTED'
        with self.assertRaises(CaseScopeError):
            self.case.session_delete(group['initial_session_id'])
        self.assertEqual(self.ledger.termination_state(session_handle)['phase'], 'RETAINED')
        self.assertFalse(self.adapter.deleted_sessions)

    def test_websocket_subscription_is_exactly_bound_and_closed(self):
        group, _ = self.adapter.group_create({'group_kind': 'normal', 'driver_bot_uuid': 'bot-a',
            'participants': [{'actor_id': 'bot-a'}]})
        connection_value = self.adapter.session_token(group['initial_session_id'])[0]['token']
        self.adapter.session_ws_connect(group['group_id'], group['initial_session_id'], connection_value)
        self.adapter.session_ws_close(group['initial_session_id'])
        subscriptions = [row for row in self.effects.snapshot() if row['kind'] == 'subscription']
        self.assertEqual(len(subscriptions), 1)
        self.assertEqual(subscriptions[0]['session_id'], group['initial_session_id'])
        self.assertEqual(subscriptions[0]['state'], 'COMPLETED')

    def test_cancel_queued_effects_reaches_cancelled_without_broad_stop(self):
        group_handle, session_handle = self.ledger.group_creation_intent('PRE-02', 1, 'group-req')
        group, _ = self.adapter.group_create({'group_kind': 'normal', 'driver_bot_uuid': 'bot-a',
            'participants': [{'actor_id': 'bot-a'}, {'actor_id': 'bot-b'}]})
        self.ledger.observed(group_handle, group['group_id'], origin='created')
        self.ledger.observed(session_handle, group['initial_session_id'], origin='created')
        self.case._initial_sessions.add(session_handle)
        self.adapter.effect_mode = 'CANCEL_QUEUED'
        self.case.session_delete(group['initial_session_id'])
        self.assertEqual({row['state'] for row in self.effects.snapshot()}, {'CANCELLED'})

    def test_late_callback_blocks_quiescence_and_parent_cleanup(self):
        group_handle, session_handle = self.ledger.group_creation_intent('PRE-02', 1, 'group-req')
        group, _ = self.adapter.group_create({'group_kind': 'normal', 'driver_bot_uuid': 'bot-a',
            'participants': [{'actor_id': 'bot-a'}]})
        self.ledger.observed(group_handle, group['group_id'], origin='created')
        self.ledger.observed(session_handle, group['initial_session_id'], origin='created')
        self.case._initial_sessions.add(session_handle)
        self.adapter.effect_mode = 'LATE_CALLBACK'
        with self.assertRaises(Exception):
            self.case.session_delete(group['initial_session_id'])
        self.assertEqual(self.case.termination_snapshots()[0]['phase'], 'RETAINED')
        self.assertTrue(any(row['state'] == 'QUEUED' for row in self.effects.snapshot()))

    def test_unknown_effect_blocks_quiescence_and_parent_cleanup(self):
        group_handle,session_handle=self.ledger.group_creation_intent('PRE-02',1,'group-req')
        group,_=self.adapter.group_create({'group_kind':'normal','driver_bot_uuid':'bot-a',
            'participants':[{'actor_id':'bot-a'}]})
        self.ledger.observed(group_handle,group['group_id'],origin='created')
        self.ledger.observed(session_handle,group['initial_session_id'],origin='created')
        self.case._initial_sessions.add(session_handle)
        effect=self.effects.snapshot()[0]['handle']
        self.effects.unknown(effect,reason='lost local execution response')
        with self.assertRaises(Exception): self.case.session_delete(group['initial_session_id'])
        self.assertFalse(self.adapter.deleted_sessions)
        self.assertEqual(self.case.termination_snapshots()[0]['phase'], 'RETAINED')
        with self.assertRaises(Exception):
            self.case.session_update(group['initial_session_id'], {'title': 'late'})
