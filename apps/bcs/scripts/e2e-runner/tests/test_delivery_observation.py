"""Exact delivery reads never turn known-task completion into scope quiescence."""
import copy
from dataclasses import replace
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.alisddbquery import McporterAlisdDbQueryClient
from bcs_e2e.case_scope import CaseScopedAdapter
from bcs_e2e.delivery_observation import (CONTRACT, TABLE, FIELDS, DeliveryReadPlan,
    DeliveryObserver, DeliveryReadScope, project_rows, query_arguments, summarize)
from bcs_e2e.evidence import EvidenceError, EvidencePlan
from bcs_e2e.inventory import current_manifest
from bcs_e2e.ledger import Ledger
from bcs_e2e.profile import ConfigError


def row(plan, **extra):
    value=dict(delivery_id='delivery-1',env=plan.environment,group_id=plan.group_id,
        session_id=plan.session_id,source_message_id='message-1',target_bot_id='bot-1',
        kind='send',status='completed',state_version=4,attempt_no=1,request_id='request-1',
        run_id='run-1',downstream_run_id=None,bound_to_delivery_id=None,
        may_have_been_sent=1,terminal_at_ms=100)
    return {**value,**extra}


class DeliveryProjectionTests(unittest.TestCase):
    def setUp(self):self.plan=DeliveryReadPlan('testdb','pre','group-1','session-1',('bot-1','bot-2'))

    def test_wire_uses_fixed_projection_and_three_exact_filters(self):
        runner=Mock(return_value=subprocess.CompletedProcess([],0,json.dumps({'success':True,'data':[]} ),''))
        client=McporterAlisdDbQueryClient(server='test-observer',executable=('/mock/mcporter',),runner=runner)
        client.delivery_query(self.plan,timeout_seconds=5)
        command=runner.call_args.args[0];args=json.loads(command[command.index('--args')+1])
        self.assertEqual(command[2],'test-observer.db_query')
        self.assertEqual(args,{'db':'testdb','table':TABLE,'select':','.join(FIELDS),
            'where':"env = 'pre' AND group_id = 'group-1' AND session_id = 'session-1' AND target_bot_id IN ('bot-1','bot-2')",
            'limit':'100','orderby':'delivery_id asc'})
        self.assertFalse(runner.call_args.kwargs['shell'])
        for field in ('content','transport_context_json','semantic_projection_json','api_key_prefix'):
            self.assertNotIn(field,args['select'])

    def test_invalid_plan_or_identifier_fails_before_invocation(self):
        client=McporterAlisdDbQueryClient(server='test-observer',runner=Mock())
        for plan in ({'session_id':'session-1'},replace(self.plan,session_id="x' OR 1=1"),
                     replace(self.plan,target_bot_ids=('bot-1',"x' OR 1=1")),
                     replace(self.plan,target_bot_ids=('bot-1','bot-1')),
                     EvidencePlan('group.persisted','testdb',TABLE,(),{})):
            with self.subTest(plan=type(plan).__name__),self.assertRaises((EvidenceError,ConfigError)):
                client.delivery_query(plan,timeout_seconds=5)
        client.runner.assert_not_called()

    def test_completed_send_is_only_known_task_evidence(self):
        rows=project_rows(self.plan,{'success':True,'data':[row(self.plan,content='private')]},frozenset({'bot-1'}))
        self.assertNotIn('private',json.dumps(rows))
        result=summarize(rows)
        self.assertEqual(result['known_completed_send_ids'],['delivery-1'])
        self.assertEqual(result['scope_status'],'UNCONFIRMED')
        self.assertTrue(result['coverage_gaps'])

    def test_empty_page_is_not_no_execution_proof(self):
        result=summarize(project_rows(self.plan,{'success':True,'data':[]},frozenset({'bot-1'})))
        self.assertEqual(result['scope_status'],'UNCONFIRMED')
        self.assertEqual(result['known_completed_send_ids'],[])

    def test_full_page_tool_error_truncation_duplicates_and_foreign_rows_refused(self):
        good=row(self.plan)
        candidates=[{'success':False,'data':[]},{'success':True,'data':[good],'truncated':True},
            {'success':True,'data':[row(self.plan,delivery_id=f'd-{n}') for n in range(100)]},
            {'success':True,'data':[good,good]}]
        for field,value in [('env','prod'),('group_id','foreign'),('session_id','foreign'),
                            ('target_bot_id','foreign'),('status','unrecognized'),('kind','invalid'),
                            ('state_version',True),('attempt_no',-1),('terminal_at_ms','123'),
                            ('may_have_been_sent',2),('request_id','bad id')]:
            candidates.append({'success':True,'data':[row(self.plan,**{field:value})]})
        for payload in candidates:
            with self.subTest(payload=str(payload)[:80]),self.assertRaises((EvidenceError,ConfigError)):
                project_rows(self.plan,payload,frozenset({'bot-1'}))

    def test_inject_consumed_failure_unknown_and_active_not_successful_send(self):
        for status in ('pending_context','bound','consumed','discarded_context'):
            result=summarize([row(self.plan,kind='inject',status=status)])
            self.assertEqual(result['known_completed_send_ids'],[])
            self.assertEqual(result['context_dispositions'][0]['status'],status)
        for status,key in [('queued','active_send_ids'),('running','active_send_ids'),
                           ('unknown','unresolved_send_ids'),('expired','unresolved_send_ids'),
                           ('failed','unsuccessful_send_ids'),('cancelled','unsuccessful_send_ids')]:
            result=summarize([row(self.plan,status=status)])
            self.assertEqual(result['known_completed_send_ids'],[])
            self.assertEqual(result[key],['delivery-1'])
        for extra in ({'run_id':None},{'terminal_at_ms':None}):
            self.assertEqual(summarize([row(self.plan,**extra)])['unresolved_send_ids'],['delivery-1'])


class DeliveryObserverTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        from bcs_e2e.pool import PoolLease
        self.lease=PoolLease.acquire(Path(tmp.name)/'pool.lock','e2e-test','a'*64)
        self.addCleanup(self.lease.close)
        self.ledger=Ledger(Path(tmp.name)/'run.sqlite','e2e-test','a'*64,create=True);self.addCleanup(self.ledger.close)
        self.case='RETAIN-SESSION-01'
        g,s=self.ledger.group_creation_intent(self.case,1,'req-create')
        self.ledger.observed(g,'group-1',origin='created');self.ledger.observed(s,'session-1',origin='created')
        s2=self.ledger.intent(self.case,1,'session','req-create-2',g)
        self.ledger.observed(s2,'session-2',origin='created')
        definition=current_manifest('preprod-retained-v1')['cases'][1]
        self.scoped=CaseScopedAdapter(object(),self.ledger,'a'*64,definition,attempt=1)
        self.client=Mock()
        self.client.table_describe.return_value={'success':True,'columns':list(FIELDS)}
        self.client.delivery_query.side_effect=lambda plan,**kwargs:{'success':True,'data':[row(plan)]}
        self.observer=DeliveryObserver(client=self.client,database='testdb',environment='pre',allowed_bots=frozenset({'bot-1','bot-2'}))
        self.active=Mock()

    def observe(self):
        return self.observer.observe(self.ledger,self.scoped.observation_scope,self.active,lease=self.lease,case_id=self.case,timeout_seconds=10)

    def test_both_implicit_and_explicit_sessions_scoped_independently(self):
        self.observer.admit();result=self.observe()
        self.assertEqual(result['status'],'OBSERVED')
        self.assertEqual(result['scope_status'],'UNCONFIRMED')
        self.assertEqual(result['queries'],2)
        self.assertEqual([c.args[0].session_id for c in self.client.delivery_query.call_args_list],['session-1','session-2'])
        self.assertEqual(self.active.call_count,5)
        self.assertTrue(all(s['scope_status']=='UNCONFIRMED' for s in result['sessions']))

    def test_missing_schema_rejected_before_business_query(self):
        self.client.table_describe.return_value={'success':True,'columns':['status']}
        with self.assertRaises(EvidenceError):self.observer.admit()
        with self.assertRaises(EvidenceError):self.observe()
        self.client.delivery_query.assert_not_called()

    def test_authority_loss_stops_before_second_session(self):
        self.observer.admit()
        self.active.side_effect=[None,None,RuntimeError('private-lease')]
        result=self.observe()
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(self.client.delivery_query.call_count,1)
        self.assertEqual(result['sessions'],[])
        self.assertNotIn('private-lease',json.dumps(result))

    def test_empty_full_page_and_timeout_are_not_terminal_evidence(self):
        self.observer.admit();self.client.delivery_query.side_effect=lambda plan,**kwargs:{'success':True,'data':[]}
        self.assertEqual(self.observe()['status'],'INCOMPLETE')
        with patch('bcs_e2e.delivery_observation.time.monotonic',side_effect=[0,1,2,11]):
            result=self.observe()
        self.assertEqual(result['status'],'TIMED_OUT')
        self.assertEqual(result['sessions'],[])
        self.assertEqual(result['scope_status'],'UNCONFIRMED')

    def test_ownership_drift_during_query_never_credits_rows(self):
        self.observer.admit()
        def drift(plan,**kwargs):
            self.ledger.db.execute("UPDATE resource SET origin='borrowed' WHERE resource_id=?",(plan.session_id,))
            self.ledger.db.commit()
            return {'success':True,'data':[row(plan)]}
        self.client.delivery_query.side_effect=drift
        result=self.observe()
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(result['sessions'],[])

    def test_cancellation_propagates_to_owned_socket_finalizer(self):
        self.observer.admit();self.client.delivery_query.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.observe()


