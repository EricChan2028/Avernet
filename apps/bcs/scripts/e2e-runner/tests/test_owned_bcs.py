"""Real loopback child-process tests; never manage existing product processes."""
from pathlib import Path
import contextlib
import io
import json
import os
import shutil
import signal
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.owned_bcs import OwnedBcsProcess, file_sha256, local_runtime_smoke
from bcs_e2e.runtime import RuntimeContractError

HELPER = '''
import sys, json, signal
from http.server import BaseHTTPRequestHandler, HTTPServer
config = json.load(open(sys.argv[2]))
MODE
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *_args): pass
with HTTPServer((config['bind'], config['port']), Handler) as server:
    server.serve_forever(poll_interval=.05)
'''


@unittest.skipUnless(shutil.which('lsof'), 'owned-listener verification requires lsof')
class OwnedBcsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.binary = self.root / 'fake-bcs'
        self.write_helper("signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))")

    def write_helper(self, mode):
        self.binary.write_text('#!' + sys.executable + '\n' + HELPER.replace('MODE', mode))
        self.binary.chmod(0o700)

    def runtime(self, name='runtime', **kwargs):
        runtime = OwnedBcsProcess(binary=self.binary, binary_sha256=file_sha256(self.binary),
            root=self.root / name, run_id=name, startup_timeout=5, stop_timeout=1, **kwargs)
        self.addCleanup(runtime._reap)
        return runtime

    def test_two_owned_children_are_isolated_and_one_stop_preserves_the_other(self):
        a, b = self.runtime('a'), self.runtime('b')
        a.start(); b.start()
        self.assertNotEqual(a.port, b.port)
        self.assertNotEqual(a.inputs.human_id, b.inputs.human_id)
        (a.root / 'keep').write_text('retained')
        self.assertEqual(a.stop()['status'], 'STOPPED')
        self.assertIsNone(b.child.poll())
        self.assertTrue(b._owns_listener(1))
        self.assertEqual((a.root / 'keep').read_text(), 'retained')
        self.assertEqual(a.stop()['status'], 'STOPPED')
        self.assertEqual(b.stop()['status'], 'STOPPED')

    def test_occupied_port_blocks_before_spawn_without_touching_listener(self):
        with socket.socket() as other:
            other.bind(('127.0.0.1', 0)); other.listen()
            runtime = self.runtime(port=other.getsockname()[1])
            with patch('bcs_e2e.owned_bcs.subprocess.Popen') as spawn:
                with self.assertRaisesRegex(RuntimeContractError, 'occupied'):
                    runtime.start()
            spawn.assert_not_called()
            self.assertFalse(runtime.root.exists())
            self.assertGreater(other.getsockname()[1], 0)

    def test_digest_and_existing_root_refuse_adoption(self):
        runtime = self.runtime()
        runtime.binary_sha256 = '0' * 64
        with self.assertRaisesRegex(RuntimeContractError, 'SHA256'):
            runtime.start()
        runtime.binary_sha256 = file_sha256(self.binary)
        runtime.root.mkdir()
        (runtime.root / 'owned-bcs.json').write_text(json.dumps({'pid': os.getpid()}))
        with self.assertRaisesRegex(RuntimeContractError, 'already exists'):
            runtime.start()
        with patch('bcs_e2e.owned_bcs.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeContractError, 'no owned child'):
                runtime.stop()
            spawn.assert_not_called()

    def test_early_exit_is_reaped_and_failure_artifacts_are_retained(self):
        self.write_helper('sys.exit(3)')
        runtime = self.runtime()
        with self.assertRaisesRegex(RuntimeContractError, 'exited before readiness'):
            runtime.start()
        self.assertEqual(runtime.child.poll(), 3)
        self.assertEqual(json.loads((runtime.root / 'owned-bcs.json').read_text())['status'], 'FAILED')
        self.assertTrue((runtime.root / 'console.log').is_file())

    def test_health_without_owned_listener_is_never_queried(self):
        runtime = self.runtime()
        runtime.startup_timeout = 1
        with patch.object(runtime, '_owns_listener', return_value=False), \
             patch('bcs_e2e.owned_bcs.build_opener') as http:
            with self.assertRaisesRegex(RuntimeContractError, 'timed out'):
                runtime.start()
        http.return_value.open.assert_not_called()
        self.assertIsNotNone(runtime.child.poll())

    def test_forced_stop_is_not_reported_as_clean_shutdown(self):
        self.write_helper('signal.signal(signal.SIGTERM, signal.SIG_IGN)')
        runtime = self.runtime()
        runtime.start()
        result = runtime.stop()
        self.assertEqual(result['status'], 'FAILED_STOP')
        self.assertTrue(result['forced_stop'])
        self.assertIsNotNone(runtime.child.poll())

    def test_cli_smoke_guard_and_success_restore_signal_handler(self):
        argv = ['runtime', 'smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                '--state-dir', str(self.root / 'cli'), '--run-id', 'cli-smoke',
                '--binary', str(self.binary), '--binary-sha256', file_sha256(self.binary)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv), 2)
        self.assertFalse((self.root / 'cli').exists())
        before = signal.getsignal(signal.SIGTERM)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([*argv, '--allow-local-processes']), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'LOCAL_RUNTIME_SMOKE_PASS')
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        self.assertEqual(result['shutdown']['returncode'], 0)

    def test_sigterm_interrupt_reaps_child_and_restores_previous_handler(self):
        original = OwnedBcsProcess._owns_listener
        observed = []
        def interrupt_when_ready(runtime, timeout):
            owns = original(runtime, timeout)
            if owns:
                observed.append(runtime)
                os.kill(os.getpid(), signal.SIGTERM)
            return owns
        before = signal.getsignal(signal.SIGTERM)
        with patch.object(OwnedBcsProcess, '_owns_listener', interrupt_when_ready):
            with self.assertRaises(KeyboardInterrupt):
                local_runtime_smoke(binary=self.binary, binary_sha256=file_sha256(self.binary),
                    root=self.root / 'cancelled', run_id='cancelled', startup_timeout=5, stop_timeout=1)
        self.assertEqual(len(observed), 1)
        self.assertIsNotNone(observed[0].child.poll())
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        self.assertEqual(json.loads((self.root / 'cancelled/owned-bcs.json').read_text())['status'], 'FAILED')

    def test_fixture_smoke_requires_separate_write_permission_before_start(self):
        argv = ['runtime', 'fixture-smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                '--state-dir', str(self.root / 'fixture-cli'), '--run-id', 'fixture-cli',
                '--binary', str(self.binary), '--binary-sha256', file_sha256(self.binary),
                '--allow-local-processes']
        with contextlib.redirect_stdout(io.StringIO()), patch('bcs_e2e.cli.local_runtime_smoke') as smoke:
            self.assertEqual(main(argv), 2)
            smoke.assert_not_called()
        self.assertFalse((self.root / 'fixture-cli').exists())

    def test_fixture_failure_still_reaps_runtime_and_reports_blocked(self):
        from bcs_e2e.local_fixtures import LocalFixtureError
        state = self.root / 'fixture-failure'
        with patch('bcs_e2e.local_fixtures.OwnedLocalFixtures.apply',
                   side_effect=LocalFixtureError('synthetic fixture failure')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(['runtime', 'fixture-smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                       '--state-dir', str(state), '--run-id', 'fixture-failure', '--binary', str(self.binary),
                       '--binary-sha256', file_sha256(self.binary),
                       '--allow-local-processes', '--allow-fixture-write'])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')
        receipt = json.loads((state / 'singlebox-runtime/owned-bcs.json').read_text())
        self.assertEqual(receipt['status'], 'STOPPED')
        self.assertEqual(receipt['returncode'], 0)

    def test_remote_profile_cannot_enter_local_smoke(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('bcs_e2e.cli.local_runtime_smoke') as smoke:
            rc = main(['runtime', 'smoke', '--profile', str(ROOT / 'profiles/preprod.example.json'),
                       '--state-dir', str(self.root / 'remote'), '--allow-local-processes'])
        self.assertEqual(rc, 2)
        smoke.assert_not_called()
