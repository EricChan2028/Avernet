"""Config-fragment bridge tests for the Draft delivery slice."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bcs_e2e.owned_bcs as owned_bcs
from bcs_e2e.draft_delivery.runtime import (draft_delivery_config_overlay,
                                            merge_draft_config)
from bcs_e2e.run_state import RunStateError


class DraftDeliveryRuntimeConfigTests(unittest.TestCase):
    def test_merge_enables_only_the_slice_owned_section(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bcs-config.json'
            path.write_text(json.dumps({'database': {'type': 'sqlite'}}), encoding='utf-8')
            path.chmod(0o600)
            merge_draft_config(path)
            value = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(value['database'], {'type': 'sqlite'})
            self.assertEqual(value['collaboration_drafts'], {'enabled': True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_conflicting_section_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bcs-config.json'
            path.write_text(json.dumps({'collaboration_drafts': {'enabled': False}}),
                            encoding='utf-8')
            with self.assertRaisesRegex(RunStateError, 'conflicting Draft section'):
                merge_draft_config(path)

    def test_overlay_merges_after_the_shared_preparer_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bcs-config.json'
            path.write_text('{}', encoding='utf-8')
            path.chmod(0o600)
            inputs = SimpleNamespace(config_path=path)
            with patch.object(owned_bcs, 'prepare_local_bcs_inputs', return_value=inputs):
                with draft_delivery_config_overlay():
                    owned_bcs.prepare_local_bcs_inputs()
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),
                             {'collaboration_drafts': {'enabled': True}})


if __name__ == '__main__':
    unittest.main()
