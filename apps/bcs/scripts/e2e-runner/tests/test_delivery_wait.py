"""Finite opt-in natural completion polling without inventing scope quiescence."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.delivery_observation import DeliveryObserver, WAIT_CONTRACT
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read,write
from bcs_e2e.retained_remote import validate_review
import test_delivery_observation as fixtures
from test_delivery_observation import row


class DeliveryWaitTests(unittest.TestCase):
    setUp=fixtures.DeliveryObserverTests.setUp

    def observe(self):
        self.observer=DeliveryObserver(client=self.client,database='testdb',environment='pre',
            allowed_bots=frozenset({'bot-1','bot-2'}),contract=WAIT_CONTRACT)
        self.observer.admit()
        return self.observer.observe(self.ledger,self.scoped.observation_scope,self.active,
                                    lease=self.lease,case_id=self.case,timeout_seconds=10)

    def scripted(self,states):
        calls=[]
        def query(plan,**kwargs):
            round_number=len(calls)//2
            calls.append(plan)
            state=states[round_number]
            if callable(state):return state(plan,round_number)
            return {'success':True,'data':[row(plan,status=state,state_version=round_number+1,
                terminal_at_ms=100 if state=='completed' else None)]}
        self.client.delivery_query.side_effect=query
        return calls

    def test_running_then_completed_waits_but_never_qualifies_scope(self):
        calls=self.scripted(['running','completed'])
        with patch('bcs_e2e.delivery_observation.time.sleep') as sleep:result=self.observe()
        self.assertEqual(len(calls),4);sleep.assert_called_once()
        self.assertEqual(result['stop_reason'],'KNOWN_DELIVERIES_SETTLED')
        self.assertEqual(result['scope_status'],'UNCONFIRMED')
        self.assertEqual(len(result['rounds']),2)
        self.assertEqual(result['rounds'][0]['sessions'][0]['active_send_ids'],['delivery-1'])
        self.assertNotIn('rows',result['rounds'][0]['sessions'][0])

    def test_running_or_empty_observations_end_at_six_queries(self):
        for state in ('running',lambda plan,n:{'success':True,'data':[]}):
            with self.subTest(state=str(state)):
                calls=self.scripted([state]*3)
                with patch('bcs_e2e.delivery_observation.time.sleep') as sleep:result=self.observe()
                self.assertEqual(len(calls),6);self.assertEqual(sleep.call_count,2)
                self.assertEqual(result['stop_reason'],'OBSERVATION_LIMIT')
                self.assertEqual(result['scope_status'],'UNCONFIRMED')
                self.assertEqual(len(result['rounds']),3)

    def test_successor_arrival_prevents_early_settled_stop(self):
        def successor(plan,n):
            return {'success':True,'data':[row(plan,status='completed',state_version=2),
                row(plan,delivery_id='successor',status='queued',run_id=None,terminal_at_ms=None,state_version=1)]}
        self.scripted(['running',successor,successor])
        with patch('bcs_e2e.delivery_observation.time.sleep'):result=self.observe()
        self.assertEqual(result['stop_reason'],'OBSERVATION_LIMIT')
        self.assertEqual(result['sessions'][0]['known_completed_send_ids'],['delivery-1'])
        self.assertEqual(result['sessions'][0]['active_send_ids'],['successor'])

    def test_disappearance_regression_or_same_version_mutation_is_incomplete(self):
        for next_row in (lambda p:[], lambda p:[row(p,status='completed',state_version=0)],
                         lambda p:[row(p,status='completed',state_version=1)]):
            with self.subTest(next_row=next_row):
                self.scripted(['running',lambda p,n:{'success':True,'data':next_row(p)}])
                with patch('bcs_e2e.delivery_observation.time.sleep'):result=self.observe()
                self.assertEqual(result['stop_reason'],'INCONSISTENT_HISTORY')
                self.assertEqual(result['status'],'INCOMPLETE')
                self.assertEqual(result['rounds'][-1]['inconsistent_delivery_ids'],['delivery-1'])
                self.assertEqual(result['queries'],4)

    def test_pending_context_is_not_settled_and_is_not_cancelled(self):
        def context(plan,n):
            return {'success':True,'data':[row(plan,state_version=n+1),row(plan,delivery_id='context',
                kind='inject',status='pending_context',state_version=n+1,run_id=None,terminal_at_ms=None)]}
        self.scripted([context]*3)
        with patch('bcs_e2e.delivery_observation.time.sleep'):result=self.observe()
        self.assertEqual(result['stop_reason'],'OBSERVATION_LIMIT')
        self.assertEqual(result['queries'],6)

    def test_timeout_after_backoff_issues_no_extra_query(self):
        self.scripted(['running'])
        with patch('bcs_e2e.delivery_observation.time.monotonic',side_effect=[0,1,2,3,4,5,6,7,11]), \
             patch('bcs_e2e.delivery_observation.time.sleep'):
            result=self.observe()
        self.assertEqual(result['status'],'TIMED_OUT')
        self.assertEqual(result['queries'],2)
        self.assertEqual(result['stop_reason'],'TIMED_OUT')
        self.assertEqual(len(result['rounds']),1)

    def test_cancellation_between_rounds_keeps_history(self):
        self.scripted(['running'])
        with patch('bcs_e2e.delivery_observation.time.sleep',side_effect=KeyboardInterrupt()), \
             self.assertRaises(KeyboardInterrupt) as caught:self.observe()
        observation=caught.exception.execution_observation
        self.assertEqual(observation['status'],'CANCELLED')
        self.assertEqual(observation['stop_reason'],'CANCELLED')
        self.assertEqual(len(observation['rounds']),1)
        self.assertEqual(observation['queries'],2)

    def test_second_round_failure_preserves_last_valid_rows_per_session(self):
        for fail_at in (3,4):
            with self.subTest(fail_at=fail_at):
                count=0
                def query(plan,**kwargs):
                    nonlocal count
                    count+=1
                    if count==fail_at:raise TimeoutError('private-query-timeout')
                    n=(count-1)//2+1
                    return {'success':True,'data':[row(plan,status='running',state_version=n,terminal_at_ms=None)]}
                self.client.delivery_query.side_effect=query
                with patch('bcs_e2e.delivery_observation.time.sleep'):result=self.observe()
                self.assertEqual(result['status'],'TIMED_OUT')
                self.assertEqual(len(result['sessions']),2)
                self.assertEqual([s['observed_round'] for s in result['sessions']], [1,1] if fail_at==3 else [2,1])
                self.assertEqual(len(result['rounds']),1)
                self.assertEqual(result['attempted_round'],2)
                self.assertTrue(all(s['rows'][0]['source_message_id']=='message-1' for s in result['sessions']))
                self.assertNotIn('private-query-timeout',json.dumps(result))

    def test_second_round_cancel_keeps_last_successful_projections(self):
        calls=0
        def query(plan,**kwargs):
            nonlocal calls
            calls+=1
            if calls==3:raise KeyboardInterrupt()
            return {'success':True,'data':[row(plan,status='running',terminal_at_ms=None)]}
        self.client.delivery_query.side_effect=query
        with patch('bcs_e2e.delivery_observation.time.sleep'),self.assertRaises(KeyboardInterrupt) as caught:self.observe()
        result=caught.exception.execution_observation
        self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual(len(result['sessions']),2)
        self.assertEqual([s['observed_round'] for s in result['sessions']],[1,1])
        self.assertEqual(result['stop_reason'],'CANCELLED')



class RemoteWaitCompositionTests(unittest.TestCase):
    setUp=fixtures.RetainedDeliveryCompositionTests.setUp
    command=fixtures.RetainedDeliveryCompositionTests.command
    args=fixtures.RetainedDeliveryCompositionTests.args
    boundaries=fixtures.RetainedDeliveryCompositionTests.boundaries
    process=fixtures.RetainedDeliveryCompositionTests.process
    enable=fixtures.RetainedDeliveryCompositionTests.enable

    def test_explicit_wait_review_reaches_real_mcp_composition_three_times(self):
        self.enable();self.review['execution_observation']=WAIT_CONTRACT;write(self.review_path,self.review)
        self.delivery_response='running'
        with self.boundaries(),patch('bcs_e2e.delivery_observation.time.sleep'):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(result['functional'],'PASS')
        self.assertEqual(result['execution_observation']['contract'],WAIT_CONTRACT)
        self.assertEqual(result['execution_observation']['queries'],3)
        self.assertEqual(result['execution_observation']['stop_reason'],'OBSERVATION_LIMIT')
        self.assertTrue(Path(read(self.state/'run.json')['lock_path']).exists())
        self.assertFalse(self.adapter.deleted)

    def test_legacy_snapshot_permission_still_queries_only_once(self):
        self.enable();self.delivery_response='running'
        with self.boundaries(),patch('bcs_e2e.delivery_observation.time.sleep') as sleep:
            rc,result=self.command(self.args())
        self.assertEqual(rc,2);sleep.assert_not_called()
        self.assertEqual(result['execution_observation']['queries'],1)
        self.assertNotIn('rounds',result['execution_observation'])


    def test_wait_rounds_are_visible_in_all_report_formats_without_pass(self):
        from bcs_e2e.reporting import build_report,render_markdown,render_junit
        self.enable();self.review['execution_observation']=WAIT_CONTRACT;write(self.review_path,self.review)
        self.delivery_response='running'
        with self.boundaries(),patch('bcs_e2e.delivery_observation.time.sleep'):
            rc,result=self.command(self.args())
        summary=build_report(self.profile,self.state)
        observation=summary['cases'][0]['execution_observation']
        self.assertEqual(observation['observation_rounds'],3)
        self.assertEqual(observation['stop_reason'],'OBSERVATION_LIMIT')
        self.assertIn('3 observation rounds',render_markdown(summary))
        self.assertIn('execution_observation.observation_rounds',render_junit(summary))
        self.assertNotEqual(summary['status'],'PILOT_PASS')


    def test_malformed_or_unknown_wait_permission_is_a_configuration_error(self):
        for value in ([],{},None,False,'wait-forever'):
            review=copy.deepcopy(self.review);review['execution_observation']=value
            with self.subTest(value=value),self.assertRaises(ConfigError):
                validate_review(review,self.profile,self.run_id,review['case_id'])
