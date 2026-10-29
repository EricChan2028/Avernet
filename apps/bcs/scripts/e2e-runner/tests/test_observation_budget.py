"""Reviewed wait-v2 separates the observation window from per-request limits."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.alisddbquery import McporterAlisdDbQueryClient
from bcs_e2e.delivery_observation import CONTRACT, WAIT_CONTRACT, DeliveryObserver
from bcs_e2e.observation_budget import BUDGETED_WAIT_CONTRACT, reviewed_budget
from bcs_e2e.profile import ConfigError
from bcs_e2e.retained_remote import RetainedMcpClient, validate_review
from bcs_e2e.run_state import read, write
import test_delivery_observation as fixtures
import test_downstream_observation as downstream

BUDGET = {'timeout_seconds': 120, 'max_rounds': 6, 'poll_interval_seconds': 10}


class BudgetValidationTests(unittest.TestCase):
    def test_legacy_has_no_implicit_budget_and_v2_requires_all_fields(self):
        for contract in (CONTRACT, WAIT_CONTRACT):
            self.assertIsNone(reviewed_budget(contract, None))
            with self.assertRaises(ConfigError): reviewed_budget(contract, BUDGET)
        for invalid in (None, {}, {**BUDGET, 'extra': 1}, {k:v for k,v in BUDGET.items() if k != 'max_rounds'}):
            with self.subTest(value=invalid), self.assertRaises(ConfigError):
                reviewed_budget(BUDGETED_WAIT_CONTRACT, invalid)

    def test_strict_integer_bounds_and_detached_value(self):
        for field, values in (
                ('timeout_seconds', [0, 301, True, 1.0, '120', float('nan')]),
                ('max_rounds', [0, 1, 7, True, 6.0, '6']),
                ('poll_interval_seconds', [0, 31, True, 1.0, '10'])):
            for value in values:
                with self.subTest(field=field,value=value), self.assertRaises(ConfigError):
                    reviewed_budget(BUDGETED_WAIT_CONTRACT, {**BUDGET, field:value})
        original = dict(BUDGET)
        parsed = reviewed_budget(BUDGETED_WAIT_CONTRACT, original)
        original['max_rounds'] = 100
        self.assertEqual(parsed.public(), BUDGET)
        for value in ({'timeout_seconds':1,'max_rounds':2,'poll_interval_seconds':1},
                      {'timeout_seconds':300,'max_rounds':6,'poll_interval_seconds':30}):
            self.assertEqual(reviewed_budget(BUDGETED_WAIT_CONTRACT,value).public(),value)

    def test_mcp_still_caps_each_call_to_its_transport_timeout(self):
        runner = Mock(return_value=subprocess.CompletedProcess([],0,'{"success":true,"data":[]}', ''))
        client = McporterAlisdDbQueryClient(server='test-observer', timeout_seconds=2, runner=runner)
        client.delivery_query(downstream.plan().parent, timeout_seconds=120)
        self.assertEqual(runner.call_args.kwargs['timeout'],2)
        client.delivery_query(downstream.plan().parent, timeout_seconds=0.5)
        self.assertEqual(runner.call_args.kwargs['timeout'],0.5)

    def test_shared_phase_budget_refuses_thirty_third_call_before_transport(self):
        raw, review = Mock(), Mock()
        client = RetainedMcpClient(raw, review)
        for _ in range(32): client.table_describe('testdb','bcs_message_deliveries')
        from bcs_e2e.evidence import EvidenceError
        with self.assertRaises(EvidenceError): client.table_describe('testdb','bcs_message_deliveries')
        self.assertEqual(raw.table_describe.call_count,32)


class BudgetObserverTests(unittest.TestCase):
    setUp = fixtures.DeliveryObserverTests.setUp

    def run_observer(self, *, budget=None, completed_round=99, sleep_error=None):
        self.clock = 0.0
        self.query_calls = []
        budget = dict(BUDGET) if budget is None else budget
        def query(plan, **kwargs):
            round_number = len(self.query_calls)//2 + 1
            self.query_calls.append(kwargs)
            self.clock += 1
            status = 'completed' if round_number >= completed_round else 'running'
            return {'success':True, 'data':[fixtures.row(plan,status=status,state_version=round_number,
                    terminal_at_ms=100 if status=='completed' else None)]}
        def sleep(seconds):
            self.clock += seconds
            if sleep_error is not None: raise sleep_error
        self.client.delivery_query.side_effect = query
        observer = DeliveryObserver(client=self.client,database='testdb',environment='pre',
            allowed_bots=frozenset({'bot-1','bot-2'}),contract=BUDGETED_WAIT_CONTRACT,observation_budget=budget)
        observer.admit()
        with patch('bcs_e2e.delivery_observation.time.monotonic',side_effect=lambda:self.clock), \
             patch('bcs_e2e.delivery_observation.time.sleep',side_effect=sleep):
            return observer.observe(self.ledger,self.scoped.observation_scope,self.active,
                                    lease=self.lease,case_id=self.case,timeout_seconds=2)

    def test_natural_completion_beyond_http_window_is_observed_not_scope_pass(self):
        report = self.run_observer(completed_round=4)
        self.assertGreater(self.clock,10)
        self.assertEqual(report['status'],'OBSERVED')
        self.assertEqual(report['stop_reason'],'KNOWN_DELIVERIES_SETTLED')
        self.assertEqual(report['queries'],8)
        self.assertEqual(report['observation_budget'],BUDGET)
        self.assertEqual(report['scope_status'],'UNCONFIRMED')
        self.assertEqual({q['deadline_monotonic'] for q in self.query_calls},{120})
        self.assertTrue(all(a['timeout_seconds'] > b['timeout_seconds']
                            for a,b in zip(self.query_calls,self.query_calls[1:])))

    def test_six_rounds_and_two_sessions_are_bounded_at_twelve_queries(self):
        report = self.run_observer()
        self.assertEqual(report['queries'],12)
        self.assertEqual(len(report['rounds']),6)
        self.assertEqual(report['stop_reason'],'OBSERVATION_LIMIT')
        self.assertEqual(report['scope_status'],'UNCONFIRMED')

    def test_backoff_uses_only_remaining_deadline_and_does_not_restart_window(self):
        report = self.run_observer(budget={**BUDGET,'timeout_seconds':5,'poll_interval_seconds':30})
        self.assertEqual(self.clock,5)
        self.assertEqual(report['status'],'TIMED_OUT')
        self.assertEqual(report['queries'],2)
        self.assertEqual(len(report['rounds']),1)

    def test_cancel_in_long_backoff_keeps_last_valid_evidence(self):
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.run_observer(sleep_error=KeyboardInterrupt())
        report = caught.exception.execution_observation
        self.assertEqual(report['status'],'CANCELLED')
        self.assertEqual(report['queries'],2)
        self.assertEqual(len(report['sessions']),2)
        self.assertEqual(report['observation_budget'],BUDGET)

    def test_late_second_session_keeps_first_session_without_accepting_late_rows(self):
        report = self.run_observer(budget={**BUDGET,'timeout_seconds':2})
        self.assertEqual(report['status'],'TIMED_OUT')
        self.assertEqual(report['queries'],2)
        self.assertEqual(len(report['sessions']),1)
        self.assertEqual(report['sessions'][0]['observed_round'],1)


class BudgetQueryBoundaryTests(unittest.TestCase):
    setUp = downstream.DownstreamScopeTests.setUp
    def enable(self):
        fixtures.RetainedDeliveryCompositionTests.enable(self)
        contract = getattr(self, 'review_contract', BUDGETED_WAIT_CONTRACT)
        self.review['execution_observation'] = contract
        if contract == BUDGETED_WAIT_CONTRACT:
            self.review['execution_observation_budget'] = dict(BUDGET)
        write(self.review_path,self.review)

    make_owned_scope = downstream.DownstreamScopeTests.make_owned_scope
    ready = downstream.DownstreamScopeTests.ready

    def test_expensive_authorization_cannot_extend_delivery_deadline(self):
        wrapped,raw,scope,review = self.ready()
        clock = [4.9]
        original = review.validate
        def delayed():
            clock[0] += 0.2
            return original()
        with patch.object(review,'validate',side_effect=delayed), \
             patch('bcs_e2e.remote_observer.time.monotonic',side_effect=lambda:clock[0]), \
             self.assertRaises(TimeoutError):
            wrapped.delivery_query(downstream.plan().parent,review=review,scope=scope,
                                   timeout_seconds=100,deadline_monotonic=5)
        raw.delivery_query.assert_not_called()

    def test_late_query_result_is_not_cached_as_downstream_provenance(self):
        wrapped,raw,scope,review = self.ready()
        with patch('bcs_e2e.remote_observer.time.monotonic',side_effect=[1,6]), \
             self.assertRaises(TimeoutError):
            wrapped.delivery_query(downstream.plan().parent,review=review,scope=scope,
                                   timeout_seconds=100,deadline_monotonic=5)
        self.assertEqual(raw.delivery_query.call_args.kwargs['timeout_seconds'],4)
        self.assertEqual(wrapped._delivery_evidence,{})


    def test_v2_missing_or_none_deadline_is_refused_before_transport(self):
        wrapped,raw,scope,review = self.ready()
        for kwargs in ({}, {'deadline_monotonic':None}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ConfigError):
                wrapped.delivery_query(downstream.plan().parent,review=review,scope=scope,
                                       timeout_seconds=10,**kwargs)
        raw.delivery_query.assert_not_called()

    def test_v2_invalid_and_expired_deadlines_do_not_read(self):
        wrapped,raw,scope,review = self.ready()
        for deadline in (True, '10', float('nan'), float('inf')):
            with self.subTest(deadline=deadline),self.assertRaises(ConfigError):
                wrapped.delivery_query(downstream.plan().parent,review=review,scope=scope,
                                       timeout_seconds=10,deadline_monotonic=deadline)
        with patch('bcs_e2e.remote_observer.time.monotonic',return_value=10),self.assertRaises(TimeoutError):
            wrapped.delivery_query(downstream.plan().parent,review=review,scope=scope,
                                   timeout_seconds=10,deadline_monotonic=5)
        raw.delivery_query.assert_not_called()

    def legacy_without_deadline(self, contract):
        self.review_contract=contract
        wrapped,raw,scope,review = self.ready()
        wrapped.delivery_query(downstream.plan().parent,review=review,scope=scope,timeout_seconds=10)
        self.assertEqual(raw.delivery_query.call_count,1)
        self.assertEqual(raw.delivery_query.call_args.kwargs['timeout_seconds'],10)

    def test_snapshot_v1_still_accepts_relative_timeout(self):
        self.legacy_without_deadline(CONTRACT)

    def test_wait_v1_still_accepts_relative_timeout(self):
        self.legacy_without_deadline(WAIT_CONTRACT)


class BudgetCompositionTests(unittest.TestCase):
    setUp = fixtures.RetainedDeliveryCompositionTests.setUp
    command = fixtures.RetainedDeliveryCompositionTests.command
    args = fixtures.RetainedDeliveryCompositionTests.args
    boundaries = fixtures.RetainedDeliveryCompositionTests.boundaries
    process = fixtures.RetainedDeliveryCompositionTests.process

    def enable_budget(self, case='RETAIN-GROUP-01'):
        fixtures.RetainedDeliveryCompositionTests.enable(self,case)
        self.review.update(execution_observation=BUDGETED_WAIT_CONTRACT,
                           execution_observation_budget=dict(BUDGET))
        write(self.review_path,self.review)
        self.delivery_response = 'running'

    def test_invalid_or_unreviewed_budget_is_blocked_before_http(self):
        self.enable_budget()
        for contract,budget in ((WAIT_CONTRACT,BUDGET),(CONTRACT,BUDGET),(WAIT_CONTRACT,None),
                                (BUDGETED_WAIT_CONTRACT,None),(BUDGETED_WAIT_CONTRACT,{**BUDGET,'max_rounds':7})):
            value = copy.deepcopy(self.review)
            value.update(execution_observation=contract,execution_observation_budget=budget)
            with self.subTest(contract=contract,budget=budget),self.assertRaises(ConfigError):
                validate_review(value,self.profile,self.run_id,value['case_id'])
        del self.review['execution_observation_budget']
        write(self.review_path,self.review)
        with self.boundaries(): rc,report=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.http,[])

    def test_real_composition_six_rounds_and_reports_keep_pool_quarantined(self):
        from bcs_e2e.reporting import build_report, render_markdown, render_junit
        self.enable_budget('RETAIN-SESSION-01')
        with self.boundaries(),patch('bcs_e2e.delivery_observation.time.sleep'):
            rc,report=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(report['functional'],'PASS')
        self.assertEqual(report['execution_observation']['queries'],12)
        self.assertEqual(report['execution_status'],'UNCONFIRMED')
        self.assertTrue(Path(read(self.state/'run.json')['lock_path']).exists())
        self.assertFalse(self.adapter.deleted_sessions)
        summary=build_report(self.profile,self.state)
        observation=summary['cases'][1]['execution_observation']
        self.assertEqual(observation['observation_rounds'],6)
        self.assertEqual(observation['observation_timeout_seconds'],120)
        self.assertEqual(observation['maximum_observation_rounds'],6)
        self.assertIn('observation budget=120s',render_markdown(summary))
        self.assertIn('execution_observation.observation_timeout_seconds',render_junit(summary))
        self.assertNotEqual(summary['status'],'PILOT_PASS')

    def test_receipt_budget_mutation_between_rounds_is_not_renewal(self):
        self.enable_budget()
        def mutate(_):
            value=read(self.state/'retained-review.json')
            value['execution_observation_budget']['timeout_seconds']=300
            write(self.state/'retained-review.json',value)
        with self.boundaries(),patch('bcs_e2e.delivery_observation.time.sleep',side_effect=mutate):
            rc,report=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(report['functional'],'PASS')
        self.assertEqual(report['execution_observation']['status'],'BLOCKED')
        self.assertEqual(report['execution_observation']['queries'],2)  # attempt is counted; query is denied
        self.assertEqual(len(self.delivery_queries),1)
        self.assertEqual(report['execution_status'],'UNCONFIRMED')
        self.assertFalse(self.adapter.deleted)

    def test_legacy_report_cannot_claim_v2_round_count_or_budget(self):
        from bcs_e2e.reporting import _execution_observation_summary
        value={'status':'OBSERVED','scope_status':'UNCONFIRMED','sessions':[],
               'contract':WAIT_CONTRACT,'rounds':[{}]*6,'stop_reason':'OBSERVATION_LIMIT'}
        self.assertEqual(_execution_observation_summary(value)['status'],'INVALID')
        value['observation_budget']=dict(BUDGET)
        self.assertEqual(_execution_observation_summary(value)['status'],'INVALID')


class BudgetDownstreamCompositionTests(unittest.TestCase):
    setUp = downstream.DownstreamCompositionTests.setUp
    command = downstream.DownstreamCompositionTests.command
    args = downstream.DownstreamCompositionTests.args
    boundaries = downstream.DownstreamCompositionTests.boundaries

    def process(self, command, **kwargs):
        args=json.loads(command[4]) if '--args' in command else {}
        table=args.get('table')
        if table=='bcs_message_deliveries' and command[2].endswith('.db_query'):
            response=downstream.DownstreamCompositionTests.process(self,command,**kwargs)
            payload=json.loads(response.stdout)
            first=payload['data'][0]
            member={**first,'delivery_id':'delivery-member','target_bot_id':'test-member',
                    'run_id':first['run_id']+'-member','downstream_run_id':first['downstream_run_id']+'-member'}
            payload['data'].append(member)
            for row in payload['data']:
                baas='baas-driver' if row['target_bot_id']=='test-driver' else 'baas-member'
                self.bindings[row['downstream_run_id']]=(row['session_id'],baas)
            return subprocess.CompletedProcess(command,0,json.dumps(payload),'')
        if table in (downstream.QUEUE_TABLE,downstream.RESULT_TABLE) and command[2].endswith('.db_query'):
            import re
            match=re.fullmatch(("env = 'pre' AND " if table==downstream.QUEUE_TABLE else '')+
                              r"run_id = '([A-Za-z0-9_.:@-]+)' AND bot_id = '(baas-driver|baas-member)'",args['where'])
            self.assertIsNotNone(match)
            session,baas=self.bindings[match[1]]
            self.assertEqual(match[2],baas)
            self.assertLessEqual(kwargs['timeout'],self.profile['policy']['timeout_seconds'])
            self.calls.append(command)
            self.downstream_calls.append(table)
            row={'run_id':match[1],'bot_id':baas,'status':'COMPLETED'}
            if table==downstream.QUEUE_TABLE:row.update(env='pre',session_id=session,status='DONE')
            return subprocess.CompletedProcess(command,0,json.dumps({'success':True,'data':[row]}),'')
        return downstream.DownstreamCompositionTests.process(self,command,**kwargs)

    def test_maximum_two_sessions_four_runs_uses_thirty_one_execution_calls(self):
        downstream.DownstreamCompositionTests.enable(self,case='RETAIN-SESSION-01')
        self.bindings={}
        self.delivery_response='running'
        self.review.update(execution_observation=BUDGETED_WAIT_CONTRACT,execution_observation_budget=dict(BUDGET))
        write(self.review_path,self.review)
        clients=[]
        original=RetainedMcpClient.__init__
        def track(client,*args,**kwargs):
            original(client,*args,**kwargs)
            clients.append(client)
        with patch.object(RetainedMcpClient,'__init__',track),self.boundaries(), \
             patch('bcs_e2e.delivery_observation.time.sleep'):
            rc,report=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(report['functional'],'PASS')
        self.assertEqual(len(self.delivery_queries),12)
        self.assertEqual(len(self.downstream_calls),8)
        self.assertEqual(len(self.calls),38)  # 7 preflight + 31 execution, including repeated describes.
        self.assertEqual(min(client._remaining for client in clients),1)
        observed=report['execution_observation']['downstream_observation']
        self.assertEqual(observed['status'],'OBSERVED')
        self.assertEqual(len(observed['runs']),4)
        self.assertEqual(report['execution_status'],'UNCONFIRMED')
        self.assertEqual(report['pool_disposition'],'QUARANTINED')
        self.assertFalse(self.adapter.deleted_sessions)
