"""Correlated downstream reads require delivery provenance and queue env proof."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.alisddbquery import McporterAlisdDbQueryClient
from bcs_e2e.delivery_observation import DeliveryReadPlan
from bcs_e2e.downstream_observation import (CONTRACT, QUEUE_TABLE, QUEUE_FIELDS, RESULT_TABLE,
    RESULT_FIELDS, DownstreamReadPlan, query_arguments, project_row, correlate)
from bcs_e2e.evidence import EvidenceError
from bcs_e2e.profile import ConfigError
from bcs_e2e.remote_observer import ReviewedMcpClient
from bcs_e2e.retained_remote import validate_review
from bcs_e2e.run_state import read,write
import test_delivery_observation as fixtures


def plan():
    return DownstreamReadPlan(DeliveryReadPlan('testdb','pre','group-1','session-1',('test-driver','test-member')),
                              'delivery-1','test-driver','downstream-1','baas-driver')


def queue(p, **extra):return {'run_id':p.downstream_run_id,'bot_id':p.baas_bot_id,'env':p.parent.environment,'session_id':p.parent.session_id,'status':'DONE',**extra}
def result(p, **extra):return {'run_id':p.downstream_run_id,'bot_id':p.baas_bot_id,'status':'COMPLETED',**extra}


class DownstreamProjectionTests(unittest.TestCase):
    def test_wire_is_exact_indexed_identity_and_has_no_payload_fields(self):
        for is_result,table,fields in ((False,QUEUE_TABLE,QUEUE_FIELDS),(True,RESULT_TABLE,RESULT_FIELDS)):
            q=query_arguments(plan(),result=is_result)
            self.assertEqual(q['table'],table);self.assertEqual(q['select'],','.join(fields))
            self.assertEqual(q['where'],('' if is_result else "env = 'pre' AND ")+"run_id = 'downstream-1' AND bot_id = 'baas-driver'")
            self.assertEqual(q['limit'],'2')
            runner=Mock(return_value=subprocess.CompletedProcess([],0,'{"success":true,"data":[]}',''))
            client=McporterAlisdDbQueryClient(server='test-observer',runner=runner)
            client.downstream_query(plan(),result=is_result,timeout_seconds=0.5)
            self.assertEqual(runner.call_args.kwargs['timeout'],0.5)
            self.assertEqual(json.loads(runner.call_args.args[0][4]),q)

    def test_invalid_id_or_target_refused_before_sql(self):
        for p in (replace(plan(),downstream_run_id="x' OR 1=1"),replace(plan(),target_bot_id='foreign'),{}):
            with self.assertRaises((EvidenceError,ConfigError)):query_arguments(p)

    def test_projection_missing_foreign_duplicate_and_truncated_are_not_completion(self):
        p=plan()
        self.assertIsNone(project_row(p,{'success':True,'data':[]}))
        for payload in ({'success':False,'data':[]},{'success':True,'data':[queue(p)],'truncated':True},
                        {'success':True,'data':[queue(p),queue(p)]},
                        *({'success':True,'data':[queue(p,**change)]} for change in
                          ({'env':'prod'},{'bot_id':'foreign'},{'run_id':'foreign'},{'status':'COMPLETED'}))):
            with self.assertRaises(EvidenceError):project_row(p,payload)
        projected=project_row(p,{'success':True,'data':[queue(p,meta='private')]})
        self.assertNotIn('private',json.dumps(projected))

    def test_done_plus_completed_is_not_callback_or_scope_completion(self):
        p=plan();observed=correlate(p,queue(p),result(p))
        self.assertTrue(observed['known_run_result_completed'])
        self.assertEqual(observed['callback_status'],'UNCONFIRMED')
        self.assertEqual(observed['scope_status'],'UNCONFIRMED')
        for status in ('PENDING','RUNNING','FAILED','TIME_OUT','ABORTED'):
            self.assertFalse(correlate(p,queue(p),result(p,status=status))['known_run_result_completed'])
        self.assertFalse(correlate(p,None,None)['known_run_result_completed'])


class DownstreamScopeTests(unittest.TestCase):
    setUp=fixtures.RetainedDeliveryCompositionTests.setUp
    enable=fixtures.RetainedDeliveryCompositionTests.enable
    make_owned_scope=fixtures.RetainedDeliveryCompositionTests.make_owned_scope

    def ready(self):
        self.enable()
        self.review['downstream_observation']={'contract':CONTRACT,'run_id_source':'accepted-alias','queue_enabled':True,
            'session_binding':'bcs-session-id','bot_bindings':{
            'test-driver':'baas-driver','test-member':'baas-member'}}
        write(self.review_path,self.review)
        scope,review=self.make_owned_scope();client=Mock();binding=Mock();binding.validate.return_value=self.receipt
        client.delivery_query.return_value={'success':True,'data':[fixtures.row(plan().parent,
            target_bot_id='test-driver',downstream_run_id='downstream-1')]}
        client.downstream_query.side_effect=lambda p,**kw:{'success':True,'data':[result(p) if kw['result'] else queue(p)]}
        wrapped=ReviewedMcpClient(client,binding)
        return wrapped,client,scope,review

    def observe_delivery(self,wrapped,scope,review):
        wrapped.delivery_query(plan().parent,scope=scope,review=review,timeout_seconds=5)

    def test_unobserved_or_foreign_alias_never_queries_downstream(self):
        wrapped,client,scope,review=self.ready()
        with self.assertRaises(ConfigError):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        self.observe_delivery(wrapped,scope,review)
        for p in (replace(plan(),downstream_run_id='foreign'),replace(plan(),delivery_id='foreign'),
                  replace(plan(),baas_bot_id='foreign'),replace(plan(),target_bot_id='test-member')):
            with self.assertRaises(ConfigError):wrapped.downstream_observe(p,scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        client.downstream_query.assert_not_called()

    def test_exact_queue_binding_precedes_no_env_result_read_and_timeout_decreases(self):
        wrapped,client,scope,review=self.ready();self.observe_delivery(wrapped,scope,review)
        with patch('bcs_e2e.remote_observer.time.monotonic',side_effect=[1,2,3,4]):
            r=wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=5)
        calls=client.downstream_query.call_args_list
        self.assertEqual([c.kwargs['result'] for c in calls],[False,True])
        self.assertEqual([c.kwargs['timeout_seconds'] for c in calls],[4,2])
        self.assertTrue(r['known_run_result_completed']);self.assertEqual(r['scope_status'],'UNCONFIRMED')

    def test_empty_or_wrong_environment_queue_cannot_authorize_no_env_table(self):
        wrapped,client,scope,review=self.ready();self.observe_delivery(wrapped,scope,review)
        client.downstream_query.side_effect=lambda p,**kw:{'success':True,'data':[]}
        r=wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        self.assertEqual(r['queue_status'],'UNOBSERVED');self.assertEqual(client.downstream_query.call_count,1)
        client.downstream_query.reset_mock()
        client.downstream_query.side_effect=lambda p,**kw:{'success':True,'data':[queue(p,env='prod')]}
        with self.assertRaises(EvidenceError):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        self.assertEqual(client.downstream_query.call_count,1)

    def test_review_change_during_queue_read_blocks_result(self):
        wrapped,client,scope,review=self.ready();self.observe_delivery(wrapped,scope,review)
        def expire(p,**kwargs):
            v=read(review.path);v['expires_at']=v['reviewed_at'];write(review.path,v)
            return {'success':True,'data':[queue(p)]}
        client.downstream_query.side_effect=expire
        with self.assertRaises(ConfigError):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        self.assertEqual(client.downstream_query.call_count,1)

    def test_old_delivery_only_permission_never_authorizes_downstream(self):
        self.enable();scope,review=self.make_owned_scope();binding=Mock();binding.validate.return_value=self.receipt
        client=Mock();wrapped=ReviewedMcpClient(client,binding)
        with self.assertRaises(ConfigError):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        client.downstream_query.assert_not_called()

    def test_maximum_four_attempts_without_hidden_retry(self):
        wrapped,client,scope,review=self.ready();self.observe_delivery(wrapped,scope,review)
        for _ in range(4):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        with self.assertRaises(ConfigError):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        self.assertEqual(client.downstream_query.call_count,8)

    def test_missing_or_mismatched_queue_session_blocks_result(self):
        wrapped,client,scope,review=self.ready();self.observe_delivery(wrapped,scope,review)
        client.downstream_query.side_effect=lambda p,**kw:{'success':True,'data':[queue(p,session_id='foreign')]}
        with self.assertRaises(EvidenceError):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        self.assertEqual(client.downstream_query.call_count,1)

    def test_failed_new_delivery_read_invalidates_prior_alias_provenance(self):
        wrapped,client,scope,review=self.ready();self.observe_delivery(wrapped,scope,review)
        client.delivery_query.side_effect=RuntimeError('private-failed-read')
        with self.assertRaises(RuntimeError):self.observe_delivery(wrapped,scope,review)
        with self.assertRaises(ConfigError):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=time.monotonic()+5)
        client.downstream_query.assert_not_called()

    def test_expired_remaining_budget_does_not_read_no_env_result(self):
        wrapped,client,scope,review=self.ready();self.observe_delivery(wrapped,scope,review)
        with patch('bcs_e2e.remote_observer.time.monotonic',side_effect=[1,6]),self.assertRaises(TimeoutError):
            wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=5)
        self.assertEqual(client.downstream_query.call_count,1)
        for value in (0,-1,True,float('nan'),float('inf')):
            with self.assertRaises(ConfigError):wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=value)
        self.assertEqual(client.downstream_query.call_count,1)


    def test_prequery_validation_cannot_renew_absolute_deadline(self):
        wrapped,client,scope,review=self.ready();self.observe_delivery(wrapped,scope,review)
        clock=[4.99]
        validate=review.validate
        def slow_review():
            clock[0]+=0.1
            return validate()
        with patch.object(review,'validate',side_effect=slow_review),patch(
                'bcs_e2e.remote_observer.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(TimeoutError):
                wrapped.downstream_observe(plan(),scope=scope,review=review,deadline_monotonic=5)
        client.downstream_query.assert_not_called()



class DownstreamCompositionTests(unittest.TestCase):
    setUp=fixtures.RetainedDeliveryCompositionTests.setUp
    command=fixtures.RetainedDeliveryCompositionTests.command
    args=fixtures.RetainedDeliveryCompositionTests.args
    boundaries=fixtures.RetainedDeliveryCompositionTests.boundaries

    def enable(self, *, source='accepted-alias', case='RETAIN-GROUP-01'):
        fixtures.RetainedDeliveryCompositionTests.enable(self,case)
        self.review['downstream_observation']={'contract':CONTRACT,'run_id_source':source,
            'queue_enabled':True,'session_binding':'bcs-session-id',
            'bot_bindings':{'test-driver':'baas-driver','test-member':'baas-member'}}
        write(self.review_path,self.review)
        self.downstream_calls=[];self.queue_state='DONE';self.result_state='COMPLETED';self.alias_present=True

    def process(self,command,**kwargs):
        args=json.loads(command[4]) if '--args' in command else {}
        table=args.get('table')
        if table not in (QUEUE_TABLE,RESULT_TABLE):
            response=fixtures.RetainedDeliveryCompositionTests.process(self,command,**kwargs)
            if table=='bcs_message_deliveries' and command[2].endswith('.db_query'):
                payload=json.loads(response.stdout)
                for r in payload.get('data',[]):
                    r['run_id']='canonical-'+r['session_id']
                    r['downstream_run_id']='downstream-'+r['session_id'] if self.alias_present else None
                return subprocess.CompletedProcess(command,0,json.dumps(payload),'')
            return response
        self.calls.append(command)
        fields=QUEUE_FIELDS if table==QUEUE_TABLE else RESULT_FIELDS
        if command[2].endswith('.table_describe'):
            ddl='CREATE TABLE `'+table+'` ('+','.join('`'+f+'` varchar(128)' for f in fields)+')'
            data=[{'Table':table,'Create Table':ddl}]
        else:
            import re
            self.assertEqual(args['select'],','.join(fields));self.assertEqual(args['limit'],'2')
            prefix="env = 'pre' AND " if table==QUEUE_TABLE else ''
            match=re.fullmatch(prefix+r"run_id = '(downstream-|canonical-)([A-Za-z0-9_.:@-]+)' AND bot_id = 'baas-driver'",args['where'])
            self.assertIsNotNone(match);self.assertIn(match[2],self.adapter.sessions)
            self.downstream_calls.append((table,match[1]))
            if table==QUEUE_TABLE and self.queue_state=='missing':data=[]
            else:
                state=self.queue_state if table==QUEUE_TABLE else self.result_state
                if state=='cancel':raise KeyboardInterrupt()
                if state=='error':return subprocess.CompletedProcess(command,0,json.dumps({'success':False,'message':'private-query-error'}),'')
                value={'run_id':match[1]+match[2],'bot_id':'baas-driver','status':state}
                if table==QUEUE_TABLE:value.update(env='pre',session_id=match[2])
                data=[value]
        return subprocess.CompletedProcess(command,0,json.dumps({'success':True,'data':data}),'')

    def test_alias_route_observes_both_sessions_and_preserves_nonqualification(self):
        self.enable(case='RETAIN-SESSION-01')
        with self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(report['functional'],'PASS')
        observation=report['execution_observation']['downstream_observation']
        self.assertEqual(observation['status'],'OBSERVED')
        self.assertEqual(len(observation['runs']),2)
        self.assertTrue(all(r['known_run_result_completed'] for r in observation['runs']))
        self.assertTrue(all(r['callback_status']=='UNCONFIRMED' for r in observation['runs']))
        self.assertEqual(self.downstream_calls,[(QUEUE_TABLE,'downstream-'),(RESULT_TABLE,'downstream-')]*2)
        self.assertEqual(report['execution_status'],'UNCONFIRMED')
        self.assertEqual(report['socket_status'],'CLOSED')
        self.assertTrue(Path(read(self.state/'run.json')['lock_path']).exists())

    def test_canonical_bcn_binding_uses_canonical_not_attempt_or_ack_alias(self):
        self.enable(source='bcn-canonical');self.alias_present=False
        with self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(report['functional'],'PASS');self.assertEqual(rc,2)
        self.assertEqual(self.downstream_calls,[(QUEUE_TABLE,'canonical-'),(RESULT_TABLE,'canonical-')])

    def test_missing_alias_never_falls_back_to_canonical_id(self):
        self.enable();self.alias_present=False
        with self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(self.downstream_calls,[])
        observation=report['execution_observation']['downstream_observation']
        self.assertEqual(observation['status'],'INCOMPLETE')
        self.assertEqual(observation['missing_run_binding_delivery_ids'],['delivery-1'])

    def test_missing_queue_never_reads_no_env_result_or_claims_absent_execution(self):
        self.enable();self.queue_state='missing'
        with self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(self.downstream_calls,[(QUEUE_TABLE,'downstream-')])
        observation=report['execution_observation']['downstream_observation']
        self.assertEqual(observation['status'],'INCOMPLETE')
        self.assertEqual(observation['runs'][0]['result_status'],'UNOBSERVED')

    def test_downstream_error_keeps_functional_and_delivery_evidence(self):
        self.enable();self.result_state='error'
        with self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(report['functional'],'PASS')
        self.assertEqual(report['execution_observation']['status'],'BLOCKED')
        self.assertEqual(report['execution_observation']['sessions'][0]['known_completed_send_ids'],['delivery-1'])
        self.assertNotIn('private-query-error',json.dumps(report))
        partial=report['execution_observation']['downstream_observation']['runs'][0]
        self.assertEqual(partial['queue_status'],'DONE')
        self.assertEqual(partial['result_status'],'UNOBSERVED')

    def test_downstream_cancellation_closes_owned_ws_and_preserves_delivery(self):
        self.enable(case='RETAIN-SESSION-01');self.queue_state='cancel'
        with self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(rc,130);self.assertEqual(report['functional'],'PASS')
        self.assertEqual(report['socket_status'],'CLOSED')
        self.assertEqual(report['execution_observation']['downstream_observation']['status'],'CANCELLED')
        self.assertEqual(len(report['execution_observation']['sessions']),2)
        self.assertFalse(self.adapter.deleted_sessions)

    def test_old_delivery_permission_does_not_describe_or_query_baas_tables(self):
        self.enable();del self.review['downstream_observation'];write(self.review_path,self.review)
        with self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(self.downstream_calls,[])
        self.assertFalse(any(QUEUE_TABLE in c[4] or RESULT_TABLE in c[4] for c in self.calls if '--args' in c))
        self.assertNotIn('downstream_observation',report['execution_observation'])

    def test_mapping_requirements_are_explicit_not_inferred(self):
        self.enable()
        for key,value in (('queue_enabled',False),('session_binding','engine-session'),('run_id_source','guess')):
            review=json.loads(json.dumps(self.review));review['downstream_observation'][key]=value
            with self.assertRaises(ConfigError):validate_review(review,self.profile,self.run_id,review['case_id'])


    def test_downstream_report_counts_remain_separate_from_scope_qualification(self):
        from bcs_e2e.reporting import build_report,render_junit,render_markdown
        self.enable()
        with self.boundaries():rc,report=self.command(self.args())
        summary=build_report(self.profile,self.state)
        observation=summary['cases'][0]['execution_observation']
        self.assertEqual(observation['downstream_status'],'OBSERVED')
        self.assertEqual(observation['known_downstream_completed_count'],1)
        self.assertEqual(observation['downstream_callback_status'],'UNCONFIRMED')
        self.assertIn('execution_observation.known_downstream_completed_count',render_junit(summary))
        self.assertIn('known completed results=1',render_markdown(summary))
        self.assertNotEqual(summary['status'],'PILOT_PASS')

    def test_result_cancel_preserves_verified_queue_and_functional_pass(self):
        self.enable(case='RETAIN-SESSION-01');self.result_state='cancel'
        with self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(rc,130);self.assertEqual(report['functional'],'PASS')
        self.assertEqual(report['socket_status'],'CLOSED')
        observation=report['execution_observation']['downstream_observation']
        self.assertEqual(observation['status'],'CANCELLED')
        self.assertEqual(observation['runs'][0]['queue_status'],'DONE')
        self.assertEqual(observation['runs'][0]['result_status'],'UNOBSERVED')


    def test_baas_schema_failure_blocks_before_business_creation(self):
        self.enable()
        original=self.process
        def missing(command,**kwargs):
            args=json.loads(command[4]) if '--args' in command else {}
            if command[2].endswith('.table_describe') and args.get('table')==RESULT_TABLE:
                return subprocess.CompletedProcess(command,0,json.dumps({'success':False,'message':'private-schema'}),'')
            return original(command,**kwargs)
        with patch.object(self,'process',side_effect=missing),self.boundaries():rc,report=self.command(self.args())
        self.assertEqual(rc,2);self.assertFalse(report['prepared'])
        self.assertEqual(report['functional'],'NOT_RUN')
        self.assertFalse(self.state.exists());self.assertEqual(self.downstream_calls,[])
        self.assertFalse(any(kind=='write' for kind,_ in self.http))
        self.assertNotIn('private-schema',json.dumps(report))


class DownstreamReturnBoundaryTests(unittest.TestCase):
    def observe_at_boundary(self, failure, *, late=False):
        from bcs_e2e.delivery_observation import DeliveryObserver
        p=plan();clock=[1.0];returned=[False]
        client=Mock()
        def query(*args,**kwargs):
            self.assertEqual(kwargs['deadline_monotonic'],5)
            returned[0]=True
            if late:clock[0]=6
            return correlate(p,queue(p),result(p))
        client.downstream_observe.side_effect=query
        def active():
            if returned[0] and not late:raise failure
        observer=DeliveryObserver(client=client,database='testdb',environment='pre',
            allowed_bots=frozenset(p.parent.target_bot_ids),downstream_config={
                'run_id_source':'accepted-alias','bot_bindings':{'test-driver':'baas-driver'}})
        report={'sessions':[{'group_id':'group-1','session_id':'session-1','rows':[
            fixtures.row(p.parent,target_bot_id='test-driver',downstream_run_id='downstream-1')]}],
            'downstream_observation':{'runs':[],'missing_run_binding_delivery_ids':[]}}
        with patch('bcs_e2e.delivery_observation.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(type(failure)):
                observer._observe_downstream(report,object(),active,0,5)
        partial=report['downstream_observation']['runs'][0]
        self.assertEqual(partial['queue_status'],'DONE')
        self.assertEqual(partial['result_status'],'UNOBSERVED')
        self.assertFalse(partial['known_run_result_completed'])
        self.assertEqual(partial['scope_status'],'UNCONFIRMED')

    def test_return_boundary_cancel_preserves_queue_not_completion(self):
        self.observe_at_boundary(KeyboardInterrupt())

    def test_return_boundary_authority_loss_preserves_queue_not_completion(self):
        self.observe_at_boundary(ConfigError('lease lost'))

    def test_late_result_preserves_queue_not_completion(self):
        self.observe_at_boundary(TimeoutError(),late=True)
