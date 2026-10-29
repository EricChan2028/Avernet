"""Tests for the Provider subbots slice-owned configuration overlay."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.provider_subbots.contract import CONFIG_REQUIRED
from bcs_e2e.provider_subbots.runtime import (merge_provider_subbots_config,
                                              provider_subbots_config_overlay)
from bcs_e2e.run_state import RunStateError


class ProviderSubbotsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'bcs-config.json'
        self.config.write_text(json.dumps({
            'bind': '127.0.0.1',
            'save_provider_stream': True,
            'provider_stream_gray_enabled': False,
            'provider_stream_gray_created_by': [],
        }), encoding='utf-8')
        os.chmod(self.config, 0o600)

    def tearDown(self):
        self.temp.cleanup()

    def read_config(self):
        return json.loads(self.config.read_text(encoding='utf-8'))

    def test_merge_preserves_shared_config_and_applies_required_pin(self):
        merge_provider_subbots_config(self.config)
        value = self.read_config()
        self.assertEqual(value['bind'], '127.0.0.1')
        self.assertTrue(value['save_provider_stream'])
        for key, expected in CONFIG_REQUIRED.items():
            self.assertEqual(value[key], expected)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_conflicting_provider_policy_fails_closed(self):
        value = self.read_config()
        value['provider_stream_gray_enabled'] = True
        self.config.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(RunStateError, 'conflicts'):
            merge_provider_subbots_config(self.config)
        self.assertTrue(self.read_config()['provider_stream_gray_enabled'])

    def test_symlink_config_is_rejected(self):
        target = self.root / 'target.json'
        target.write_text('{}', encoding='utf-8')
        link = self.root / 'link.json'
        link.symlink_to(target)
        with self.assertRaisesRegex(RunStateError, 'regular unshared file'):
            merge_provider_subbots_config(link)

    def test_overlay_merges_after_the_shared_generator(self):
        import bcs_e2e.owned_bcs as owned_bcs

        def prepare(root, **_kwargs):
            path = Path(root) / 'generated.json'
            Path(root).mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'bind': '127.0.0.1'}), encoding='utf-8')
            os.chmod(path, 0o600)
            return SimpleNamespace(config_path=path)

        with patch.object(owned_bcs, 'prepare_local_bcs_inputs', prepare):
            with provider_subbots_config_overlay():
                inputs = owned_bcs.prepare_local_bcs_inputs(self.root)
                value = json.loads(inputs.config_path.read_text(encoding='utf-8'))
        self.assertEqual(value['bind'], '127.0.0.1')
        for key, expected in CONFIG_REQUIRED.items():
            self.assertEqual(value[key], expected)


if __name__ == '__main__':
    unittest.main()
