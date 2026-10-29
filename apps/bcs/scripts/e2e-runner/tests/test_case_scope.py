"""Runtime scope refusals must happen before any underlying adapter call."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import PilotAdapter
from bcs_e2e.case_scope import CaseScopedAdapter, CaseScopeError
from bcs_e2e.ledger import Ledger, LedgerError


class CaseScopeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.ledger = Ledger(Path(tmp.name) / 'run.sqlite', 'scope-run', 'a' * 64, create=True)
        self.addCleanup(self.ledger.close)
        cases = json.loads((ROOT / 'manifest.json').read_text())['cases']
        self.case = copy.deepcopy(cases[2])
        self.case['effects'] = sorted(set().union(*(set(c['effects']) for c in cases)))
        self.raw = Mock(spec=PilotAdapter)
        self.raw.termination_mode = 'LOCAL_CONTROLLED'
        self.response = ({'deleted': True}, {'operation': 'unchanged', 'http_status': 200})
        for name in PilotAdapter.__dict__:
            if not name.startswith('_'):
                getattr(self.raw, name).return_value = self.response
        self.scope = CaseScopedAdapter(self.raw, self.ledger, 'a' * 64, self.case, attempt=1)
        self.group, self.initial = self.ledger.group_creation_intent('PRE-03', 1, 'create-group')
        self.scope.group_create({'group_kind': 'normal'})
        self.ledger.observed(self.group, 'group-1', origin='created')
        self.ledger.observed(self.initial, 'initial-1', origin='created')
        self.session = self.ledger.intent('PRE-03', 1, 'session', 'create-session', self.group)
        self.scope.session_create('group-1', {})
        self.ledger.observed(self.session, 'session-1', origin='created')
        self.file = self.ledger.intent('PRE-03', 1, 'file', 'create-file', self.session)
        self.scope.file_prepare('session-1', {})
        self.ledger.observed(self.file, 'file-1', origin='created')
        self.raw.reset_mock()

    def change(self, handle, **fields):
        for field, value in fields.items():
            # Test-only controlled field names, not an operator SQL interface.
            self.ledger.db.execute(f'UPDATE resource SET {field}=? WHERE handle=?', (value, handle))
        self.ledger.db.commit()

    def denied(self, action):
        self.raw.reset_mock()
        with self.assertRaises((CaseScopeError, LedgerError)):
            action()
        self.assertEqual(self.raw.mock_calls, [])

    def test_all_targeted_methods_reject_unregistered_ids_before_transport(self):
        calls = [
            lambda: self.scope.group_read('foreign'),
            lambda: self.scope.group_update('foreign', {}),
            lambda: self.scope.group_delete('foreign'),
            lambda: self.scope.group_verify_deleted('foreign'),
            lambda: self.scope.session_create('foreign', {}),
            lambda: self.scope.session_read('foreign'),
            lambda: self.scope.session_update('foreign', {}),
            lambda: self.scope.session_messages('foreign'),
            lambda: self.scope.session_token('foreign'),
            lambda: self.scope.session_ws_connect('group-1', 'foreign', 'secret'),
            lambda: self.scope.session_ws_close('foreign'),
            lambda: self.scope.session_delete('foreign'),
            lambda: self.scope.session_verify_deleted('foreign'),
            lambda: self.scope.file_list('foreign'),
            lambda: self.scope.file_prepare('foreign', {}),
            lambda: self.scope.file_upload('session-1', 'foreign', b'x'),
            lambda: self.scope.file_complete('session-1', 'foreign'),
            lambda: self.scope.file_metadata('session-1', 'foreign'),
            lambda: self.scope.file_download('session-1', 'foreign'),
            lambda: self.scope.file_share('session-1', 'foreign'),
            lambda: self.scope.file_delete('session-1', 'foreign'),
            lambda: self.scope.file_verify_deleted('session-1', 'foreign'),
            lambda: self.scope.shared_file_download('secret'),
            lambda: self.scope.shared_file_verify_deleted('secret'),
        ]
        for index, call in enumerate(calls):
            with self.subTest(operation=index):
                self.denied(call)

    def test_borrowed_reused_unknown_tombstoned_and_other_case_attempt_are_refused(self):
        for field, values, original in (
                ('origin', ('borrowed', 'reused', 'unknown'), 'created'),
                ('state', ('intended', 'unknown', 'deleted', 'retained'), 'live'),
                ('case_id', ('PRE-01',), 'PRE-03'), ('attempt', (2,), 1)):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.change(self.file, **{field: value})
                    self.denied(lambda: self.scope.file_delete('session-1', 'file-1'))
                    self.denied(lambda: self.scope.file_metadata('session-1', 'file-1'))
            self.change(self.file, **{field: original})

    def test_full_parent_chain_checked_live_on_every_operation(self):
        for handle in (self.session, self.group):
            for field, value, original in (
                    ('origin', 'borrowed', 'created'), ('state', 'deleted', 'live'),
                    ('case_id', 'PRE-02', 'PRE-03'), ('attempt', 2, 1)):
                with self.subTest(handle=handle, field=field):
                    self.change(handle, **{field: value})
                    self.denied(lambda: self.scope.file_upload('session-1', 'file-1', b'x'))
                    self.change(handle, **{field: original})
        self.assertIs(self.scope.file_upload('session-1', 'file-1', b'x'), self.response)

    def test_wrong_kind_cycle_and_extra_group_parent_are_refused_without_loop(self):
        for handle, parent, original in ((self.file, self.group, self.session),
                                         (self.session, self.file, self.group),
                                         (self.group, self.group, None)):
            self.change(handle, parent=parent)
            self.denied(lambda: self.scope.file_delete('session-1', 'file-1'))
            self.change(handle, parent=original)

    def test_exact_file_parent_not_any_owned_session(self):
        self.denied(lambda: self.scope.file_delete('initial-1', 'file-1'))
        self.denied(lambda: self.scope.file_verify_deleted('initial-1', 'file-1'))
        self.denied(lambda: self.scope.file_download('initial-1', 'file-1'))

    def test_run_and_profile_drift_refused_before_transport(self):
        for field in ('run_id', 'profile_digest'):
            original = 'scope-run' if field == 'run_id' else 'a' * 64
            self.ledger.db.execute(f'UPDATE run SET {field}=?', ('foreign',))
            self.denied(lambda: self.scope.group_read('group-1'))
            self.ledger.db.execute(f'UPDATE run SET {field}=?', (original,))

    def test_mutating_effect_must_be_present_in_frozen_copy(self):
        calls = {
            'update:owned_group': lambda: self.scope.group_update('group-1', {}),
            'update:owned_session': lambda: self.scope.session_update('session-1', {}),
            'mint:session_token': lambda: self.scope.session_token('session-1'),
            'upload:file': lambda: self.scope.file_upload('session-1', 'file-1', b'x'),
            'share:owned_file': lambda: self.scope.file_share('session-1', 'file-1'),
            'delete:owned_file': lambda: self.scope.file_delete('session-1', 'file-1'),
            'delete:owned_initial_session': lambda: self.scope.session_delete('initial-1'),
        }
        for effect, call in calls.items():
            case = copy.deepcopy(self.case); case['effects'].remove(effect)
            self.scope = CaseScopedAdapter(self.raw, self.ledger, 'a' * 64, case, attempt=1)
            self.scope._initial_sessions.add(self.initial)
            # Caller mutation after composition must not widen permissions.
            case['effects'].append(effect)
            with self.subTest(effect=effect):
                self.denied(call)

    def test_parent_api_delete_is_refused_for_live_unknown_or_retained_children(self):
        for state in ('live', 'intended', 'unknown', 'retained'):
            self.change(self.file, state=state)
            self.denied(lambda: self.scope.session_delete('session-1'))
            self.denied(lambda: self.scope.group_delete('group-1'))

    def test_delete_verify_then_tombstone_and_no_ordinary_read_after_delete(self):
        self.assertIs(self.scope.file_delete('session-1', 'file-1'), self.response)
        self.assertEqual(self.ledger._get(self.file)['state'], 'live')
        self.assertIs(self.scope.file_verify_deleted('session-1', 'file-1'), self.response)
        self.denied(lambda: self.scope.file_download('session-1', 'file-1'))
        self.denied(lambda: self.scope.file_upload('session-1', 'file-1', b'x'))
        self.ledger.mark_deleted(self.file)
        self.denied(lambda: self.scope.file_verify_deleted('session-1', 'file-1'))
        self.scope.session_delete('session-1'); self.scope.session_verify_deleted('session-1')
        self.ledger.mark_deleted(self.session)
        self.scope.session_delete('initial-1'); self.scope.session_verify_deleted('initial-1')
        self.ledger.mark_deleted(self.initial)
        self.scope.group_delete('group-1'); self.scope.group_verify_deleted('group-1')
        self.ledger.mark_deleted(self.group)
        self.assertEqual({r['state'] for r in self.ledger.resource_snapshot()}, {'deleted'})

    def test_share_issuance_binding_survives_delete_but_not_ledger_drift(self):
        self.raw.file_share.return_value = ({'share_token': 'test-token'}, {})
        self.scope.file_share('session-1', 'file-1')
        self.scope.shared_file_download('test-token')
        self.scope.file_delete('session-1', 'file-1')
        self.scope.shared_file_verify_deleted('test-token')
        self.denied(lambda: self.scope.shared_file_download('test-token'))
        self.change(self.group, origin='reused')
        self.denied(lambda: self.scope.shared_file_verify_deleted('test-token'))

    def test_ws_requires_issued_token_exact_parent_and_single_held_connection(self):
        self.raw.session_token.return_value = ({'connection_token': 'opaque-ws'}, {})
        self.scope.session_token('session-1')
        self.denied(lambda: self.scope.session_ws_connect('group-1', 'initial-1', 'opaque-ws'))
        self.denied(lambda: self.scope.session_ws_connect('group-1', 'session-1', 'foreign-secret'))
        self.scope.session_ws_connect('group-1', 'session-1', 'opaque-ws')
        self.denied(lambda: self.scope.session_ws_connect('group-1', 'session-1', 'opaque-ws'))
        self.ledger.mark_deleted(self.file)
        self.denied(lambda: self.scope.session_delete('session-1'))
        # Closing a held socket must still work if ledger or server validation fails.
        self.change(self.session, origin='unknown')
        self.assertIs(self.scope.session_ws_close('session-1'), self.response)
        self.raw.session_ws_close.assert_called_once_with('session-1')
        self.denied(lambda: self.scope.session_ws_close('session-1'))

    def test_creations_require_fresh_intent_and_normal_group_kind(self):
        self.denied(lambda: self.scope.group_create({'group_kind': 'normal'}))
        self.denied(lambda: self.scope.group_create({'group_kind': 'dm'}))
        self.denied(lambda: self.scope.session_create('group-1', {}))
        self.denied(lambda: self.scope.file_prepare('session-1', {}))

    def test_group_requires_atomic_initial_session_intent(self):
        self.ledger.intent('PRE-03', 1, 'group', 'second-group')
        self.denied(lambda: self.scope.group_create({'group_kind': 'normal'}))

    def test_ambiguous_intents_and_unknown_intent_are_not_adopted(self):
        first = self.ledger.intent('PRE-03', 1, 'file', 'file-2', self.session)
        second = self.ledger.intent('PRE-03', 1, 'file', 'file-3', self.session)
        self.denied(lambda: self.scope.file_prepare('session-1', {}))
        self.ledger.unknown(first); self.ledger.unknown(second)
        self.denied(lambda: self.scope.file_prepare('session-1', {}))

    def test_failed_creation_is_not_retried_or_rewritten_and_evidence_is_preserved(self):
        handle = self.ledger.intent('PRE-03', 1, 'file', 'file-2', self.session)
        failure = RuntimeError('synthetic response lost')
        failure.request_records = [{'submitted_request_id': 'exact-request'}]
        self.raw.file_prepare.side_effect = failure
        with self.assertRaises(RuntimeError) as caught:
            self.scope.file_prepare('session-1', {})
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.ledger._get(handle)['state'], 'intended')
        self.denied(lambda: self.scope.file_prepare('session-1', {}))
        self.denied(lambda: self.scope.session_delete('session-1'))

    def test_transport_cancellation_is_not_caught_or_retried(self):
        cancel = KeyboardInterrupt()
        self.raw.file_upload.side_effect = cancel
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.scope.file_upload('session-1', 'file-1', b'x')
        self.assertIs(caught.exception, cancel)
        self.raw.file_upload.assert_called_once()

    def test_current_adapter_surface_has_no_unguarded_generic_forwarding(self):
        methods = {name for name in PilotAdapter.__dict__ if not name.startswith('_')}
        self.assertTrue(methods <= CaseScopedAdapter.__dict__.keys())
        for name in ('write', 'files', 'websocket', 'profile', 'delete_all', 'verify_cleanup_scope'):
            self.assertFalse(hasattr(self.scope, name))

    def test_checks_are_bounded_local_reads_without_remote_metadata_queries(self):
        sql = []
        self.ledger.db.set_trace_callback(sql.append)
        try:
            self.scope.file_delete('session-1', 'file-1')
        finally:
            self.ledger.db.set_trace_callback(None)
        self.assertLessEqual(len(sql), 8)
        self.assertTrue(all(statement.startswith('SELECT') for statement in sql))
        self.raw.file_delete.assert_called_once_with('session-1', 'file-1')
        self.assertEqual(len(self.raw.mock_calls), 1)


class CaseScopeCompositionTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from bcs_e2e.adapters import LocalMockAdapter
        from bcs_e2e.prepare import prepare_run
        from bcs_e2e.profile import load
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        pool = patch('bcs_e2e.pool.pool_root', return_value=root / 'pools')
        pool.start(); self.addCleanup(pool.stop)
        self.profile = load(ROOT / 'profiles/local.mock.json')
        self.state = root / 'run'
        prepare_run(self.profile, self.state, 'scoped-composition')
        self.raw = LocalMockAdapter(self.profile)

    def execute(self, case_id):
        from bcs_e2e.pilot import run_case
        return run_case(self.profile, self.state, 'scoped-composition', case_id,
                        adapter_factory=lambda *args, **kwargs: self.raw)

    def test_composition_refuses_borrowed_resource_without_read_update_or_delete(self):
        from unittest.mock import patch
        from bcs_e2e.run_state import read
        observed = Ledger.observed
        def observe(ledger, handle, resource_id, **kwargs):
            if ledger._get(handle)['kind'] == 'group':
                kwargs['origin'] = 'borrowed'
            return observed(ledger, handle, resource_id, **kwargs)
        with patch.object(Ledger, 'observed', observe), \
             patch.object(self.raw, 'group_read') as read_group, \
             patch.object(self.raw, 'group_update') as update, \
             patch.object(self.raw, 'group_delete') as delete:
            result = self.execute('PRE-01')
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['cleanup'], 'BLOCKED')
        read_group.assert_not_called(); update.assert_not_called(); delete.assert_not_called()
        state = read(self.state / 'run.json')
        self.assertEqual(state['status'], 'FAILED')
        self.assertTrue(Path(state['lock_path']).exists())

    def test_bad_ws_handshake_projection_still_closes_owned_socket(self):
        from unittest.mock import patch
        connect = self.raw.session_ws_connect
        def wrong_parent(*args):
            data, record = connect(*args)
            return {**data, 'group_id': 'foreign'}, record
        with patch.object(self.raw, 'session_ws_connect', side_effect=wrong_parent), \
             patch.object(self.raw, 'session_ws_close', wraps=self.raw.session_ws_close) as close:
            result = self.execute('PRE-02')
        self.assertEqual(result['functional'], 'FAIL')
        self.assertEqual(result['cleanup'], 'PASS')
        self.assertEqual(result['quiescence'], 'CONFIRMED')
        self.assertFalse(self.raw.connected_sessions)
        close.assert_called_once()

    def test_ws_evidence_failure_keeps_cleanup_and_never_double_closes(self):
        from unittest.mock import patch
        from bcs_e2e.correlation import CorrelatedEvidence
        append = CorrelatedEvidence.append
        for operation in ('session.ws.connect', 'session.ws.close'):
            # Each Case can only execute once per Run; use a fresh setup.
            if operation == 'session.ws.close':
                self.setUp()
            def fail_once(evidence, record):
                if record.get('operation') == operation:
                    raise LedgerError('synthetic correlation checkpoint failure')
                return append(evidence, record)
            with self.subTest(operation=operation), \
                 patch.object(CorrelatedEvidence, 'append', fail_once), \
                 patch.object(self.raw, 'session_ws_close', wraps=self.raw.session_ws_close) as close:
                result = self.execute('PRE-02')
            self.assertEqual(result['functional'], 'FAIL')
            self.assertEqual(result['cleanup'], 'PASS')
            self.assertEqual(result['quiescence'], 'CONFIRMED')
            self.assertFalse(self.raw.connected_sessions)
            close.assert_called_once()
