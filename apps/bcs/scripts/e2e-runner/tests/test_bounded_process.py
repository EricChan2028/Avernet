"""Real local subprocess probes, not MCP or product deployment validation."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.bounded_process import ProcessCaptureError, capture
from bcs_e2e.alisddbquery import AlisdDbQueryError, McporterAlisdDbQueryClient


class BoundedProcessTests(unittest.TestCase):
    def test_stdout_bytes_are_bounded_and_stderr_is_drained_but_not_returned(self):
        result = capture([sys.executable, '-c', "import sys; print('ok'); print('private',file=sys.stderr); sys.exit(3)"],
                         timeout_seconds=2, max_output_bytes=1024)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, b'ok\n')
        self.assertEqual(result.stderr, b'')

    def test_selected_stderr_capture_is_bounded_and_opt_in(self):
        script = "import sys; print('ok'); print('private',file=sys.stderr); sys.exit(3)"
        result = capture([sys.executable, '-c', script],
                         timeout_seconds=2, max_output_bytes=1024,
                         collect_stderr=True)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, b'ok\n')
        self.assertEqual(result.stderr, b'private\n')

    def test_stdout_stderr_and_combined_floods_abort_before_exit_and_reap_child(self):
        spawn = subprocess.Popen
        for script in ("import os,time; os.write(1,b'x'*100000); time.sleep(30)",
                       "import os,time; os.write(2,b'x'*100000); time.sleep(30)",
                       "import os,time; os.write(1,b'x'*600); os.write(2,b'x'*600); time.sleep(30)"):
            children = []
            def recorded(*args, **kwargs):
                child = spawn(*args, **kwargs); children.append(child); return child
            with patch('bcs_e2e.bounded_process.subprocess.Popen', side_effect=recorded), self.assertRaisesRegex(ProcessCaptureError, 'budget'):
                capture([sys.executable, '-c', script], timeout_seconds=2, max_output_bytes=1024)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())

    def test_closed_pipes_do_not_bypass_process_deadline(self):
        with self.assertRaisesRegex(ProcessCaptureError, 'deadline'):
            capture([sys.executable, '-c', 'import os,time; os.close(1); os.close(2); time.sleep(30)'],
                    timeout_seconds=0.2, max_output_bytes=1024)

    def test_silent_or_valid_json_then_hang_is_not_accepted_before_exit(self):
        for script in ('import time; time.sleep(30)',
                       'import time; print("{}",flush=True); time.sleep(30)'):
            with self.assertRaisesRegex(ProcessCaptureError, 'deadline'):
                capture([sys.executable, '-c', script], timeout_seconds=0.2, max_output_bytes=1024)

    def test_client_keeps_cancellation_even_when_transport_cleanup_raises(self):
        def interrupted(*args, **kwargs):
            try:
                raise KeyboardInterrupt()
            except KeyboardInterrupt:
                raise OSError('private-cleanup-error')
        with patch('bcs_e2e.alisddbquery.capture', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            McporterAlisdDbQueryClient(server='test-observer').db_list()

    def test_invalid_budgets_precede_spawn(self):
        with patch('bcs_e2e.bounded_process.subprocess.Popen') as spawn:
            for timeout, size in ((True, 1), (float('nan'), 1), (0, 1), (1, 0), (1, True)):
                with self.assertRaises(ProcessCaptureError):
                    capture([sys.executable], timeout_seconds=timeout, max_output_bytes=size)
            spawn.assert_not_called()

    def test_cleanup_wait_timeout_preserves_failure_and_closes_pipes(self):
        from unittest.mock import Mock
        import io
        for original in (ProcessCaptureError('original deadline'), KeyboardInterrupt()):
            child = Mock(stdout=io.BytesIO(), stderr=io.BytesIO())
            child.poll.return_value = None
            child.wait.side_effect = subprocess.TimeoutExpired('private-command', 2)
            with self.subTest(original=type(original).__name__), \
                 patch('bcs_e2e.bounded_process.subprocess.Popen', return_value=child), \
                 patch('bcs_e2e.bounded_process.selectors.DefaultSelector', side_effect=original), \
                 self.assertRaises(type(original)) as caught:
                capture([sys.executable], timeout_seconds=1, max_output_bytes=1024)
            self.assertIs(caught.exception, original)
            self.assertEqual(original.child_shutdown_status, 'UNCONFIRMED')
            child.wait.assert_called_once_with(timeout=2)
            child.kill.assert_called_once()
            self.assertTrue(child.stdout.closed)
            self.assertTrue(child.stderr.closed)
            self.assertNotIn('private-command', str(caught.exception))

    def test_adapter_preserves_unconfirmed_shutdown_as_finite_secondary_state(self):
        error = ProcessCaptureError('original deadline')
        error.child_shutdown_status = 'UNCONFIRMED'
        with patch('bcs_e2e.alisddbquery.capture', side_effect=error), self.assertRaises(AlisdDbQueryError) as caught:
            McporterAlisdDbQueryClient(server='test-observer').db_list()
        self.assertEqual(caught.exception.child_shutdown_status, 'UNCONFIRMED')

    def test_actual_client_transport_does_not_leak_output_errors_or_invalid_utf8(self):
        for script in ("import os; os.write(1,b'\\xffprivate')", "import sys; print('private'); sys.exit(2)",
                       "import os,time; os.write(2,b'private'*1000); time.sleep(30)"):
            client = McporterAlisdDbQueryClient(server='test-observer', executable=(sys.executable, '-c', script),
                                               timeout_seconds=1, max_output_bytes=1024)
            with self.assertRaises(AlisdDbQueryError) as caught:
                client.db_list()
            self.assertNotIn('private', str(caught.exception))

    def test_actual_client_transport_decodes_only_the_normalized_result(self):
        script = "import json; print(json.dumps({'success':True,'data':['testdb']}))"
        client = McporterAlisdDbQueryClient(server='test-observer', executable=(sys.executable, '-c', script))
        self.assertEqual(client.db_list(), {'success': True, 'data': ['testdb']})
