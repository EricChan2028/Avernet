"""A green HTTP/DB check does not admit implicit remote Bot execution."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from bcs_e2e.adapters import RemotePilotAdapter, adapter_for
from bcs_e2e.cli import plan
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import run_case
from bcs_e2e.profile import fingerprint
from bcs_e2e.remote_effects import RemoteBootstrapAdmissionError, remote_effect_admission
from bcs_e2e.run_state import read
import test_remote_observer_cli as harness


class RemoteEffectTests(unittest.TestCase):
    command = harness.RemoteObserverCliTests.command
    prepare = harness.RemoteObserverCliTests.prepare

    def setUp(self):
        harness.RemoteObserverCliTests.setUp(self)

    def test_plan_exposes_blocker_even_when_mapping_and_exclusivity_confirmed(self):
        self.assertTrue(self.profile['policy']['exclusive_use_confirmed'])
        self.assertTrue(self.profile['observer']['environment_binding_confirmed'])
        result = plan(self.profile)
        self.assertEqual(result['effect_admission']['status'], 'BLOCKED')
        self.assertFalse(result['ready_to_execute'])

    def test_real_execution_adapter_blocks_before_credentials_or_clients(self):
        with patch('bcs_e2e.adapters.ReadClient') as read_client, \
             patch('bcs_e2e.adapters.WriteClient') as write_client:
            with self.assertRaises(RemoteBootstrapAdmissionError):
                adapter_for(self.profile, state_dir=self.state, case_id='PRE-01')
        read_client.assert_not_called(); write_client.assert_not_called()

    def test_readonly_adapter_remains_available(self):
        with patch('bcs_e2e.adapters.ReadClient'), patch('bcs_e2e.adapters.WriteClient'), \
             patch('bcs_e2e.adapters.RemoteFileClient'):
            self.assertIsInstance(adapter_for(self.profile), RemotePilotAdapter)

    def test_direct_remote_group_create_cannot_bypass_effect_admission(self):
        adapter = RemotePilotAdapter.__new__(RemotePilotAdapter)
        adapter.profile = self.profile; adapter.write = Mock()
        with self.assertRaises(RemoteBootstrapAdmissionError):
            adapter.group_create({'group_kind': 'normal', 'collaboration': {'strategy': 'chat'}})
        adapter.write.call.assert_not_called()

    def test_actual_case_reports_not_run_and_no_resource_intents_or_api_calls(self):
        self.prepare()
        with patch('bcs_e2e.adapters.ReadClient') as client:
            result = run_case(self.profile, self.state, self.run_id, 'PRE-01')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertEqual(result['cleanup'], 'NOT_ATTEMPTED')
        self.assertEqual(result['assertions'], [{'id': 'PRE-01.effect_admission', 'status': 'BLOCKED'}])
        self.assertIn('bootstrap', result['remediation']['reason'])
        client.assert_not_called()
        with Ledger(self.state / 'run.sqlite', self.run_id, fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual(ledger.resource_snapshot(), [])
        self.assertEqual(read(self.state / 'run.json')['quiescence'], 'CONFIRMED')
        self.assertEqual(read(self.state / 'report-PRE-01.json')['functional'], 'NOT_RUN')

    def test_no_profile_switch_suppresses_remote_blocker(self):
        for kind in ('gateway', 'bcs-direct'):
            for tier in ('local', 'preprod'):
                profile = json.loads(json.dumps(self.profile))
                profile['entrypoint']['kind'] = kind; profile['environment']['tier'] = tier
                self.assertEqual(remote_effect_admission(profile)['status'], 'BLOCKED')

    def test_reviewed_preprod_effect_scope_admits_remote_execution(self):
        profile = json.loads(json.dumps(self.profile))
        profile['policy']['remote_effect_review'] = {
            'isolated_test_runtime': True,
            'no_operational_channels': True,
            'implicit_bot_execution_allowed': True,
        }
        admission = remote_effect_admission(profile)
        self.assertEqual(admission['status'], 'ADMITTED')
        self.assertTrue(admission['grants_write_authority'])
        plan_result = plan(profile)
        self.assertEqual(plan_result['effect_admission']['status'], 'ADMITTED')
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'synthetic-cookie'}):
            adapter = adapter_for(profile, state_dir=self.state, case_id='PRE-01')
            self.assertEqual(adapter.termination_mode, 'REVIEWED_REMOTE')
            adapter.close_production_channels('session-id')
            adapter.quiesce_execution('session-id')
            adapter.close_termination_channel('session-id')

    def test_remote_effect_review_requires_exact_reviewed_preprod_scope(self):
        from bcs_e2e.profile import ConfigError
        profile = json.loads(json.dumps(self.profile))
        profile['environment']['tier'] = 'local'
        profile['policy']['remote_effect_review'] = {
            'isolated_test_runtime': True, 'no_operational_channels': True,
            'implicit_bot_execution_allowed': True}
        with self.assertRaises(ConfigError):
            remote_effect_admission(profile)

        profile = json.loads(json.dumps(self.profile))
        profile['policy']['remote_effect_review'] = {
            'isolated_test_runtime': True, 'no_operational_channels': True,
            'implicit_bot_execution_allowed': False}
        with self.assertRaises(ConfigError):
            remote_effect_admission(profile)
