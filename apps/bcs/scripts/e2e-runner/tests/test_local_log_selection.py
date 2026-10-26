"""Operator selection and optional-diagnostic cancellation boundaries."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.diagnostics import case_log_diagnostics, collect_log_diagnostics
from bcs_e2e.ledger import Ledger, LedgerError
from bcs_e2e.local_run import _local_logs_result, local_run
from bcs_e2e.profile import ConfigError
from bcs_e2e.log_observer import BoundedLogObserver, DeferredLogObserver, LogObservationError, LogQuery


def interrupted(*args, **kwargs):
    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt:
        raise OSError('private-secondary-error')


class LocalLogSelectionTests(unittest.TestCase):
    def test_selected_empty_or_incomplete_inventory_is_never_all_logs_pass(self):
        self.assertEqual(_local_logs_result(False, []), {'status': 'NOT_SELECTED'})
        self.assertEqual(_local_logs_result(True, [])['status'], 'NOT_RUN')
        partial = [{'case_id':'PRE-01','diagnostic_status':'PASS'}]
        result = _local_logs_result(True, partial)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(len(result['cases']),3)
        self.assertEqual(result['cases'][1]['status'],'NOT_RUN')
        full = [{'case_id':case,'diagnostic_status':'PASS'} for case in ('PRE-01','PRE-02','PRE-03')]
        self.assertEqual(_local_logs_result(True,full)['status'],'PASS')
        full[2]['diagnostic_status']='BLOCKED'
        self.assertEqual(_local_logs_result(True,full)['status'],'INCOMPLETE')
        self.assertFalse(result['required'])

    def test_cli_forwards_selection_without_requiring_remote_profile(self):
        argv=['local-run','--binary','/test/bcs','--binary-sha256','a'*64,
              '--state-dir','/test/state','--run-id','r','--confirm-run-id','r',
              '--allow-local-processes','--allow-fixture-write','--allow-write','--local-logs']
        with patch('bcs_e2e.cli.local_run',return_value={'status':'LOCAL_PILOT_PASS'}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv),0)
        self.assertIs(run.call_args.kwargs['collect_local_logs'],True)
        self.assertNotIn('profile',run.call_args.kwargs)

    def test_non_boolean_selection_blocks_before_startup(self):
        with patch('bcs_e2e.local_run.OwnedBcsProcess') as runtime, self.assertRaises(ConfigError):
            local_run(binary=Path('/unused/bcs'), binary_sha256='a'*64, state_dir=Path('/unused/state'),
                      run_id='r', allow_local_processes=True, allow_fixture_write=True,
                      allow_write=True, confirm_run_id='r', collect_local_logs=1)
        runtime.assert_not_called()

    def test_optional_initialization_is_deferred_and_failed_readmission_invalidates_source(self):
        factory = Mock()
        provider = factory.return_value
        provider.admit.return_value = {'status': 'READY', 'source': 'logs', 'environment': 'local'}
        observer = DeferredLogObserver(factory)
        factory.assert_not_called()
        query = LogQuery('local', request_id='r', start_epoch=1, end_epoch=2)
        with self.assertRaises(LogObservationError): observer.observe(query)
        observer.admit()
        observer.observe(query, timeout_seconds=1)
        provider.observe.assert_called_once_with(query, timeout_seconds=1)
        factory.side_effect = OSError('private-init-error')
        with self.assertRaises(OSError): observer.admit()
        with self.assertRaises(LogObservationError): observer.observe(query)
        self.assertEqual(provider.observe.call_count, 1)

    def test_normalized_log_boundary_does_not_hide_chained_cancellation(self):
        client=Mock(); client.query.side_effect=interrupted
        observer=BoundedLogObserver(client=client,environment='local',environment_binding_confirmed=True)
        with self.assertRaises(KeyboardInterrupt):
            observer.observe(LogQuery('local',request_id='r',start_epoch=1,end_epoch=2))

    def test_optional_collector_and_admission_preserve_chained_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp, Ledger(Path(tmp)/'run.sqlite','r','a'*64,create=True) as ledger:
            ledger.record_correlation('PRE-01',1,{'operation':'group.read','submitted_request_id':'request-1'})
            observer=Mock(); observer.observe.side_effect=interrupted
            with self.assertRaises(KeyboardInterrupt):
                collect_log_diagnostics(ledger,observer,case_id='PRE-01',attempt=1,environment='local',start_epoch=1,end_epoch=2)
            observer.admit.side_effect=interrupted
            with self.assertRaises(KeyboardInterrupt):
                case_log_diagnostics(ledger,observer,case_id='PRE-01',environment='local',start_epoch=1,end_epoch=2)

    def test_diagnostic_persistence_failure_cannot_hide_cancellation(self):
        def failed_persistence(*args):
            try: raise KeyboardInterrupt()
            except KeyboardInterrupt: raise LedgerError('private-ledger-error')
        with tempfile.TemporaryDirectory() as tmp, Ledger(Path(tmp)/'run.sqlite','r','a'*64,create=True) as ledger:
            ledger.record_correlation('PRE-01',1,{'operation':'group.read','submitted_request_id':'request-1'})
            observer=Mock()
            observer.observe.side_effect=lambda query,**kwargs:{'source':'logs','status':'OBSERVED',
                'query':query.public(),'record_count':1,'truncated':False}
            with patch.object(ledger,'record_diagnostic',side_effect=failed_persistence),self.assertRaises(KeyboardInterrupt):
                collect_log_diagnostics(ledger,observer,case_id='PRE-01',attempt=1,environment='local',start_epoch=1,end_epoch=2)
