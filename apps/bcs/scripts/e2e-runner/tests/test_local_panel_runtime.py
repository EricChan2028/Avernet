"""Opt-in real asset serving, with default and failure lifecycle regression."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_panel_asset import LocalPanelAsset
from bcs_e2e.local_panel_probe import verify_owned_panel
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.run_state import read
from bcs_e2e.runtime import RuntimeContractError

BINARY=os.environ.get('BCS_E2E_TEST_BINARY','')
DIGEST=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')
ASSET=Path(os.environ.get('BCS_E2E_TEST_PANEL_ASSET',str(ROOT.parents[1]/'assets/panel/dist/index.umd.js')))
ASSET_SHA=os.environ.get('BCS_E2E_TEST_PANEL_SHA256','')


class PanelConfigTests(unittest.TestCase):
    def test_default_has_no_panel_or_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs=prepare_local_bcs_inputs(Path(tmp)/'runtime',run_id='no-panel',port=45001)
            self.assertEqual(json.loads(inputs.config_path.read_text())['manifest']['bundles'],[])
            self.assertFalse((inputs.root/'panel').exists())
            self.assertFalse((inputs.root/'panel-asset.json').exists())

    def test_invalid_asset_is_denied_before_creating_runtime_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'runtime'
            with self.assertRaises(RuntimeContractError):
                prepare_local_bcs_inputs(root,run_id='bad-panel',port=45001,panel_asset=object())
            self.assertFalse(root.exists())

    def test_probe_rejects_unmanaged_objects(self):
        with self.assertRaises(RuntimeContractError):verify_owned_panel(object())

    def test_cli_partial_asset_arguments_do_not_start_runtime(self):
        with tempfile.TemporaryDirectory() as tmp,patch('bcs_e2e.cli.local_runtime_smoke') as smoke:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc=main(['runtime','smoke','--profile',str(ROOT/'profiles/local.singlebox.example.json'),
                         '--state-dir',str(Path(tmp)/'state'),'--panel-asset','/not-used.js'])
            self.assertEqual(rc,2)
            self.assertEqual(json.loads(output.getvalue())['status'],'BLOCKED')
            smoke.assert_not_called()


@unittest.skipUnless(BINARY and DIGEST and ASSET.is_file() and ASSET_SHA and shutil.which('lsof'),
                     'approved actual BCS and built panel asset SHA256 required')
class PanelRuntimeLiveTests(unittest.TestCase):
    def test_actual_manifest_smoke_uses_private_copy_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc=main(['runtime','smoke','--profile',str(ROOT/'profiles/local.singlebox.example.json'),
                         '--state-dir',str(root/'state'),'--binary',BINARY,'--binary-sha256',DIGEST,
                         '--run-id','panel-smoke','--allow-local-processes',
                         '--panel-asset',str(ASSET),'--panel-sha256',ASSET_SHA])
            report=json.loads(output.getvalue());self.assertEqual(rc,0,report)
            self.assertEqual(report['panel_asset']['status'],'PASS')
            self.assertEqual(report['panel_asset']['sha256'],ASSET_SHA)
            self.assertEqual(report['panel_asset']['full_story_status'],'NOT_MIGRATED')
            self.assertEqual(report['shutdown']['status'],'STOPPED')
            owned=Path(report['runtime_dir'])
            self.assertEqual(hashlib.sha256((owned/'panel/index.umd.js').read_bytes()).hexdigest(),ASSET_SHA)
            self.assertEqual((owned/'panel/index.umd.js').stat().st_mode&0o777,0o600)
            self.assertTrue((owned/'panel-asset.json').is_file())

    def test_mutated_owned_copy_is_rejected_and_child_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'runtime'
            with OwnedBcsProcess(binary=Path(BINARY),binary_sha256=DIGEST,root=root,run_id='changed-panel',
                                 panel_asset=LocalPanelAsset(ASSET,ASSET_SHA)) as runtime:
                (runtime.root/'panel/index.umd.js').write_text('changed')
                with self.assertRaises(RuntimeContractError):verify_owned_panel(runtime)
            self.assertEqual(read(root/'owned-bcs.json')['status'],'STOPPED')

    def test_forged_foreign_manifest_is_rejected_without_fetching_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'runtime'
            with OwnedBcsProcess(binary=Path(BINARY),binary_sha256=DIGEST,root=root,run_id='foreign-panel',
                                 panel_asset=LocalPanelAsset(ASSET,ASSET_SHA)) as runtime:
                class Response:
                    status=200
                    def __enter__(self):return self
                    def __exit__(self,*a):return None
                    def read(self,size):return json.dumps({'schema_version':1,'env':'local','bundles':[{'name':'bcsPanel','url':'https://foreign.invalid/a.js'}]}).encode()
                with patch('bcs_e2e.local_panel_probe.build_opener') as opener:
                    opener.return_value.open.return_value=Response()
                    with self.assertRaises(RuntimeContractError):verify_owned_panel(runtime)
                    self.assertEqual(opener.return_value.open.call_count,1)
            self.assertEqual(read(root/'owned-bcs.json')['status'],'STOPPED')

    def test_missing_permission_blocks_before_asset_runtime_creation(self):
        with tempfile.TemporaryDirectory() as tmp,patch('bcs_e2e.cli.local_runtime_smoke') as smoke:
            with contextlib.redirect_stdout(io.StringIO()):
                rc=main(['runtime','smoke','--profile',str(ROOT/'profiles/local.singlebox.example.json'),
                         '--state-dir',str(Path(tmp)/'state'),'--binary',BINARY,'--binary-sha256',DIGEST,
                         '--run-id','denied-panel','--panel-asset',str(ASSET),'--panel-sha256',ASSET_SHA])
            self.assertEqual(rc,2);smoke.assert_not_called()


if __name__=='__main__':unittest.main()
