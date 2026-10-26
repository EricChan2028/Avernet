"""Real product catalog plus bounded failure tests; no business write or engine."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_template_asset import CONTRACT, LocalTemplateAsset
from bcs_e2e.local_template_probe import PATHS, TEMPLATE, _get, verify_owned_templates
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.run_state import read, write
from bcs_e2e.runtime import RuntimeContractError

SEEDS = ROOT.parents[1] / 'seeds/collaboration-templates'
BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


def approved_asset(root):
    files = []
    for path in sorted(SEEDS.rglob('*.yaml')):
        raw = path.read_bytes()
        files.append({'path': path.relative_to(SEEDS).as_posix(),
                      'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)})
    manifest = root / 'approved-templates.json'
    write(manifest, {'schema_version': 1, 'contract': CONTRACT, 'files': files})
    return LocalTemplateAsset(SEEDS, manifest, hashlib.sha256(manifest.read_bytes()).hexdigest())


class TemplateProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.asset = approved_asset(root)
        inputs = prepare_local_bcs_inputs(root / 'runtime', run_id='template-unit', port=45001,
                                          template_asset=self.asset)
        runtime = object.__new__(OwnedBcsProcess)
        runtime.template_asset = self.asset
        runtime.inputs = inputs
        runtime.root = inputs.root
        runtime.run_id = 'template-unit'
        runtime.state = 'RUNNING'
        runtime.ready = True
        runtime.child = Mock()
        runtime.child.poll.return_value = None
        runtime._owns_listener = Mock(return_value=True)
        self.runtime = runtime
        self.catalog = {'templates': [{'id': path.stem} for path in sorted((SEEDS / 'zh-CN').glob('*.yaml'))],
                        'supported_languages': ['en-US', 'zh-CN']}
        self.detail = {'id': TEMPLATE, 'lang': 'zh-CN',
                       'yaml': (SEEDS / 'zh-CN' / (TEMPLATE + '.yaml')).read_text()}

    def test_exact_catalog_and_yaml_are_verified_without_claiming_full_story(self):
        with patch('bcs_e2e.local_template_probe._get', side_effect=[self.catalog, self.detail]) as get:
            result = verify_owned_templates(self.runtime)
        self.assertEqual([call.args for call in get.call_args_list], [(45001, path) for path in PATHS])
        self.assertEqual(result['scope'], 'OWNED_TEMPLATE_CATALOG_ONLY')
        self.assertEqual(result['full_story_status'], 'NOT_MIGRATED')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')

    def test_foreign_runtime_dead_child_origin_and_tampered_copy_block_before_http(self):
        for mutation in ('type', 'child', 'origin', 'copy', 'receipt'):
            with self.subTest(mutation=mutation):
                runtime = self.runtime
                if mutation == 'type': runtime = object()
                elif mutation == 'child': runtime.child.poll.return_value = 1
                elif mutation == 'origin':
                    runtime.inputs = replace(runtime.inputs, origin='https://foreign.invalid')
                elif mutation == 'copy':
                    (runtime.root / 'templates/zh-CN/single-bot-guided-answer.yaml').write_text('changed')
                else:
                    receipt = read(runtime.root / 'template-assets.json')
                    receipt['run_id'] = 'foreign'
                    write(runtime.root / 'template-assets.json', receipt)
                with patch('bcs_e2e.local_template_probe._get') as get:
                    with self.assertRaises(RuntimeContractError): verify_owned_templates(runtime)
                    get.assert_not_called()
                # Restore only this unit fixture between failure injections.
                runtime = self.runtime
                runtime.child.poll.return_value = None
                runtime.inputs = replace(runtime.inputs, origin='http://127.0.0.1:45001')
                if mutation == 'copy':
                    shutil.copyfile(SEEDS / 'zh-CN/single-bot-guided-answer.yaml',
                                    runtime.root / 'templates/zh-CN/single-bot-guided-answer.yaml')

    def test_catalog_missing_duplicate_foreign_and_yaml_drift_never_pass(self):
        for mutation in ('missing', 'duplicate', 'foreign', 'yaml', 'language'):
            catalog = json.loads(json.dumps(self.catalog)); detail = dict(self.detail)
            if mutation == 'missing': catalog['templates'].pop()
            elif mutation == 'duplicate': catalog['templates'].append(catalog['templates'][0])
            elif mutation == 'foreign': catalog['templates'][0]['id'] = 'foreign'
            elif mutation == 'yaml': detail['yaml'] += '\n# drift'
            else: detail['lang'] = 'en-US'
            with self.subTest(mutation=mutation), patch('bcs_e2e.local_template_probe._get', side_effect=[catalog, detail]):
                with self.assertRaises(RuntimeContractError): verify_owned_templates(self.runtime)

    def test_directory_scan_stops_at_bound_instead_of_materializing_all_entries(self):
        from bcs_e2e.local_template_asset import _bounded_entries
        seen = []
        def entries():
            for index in range(1000):
                seen.append(index)
                yield Mock(name='file')
        scan = Mock()
        scan.__enter__ = Mock(return_value=entries())
        scan.__exit__ = Mock(return_value=False)
        with patch('bcs_e2e.local_template_asset.os.scandir', return_value=scan):
            with self.assertRaisesRegex(RuntimeContractError, 'directory-entry limit'):
                _bounded_entries(Path('/unused'), 4)
        self.assertEqual(seen, [0, 1, 2, 3, 4])
        scan.__exit__.assert_called_once()

    def test_invalid_path_and_redirect_are_not_followed(self):
        with patch('bcs_e2e.local_template_probe.HTTPConnection') as wire:
            with self.assertRaises(RuntimeContractError): _get(45001, '/groups')
            wire.assert_not_called()
            response = Mock(status=302)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            wire.return_value.getresponse.return_value = response
            with self.assertRaises(RuntimeContractError): _get(45001, PATHS[0])
            self.assertEqual(wire.return_value.request.call_count, 1)
            wire.return_value.close.assert_called_once()


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'), 'approved actual BCS binary required')
class TemplateRuntimeLiveTests(unittest.TestCase):
    def test_actual_product_reads_pinned_catalog_and_stops_owned_child(self):
        # Optional caller-owned evidence directory preserves this single run.
        evidence = os.environ.get('BCS_E2E_TEMPLATE_EVIDENCE_DIR')
        if evidence:
            root = Path(evidence); root.mkdir(mode=0o700, parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
        asset = approved_asset(root)
        with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST, root=root / 'runtime',
                             run_id='template-catalog', template_asset=asset) as runtime:
            result = verify_owned_templates(runtime)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(len(result['operations']), 2)
            self.assertFalse((runtime.root / 'template-assets.json').is_symlink())
        shutdown = read(runtime.root / 'owned-bcs.json')
        self.assertEqual(shutdown['status'], 'STOPPED')
        self.assertEqual(shutdown['returncode'], 0)
        write(root / 'catalog-acceptance.json', {'result': result, 'shutdown': shutdown,
                                               'bcs_binary_sha256': DIGEST})
