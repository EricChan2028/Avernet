"""Programmatic preparation shares CLI lease and durable-journal semantics."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load, ConfigError, fingerprint
from bcs_e2e.pool import PoolBusyError
from bcs_e2e.ledger import Ledger


class PrepareTests(unittest.TestCase):
    def test_programmatic_preparation_is_durable_and_cli_can_release_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                profile = load(ROOT / 'profiles/local.mock.json')
                result = prepare_run(profile, root / 'state', 'prepare-programmatic')
                self.assertEqual(result['status'], 'PREPARED')
                with Ledger(root / 'state/run.sqlite', 'prepare-programmatic', fingerprint(profile), read_only=True) as ledger:
                    self.assertEqual(ledger.resource_snapshot(), [])
                with self.assertRaises(PoolBusyError):
                    prepare_run(profile, root / 'another-state', 'second-run')
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(['release', '--state-dir', str(root / 'state')]), 0)
                self.assertEqual(json.loads(output.getvalue())['status'], 'RELEASED')

    def test_unresolved_profile_blocks_before_creating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            with self.assertRaises(ConfigError):
                prepare_run(load(ROOT / 'profiles/local.singlebox.example.json'), state, 'no-actors')
            self.assertFalse(state.exists())