import test_retained_remote as remote_fixtures
from bcs_e2e.retained_remote import RetainedReview, validate_review
from bcs_e2e.remote_observer import ReviewedMcpClient
from bcs_e2e.run_state import read, write


class RetainedDeliveryCompositionTests(unittest.TestCase):
    setUp=remote_fixtures.RetainedRemoteTests.setUp
    command=remote_fixtures.RetainedRemoteTests.command
    args=remote_fixtures.RetainedRemoteTests.args
    boundaries=remote_fixtures.RetainedRemoteTests.boundaries

    def enable(self,case='RETAIN-GROUP-01'):
        self.review.update(execution_observation=CONTRACT,case_id=case)
        write(self.review_path,self.review)
        self.delivery_queries=[]
        self.delivery_response='completed'

    def process(self,command,**kwargs):
        args=json.loads(command[4]) if '--args' in command else {}
        if args.get('table') != TABLE:
            return remote_fixtures.RetainedRemoteTests.process(self,command,**kwargs)
        self.calls.append(command)
        if command[2].endswith('.table_describe'):
            ddl='CREATE TABLE `'+TABLE+'` ('+','.join('`'+f+'` varchar(128)' for f in FIELDS)+')'
            data=[{'Table':TABLE,'Create Table':ddl}]
        elif command[2].endswith('.db_query'):
            import re
            match=re.fullmatch(r"env = 'pre' AND group_id = '([A-Za-z0-9_.:@-]+)' AND session_id = '([A-Za-z0-9_.:@-]+)' AND target_bot_id IN \('test-driver','test-member'\)",args['where'])
            self.assertIsNotNone(match)
            self.assertEqual(args['select'],','.join(FIELDS));self.assertEqual(args['limit'],'100')
            plan=DeliveryReadPlan('testdb','pre',match[1],match[2],('test-driver','test-member'))
            self.assertIn(plan.group_id,self.adapter.groups);self.assertIn(plan.session_id,self.adapter.sessions)
            self.delivery_queries.append(plan)
            if self.delivery_response=='cancel' or self.delivery_response=='cancel-second' and len(self.delivery_queries)==2:raise KeyboardInterrupt()
            if self.delivery_response=='cancel-chain' and len(self.delivery_queries)==2:
                from bcs_e2e.bounded_process import ProcessCaptureTimeout
                try:raise ProcessCaptureTimeout('private-timeout')
                except ProcessCaptureTimeout:
                    try:raise KeyboardInterrupt()
                    except KeyboardInterrupt:raise OSError('private-finalizer')
            if self.delivery_response=='error':return subprocess.CompletedProcess(command,0,json.dumps({'success':False,'message':'private-error'}),'')
            data=[] if self.delivery_response=='empty' else [row(plan,target_bot_id='test-driver',status='completed' if self.delivery_response in {'cancel-second','cancel-chain'} else self.delivery_response)]
        else:raise AssertionError('unexpected delivery method')
        return subprocess.CompletedProcess(command,0,json.dumps({'success':True,'data':data}),'')

    def test_real_composition_observes_two_sessions_before_ws_close_without_qualifying(self):
        self.enable('RETAIN-SESSION-01')
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,2,result);self.assertEqual(result['functional'],'PASS')
        observation=result['execution_observation']
        self.assertEqual(observation['status'],'OBSERVED')
        self.assertEqual(observation['queries'],2)
        self.assertEqual(len(self.delivery_queries),2)
        self.assertEqual(result['socket_status'],'CLOSED')
        self.assertEqual(result['execution_status'],'UNCONFIRMED')
        self.assertEqual(result['pool_disposition'],'QUARANTINED')
        self.assertTrue(all(s['known_completed_send_ids']==['delivery-1'] for s in observation['sessions']))
        self.assertFalse(self.adapter.deleted_sessions)
        self.assertEqual(read(self.state/'preflight.json')['observer_preflight']['execution_schema'],'ADMITTED')

    def test_no_opt_in_does_not_read_new_table(self):
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(result['execution_observation']['status'],'NOT_CONFIGURED')
        self.assertFalse(any(TABLE in c[4] for c in self.calls if '--args' in c))

    def test_query_failure_preserves_functional_pass_but_not_overall_success(self):
        self.enable();self.delivery_response='error'
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(result['functional'],'PASS')
        self.assertEqual(result['execution_observation']['status'],'BLOCKED')
        self.assertNotIn('private-error',json.dumps(result))
        self.assertEqual(result['execution_status'],'UNCONFIRMED')
        self.assertTrue(Path(read(self.state/'run.json')['lock_path']).exists())

    def test_observation_cancellation_closes_socket_without_deletion(self):
        self.enable('RETAIN-SESSION-01');self.delivery_response='cancel'
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,130)
        self.assertEqual(result['functional'],'PASS')
        self.assertEqual(result['execution_observation']['status'],'CANCELLED')
        self.assertEqual(result['socket_status'],'CLOSED')
        self.assertFalse(self.adapter.connected_sessions)
        self.assertFalse(self.adapter.deleted_sessions)
        self.assertTrue(Path(read(self.state/'run.json')['lock_path']).exists())

    def test_missing_additional_schema_blocks_before_creation(self):
        self.enable()
        with self.boundaries(),patch('bcs_e2e.delivery_observation.DeliveryObserver.admit',side_effect=EvidenceError('private-schema')):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2);self.assertFalse(result['prepared'])
        self.assertEqual(result['functional'],'NOT_RUN')
        self.assertFalse(self.state.exists());self.assertEqual(self.delivery_queries,[])
        self.assertNotIn('private-schema',json.dumps(result))

    def test_old_mapping_alone_or_mismatched_explicit_permission_does_not_query(self):
        client=Mock();binding=Mock();binding.validate.return_value=self.receipt
        wrapped=ReviewedMcpClient(client,binding)
        review=RetainedReview(self.profile,self.run_id,self.review['case_id'],self.review_path)
        plan=DeliveryReadPlan('testdb','pre','group-1','session-1',('test-driver','test-member'))
        with self.assertRaises(ConfigError):wrapped.delivery_query(plan,review=review,scope=None,timeout_seconds=5)
        client.delivery_query.assert_not_called()
        self.enable()
        review=RetainedReview(self.profile,self.run_id,self.review['case_id'],self.review_path)
        with self.assertRaises(ConfigError):wrapped.delivery_query(replace(plan,environment='prod'),review=review,scope=None,timeout_seconds=5)
        client.delivery_query.assert_not_called()
        self.review['execution_observation']='all-tables'
        with self.assertRaises(ConfigError):validate_review(self.review,self.profile,self.run_id,self.review['case_id'])

    def make_owned_scope(self):
        from bcs_e2e.pool import PoolLease, bound_pool_path
        from bcs_e2e.prepare import prepare_run
        prepare_run(self.profile,self.state,self.run_id,manifest=current_manifest('preprod-retained-v1'),
                    observer_binding=self.receipt_path)
        review=RetainedReview(self.profile,self.run_id,self.review['case_id'],self.review_path)
        review.freeze(self.state)
        state=read(self.state/'run.json');state.update(status='RUNNING',active_case=self.review['case_id'])
        write(self.state/'run.json',state)
        lease=PoolLease.from_file(bound_pool_path(state),self.run_id,state['profile_digest']);self.addCleanup(lease.close)
        ledger=Ledger(self.state/'run.sqlite',self.run_id,state['profile_digest']);self.addCleanup(ledger.close)
        g,s=ledger.group_creation_intent(self.review['case_id'],1,'req-observe')
        ledger.observed(g,'group-1',origin='created');ledger.observed(s,'session-1',origin='created')
        return DeliveryReadScope(ledger,lease,self.review['case_id']),review

    def test_authorization_change_in_flight_rejects_the_response(self):
        self.enable();scope,review=self.make_owned_scope()
        binding=Mock();binding.validate.return_value=self.receipt
        client=Mock()
        def expire(plan,**kwargs):
            value=read(review.path);value['expires_at']=value['reviewed_at'];write(review.path,value)
            return {'success':True,'data':[row(plan)]}
        client.delivery_query.side_effect=expire
        with self.assertRaises(ConfigError):
            ReviewedMcpClient(client,binding).delivery_query(DeliveryReadPlan('testdb','pre','group-1','session-1',('test-driver','test-member')),
                review=review,scope=scope,timeout_seconds=5)
        self.assertEqual(client.delivery_query.call_count,1)

    def test_query_boundary_rejects_foreign_ids_or_unheld_lease(self):
        self.enable();scope,review=self.make_owned_scope()
        binding=Mock();binding.validate.return_value=self.receipt;client=Mock()
        wrapped=ReviewedMcpClient(client,binding)
        for plan in (DeliveryReadPlan('testdb','pre','foreign','session-1',('test-driver','test-member')),
                     DeliveryReadPlan('testdb','pre','group-1','foreign',('test-driver','test-member')),
                     DeliveryReadPlan('testdb','pre','group-1','session-1',('foreign','test-member'))):
            with self.assertRaises(EvidenceError):wrapped.delivery_query(plan,review=review,scope=scope,timeout_seconds=5)
        client.delivery_query.assert_not_called()
        scope.lease.close()
        with self.assertRaises(RuntimeError):
            wrapped.delivery_query(DeliveryReadPlan('testdb','pre','group-1','session-1',('test-driver','test-member')),review=review,scope=scope,timeout_seconds=5)
        client.delivery_query.assert_not_called()

    def test_query_boundary_rejects_old_permission_with_real_scope(self):
        scope,review=self.make_owned_scope()
        binding=Mock();binding.validate.return_value=self.receipt;client=Mock()
        with self.assertRaises(ConfigError):
            ReviewedMcpClient(client,binding).delivery_query(DeliveryReadPlan('testdb','pre','group-1','session-1',('test-driver','test-member')),
                review=review,scope=scope,timeout_seconds=5)
        client.delivery_query.assert_not_called()

    def test_json_junit_and_markdown_keep_observation_separate_from_pass(self):
        from bcs_e2e.reporting import build_report, render_junit, render_markdown
        import xml.etree.ElementTree as ET
        self.enable()
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,2)
        summary=build_report(self.profile,self.state)
        case=summary['cases'][0]
        self.assertEqual(case['execution_observation'],{'status':'OBSERVED','scope_status':'UNCONFIRMED',
            'session_count':1,'known_completed_send_count':1})
        self.assertNotEqual(case['status'],'PASS')
        xml=ET.fromstring(render_junit(summary))
        self.assertIsNotNone(xml.find('testcase/skipped'))
        props={p.attrib['name']:p.attrib['value'] for p in xml.findall('testcase/properties/property')}
        self.assertEqual(props['execution_observation.scope_status'],'UNCONFIRMED')
        self.assertIn('Known completed tasks are not proof',render_markdown(summary))
        report_path=self.state/'report-RETAIN-GROUP-01.json'
        actual=read(report_path);actual['execution_observation']['scope_status']='CONFIRMED'
        write(report_path,actual)
        malformed=build_report(self.profile,self.state)
        self.assertNotEqual(malformed['integrity']['status'],'PASS')
        self.assertEqual(malformed['cases'][0]['execution_observation']['status'],'INVALID')


    def test_cancel_on_second_session_keeps_first_observed_evidence(self):
        self.enable('RETAIN-SESSION-01');self.delivery_response='cancel-second'
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,130);self.assertEqual(result['functional'],'PASS')
        observation=result['execution_observation']
        self.assertEqual(observation['status'],'CANCELLED')
        self.assertEqual(observation['queries'],2)
        self.assertEqual(len(observation['sessions']),1)
        self.assertEqual(observation['sessions'][0]['known_completed_send_ids'],['delivery-1'])
        self.assertEqual(result['socket_status'],'CLOSED')

    def test_chained_timeout_then_cancel_then_cleanup_error_keeps_first_evidence(self):
        self.enable('RETAIN-SESSION-01');self.delivery_response='cancel-chain'
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,130);self.assertEqual(result['functional'],'PASS')
        observation=result['execution_observation']
        self.assertEqual(observation['status'],'CANCELLED')
        self.assertEqual(observation['queries'],2)
        self.assertEqual(len(observation['sessions']),1)
        self.assertEqual(observation['sessions'][0]['known_completed_send_ids'],['delivery-1'])
        self.assertEqual(result['socket_status'],'CLOSED')


