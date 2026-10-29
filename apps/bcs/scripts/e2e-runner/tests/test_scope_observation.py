"""Exact Group/Session shape reads under separate recheck authorization."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.scope_observation import (CONTRACT, GROUP_TABLE, GROUP_FIELDS, SESSION_TABLE,
    SESSION_FIELDS, query_arguments, project_row)
from bcs_e2e.delivery_observation import DeliveryReadPlan
from bcs_e2e.evidence import EvidenceError
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read, write
import test_retained_recheck as fixtures


def plan():return DeliveryReadPlan('testdb','pre','group-1','session-1',('driver','member'))
def profile():return {'human':{'user_id':'owner'},'actors':{'driver':{'bot_id':'driver'}}}
def group_row():return dict(group_id='group-1',env='pre',group_kind='normal',group_strategy='chat',status='active',originator='human_owner',driver_bot='driver')
def session_row():return dict(session_id='session-1',group_id='group-1',env='pre',session_kind='chat',status='running',activation_count=1,callback_status=None,completed_at=None)


class ScopeProjectionTests(unittest.TestCase):
    def test_fixed_query_uses_only_exact_env_and_owned_ids(self):
        g=query_arguments(plan(),group=True);s=query_arguments(plan(),group=False)
        self.assertEqual(g['select'],','.join(GROUP_FIELDS));self.assertEqual(s['select'],','.join(SESSION_FIELDS))
        self.assertEqual(g['where'],"env = 'pre' AND group_id = 'group-1'")
        self.assertEqual(s['where'],g['where']+" AND session_id = 'session-1'")
        self.assertEqual(g['limit'],'2');self.assertEqual(s['limit'],'2')
        for field in ('input','output','meta','callback_lease_token','callback_lease_owner','participants'):
            self.assertNotIn(field,SESSION_FIELDS)
        with self.assertRaises(ConfigError):query_arguments(replace(plan(),session_id="x' OR 1=1"),group=False)
        with self.assertRaises(EvidenceError):query_arguments(plan(),group='session')

    def test_only_complete_exact_rows_are_projected(self):
        for group, row in ((True,group_row()),(False,session_row())):
            expected=row.copy();row['private']='do-not-record'
            self.assertEqual(project_row(plan(),{'success':True,'data':[row]},group=group,profile=profile()),expected)
            for raw in ({'success':True,'data':[]},{'success':False,'data':[]},
                        {'success':True,'data':[row,row]},{'success':True,'data':[row],'truncated':True}):
                with self.assertRaises(EvidenceError):project_row(plan(),raw,group=group,profile=profile())
            for key,value in (('env','prod'),('group_id','foreign')):
                with self.assertRaises(EvidenceError):
                    project_row(plan(),{'success':True,'data':[{**row,key:value}]},group=group,profile=profile())

    def test_owner_session_activation_and_callback_validation(self):
        for group,changes in ((True,{'originator':'human_foreign'}),(True,{'driver_bot':'foreign'}),
            (False,{'session_id':'foreign'}),(False,{'activation_count':True}),(False,{'activation_count':0}),
            (False,{'callback_status':'mystery'}),(False,{'completed_at':True}),(False,{'session_kind':'unknown'})):
            row=group_row() if group else session_row()
            with self.assertRaises(EvidenceError):
                project_row(plan(),{'success':True,'data':[{**row,**changes}]},group=group,profile=profile())


class ScopeRecheckTests(unittest.TestCase):
    command=fixtures.RetainedRecheckTests.command
    boundaries=fixtures.RetainedRecheckTests.boundaries
    args=fixtures.RetainedRecheckTests.args
    execute=fixtures.RetainedRecheckTests.execute
    assert_original_unchanged=fixtures.RetainedRecheckTests.assert_original_unchanged

    def setUp(self):
        self.scope_enabled=False
        fixtures.RetainedRecheckTests.setUp(self)
        self.scope_enabled=True;self.scope_calls=[];self.scope_failure=None
        self.recheck['scope_observation']=CONTRACT
        write(self.recheck_path,self.recheck)

    def process(self,command,**kwargs):
        args=json.loads(command[4]) if '--args' in command else {}
        table=args.get('table')
        if not self.scope_enabled or table not in (GROUP_TABLE,SESSION_TABLE):
            return fixtures.RetainedRecheckTests.process(self,command,**kwargs)
        self.calls.append(command)
        fields=GROUP_FIELDS if table==GROUP_TABLE else SESSION_FIELDS
        if command[2].endswith('.table_describe'):
            return subprocess.CompletedProcess(command,0,json.dumps({'success':True,'data':[{
                'Table':table,'Create Table':'CREATE TABLE `'+table+'` ('+','.join('`'+f+'` varchar(128)' for f in fields)+')'}]}),'')
        self.scope_calls.append(args)
        if self.scope_failure is not None and table==SESSION_TABLE:raise self.scope_failure
        resources=read(self.state/'retention.json')['resources']
        g=next(r['resource_id'] for r in resources if r['kind']=='group')
        s=next(r['resource_id'] for r in resources if r['kind']=='session')
        self.assertEqual(args['where'],f"env = 'pre' AND group_id = '{g}'"+(f" AND session_id = '{s}'" if table==SESSION_TABLE else ''))
        self.assertEqual(args['select'],','.join(fields));self.assertEqual(args['limit'],'2')
        row=group_row() if table==GROUP_TABLE else session_row()
        row.update(group_id=g)
        if table==GROUP_TABLE:row.update(originator='human_test-owner',driver_bot='test-driver')
        else:row.update(session_id=s)
        return subprocess.CompletedProcess(command,0,json.dumps({'success':True,'data':[row]}),'')

    def test_shape_facts_are_independent_not_producer_completion(self):
        rc,report=self.execute()
        self.assertEqual(rc,2,report)
        facts=report['execution_observation']['scope_observation']
        self.assertEqual(facts['status'],'OBSERVED',report)
        self.assertEqual(facts['queries'],2)
        self.assertEqual(facts['group']['group_strategy'],'chat')
        self.assertEqual(facts['sessions'][0]['activation_count'],1)
        self.assertEqual(facts['producer_coverage'],'NOT_VERIFIED')
        self.assertFalse(facts['pool_reuse_authorized'])
        self.assertEqual(len(self.calls),6)  # three describes, two facts, one delivery read
        self.assert_original_unchanged()

    def test_no_new_permission_does_not_read_or_describe_shape_tables(self):
        del self.recheck['scope_observation'];write(self.recheck_path,self.recheck)
        rc,report=self.execute()
        self.assertEqual(rc,2)
        self.assertEqual(self.scope_calls,[])
        self.assertFalse(any(json.loads(c[4]).get('table') in (GROUP_TABLE,SESSION_TABLE) for c in self.calls))
        self.assertNotIn('scope_observation',report['execution_observation'])
        self.assert_original_unchanged()

    def test_second_shape_query_failure_keeps_first_group_projection(self):
        from bcs_e2e.bounded_process import ProcessCaptureTimeout
        self.scope_failure=ProcessCaptureTimeout('private-timeout')
        rc,report=self.execute()
        self.assertEqual(rc,2)
        obs=report['execution_observation'];facts=obs['scope_observation']
        self.assertEqual(obs['status'],'TIMED_OUT')
        self.assertEqual(facts['status'],'INCOMPLETE')
        self.assertIsNotNone(facts['group']);self.assertEqual(facts['sessions'],[])
        self.assertEqual(self.delivery_queries,[])
        self.assert_original_unchanged()

    def test_cancelled_shape_query_keeps_partial_and_no_delivery_read(self):
        self.scope_failure=KeyboardInterrupt()
        rc,report=self.execute()
        self.assertEqual(rc,130)
        self.assertEqual(report['execution_observation']['scope_observation']['status'],'CANCELLED')
        self.assertIsNotNone(report['execution_observation']['scope_observation']['group'])
        self.assertEqual(self.delivery_queries,[])
        self.assert_original_unchanged()

    def test_scope_query_is_not_admitted_for_original_write_review(self):
        from bcs_e2e.retained_remote import validate_review
        value=read(self.state/'retained-review.json');value['scope_observation']=CONTRACT
        with self.assertRaises(ConfigError):validate_review(value,self.profile,self.run_id,'RETAIN-GROUP-01')

    def test_scope_facts_consume_original_window_before_delivery_reads(self):
        original=self.process;clock=[0.0]
        def slow(command,**kwargs):
            response=original(command,**kwargs)
            if command[2].endswith('.db_query'):clock[0]+=20
            return response
        with patch.object(self,'process',side_effect=slow),patch('time.monotonic',side_effect=lambda:clock[0]):
            rc,report=self.execute()
        self.assertEqual(rc,2)
        self.assertEqual(report['execution_observation']['status'],'TIMED_OUT')
        self.assertEqual(self.delivery_queries,[])
        self.assertEqual(len(report['execution_observation']['scope_observation']['sessions']),0)
        self.assert_original_unchanged()


class ScopeReturnBoundaryTests(unittest.TestCase):
    def test_last_scope_query_returned_in_time_but_postcheck_expires(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from bcs_e2e.scope_observation import observe_facts
        clock=[0.0];count=[0]
        def query(*args,**kwargs):
            count[0]+=1
            return group_row() if kwargs['group'] else session_row()
        def active():
            if count[0]==2:clock[0]=11.0
        observer=SimpleNamespace(database='testdb',environment='pre',allowed_bots={'driver','member'},
                                 client=SimpleNamespace(scope_query=query))
        report={'scope_observation':{'status':'NOT_RUN','queries':0,'group':None,'sessions':[]}}
        scope=Mock(return_value=({'resource_id':'session-1'},{'resource_id':'group-1'}))
        with patch('bcs_e2e.scope_observation.time.monotonic',side_effect=lambda:clock[0]),self.assertRaises(TimeoutError):
            observe_facts(observer,[{'resource_id':'session-1'}],scope,object(),active,report,10)
        self.assertEqual(report['scope_observation']['queries'],2)
        self.assertEqual(report['scope_observation']['group'],group_row())
        self.assertEqual(report['scope_observation']['sessions'],[])
        self.assertNotEqual(report['scope_observation']['status'],'OBSERVED')
