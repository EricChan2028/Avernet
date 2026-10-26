"""Owned-local Edge friend-mutation runner, adapter and fixture-admission
admission gates. None of these tests may create a runtime or touch the
network; every gate must fail closed before any owned work begins."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.edge_friend_mutation_case import CASE_IDS
from bcs_e2e.local_edge_friend_mutations_case import (
    OwnedEdgeFriendFixtureAdmission,
    OwnedEdgeFriendMutationsAdapter,
)
from bcs_e2e.local_edge_friend_mutations_reporting import (
    build_edge_friend_mutations_report,
)
from bcs_e2e.local_edge_friend_mutations_run import (
    local_edge_friend_mutations_run,
)
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.run_state import RunStateError

ROLE_MUTATIONS = ('CEO', 'PM', 'ENG', 'QA')


class EdgeFriendMutationGatingTests(unittest.TestCase):

    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        self.base = Path(base.name)

    def _kwargs(self, **override):
        kwargs = dict(
            binary=Path('/bcs'), binary_sha256='0' * 64,
            state_dir=self.base / 'state', run_id='run-1',
            confirm_run_id='run-1', allow_local_processes=True,
            allow_fixture_write=True, allow_write=True)
        kwargs.update(override)
        return kwargs

    def test_missing_permission_or_run_confirmation_is_rejected_before_any_work(self):
        for override in (
                {'allow_local_processes': False},
                {'allow_fixture_write': False},
                {'allow_write': False},
                {'confirm_run_id': 'other-run'}):
            with self.subTest(override=override), self.assertRaises(RunStateError):
                local_edge_friend_mutations_run(**self._kwargs(**override))

    def test_existing_state_directory_is_rejected_to_retain_evidence(self):
        existing = self.base / 'existing'
        existing.mkdir()
        with self.assertRaises(RunStateError):
            local_edge_friend_mutations_run(**self._kwargs(state_dir=existing))

    def test_legacy_denominator_drift_is_rejected_before_runtime_creation(self):
        with patch('bcs_e2e.local_edge_friend_mutations_run.current_manifest',
                   return_value={'legacy_inventory': [{'id': 'only-one'}]}):
            with self.assertRaises(RunStateError):
                local_edge_friend_mutations_run(**self._kwargs())

    def test_fixture_admission_requires_the_exact_live_owned_runtime(self):
        with self.assertRaises(RunStateError):
            OwnedEdgeFriendFixtureAdmission('not-an-owned-runtime', bot_id='bot-1')
        runtime = OwnedBcsProcess.__new__(OwnedBcsProcess)
        runtime.inputs = None
        runtime.state = 'STOPPED'
        runtime.ready = False
        runtime.child = None
        with self.assertRaises(RunStateError):
            OwnedEdgeFriendFixtureAdmission(runtime, bot_id='bot-1')

    def test_adapter_requires_exact_owned_objects_and_write_permission(self):
        with self.assertRaises(RunStateError):
            OwnedEdgeFriendMutationsAdapter(
                'runtime', 'identities', 'admission',
                case_id=CASE_IDS[0], allow_write=True)
        with self.assertRaises(RunStateError):
            OwnedEdgeFriendMutationsAdapter(
                'runtime', 'identities', 'admission',
                case_id=CASE_IDS[0], allow_write=False)

    def test_adapter_requires_a_planned_case_id_and_a_ready_owned_runtime(self):
        runtime = OwnedBcsProcess.__new__(OwnedBcsProcess)
        runtime.inputs = None
        runtime.state = 'STOPPED'
        runtime.ready = False
        runtime.child = None
        identities = OwnedLocalRoleIdentity.__new__(OwnedLocalRoleIdentity)
        identities.runtime = runtime
        identities.root = self.base
        identities.run_id = 'run-1'
        identities.roles = ROLE_MUTATIONS
        identities._verified = set()
        admission = OwnedEdgeFriendFixtureAdmission.__new__(OwnedEdgeFriendFixtureAdmission)
        admission.runtime = runtime
        with self.assertRaises(RunStateError):
            OwnedEdgeFriendMutationsAdapter(
                runtime, identities, admission,
                case_id='LOCAL-EDGE-BOGUS-01', allow_write=True)
        with self.assertRaises(RunStateError):
            OwnedEdgeFriendMutationsAdapter(
                runtime, identities, admission,
                case_id=CASE_IDS[0], allow_write=True)

    @staticmethod
    def _admission(root: Path):
        admission = OwnedEdgeFriendFixtureAdmission.__new__(
            OwnedEdgeFriendFixtureAdmission)
        admission.runtime = OwnedBcsProcess.__new__(OwnedBcsProcess)
        admission.runtime.root = root
        admission.runtime.run_id = 'run-1'
        admission.runtime.port = 45123
        admission.runtime.state = 'RUNNING'
        admission.runtime.ready = True
        admission.runtime.child = object()
        admission.bot_id = 'base-owned-bot'
        admission.path = root / 'edge-friend-ensure-admission.json'
        admission.attempted = True
        admission._service_key = 'private-service-key'
        admission.state = {
            'schema_version': 1, 'status': 'PASS',
            'requirement_id': 'EDGE_FRIEND_MUTATIONS.ensure-bot',
            'case_id': 'LOCAL-EDGE-ENSURE-BOT-01',
            'run_id': 'run-1', 'service_key_in_evidence': False,
            'case_fixture_ensures': [],
        }
        return admission

    def test_case_bot_ensure_contract_is_exact_fails_closed_and_secret_free(self):
        root = self.base / 'fixture'
        admission = self._admission(root)

        class Response:
            status = 200
            length = 0
            def __init__(self):
                self._payload = json.dumps(
                    {'ensured': True, 'created': False,
                     'bot_uuid': 'case-owned-bot'}).encode()
            def read(self, *_args):
                value, self._payload = self._payload, b''
                return value
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False

        class Connection:
            def __init__(self, *_args, **_kwargs):
                self.requests = []
                self.closed = False
            def request(self, method, path, body=None, headers=None):
                self.requests.append((method, path, json.loads(body), headers))
            def getresponse(self):
                return Response()
            def close(self):
                self.closed = True

        with patch(
                'bcs_e2e.local_edge_friend_fixture_admission.HTTPConnection',
                Connection) as factory:
            receipt = admission.ensure_case_bot(
                case_id='LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01',
                bot_id='case-owned-bot', staff_no='85020',
                visibility='protected', user_visibility='protected',
                allow_write=True)
        self.assertEqual(receipt['case_id'], 'LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01')
        self.assertEqual(receipt['bot_uuid'], 'case-owned-bot')
        self.assertTrue(receipt['ensured'])
        self.assertFalse(receipt['created'])
        receipt_text = admission.path.read_text(encoding='utf-8')
        self.assertNotIn('private-service-key', receipt_text)

        invalid = {
            'case': {'case_id': 'LOCAL-EDGE-BOGUS-01'},
            'write': {'case_id': 'LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01',
                      'allow_write': False},
        }
        for name, kwargs in invalid.items():
            base = {'bot_id': 'another-bot', 'staff_no': '85020',
                    'visibility': 'protected', 'allow_write': True}
            base.update(kwargs)
            with self.subTest(name=name), self.assertRaises(RunStateError):
                admission.ensure_case_bot(**base)

        with patch(
                'bcs_e2e.local_edge_friend_fixture_admission.HTTPConnection',
                Connection):
            with self.assertRaises(RunStateError):
                admission.ensure_case_bot(
                    case_id='LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01',
                    bot_id='case-owned-bot', staff_no='85020',
                    visibility='protected', allow_write=True)

    def test_offline_report_of_missing_evidence_fails_closed(self):
        with self.assertRaises(RunStateError):
            build_edge_friend_mutations_report(self.base / 'missing')


if __name__ == '__main__':
    unittest.main()