class DeliveryTimeoutTests(unittest.TestCase):
    def test_real_child_deadline_remains_machine_readable_after_mcp_wrapping(self):
        from bcs_e2e.alisddbquery import AlisdDbQueryTimeout
        with tempfile.TemporaryDirectory() as d:
            script=Path(d)/'slow.py';script.write_text('import time\ntime.sleep(5)\n')
            client=McporterAlisdDbQueryClient(server='test-observer',executable=(sys.executable,str(script)))
            with self.assertRaises(AlisdDbQueryTimeout) as caught:
                client.delivery_query(DeliveryReadPlan('testdb','pre','g','s',('bot-1','bot-2')),timeout_seconds=0.03)
            self.assertIsInstance(caught.exception,TimeoutError)
            self.assertNotIn(str(script),str(caught.exception))

    def test_timeout_override_never_exceeds_configured_transport_budget(self):
        runner=Mock(return_value=subprocess.CompletedProcess([],0,'{"success":true,"data":[]}',''))
        client=McporterAlisdDbQueryClient(server='test-observer',timeout_seconds=2,runner=runner)
        client.delivery_query(DeliveryReadPlan('testdb','pre','g','s',('bot-1','bot-2')),timeout_seconds=8)
        self.assertEqual(runner.call_args.kwargs['timeout'],2)
        for value in (0,-1,True,float('nan'),float('inf')):
            with self.assertRaises(RuntimeError):
                client.delivery_query(DeliveryReadPlan('testdb','pre','g','s',('bot-1','bot-2')),timeout_seconds=value)
        self.assertEqual(runner.call_count,1)


