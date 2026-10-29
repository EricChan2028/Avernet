"""Live owned-local run of the collaboration-team parent Story.

This is opt-in and uses one fresh owned BCS process plus CLI fixtures. It
never overstates singlebox coverage as preprod or release qualification.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.collaboration_team_adapter import RemoteCollaborationTeamAdapter
from bcs_e2e.collaboration_team_contract import execute_collaboration_team_case
from bcs_e2e.collaboration_team_profile import validate_collaboration_team_profile
from bcs_e2e.collaboration_team_runner import CollaborationTeamRunner
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.owned_bcs import OwnedBcsProcess

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
BINARY_SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI_BINARY = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_BINARY_SHA = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


@unittest.skipUnless(BINARY and BINARY_SHA and CLI_BINARY and CLI_BINARY_SHA and shutil.which('lsof'),
                     'set BCS_E2E_TEST_BINARY/SHA256 and BCS_E2E_TEST_CLI_BINARY/SHA256 for live BCS/CLI tests')
class LiveCollaborationTeamCaseTests(unittest.TestCase):
    def _admit_pm_friendship(self, identities: OwnedLocalRoleIdentity, target_role: str) -> str:
        """Bootstrap the historical trusted-team prerequisite from PM."""
        origin = identities.runtime.inputs.origin
        target = identities.actor_id(target_role)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            origin + '/friends/request',
            method='POST',
            data=json.dumps({'to_bot': target}).encode('utf-8'),
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + identities._entries['PM'][1],
            })
        with opener.open(request, timeout=10) as response:
            raw = response.read(65_537)
            self.assertIn(response.status, (200, 201))
            value = json.loads(raw)
        row = value.get('data') if isinstance(value, dict) else None
        self.assertIsInstance(row, dict)
        request_id = row.get('id')
        self.assertIsInstance(request_id, str)
        accept = urllib.request.Request(
            origin + '/friends/requests/' + request_id + '/accept',
            method='POST',
            data=b'{}',
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + identities._entries[target_role][1],
            })
        with opener.open(accept, timeout=10) as response:
            raw = response.read(65_537)
            self.assertEqual(response.status, 200)
            value = json.loads(raw)
        self.assertIsInstance(value, dict)
        return target

    def test_owned_local_collaboration_team_case_executes_all_eight_steps(self):
        with tempfile.TemporaryDirectory(prefix='bcs-collaboration-team-live-') as tmp:
            root = Path(tmp) / 'state'
            with OwnedBcsProcess(
                    binary=Path(BINARY),
                    binary_sha256=BINARY_SHA,
                    root=root,
                    run_id='live-collaboration-team-case-1',
                    startup_timeout=30) as runtime:
                fixtures = OwnedLocalFixtures(runtime, fixture_set='trusted-team')
                with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                    fixtures.apply(allow_write=True, role_identity=identities)
                    for role in fixtures.roles:
                        identities.verify(role)
                    self._admit_pm_friendship(identities, 'ENG')
                    self._admit_pm_friendship(identities, 'QA')

                    profile = {
                        'schema_version': 1,
                        'contract': 'dual-collaboration-team-cli-profile-v1',
                        'environment': {'tier': 'local', 'id': 'local', 'expected_bcs_env': 'local'},
                        'entrypoint': {'kind': 'singlebox', 'origin': runtime.inputs.origin,
                                       'api_face': 'legacy', 'manifest_face': 'legacy'},
                        'human': {'user_id': runtime.inputs.human_id},
                        'actors': {},
                        'auth': {
                            'kind': 'owned-local-bot-tokens',
                            'engineering_token_env': 'BCS_E2E_ENG_BOT_TOKEN',
                            'customer_service_token_env': 'BCS_E2E_CS_BOT_TOKEN',
                            'driver_token_env': 'BCS_E2E_PM_BOT_TOKEN',
                            'qa_token_env': 'BCS_E2E_QA_BOT_TOKEN',
                        },
                        'relationships': {
                            'visibility': 'owned-cs-protect-then-public-restore',
                            'friendship': 'owned-eng-cs-request-then-reject',
                            'group': 'two-owned-pm-qa-groups',
                            'cleanup': 'none-runtime-or-operator-created',
                            'retention': 'request-and-group-history',
                        },
                        'policy': {
                            'fixture_pool': 'trusted-team',
                            'exclusive_use_confirmed': True,
                            'timeout_seconds': 30,
                            'max_response_bytes': 65_536,
                            'failure_retention_hours': 24,
                        },
                        'cli': {'transport': 'product-cli', 'command_family': 'collaboration-team',
                                'api_face': 'legacy'},
                    }
                    for logical, role in (('engineering', 'ENG'), ('customer_service', 'CS'),
                                          ('driver', 'PM'), ('qa', 'QA')):
                        profile['actors'][logical] = {
                            'bot_id': identities.actor_id(role),
                            'owner_id': runtime.inputs.human_id,
                            'dedicated': True,
                            'visibility': 'public',
                        }

                    token_values = {
                        profile['auth']['engineering_token_env']: identities._entries['ENG'][1],
                        profile['auth']['customer_service_token_env']: identities._entries['CS'][1],
                        profile['auth']['driver_token_env']: identities._entries['PM'][1],
                        profile['auth']['qa_token_env']: identities._entries['QA'][1],
                    }
                    runner = CollaborationTeamRunner(
                        validate_collaboration_team_profile(profile),
                        binary=Path(CLI_BINARY),
                        binary_sha256=CLI_BINARY_SHA,
                        state_dir=runtime.root,
                    )
                    old_environ = dict(os.environ)
                    os.environ.update(token_values)
                    try:
                        execution = execute_collaboration_team_case(RemoteCollaborationTeamAdapter(runner))
                    finally:
                        os.environ.clear()
                        os.environ.update(old_environ)

                    self.assertEqual(execution.status, 'PASS')
                    self.assertEqual(execution.completed_count, 8)
                    self.assertEqual(execution.total_count, 8)
                    self.assertIsNone(execution.error_type)
                    self.assertTrue(execution.ready_to_execute)
                    self.assertEqual([step.status for step in execution.steps], ['PASS'] * 8)
                    self.assertEqual([step.requirement_id for step in execution.steps], [
                        'CLI-COLLAB-TEAM.protect-visibility',
                        'CLI-COLLAB-TEAM.friend-request-reject',
                        'CLI-COLLAB-TEAM.group-create-read',
                        'CLI-COLLAB-TEAM.group-add-member',
                        'CLI-COLLAB-TEAM.group-list',
                        'CLI-COLLAB-TEAM.group-fuse',
                        'CLI-COLLAB-TEAM.group-complete',
                        'CLI-COLLAB-TEAM.group-terminate',
                    ])

                    snapshot = execution.snapshot()
                    encoded = json.dumps(snapshot, sort_keys=True)
                    for token in token_values.values():
                        self.assertNotIn(token, encoded)
                    for step in execution.steps:
                        self.assertEqual(step.evidence.get('remote_validation'), 'NOT_RUN')
                        self.assertFalse(step.evidence.get('grants_release_credit'))

                self.assertEqual(identities.summary()['credential_memory'], 'CLEARED')
            self.assertEqual(runtime.state, 'STOPPED')
            self.assertEqual(runtime.child.returncode, 0)


if __name__ == '__main__':
    unittest.main()
