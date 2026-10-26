"""Exact Case-scoped persistence reads; all remote-shaped clients are fakes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import PilotAdapter
from bcs_e2e.alisddbquery import AlisdDbQueryClient, AlisdDbQueryObserver
from bcs_e2e.case_scope import CaseScopedAdapter
from bcs_e2e.evidence import ASSERTIONS
from bcs_e2e.ledger import Ledger
from bcs_e2e.observation_gate import AdmittedObserver, ObserverAdmissionError
from bcs_e2e.profile import load


def interrupted():
    try:
        raise KeyboardInterrupt()
    finally:
        raise RuntimeError('private secondary cleanup error')


class ObservationScopeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.ledger = Ledger(Path(tmp.name) / 'run.sqlite', 'observation-run', 'a' * 64, create=True)
        self.addCleanup(self.ledger.close)
        self.profile = load(ROOT / 'profiles/preprod.example.json')
        self.profile['environment']['expected_bcs_env'] = 'pre'
        self.profile['observer'].update(database='testdb', environment_binding_confirmed=True)
        self.profile['policy']['exclusive_use_confirmed'] = True
        self.cases = json.loads((ROOT / 'manifest.json').read_text())['cases']
        self.client = Mock(spec=AlisdDbQueryClient)
        self.client.db_list.return_value = {'success': True, 'data': ['testdb']}
        self.client.table_list.return_value = {'success': True, 'data': [v[0] for v in ASSERTIONS.values()]}
        self.client.table_describe.side_effect = lambda database, table: {
            'success': True, 'columns': list(next(v[1] for v in ASSERTIONS.values() if v[0] == table))}
        self.client.db_query.side_effect = self.query
        self.probe = Mock(side_effect=lambda database, environment: {
            'status': 'BOUND', 'database': database, 'environment': environment})
        self.raw = Mock(spec=PilotAdapter)
        self.raw.file_delete.return_value = ({'deleted': True}, {})
        self.scopes = {}
        self.observers = {}
        self.handles = {}
        for case in self.cases:
            cid = case['id']
            provider = AlisdDbQueryObserver(client=self.client, database='testdb', environment='pre',
                environment_binding_confirmed=True, physical_binding_probe=self.probe)
            self.raw.semantic_observer.return_value = provider
            scope = CaseScopedAdapter(self.raw, self.ledger, 'a' * 64, case, attempt=1)
            self.scopes[cid] = scope
            self.observers[cid] = AdmittedObserver(scope, self.profile, cid,
                                                   resource_scope=scope.observation_scope)
        # Admission is deliberately completed before any business resources exist.
        self.assertEqual(self.ledger.resource_snapshot(), [])
        self.client.db_query.assert_not_called()
        self.client.reset_mock()

    def resources(self, cid):
        group = self.ledger.intent(cid, 1, 'group', cid + '-group-request')
        self.ledger.observed(group, cid + '-group', origin='created')
        session = self.ledger.intent(cid, 1, 'session', cid + '-session-request', group)
        self.ledger.observed(session, cid + '-session', origin='created')
        file = self.ledger.intent(cid, 1, 'file', cid + '-file-request', session)
        self.ledger.observed(file, cid + '-file', origin='created')
        self.handles[cid] = (group, session, file)
        return group, session, file

    def query(self, plan):
        key = ASSERTIONS[plan.assertion_id][2]
        rid = plan.filters[key]
        cid = rid.rsplit('-', 1)[0]
        row = {'env': 'pre', 'group_id': cid + '-group', 'session_id': cid + '-session',
               'file_id': cid + '-file', 'driver_bot': 'test-driver', 'originator': 'human_test',
               'status': 'Ready' if plan.assertion_id == 'file.persisted' else 'running',
               'session_title': 'test title', 'size': 1, 'sha256': 'b' * 64, 'storage_backend': 'baas'}
        return {'success': True, 'data': [{field: row[field] for field in plan.select}]}

    def change(self, handle, **fields):
        for field, value in fields.items():
            self.ledger.db.execute(f'UPDATE resource SET {field}=? WHERE handle=?', (value, handle))
        self.ledger.db.commit()

    def blocked_without_query(self, cid, assertion, resource):
        self.client.reset_mock()
        status, evidence = self.observers[cid].observe(assertion, resource)
        self.assertEqual(status, 'BLOCKED')
        self.assertNotIn('rows', evidence)
        self.assertEqual(self.client.mock_calls, [])
        return evidence

    def test_all_persisted_assertions_use_exact_owned_scope_and_preserve_projection(self):
        for cid, assertion, suffix in (('PRE-01', 'group.persisted', 'group'),
                                       ('PRE-02', 'session.persisted', 'session'),
                                       ('PRE-03', 'file.persisted', 'file')):
            self.resources(cid)
            status, evidence = self.observers[cid].observe(assertion, cid + '-' + suffix)
            self.assertEqual(status, 'PASS')
            self.assertEqual(evidence['source'], 'alisddbquery')
            self.assertEqual(evidence['plan']['filters'], {'env': 'pre', suffix + '_id': cid + '-' + suffix})
            self.assertEqual(set(evidence['rows'][0]), set(ASSERTIONS[assertion][1]))
        self.assertEqual(self.client.db_query.call_count, 3)
        self.assertEqual(self.client.table_describe.call_count, 3)
        self.client.db_list.assert_not_called(); self.client.table_list.assert_not_called()

    def test_no_scope_is_not_an_optional_unguarded_mode(self):
        self.raw.reset_mock()
        with self.assertRaises(TypeError):
            AdmittedObserver(self.raw, self.profile, 'PRE-01')
        with self.assertRaises(ObserverAdmissionError):
            AdmittedObserver(self.raw, self.profile, 'PRE-01', resource_scope=None)
        self.raw.semantic_observer.assert_not_called()

    def test_missing_operational_or_other_run_id_cannot_trigger_provider_calls(self):
        for rid in ('operational-group', 'other-run-group', 'PRE-01-group', 'invalid/id'):
            self.blocked_without_query('PRE-01', 'group.persisted', rid)

    def test_unsupported_or_undeclared_assertions_never_reach_scope_or_provider(self):
        guard = Mock(side_effect=AssertionError('scope should not run'))
        self.observers['PRE-01'].resource_scope = guard
        for assertion in ('file.persisted', 'session.persisted', 'dm.persisted', 'purge.history'):
            self.blocked_without_query('PRE-01', assertion, 'owned-id')
        guard.assert_not_called()

    def test_other_case_resources_do_not_grant_query_authority(self):
        self.resources('PRE-02')
        self.blocked_without_query('PRE-01', 'group.persisted', 'PRE-02-group')
        self.blocked_without_query('PRE-03', 'file.persisted', 'PRE-02-file')

    def test_origin_state_case_attempt_and_each_parent_checked_before_query(self):
        for handle in self.resources('PRE-03'):
            for field, values, original in (
                    ('origin', ('borrowed', 'reused', 'unknown'), 'created'),
                    ('state', ('intended', 'unknown', 'retained', 'deleted'), 'live'),
                    ('case_id', ('PRE-01',), 'PRE-03'), ('attempt', (2,), 1)):
                for value in values:
                    with self.subTest(handle=handle, field=field, value=value):
                        self.change(handle, **{field: value})
                        self.blocked_without_query('PRE-03', 'file.persisted', 'PRE-03-file')
                self.change(handle, **{field: original})

    def test_run_profile_drift_blocks_before_query(self):
        self.resources('PRE-01')
        for field, old in (('run_id', 'observation-run'), ('profile_digest', 'a' * 64)):
            self.ledger.db.execute(f'UPDATE run SET {field}=?', ('foreign',))
            self.blocked_without_query('PRE-01', 'group.persisted', 'PRE-01-group')
            self.ledger.db.execute(f'UPDATE run SET {field}=?', (old,))

    def test_api_deleted_resource_is_not_queryable_even_before_ledger_tombstone(self):
        _, _, file = self.resources('PRE-03')
        self.scopes['PRE-03'].file_delete('PRE-03-session', 'PRE-03-file')
        self.assertEqual(self.ledger._get(file)['state'], 'live')
        self.blocked_without_query('PRE-03', 'file.persisted', 'PRE-03-file')

    def test_wrong_returned_parent_does_not_become_usable_evidence(self):
        for cid, assertion, parent, suffix in (('PRE-02', 'session.persisted', 'group_id', 'session'),
                                               ('PRE-03', 'file.persisted', 'session_id', 'file')):
            self.resources(cid)
            def wrong_parent(plan):
                result = self.query(plan)
                result['data'][0][parent] = 'private-operational-parent'
                return result
            self.client.db_query.side_effect = wrong_parent
            status, evidence = self.observers[cid].observe(assertion, cid + '-' + suffix)
            self.assertEqual(status, 'BLOCKED')
            self.assertNotIn('rows', evidence)
            self.assertNotIn('private-operational-parent', json.dumps(evidence))

    def test_ownership_drift_during_query_blocks_result_without_retry(self):
        group, _, _ = self.resources('PRE-03')
        def query(plan):
            result = self.query(plan)
            self.change(group, origin='borrowed')
            return result
        self.client.db_query.side_effect = query
        status, evidence = self.observers['PRE-03'].observe('file.persisted', 'PRE-03-file')
        self.assertEqual(status, 'BLOCKED'); self.assertNotIn('rows', evidence)
        self.client.db_query.assert_called_once()

    def test_valid_but_changed_chain_snapshot_is_not_accepted(self):
        group, _, _ = self.resources('PRE-03')
        def query(plan):
            result = self.query(plan)
            # Even an otherwise valid parent snapshot cannot silently change.
            self.change(group, request_id='changed-creation-request')
            return result
        self.client.db_query.side_effect = query
        status, evidence = self.observers['PRE-03'].observe('file.persisted', 'PRE-03-file')
        self.assertEqual(status, 'BLOCKED'); self.assertNotIn('rows', evidence)
        self.client.db_query.assert_called_once()

    def test_scope_reads_are_local_bounded_and_do_not_hold_transaction_during_query(self):
        self.resources('PRE-03')
        original = self.client.db_query.side_effect
        def query(plan):
            self.assertFalse(self.ledger.db.in_transaction)
            return original(plan)
        self.client.db_query.side_effect = query
        sql = []; self.ledger.db.set_trace_callback(sql.append)
        try:
            status, _ = self.observers['PRE-03'].observe('file.persisted', 'PRE-03-file')
        finally:
            self.ledger.db.set_trace_callback(None)
        self.assertEqual(status, 'PASS')
        self.assertLessEqual(len(sql), 8)
        self.assertTrue(all(s.startswith('SELECT') for s in sql))
        self.client.db_query.assert_called_once()

    def test_query_failure_is_sanitized_without_retry(self):
        self.resources('PRE-01')
        self.client.db_query.side_effect = RuntimeError('private-response-and-auth')
        status, evidence = self.observers['PRE-01'].observe('group.persisted', 'PRE-01-group')
        self.assertEqual(status, 'BLOCKED')
        self.assertNotIn('private-response-and-auth', json.dumps(evidence))
        self.client.db_query.assert_called_once()

    def test_direct_and_chained_query_cancellation_propagate(self):
        self.resources('PRE-01')
        for cancel in (lambda: (_ for _ in ()).throw(KeyboardInterrupt()), interrupted):
            self.client.db_query.side_effect = lambda plan: cancel()
            with self.assertRaises(KeyboardInterrupt):
                self.observers['PRE-01'].observe('group.persisted', 'PRE-01-group')

    def test_scope_cancellation_before_query_propagates(self):
        self.observers['PRE-01'].resource_scope = lambda *args: interrupted()
        with self.assertRaises(KeyboardInterrupt):
            self.observers['PRE-01'].observe('group.persisted', 'PRE-01-group')
        self.client.db_query.assert_not_called()

    def test_chained_admission_cancellation_is_not_sanitized_to_blocked(self):
        provider = Mock()
        provider.admit.side_effect = lambda *args: interrupted()
        self.raw.semantic_observer.return_value = provider
        guard = Mock()
        with self.assertRaises(KeyboardInterrupt):
            AdmittedObserver(self.raw, self.profile, 'PRE-01', resource_scope=guard)
        guard.assert_not_called()

    def test_physical_binding_probe_cancellation_propagates_before_metadata(self):
        self.probe.side_effect = lambda *args: interrupted()
        provider = AlisdDbQueryObserver(client=self.client, database='testdb', environment='pre',
            environment_binding_confirmed=True, physical_binding_probe=self.probe)
        self.raw.semantic_observer.return_value = provider
        with self.assertRaises(KeyboardInterrupt):
            AdmittedObserver(self.raw, self.profile, 'PRE-01',
                             resource_scope=self.scopes['PRE-01'].observation_scope)
        self.assertEqual(self.client.mock_calls, [])
