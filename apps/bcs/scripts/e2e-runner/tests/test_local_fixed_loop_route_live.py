"""Explicitly opt-in live fixed-Loop route + per-Run Judge integration tests.

Not a substitute for fixed-Loop parent Story execution.  This verifies
the prerequisite the full LOCAL-FIXED-LOOP-01 executor depends on: the
owned-local runtime boots with a fresh per-Run Provider/Judge sandbox
(FIXED_LOOP.sandbox-isolation's reset-replacement) wired in as the
deterministic OpenAI-compatible Judge, and the fixed-Loop state-machine
route family used by the original Story is served there.
"""
from pathlib import Path
import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.owned_bcs import OwnedBcsProcess, file_sha256
from bcs_e2e.local_judge_sandbox import OwnedLocalJudgeSandbox

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'),
                     'set BCS_E2E_TEST_BINARY and SHA256 with lsof for live BCS tests')
class LiveFixedLoopRouteTests(unittest.TestCase):
    def _probe(self, opener, url):
        """Return (status, body-bytes) for a route probe, capturing HTTP errors."""
        try:
            with opener.open(url, timeout=5) as response:
                return response.status, response.read(4097)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read(4097)

    def test_per_run_judge_sandbox_is_the_admitted_llm_and_routes_are_served(self):
        self.assertRegex(DIGEST, r'^[0-9a-f]{64}$')
        judge = OwnedLocalJudgeSandbox('live-fixed-loop-route-1')
        judge.start()
        binary = Path(BINARY)
        opener = build_opener(ProxyHandler({}))
        with tempfile.TemporaryDirectory() as tmp:
            # A non-existent subpath: prepare_local_bcs_inputs creates the
            # recovery-protected runtime root itself (never adopts an existing one).
            root = Path(tmp) / 'state'
            with OwnedBcsProcess(binary=binary, binary_sha256=file_sha256(binary),
                                 root=root, run_id='live-fixed-loop-route-1',
                                 judge_sandbox=judge, store_messages=True,
                                 startup_timeout=30) as owned:
                self.assertTrue(owned.ready)
                self.assertTrue(owned.inputs.origin.startswith('http://127.0.0.1:'))
                # The per-Run Provider/Judge sandbox is the admitted openai-compatible
                # Judge, replacing any shared /control reset, /control provider clear
                # or /control judge outcomes control-plane endpoint.  Its run-scoped
                # key is injected into the child environment, never echoed to a report.
                configuration = json.loads(owned.inputs.config_path.read_text())
                self.assertEqual(configuration['llm']['type'], 'openai_compatible')
                self.assertEqual(configuration['llm']['base_url'], judge.judge_base_url)
                self.assertEqual(configuration['llm']['model'], 'owned-local-slow-judge')
                self.assertTrue(configuration['security']['outbound_url']['allow_loopback'])
                self.assertEqual(
                    owned.inputs.environment['BCS_E2E_LOCAL_JUDGE_KEY'],
                    judge.judge_api_key)
                # Single-leader standalone health on the owned listener.
                health_status, health_body = self._probe(
                    opener, owned.inputs.origin + '/health')
                self.assertEqual(health_status, 200)
                self.assertIn(b'ok', health_body)
                # The fixed-Loop history-graph route family is served: a missing
                # Run yields the product JSON not-found envelope (route matched),
                # not an HTML/connection route-not-found signal.
                missing = '0' * 32
                graph_status, graph_body = self._probe(
                    opener, owned.inputs.origin + f'/state-machine-runs/{missing}/graph')
                self.assertEqual(graph_status, 404)
                self.assertNotIn(b'<', graph_body)
                self.assertEqual(json.loads(graph_body.decode()), {'error': 'not_found'})
                # The approved-Loop run creation route is served and is POST-only
                # (the original Story's approved-loop obligation uses POST), so a GET
                # here is a matched-route 405 rather than a 404 route-not-found.
                loop_status, _loop_body = self._probe(
                    opener, owned.inputs.origin + f'/groups/{missing}/state-machine-runs')
                self.assertEqual(loop_status, 405)
            # Graceful in-band termination: only the directly owned child was
            # reaped, preserving the per-Run Judge sandbox scoped to this Run.
            self.assertEqual(owned.child.returncode, 0)
            self.assertEqual(owned.state, 'STOPPED')
            self.assertFalse(owned.forced_stop)
        judge.close()
        judge_snapshot = judge.snapshot()
        self.assertEqual(judge_snapshot['state'], 'CLOSED')
        self.assertNotIn('judge_api_key', json.dumps(judge_snapshot))
        self.assertNotIn('judge_base_url', json.dumps(judge_snapshot))


if __name__ == '__main__':
    unittest.main()