class DeliveryBudgetTests(unittest.TestCase):
    setUp = DeliveryObserverTests.setUp
    observe = DeliveryObserverTests.observe

    def test_each_session_receives_remaining_not_renewed_budget(self):
        self.observer.admit()
        with patch('bcs_e2e.delivery_observation.time.monotonic',side_effect=[0,1,2,3,4,5,9]):
            result=self.observe()
        self.assertEqual(result['status'],'OBSERVED')
        self.assertEqual([call.kwargs['timeout_seconds'] for call in self.client.delivery_query.call_args_list],[8,5])

    def test_wrapped_transport_timeout_is_classified_timed_out(self):
        from bcs_e2e.alisddbquery import AlisdDbQueryTimeout
        self.observer.admit();self.client.delivery_query.side_effect=AlisdDbQueryTimeout('private deadline')
        result=self.observe()
        self.assertEqual(result['status'],'TIMED_OUT')
        self.assertNotIn('private deadline',json.dumps(result))
        self.assertEqual(result['scope_status'],'UNCONFIRMED')


    def test_direct_error_chain_prioritizes_cancel_over_prior_timeout(self):
        self.observer.admit()
        def cancel(plan,**kwargs):
            try:raise TimeoutError('private-earlier')
            except TimeoutError:
                try:raise KeyboardInterrupt()
                except KeyboardInterrupt:raise OSError('private-secondary')
        self.client.delivery_query.side_effect=cancel
        with self.assertRaises(KeyboardInterrupt) as caught:self.observe()
        self.assertEqual(caught.exception.execution_observation['status'],'CANCELLED')
        self.assertNotIn('private-',json.dumps(caught.exception.execution_observation))
