"""Scoped producer inventory uses synthetic transports, never live databases."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.producer_inventory import CONTRACT, TABLE, FIELDS, MAX_RUNS, query_arguments, project_rows
from bcs_e2e.scope_observation import GROUP_TABLE, GROUP_FIELDS, group_fields, project_row
from bcs_e2e.evidence import EvidenceError
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read, write
import test_scope_observation as fixtures


def row():
    return dict(id=1, env='pre', group_id='group-1', session_id='session-1', run_id='sm-1',
                record_status='active', status='pending', session_activation_count=None,
                root_run_id='sm-1', rerun_of=None, updated_at_ms=100, completed_at_ms=None)


class ProducerProjectionTests(unittest.TestCase):
    def test_fixed_scope_and_complete_history_not_latest_or_activation_filtered(self):
        args = query_arguments(fixtures.plan())
        self.assertEqual(args['where'], "env = 'pre' AND group_id = 'group-1' AND session_id = 'session-1'")
        self.assertEqual(args['select'], ','.join(FIELDS))
        self.assertEqual(args['limit'], '21'); self.assertEqual(args['orderby'], 'id asc')
        for key in ('record_status', 'session_activation_count', 'status'):
            self.assertNotIn(key, args['where'])
        for key in ('input_json', 'output_text', 'error_message', 'definition'):
            self.assertNotIn(key, args['select'])
        with self.assertRaises(ConfigError): query_arguments(replace(fixtures.plan(), session_id="x'"))

    def test_chat_one_shot_null_activation_and_legacy_null_lineage_preserved(self):
        value = row(); value.update(root_run_id=None, record_status='deleted', input_json='private')
        projected = project_rows(fixtures.plan(), {'success': True, 'data': [value]})
        self.assertIsNone(projected[0]['session_activation_count'])
        self.assertIsNone(projected[0]['root_run_id'])
        self.assertEqual(projected[0]['record_status'], 'deleted')
        self.assertNotIn('private', json.dumps(projected))

    def test_only_strict_ordered_exact_rows_accepted(self):
        for edit in ({'id': True}, {'id': 0}, {'env': 'prod'}, {'group_id': 'foreign'},
                     {'session_id': 'foreign'}, {'status': 'unknown'}, {'run_id': 'bad id'},
                     {'session_activation_count': True}, {'session_activation_count': 0},
                     {'updated_at_ms': True}, {'completed_at_ms': -1}, {'rerun_of': {}},
                     {'record_status': None}, {'root_run_id': 'bad id'}):
            with self.subTest(edit=edit), self.assertRaises((EvidenceError, ConfigError)):
                project_rows(fixtures.plan(), {'success': True, 'data': [{**row(), **edit}]})
        for rows in ([row(), row()], [row(), {**row(), 'id': 2}],
                     [{**row(), 'id': 2}, {**row(), 'run_id': 'sm-2'}]):
            with self.assertRaises(EvidenceError): project_rows(fixtures.plan(), {'success': True, 'data': rows})

    def test_empty_complete_and_limit_plus_one_are_distinct(self):
        self.assertEqual(project_rows(fixtures.plan(), {'success': True, 'data': []}), [])
        rows = [{**row(), 'id': n+1, 'run_id': 'sm-'+str(n)} for n in range(MAX_RUNS)]
        self.assertEqual(len(project_rows(fixtures.plan(), {'success': True, 'data': rows})), 20)
        for payload in ({'success': False, 'data': []}, {'success': True, 'data': [], 'truncated': True},
                        {'success': True, 'data': [], 'truncated': 1},
                        {'success': True, 'data': rows + [{**row(), 'id': 21, 'run_id': 'sm-21'}]}):
            with self.assertRaises(EvidenceError): project_rows(fixtures.plan(), payload)

    def test_service_mode_requires_new_contract_and_strict_scalar(self):
        self.assertEqual(group_fields(fixtures.CONTRACT), GROUP_FIELDS)
        self.assertEqual(group_fields(CONTRACT), GROUP_FIELDS + ('service_mode',))
        for value in (None, 'master_slave', 'other'):
            result = project_row(fixtures.plan(), {'success': True, 'data': [
                {**fixtures.group_row(), 'service_mode': value}]}, group=True,
                profile=fixtures.profile(), contract=CONTRACT)
            self.assertEqual(result['service_mode'], value)
        for value in (False, '', {}, ['master_slave']):
            with self.assertRaises(EvidenceError):
                project_row(fixtures.plan(), {'success': True, 'data': [
                    {**fixtures.group_row(), 'service_mode': value}]}, group=True,
                    profile=fixtures.profile(), contract=CONTRACT)
        with self.assertRaises(EvidenceError):
            project_row(fixtures.plan(), {'success': True, 'data': [fixtures.group_row()]},
                        group=True, profile=fixtures.profile(), contract=CONTRACT)


class ProducerRecheckTests(unittest.TestCase):
    command = fixtures.ScopeRecheckTests.command
    boundaries = fixtures.ScopeRecheckTests.boundaries
    args = fixtures.ScopeRecheckTests.args
    execute = fixtures.ScopeRecheckTests.execute
    assert_original_unchanged = fixtures.ScopeRecheckTests.assert_original_unchanged

    def setUp(self):
        self.producer_enabled = False
        fixtures.ScopeRecheckTests.setUp(self)
        self.producer_enabled = True
        self.producer_rows = []; self.producer_calls = []; self.producer_error = None
        self.recheck['scope_observation'] = CONTRACT
        write(self.recheck_path, self.recheck)

    def process(self, command, **kwargs):
        args = json.loads(command[4]) if '--args' in command else {}
        table = args.get('table')
        if not self.producer_enabled or table not in (TABLE, GROUP_TABLE):
            return fixtures.ScopeRecheckTests.process(self, command, **kwargs)
        self.calls.append(command)
        fields = FIELDS if table == TABLE else group_fields(CONTRACT)
        if command[2].endswith('.table_describe'):
            return subprocess.CompletedProcess(command, 0, json.dumps({'success': True, 'data': [{
                'Table': table, 'Create Table': 'CREATE TABLE `'+table+'` ('+
                ','.join('`'+f+'` varchar(128)' for f in fields)+')'}]}), '')
        resources = read(self.state/'retention.json')['resources']
        group = next(r['resource_id'] for r in resources if r['kind'] == 'group')
        session = next(r['resource_id'] for r in resources if r['kind'] == 'session')
        if table == GROUP_TABLE:
            values = [{**fixtures.group_row(), 'group_id': group, 'originator': 'human_test-owner',
                       'driver_bot': 'test-driver', 'service_mode': None}]
            self.assertEqual(args['select'], ','.join(fields))
        else:
            self.producer_calls.append(args)
            self.assertEqual(args['where'], f"env = 'pre' AND group_id = '{group}' AND session_id = '{session}'")
            self.assertEqual(args['select'], ','.join(FIELDS)); self.assertEqual(args['limit'], '21')
            if self.producer_error is not None: raise self.producer_error
            values = [{**r, 'group_id': group, 'session_id': session} for r in self.producer_rows]
        return subprocess.CompletedProcess(command, 0, json.dumps({'success': True, 'data': values}), '')

    def test_empty_inventory_is_scoped_snapshot_not_completion_or_release(self):
        rc, report = self.execute()
        self.assertEqual(rc, 2, report)
        facts = report['execution_observation']['scope_observation']
        self.assertEqual(facts['status'], 'OBSERVED', report)
        self.assertIsNone(facts['group']['service_mode'])
        self.assertEqual(facts['queries'], 3)
        inventory = facts['state_machine_inventories'][0]
        self.assertEqual(inventory['rows'], [])
        self.assertEqual(inventory['status'], 'NO_RECORDED_RUNS_IN_SNAPSHOT')
        self.assertEqual(inventory['whole_scope_completion'], 'NOT_PROVEN')
        self.assertFalse(report['pool_release_authorized'])
        self.assertEqual(len(self.calls), 8)  # four describes, three facts, one delivery
        self.assert_original_unchanged()

    def _assert_resource_state_is_not_input_closure(self, group_status, callback_status):
        group = {**fixtures.group_row(), 'status': group_status}
        session = {**fixtures.session_row(), 'status': 'completed',
                   'completed_at': 100, 'callback_status': callback_status}
        with patch.object(fixtures, 'group_row', return_value=group), \
             patch.object(fixtures, 'session_row', return_value=session):
            rc, report = self.execute()
        self.assertEqual(rc, 2, report)
        facts = report['execution_observation']['scope_observation']
        self.assertEqual(facts['status'], 'OBSERVED')
        self.assertEqual(facts['group']['status'], group_status)
        self.assertEqual(facts['sessions'][0]['status'], 'completed')
        self.assertEqual(facts['sessions'][0]['callback_status'], callback_status)
        self.assertEqual(facts['state_machine_inventories'][0]['rows'], [])
        self.assertEqual(facts['producer_coverage'], 'NOT_VERIFIED')
        self.assertEqual(report['execution_status'], 'UNCONFIRMED')
        self.assertEqual(report['pool_disposition'], 'QUARANTINED')
        self.assertFalse(report['pool_release_authorized'])
        self.assert_original_unchanged()

    def test_completed_group_session_and_callback_do_not_certify_input_drain(self):
        self._assert_resource_state_is_not_input_closure('completed', 'succeeded')

    def test_closed_group_and_no_callback_do_not_certify_input_drain(self):
        self._assert_resource_state_is_not_input_closure('closed', 'not_applicable')

    def test_inactive_group_and_completed_session_do_not_certify_input_drain(self):
        self._assert_resource_state_is_not_input_closure('inactive', None)

    def test_chat_state_machine_run_is_not_hidden_by_null_activation(self):
        self.producer_rows = [row()]
        rc, report = self.execute()
        inventory = report['execution_observation']['scope_observation']['state_machine_inventories'][0]
        self.assertEqual(rc, 2); self.assertEqual(inventory['status'], 'RUNS_REQUIRE_REVIEW')
        self.assertEqual(inventory['rows'][0]['run_id'], 'sm-1')
        self.assertIsNone(inventory['rows'][0]['session_activation_count'])
        self.assert_original_unchanged()

    def test_completed_record_does_not_certify_children(self):
        self.producer_rows = [{**row(), 'status': 'completed', 'completed_at_ms': 100}]
        rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(report['execution_observation']['scope_observation']['state_machine_inventories'][0]
                         ['whole_scope_completion'], 'NOT_PROVEN')
        self.assert_original_unchanged()

    def test_old_scope_review_does_not_acquire_new_reads(self):
        self.producer_enabled = False
        self.recheck['scope_observation'] = fixtures.CONTRACT; write(self.recheck_path, self.recheck)
        rc, report = self.execute()
        self.assertEqual(rc, 2); self.assertEqual(self.producer_calls, [])
        self.assertNotIn('service_mode', report['execution_observation']['scope_observation']['group'])
        self.assertFalse(any(json.loads(c[4]).get('table') == TABLE for c in self.calls))
        self.assert_original_unchanged()

    def test_failed_inventory_keeps_group_and_session_facts(self):
        self.producer_error = OSError('private error')
        rc, report = self.execute()
        facts = report['execution_observation']['scope_observation']
        self.assertEqual(rc, 2); self.assertEqual(facts['status'], 'INCOMPLETE')
        self.assertIsNotNone(facts['group']); self.assertEqual(len(facts['sessions']), 1)
        self.assertEqual(facts['state_machine_inventories'], [])
        self.assertEqual(self.delivery_queries, [])
        self.assertNotIn('private error', json.dumps(report))
        self.assert_original_unchanged()

    def test_cancelled_inventory_preserves_partial_evidence(self):
        self.producer_error = KeyboardInterrupt()
        rc, report = self.execute()
        self.assertEqual(rc, 130)
        facts = report['execution_observation']['scope_observation']
        self.assertEqual(facts['status'], 'CANCELLED'); self.assertEqual(len(facts['sessions']), 1)
        self.assertEqual(self.delivery_queries, [])
        self.assert_original_unchanged()

    def test_full_inventory_page_stops_before_delivery_polling(self):
        self.producer_rows = [{**row(), 'id': n+1, 'run_id': 'sm-'+str(n)} for n in range(21)]
        rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(report['execution_observation']['status'], 'BLOCKED')
        self.assertEqual(self.delivery_queries, [])
        self.assert_original_unchanged()

    def test_authorization_drift_after_inventory_read_keeps_only_prior_facts(self):
        original = self.process
        def drift(command, **kwargs):
            result = original(command, **kwargs)
            args = json.loads(command[4])
            if command[2].endswith('.db_query') and args.get('table') == TABLE:
                value = read(self.recheck_path); value['expires_at'] = value['reviewed_at']; write(self.recheck_path, value)
            return result
        with patch.object(self, 'process', side_effect=drift): rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(report['execution_observation']['scope_observation']['state_machine_inventories'], [])
        self.assertEqual(self.delivery_queries, [])
        self.assert_original_unchanged()

    def test_forged_session_is_rejected_at_query_boundary(self):
        from bcs_e2e.retained_remote import RetainedMcpClient
        original = RetainedMcpClient.producer_query
        def foreign(client, plan, **kwargs):
            return original(client, replace(plan, session_id='foreign'), **kwargs)
        with patch.object(RetainedMcpClient, 'producer_query', foreign): rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(self.producer_calls, [])
        self.assertEqual(self.delivery_queries, [])
        self.assert_original_unchanged()

    def test_old_authorization_cannot_be_used_by_direct_producer_call(self):
        from bcs_e2e.retained_remote import RetainedMcpClient
        self.producer_enabled = False
        self.recheck['scope_observation'] = fixtures.CONTRACT; write(self.recheck_path, self.recheck)
        def unauthorized(client, plan, **kwargs):
            kwargs.pop('group')
            return client.producer_query(plan, **kwargs)
        with patch.object(RetainedMcpClient, 'scope_query', unauthorized): rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(self.producer_calls, [])
        self.assertFalse(any(c[2].endswith('.db_query') for c in self.calls))
        self.assert_original_unchanged()

    def test_missing_new_schema_fails_before_any_business_query(self):
        original = self.process
        def missing(command, **kwargs):
            result = original(command, **kwargs)
            if command[2].endswith('.table_describe') and json.loads(command[4]).get('table') == TABLE:
                result.stdout = result.stdout.replace('session_activation_count', 'missing_column')
            return result
        with patch.object(self, 'process', side_effect=missing): rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertFalse(any(c[2].endswith('.db_query') for c in self.calls))
        self.assert_original_unchanged()

    def test_late_producer_query_does_not_reset_shared_deadline(self):
        original = self.process; clock = [0.0]
        def slow(command, **kwargs):
            result = original(command, **kwargs)
            if command[2].endswith('.db_query') and json.loads(command[4]).get('table') == TABLE: clock[0] = 40
            return result
        with patch.object(self, 'process', side_effect=slow), patch('time.monotonic', side_effect=lambda: clock[0]):
            rc, report = self.execute()
        self.assertEqual(rc, 2)
        self.assertEqual(report['execution_observation']['status'], 'TIMED_OUT')
        self.assertEqual(self.delivery_queries, [])
        self.assertEqual(report['execution_observation']['scope_observation']['state_machine_inventories'], [])
        self.assert_original_unchanged()


class ProducerBudgetTests(unittest.TestCase):
    def test_two_sessions_six_rounds_four_downstream_runs_fit_original_cap(self):
        from unittest.mock import Mock
        import test_delivery_observation as delivery
        from bcs_e2e.delivery_observation import DeliveryObserver
        from bcs_e2e.scope_observation import SESSION_FIELDS
        from bcs_e2e.downstream_observation import QUEUE_FIELDS, RESULT_FIELDS
        from bcs_e2e.retained_remote import RetainedMcpClient
        from bcs_e2e.observation_budget import BUDGETED_WAIT_CONTRACT
        delivery.DeliveryObserverTests.setUp(self)
        raw = self.client
        raw.table_describe.return_value = {'success': True, 'columns': list(
            set(FIELDS + delivery.FIELDS + group_fields(CONTRACT) + SESSION_FIELDS + QUEUE_FIELDS + RESULT_FIELDS))}
        raw.scope_query.side_effect = lambda plan, **kw: (fixtures.group_row() if kw['group'] else fixtures.session_row())
        raw.producer_query.return_value = []
        raw.delivery_query.side_effect = lambda plan, **kw: {'success': True, 'data': [
            delivery.row(plan, delivery_id=plan.session_id+'-'+bot, target_bot_id=bot,
                         run_id=plan.session_id+'-'+bot, status='running', terminal_at_ms=None)
            for bot in plan.target_bot_ids]}
        raw.downstream_observe.return_value = {'queue_status': 'DONE', 'result_status': 'COMPLETED'}
        review = Mock(); review.validate.return_value = {}
        client = RetainedMcpClient(raw, review)
        observer = DeliveryObserver(client=client, database='testdb', environment='pre',
            allowed_bots=frozenset({'bot-1', 'bot-2'}), contract=BUDGETED_WAIT_CONTRACT,
            observation_budget={'timeout_seconds': 60, 'max_rounds': 6, 'poll_interval_seconds': 1},
            scope_contract=CONTRACT, downstream_config={'run_id_source': 'bcn-canonical',
                'bot_bindings': {'bot-1': 'baas-1', 'bot-2': 'baas-2'}})
        observer.admit()
        with patch('bcs_e2e.delivery_observation.time.sleep'):
            report = observer.observe(self.ledger, self.scoped.observation_scope, self.active,
                lease=self.lease, case_id=self.case, timeout_seconds=10)
        self.assertEqual(report['status'], 'OBSERVED', report)
        self.assertEqual(raw.table_describe.call_count, 6)
        self.assertEqual(raw.scope_query.call_count, 3)
        self.assertEqual(raw.producer_query.call_count, 2)
        self.assertEqual(raw.delivery_query.call_count, 12)
        self.assertEqual(raw.downstream_observe.call_count, 4)  # two reads reserved each
        self.assertEqual(client._remaining, 1)  # 6 + 3 + 2 + 12 + 8 = 31, cap remains 32
        self.assertEqual(len(report['scope_observation']['state_machine_inventories']), 2)
        self.assertEqual(len(report['downstream_observation']['runs']), 4)


if __name__ == '__main__': unittest.main()
