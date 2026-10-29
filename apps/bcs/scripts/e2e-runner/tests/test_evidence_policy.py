"""Evidence declarations must match implemented gates before resource admission."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.inventory import validate_evidence_policy
from bcs_e2e.inventory import current_manifest, freeze_manifest, frozen_manifest, validate_manifest
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import RunStateError

ROOT = Path(__file__).resolve().parents[1]


class EvidencePolicyTests(unittest.TestCase):
    def manifests(self):
        return [json.loads((ROOT / name).read_text())
                for name in ('manifest.json', 'retained-manifest.json')]

    def invalid_manifests(self):
        for original in self.manifests():
            for index, case in enumerate(original['cases']):
                for missing in case['required_evidence']:
                    value = copy.deepcopy(original)
                    value['cases'][index]['required_evidence'].remove(missing)
                    yield value
                for added in ('trace', 'logs', 'sqlite', 'unknown'):
                    value = copy.deepcopy(original)
                    value['cases'][index]['required_evidence'].append(added)
                    yield value
                for diagnostic in (None, 'logs', {}, [None], [['logs']], ['logs', 'logs'],
                                   ['trace'], ['api'], ['ob']):
                    value = copy.deepcopy(original)
                    value['cases'][index]['diagnostic_evidence'] = diagnostic
                    yield value
                value = copy.deepcopy(original)
                del value['cases'][index]['diagnostic_evidence']
                yield value
                value = copy.deepcopy(original)
                value['cases'][index]['required_evidence'].remove('ob')
                value['cases'][index]['diagnostic_evidence'].append('ob')
                yield value

    def test_existing_manifests_are_unchanged(self):
        for manifest in self.manifests():
            before = copy.deepcopy(manifest)
            validate_evidence_policy(validate_manifest(manifest))
            self.assertEqual(manifest, before)
            self.assertEqual(current_manifest(manifest['suite']), before)

    def test_required_sources_cannot_be_added_removed_or_demoted(self):
        for manifest in self.invalid_manifests():
            with self.subTest(manifest=manifest), self.assertRaises(RunStateError):
                validate_evidence_policy(validate_manifest(manifest))

    def test_optional_diagnostics_can_be_omitted_or_reordered(self):
        for diagnostic in ([], ['logs'], ['request_correlation'], ['logs', 'request_correlation']):
            manifest = self.manifests()[0]
            manifest['cases'][0]['diagnostic_evidence'] = diagnostic
            manifest['cases'][0]['required_evidence'].reverse()
            validate_evidence_policy(validate_manifest(manifest))

    def test_prepare_blocks_before_manifest_lease_ledger_or_mock_state(self):
        profile = load(ROOT / 'profiles/local.mock.json')
        for index, manifest in enumerate(self.invalid_manifests()):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / 'state'
                with patch('bcs_e2e.prepare.PoolLease.acquire') as lease, \
                     patch('bcs_e2e.prepare.Ledger') as ledger, \
                     patch('bcs_e2e.prepare.MockStateStore') as store:
                    with self.assertRaises(RunStateError):
                        prepare_run(profile, state, 'invalid-evidence', manifest=manifest)
                    lease.assert_not_called(); ledger.assert_not_called(); store.assert_not_called()
                self.assertFalse((state / 'manifest.json').exists())
                self.assertFalse((state / 'run.json').exists())

    def test_current_loader_blocks_invalid_declarations(self):
        for manifest in self.invalid_manifests():
            with patch('bcs_e2e.inventory.read', return_value=manifest), self.assertRaises(RunStateError):
                current_manifest(manifest['suite'])

    def test_unimplemented_case_or_suite_cannot_borrow_an_evidence_contract(self):
        for key, value in (('id', 'PRE-99'), ('suite', 'future-suite')):
            manifest = self.manifests()[0]
            if key == 'id':
                manifest['cases'][0]['id'] = value
            else:
                manifest[key] = value
            with self.assertRaises(RunStateError):
                validate_evidence_policy(manifest)

    def test_historical_snapshot_is_preserved_but_not_admitted_for_new_execution(self):
        historical = self.manifests()[0]
        historical['cases'][0]['required_evidence'].append('historical-trace')
        del historical['cases'][0]['diagnostic_evidence']
        self.assertEqual(validate_manifest(historical), historical)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            path = state / 'manifest.json'
            path.write_text(json.dumps(historical))
            before = path.read_bytes()
            binding = {'manifest_digest': fingerprint(historical), 'executor_digest': 'a' * 64}
            self.assertEqual(frozen_manifest(state, binding), historical)
            with self.assertRaises(RunStateError):
                freeze_manifest(state, historical)
            self.assertEqual(path.read_bytes(), before)
