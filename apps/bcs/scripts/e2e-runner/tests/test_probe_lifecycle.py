"""Probe completion is distinct from handshake and remote execution completion."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_probe import FakeWsClient, prepare_live_scope, profile
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.probe import ws_probe
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import read, write
from bcs_e2e.ws_transport import WebSocketTransportError


class ProbeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = profile()
        self.state = prepare_live_scope(self.root, self.profile)
        self.token = 'test-token'
        self.environment = patch.dict('os.environ', {'BCS_E2E_SESSION_TOKEN': self.token}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = FakeWsClient(self.profile['entrypoint']['origin'], timeout_seconds=1)
        self.close = Mock(wraps=self.client.connection.close)
        self.client.connection.close = self.close
        self.factory = Mock(return_value=self.client)

    def probe(self):
        result = ws_probe(self.profile, self.state, 'ws-run', token_env='BCS_E2E_SESSION_TOKEN',
                          group_id='g-1', session_id='s-1', client_factory=self.factory)
        self.assertNotIn(self.token, json.dumps(result))
        self.assertFalse(result['ready_to_execute'])
        self.assertFalse(result['pool_release_authorized'])
        self.assertEqual(result['execution_status'], 'UNCONFIRMED')
        return result

    def edit_state(self, **updates):
        value = read(self.state/'run.json')
        value.update(updates)
        write(self.state/'run.json', value)

    def test_success_closes_once_without_changing_run_files(self):
        before = {p.name: p.read_bytes() for p in self.state.iterdir()}
        result = self.probe()
        self.assertEqual(result['status'], 'PROBE_PASSED')
        self.assertEqual(result['socket_close_status'], 'CLOSED')
        self.assertEqual(result['close_verification'], 'LOCAL_HANDLE_ONLY')
        self.assertEqual(result['failure_phases'], [])
        self.close.assert_called_once_with()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.state.iterdir()})

    def test_close_failure_blocks_success_without_leaking_exception(self):
        self.close.side_effect = OSError(self.token)
        result = self.probe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['socket_close_status'], 'UNCONFIRMED')
        self.assertEqual(result['failure_phases'], ['LOCAL_CLOSE'])
        self.close.assert_called_once_with()

    def test_failed_handshake_never_claims_a_connection_was_closed(self):
        self.client.connect = Mock(side_effect=WebSocketTransportError(self.token))
        result = self.probe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['socket_close_status'], 'NOT_ACQUIRED')
        self.assertEqual(result['failure_phases'], ['HANDSHAKE'])
        self.close.assert_not_called()

    def test_failed_evidence_and_failed_close_preserve_both_phases(self):
        self.client.connect = Mock(return_value=(self.client.connection, {'echo': self.token}))
        self.close.side_effect = OSError(self.token)
        result = self.probe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['auth_leak_check'], 'FAIL')
        self.assertEqual(result['failure_phases'], ['EVIDENCE', 'LOCAL_CLOSE'])
        self.assertEqual(result['reason'], 'session token appeared in WebSocket probe evidence')
        self.assertNotIn('evidence', result)
        self.close.assert_called_once_with()

    def test_interrupt_during_handshake_is_cancelled(self):
        self.client.connect = Mock(side_effect=KeyboardInterrupt(self.token))
        result = self.probe()
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['failure_phases'], ['HANDSHAKE'])
        self.assertEqual(result['socket_close_status'], 'NOT_ACQUIRED')

    def test_interrupt_during_close_is_cancelled_not_passed(self):
        self.close.side_effect = KeyboardInterrupt(self.token)
        result = self.probe()
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['failure_phases'], ['LOCAL_CLOSE'])
        self.assertEqual(result['socket_close_status'], 'UNCONFIRMED')

    def test_scope_change_during_handshake_closes_owned_connection(self):
        connect = self.client.connect
        def changing_connect(*args):
            result = connect(*args)
            self.edit_state(status='QUARANTINED')
            return result
        self.client.connect = changing_connect
        result = self.probe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failure_phases'], ['SCOPE_RECHECK'])
        self.assertEqual(result['socket_close_status'], 'CLOSED')
        self.close.assert_called_once_with()

    def test_rejected_states_are_checked_before_credentials_and_network(self):
        original = read(self.state/'run.json')
        for updates in ({'status': 'QUARANTINED'}, {'status': 'COMPLETED'}, {'status': 'RELEASED'},
                        {'status': []}, {'status': {}}, {'status': None}, {'last_recovery': {}},
                        {'recovery_origin_status': 'RUNNING'}):
            with self.subTest(updates=updates):
                write(self.state/'run.json', {**original, **updates})
                with patch('bcs_e2e.probe.os.environ.get') as credentials:
                    with self.assertRaisesRegex(ConfigError, 'not admitted'):
                        self.probe()
                    credentials.assert_not_called()
                self.factory.assert_not_called()

    def test_foreign_ledger_is_rejected_before_credentials_and_network(self):
        self.edit_state(ledger_path=str(self.root/'another-run.sqlite'))
        with patch('bcs_e2e.probe.os.environ.get') as credentials:
            with self.assertRaisesRegex(ConfigError, 'Run-local ledger'):
                self.probe()
            credentials.assert_not_called()
        self.factory.assert_not_called()

    def test_symlink_ledger_is_rejected_before_credentials_and_network(self):
        ledger = self.state/'run.sqlite'
        actual = self.state/'actual.sqlite'
        ledger.rename(actual)
        ledger.symlink_to(actual)
        with patch('bcs_e2e.probe.os.environ.get') as credentials:
            with self.assertRaisesRegex(ConfigError, 'Run-local ledger'):
                self.probe()
            credentials.assert_not_called()
        self.factory.assert_not_called()

    def test_canonical_ledger_symlink_cannot_be_hidden_by_regular_alias(self):
        ledger = self.state/'run.sqlite'
        actual = self.root/'actual.sqlite'
        ledger.rename(actual)
        ledger.symlink_to(actual)
        self.edit_state(ledger_path=str(actual))
        with patch('bcs_e2e.probe.os.environ.get') as credentials:
            with self.assertRaisesRegex(ConfigError, 'Run-local ledger'):
                self.probe()
            credentials.assert_not_called()
        self.factory.assert_not_called()

    def test_scope_failure_and_close_failure_preserve_primary_failure(self):
        with patch('bcs_e2e.probe._owned_ws_scope', side_effect=[{}, ConfigError(self.token)]):
            self.close.side_effect = RuntimeError(self.token)
            result = self.probe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failure_phases'], ['SCOPE_RECHECK', 'LOCAL_CLOSE'])
        self.assertEqual(result['socket_close_status'], 'UNCONFIRMED')
        self.assertNotIn('evidence', result)
        self.close.assert_called_once_with()

    def test_scope_cancellation_stays_cancelled_when_close_fails(self):
        with patch('bcs_e2e.probe._owned_ws_scope', side_effect=[{}, KeyboardInterrupt(self.token)]):
            self.close.side_effect = RuntimeError(self.token)
            result = self.probe()
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['failure_phases'], ['SCOPE_RECHECK', 'LOCAL_CLOSE'])
        self.assertEqual(result['socket_close_status'], 'UNCONFIRMED')
        self.close.assert_called_once_with()

    def test_invalid_evidence_is_blocked_but_connection_closed(self):
        self.client.connect = Mock(return_value=(self.client.connection, {'invalid': object()}))
        result = self.probe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failure_phases'], ['EVIDENCE'])
        self.assertEqual(result['socket_close_status'], 'CLOSED')
        self.assertNotIn('evidence', result)
        self.close.assert_called_once_with()

    def test_missing_connection_is_not_success(self):
        self.client.connect = Mock(return_value=(None, {}))
        result = self.probe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failure_phases'], ['HANDSHAKE'])
        self.assertEqual(result['socket_close_status'], 'NOT_ACQUIRED')
        self.close.assert_not_called()

    def test_resource_snapshot_drift_with_same_run_state_blocks(self):
        connect = self.client.connect
        def changing_connect(*args):
            result = connect(*args)
            with Ledger(self.state/'run.sqlite', 'ws-run', fingerprint(self.profile)) as ledger:
                session = next(row for row in ledger.resource_snapshot() if row['kind'] == 'session')
                ledger.mark_deleted(session['handle'])
            return result
        self.client.connect = changing_connect
        result = self.probe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failure_phases'], ['SCOPE_RECHECK'])
        self.close.assert_called_once_with()

    def test_cli_cancelled_exit_is_130(self):
        path = self.root/'profile.json'
        path.write_text(json.dumps(self.profile))
        output = io.StringIO()
        with patch('bcs_e2e.cli.ws_probe', return_value={'status': 'CANCELLED'}), \
             contextlib.redirect_stdout(output):
            rc = main(['probe', 'ws', '--profile', str(path), '--state-dir', str(self.state),
                       '--run-id', 'ws-run', '--token-env', 'BCS_E2E_SESSION_TOKEN',
                       '--group-id', 'g-1', '--session-id', 's-1', '--allow-read'])
        self.assertEqual(rc, 130)
        self.assertEqual(json.loads(output.getvalue())['status'], 'CANCELLED')


if __name__ == '__main__':
    unittest.main()
