"""Freeze once; execute only that artifact; report historical versions offline."""
import contextlib
import copy
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
from bcs_e2e.inventory import current_manifest, executor_digest, freeze_manifest, validate_manifest
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import run_case
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load, fingerprint
from bcs_e2e.reporting import build_report, render_junit, render_markdown
from bcs_e2e.run_state import read, write, RunStateError


class InventoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / 'state'
        self.profile = load(ROOT / 'profiles/local.mock.json')
        guard = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools')
        guard.start(); self.addCleanup(guard.stop)
        prepare_run(self.profile, self.state, 'frozen-run')

    def complete(self):
        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
            result = run_case(self.profile, self.state, 'frozen-run', case)
            self.assertEqual(result['status'], 'PASS')

    def test_run_and_case_reports_bind_manifest_version_and_executor(self):
        state = read(self.state / 'run.json')
        self.assertEqual(state['manifest_digest'], fingerprint(current_manifest()))
        self.assertEqual(state['executor_digest'], executor_digest())
        self.assertEqual((self.state / 'manifest.json').stat().st_mode & 0o777, 0o600)
        self.complete()
        summary = build_report(self.profile, self.state)
        self.assertIn('name="case_version"', render_junit(summary))
        self.assertIn(state['manifest_digest'], render_markdown(summary))
        for case in current_manifest()['cases']:
            report = read(self.state / f"report-{case['id']}.json")
            self.assertEqual(report['case_version'], case['version'])
            self.assertEqual(report['manifest_digest'], state['manifest_digest'])
            self.assertEqual(report['executor_digest'], state['executor_digest'])

    def test_manifest_or_executor_drift_blocks_before_lease_ledger_or_adapter(self):
        changed = copy.deepcopy(current_manifest())
        changed['cases'][0]['version'] += 1
        for patcher in (patch('bcs_e2e.inventory.current_manifest', return_value=changed),
                        patch('bcs_e2e.inventory.executor_digest', return_value='0' * 64)):
            before = (self.state / 'run.json').read_bytes()
            with patcher, patch('bcs_e2e.pilot._case_lease') as lease, \
                 patch('bcs_e2e.pilot.Ledger') as ledger, patch('bcs_e2e.pilot.adapter_for') as adapter:
                with self.assertRaises(RunStateError):
                    run_case(self.profile, self.state, 'frozen-run', 'PRE-01')
                lease.assert_not_called(); ledger.assert_not_called(); adapter.assert_not_called()
            self.assertEqual((self.state / 'run.json').read_bytes(), before)

    def test_historical_report_and_gate_do_not_load_current_inventory_or_artifact(self):
        self.complete()
        snapshot = read(self.state / 'manifest.json')
        changed = copy.deepcopy(snapshot)
        changed['cases'] = changed['cases'][:1]
        changed['cases'][0]['version'] = 999
        with patch('bcs_e2e.inventory.current_manifest', side_effect=AssertionError('must remain offline')), \
             patch('bcs_e2e.inventory.executor_digest', side_effect=AssertionError('not executing code')):
            summary = build_report(self.profile, self.state, changed)
            self.assertEqual(summary['status'], 'PILOT_PASS')
            self.assertEqual(len(summary['cases']), 3)
            self.assertEqual(summary['cases'][0]['version'], snapshot['cases'][0]['version'])
            for command, expected in (('report', 0), ('gate', 2)):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = main([command, '--profile', str(ROOT / 'profiles/local.mock.json'), '--state-dir', str(self.state)])
                self.assertEqual(rc, expected, output.getvalue())
            self.assertIn('NOT_QUALIFIED', output.getvalue())

    def test_tampered_report_versions_or_artifact_cannot_be_counted_as_pass(self):
        self.complete()
        path = self.state / 'report-PRE-01.json'
        original = read(path)
        for field, bad in (('case_version', 999), ('case_version', True), ('manifest_digest', '0' * 64),
                           ('executor_digest', '0' * 64)):
            with self.subTest(field=field, bad=bad):
                write(path, {**original, field: bad})
                summary = build_report(self.profile, self.state)
                self.assertEqual(summary['status'], 'BLOCKED')
                self.assertTrue(any('version mismatch' in text for text in summary['integrity']['issues']))
        write(path, original)

    def test_missing_or_corrupt_snapshot_never_uses_current_file_or_repairs(self):
        path = self.state / 'manifest.json'
        original = path.read_bytes()
        for corrupt in (None, b'{', original.replace(b'Human-owned', b'Changed-old')):
            if path.exists(): path.unlink()
            if corrupt is not None: path.write_bytes(corrupt)
            with self.assertRaises(RunStateError):
                run_case(self.profile, self.state, 'frozen-run', 'PRE-01')
            summary = build_report(self.profile, self.state)
            self.assertEqual(summary['status'], 'BLOCKED')
            self.assertEqual(summary['integrity']['status'], 'BLOCKED')
            if corrupt is None: self.assertFalse(path.exists())
            else: self.assertEqual(path.read_bytes(), corrupt)

    def test_legacy_unbound_run_is_not_retroactively_qualified(self):
        self.complete()
        state = read(self.state / 'run.json')
        state.pop('manifest_digest'); state.pop('executor_digest')
        write(self.state / 'run.json', state)
        originals = {p: p.read_bytes() for p in self.state.glob('*.json')}
        summary = build_report(self.profile, self.state, current_manifest())
        self.assertEqual(summary['status'], 'BLOCKED')
        for path, value in originals.items(): self.assertEqual(path.read_bytes(), value)

    def test_freeze_refuses_overwrite_or_symlink_and_validation_rejects_ambiguous_inventory(self):
        snapshot = current_manifest()
        original = (self.state / 'manifest.json').read_bytes()
        changed = copy.deepcopy(snapshot)
        changed['cases'][0]['version'] += 1
        with self.assertRaises(RunStateError): freeze_manifest(self.state, changed)
        self.assertEqual((self.state / 'manifest.json').read_bytes(), original)
        for mutation in ('duplicate', 'bool-version', 'missing-assertion', 'cross-case-assertion'):
            value = copy.deepcopy(snapshot)
            if mutation == 'duplicate': value['cases'].append(value['cases'][0])
            elif mutation == 'bool-version': value['cases'][0]['version'] = True
            elif mutation == 'missing-assertion': value['cases'][0]['assertions'] = []
            else: value['cases'][0]['assertions'][0] = 'PRE-99.foreign'
            with self.assertRaises(RunStateError): validate_manifest(value)
        (self.state / 'manifest.json').rename(self.state / 'retained.json')
        (self.state / 'manifest.json').symlink_to(self.state / 'retained.json')
        with self.assertRaises(RunStateError): freeze_manifest(self.state, snapshot)

    def test_source_drift_since_process_import_blocks_preparation_before_files(self):
        fresh = self.root / 'stale-process'
        with patch('bcs_e2e.artifact.disk_digest', return_value='0' * 64):
            with self.assertRaises(RunStateError):
                prepare_run(self.profile, fresh, 'stale-process')
        self.assertFalse(fresh.exists())

    def test_rejected_prepare_does_not_backfill_old_run_inventory(self):
        for marker in ('run.json', 'run.sqlite', 'report-PRE-01.json', 'local-run.json', 'runtime.json'):
            with self.subTest(marker=marker):
                state = self.root / ('historical-' + marker)
                state.mkdir()
                (state / marker).write_text('historical data')
                original = {p.name: p.read_bytes() for p in state.iterdir()}
                with self.assertRaises(ValueError):
                    prepare_run(self.profile, state, 'historical')
                self.assertEqual({p.name: p.read_bytes() for p in state.iterdir()}, original)
