"""Lifecycle, drift, marker and redaction tests for the Provider subbots run."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.provider_subbots.contract import CASE_ID, STATUS, STORY_CHECKS
from bcs_e2e.provider_subbots.run import (BINDING_NAME, FLOW_NAME, PRINCIPAL_ENV,
                                          _parse_story_marker, _story_environment,
                                          local_provider_subbots_run)
from bcs_e2e.run_state import RunStateError, read
from tests.test_local_provider_subbots_checks import valid_evidence

GOOD_MARKER = ('Provider subbots story: ' + str(STORY_CHECKS)
               + ' live HTTP assertions passed').encode()
OWNED_KEY = 'owned-run-principal-key-never-written-to-evidence'


class FakeOwnedBcs:
    instances = []

    def __init__(self, *, binary, binary_sha256, root, run_id,
                 allow_ambient_local_identity):
        self.root = Path(root)
        self.binary = Path(binary)
        self.binary_sha256 = binary_sha256
        self.run_id = run_id
        self.allow_ambient_local_identity = allow_ambient_local_identity
        self.inputs = SimpleNamespace(
            human_id='e2ehumanabcdef0123456789',
            origin='http://127.0.0.1:12345',
            environment={PRINCIPAL_ENV: OWNED_KEY})
        self.state = 'NEW'
        self.returncode = 0
        FakeOwnedBcs.instances.append(self)

    def __enter__(self):
        self.root.mkdir(parents=True)
        self.root.joinpath('console.log').write_text(
            'owned runtime console clean\n', encoding='utf-8')
        self.state = 'RUNNING'
        (self.root / 'owned-bcs.json').write_text(json.dumps({
            'schema_version': 1, 'run_id': self.run_id, 'status': 'RUNNING',
            'returncode': None, 'ready': True, 'forced_stop': False,
        }), encoding='utf-8')
        return self

    def __exit__(self, *_args):
        self.state = 'STOPPED'
        (self.root / 'owned-bcs.json').write_text(json.dumps({
            'schema_version': 1, 'run_id': self.run_id, 'status': 'STOPPED',
            'returncode': 0, 'ready': False, 'forced_stop': False,
        }), encoding='utf-8')
        return False


def fake_story(evidence=None, *, marker=GOOD_MARKER, exit_code=0, stderr=b'',
               mutate_binding=False):
    def launch(argv, cwd, deadline, environment):
        if environment.get(PRINCIPAL_ENV) != OWNED_KEY:
            raise AssertionError('owned principal key was not injected')
        evidence_path = Path(argv[argv.index('--evidence-file') + 1])
        payload = valid_evidence() if evidence is None else evidence
        evidence_path.write_text(json.dumps(payload), encoding='utf-8')
        if mutate_binding:
            binding_path = Path(cwd) / BINDING_NAME
            value = read(binding_path)
            value['run_id'] = 'changed-after-binding'
            binding_path.write_text(json.dumps(value), encoding='utf-8')
        return {'exit_code': exit_code, 'timed_out': False, 'duration_seconds': 0.01,
                'stdout': marker, 'stderr': stderr, 'process': None}
    return launch


class ProviderSubbotsRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.binary = self.base / 'bcs'
        self.binary.write_bytes(b'owned-bcs-binary')
        self.binary.chmod(0o700)
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.base / 'state'
        self.kwargs = dict(binary=self.binary, binary_sha256=self.sha,
                           state_dir=self.state,
                           run_id='provider-subbots-run',
                           confirm_run_id='provider-subbots-run',
                           allow_local_processes=True, allow_fixture_write=True,
                           allow_write=True)
        FakeOwnedBcs.instances = []

    def tearDown(self):
        self.temp.cleanup()

    def run_story(self, launch):
        with patch('bcs_e2e.provider_subbots.run.OwnedBcsProcess', FakeOwnedBcs), \
                patch('bcs_e2e.provider_subbots.run._launch_story', side_effect=launch):
            return local_provider_subbots_run(**self.kwargs)

    def test_success_publishes_local_suite_pass_without_release_credit(self):
        result = self.run_story(fake_story())
        self.assertEqual(result['status'], STATUS)
        self.assertEqual(result['canonical_summary']['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertFalse(result['canonical_summary']['verification_boundary']
                         ['grants_release_credit'])
        self.assertEqual(result['canonical_summary']['verification_boundary']['preprod'],
                         'REMOTE_UNSUPPORTED')
        self.assertEqual(read(self.state / 'owned-bcs.json')['status'], 'STOPPED')
        self.assertFalse(result['runtime_shutdown']['ready'])
        self.assertEqual(result['child_receipt']['observed_checks'], STORY_CHECKS)
        self.assertEqual(len(result['checks']['checked']), 15)
        for name in ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md'):
            self.assertTrue((self.state / name).is_file())
        summary = (self.state / 'reports/summary.json').read_text(encoding='utf-8')
        self.assertNotIn('Bear' + 'er ', summary)
        self.assertNotIn('provider_admin_token', summary)
        self.assertNotIn(OWNED_KEY, summary)

    def test_bad_marker_wrong_count_or_nonzero_exit_is_blocked(self):
        cases = (
            fake_story(marker=b'Provider subbots story: 1 live HTTP assertions passed'),
            fake_story(marker=GOOD_MARKER + b'\n' + GOOD_MARKER),
            fake_story(exit_code=1),
        )
        for index, launch in enumerate(cases):
            with self.subTest(index=index):
                kwargs = dict(self.kwargs)
                kwargs['state_dir'] = self.base / ('state-bad-' + str(index))
                with patch('bcs_e2e.provider_subbots.run.OwnedBcsProcess', FakeOwnedBcs), \
                        patch('bcs_e2e.provider_subbots.run._launch_story',
                              side_effect=launch):
                    result = local_provider_subbots_run(**kwargs)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['canonical_summary']['status'], 'BLOCKED')

    def test_token_leak_blocks_and_never_writes_child_logs(self):
        result = self.run_story(fake_story(stderr=b'leaked ' + b'Bear' + b'er secret-value'))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RedactionGateError')
        self.assertFalse((self.state / 'provider-subbots-story.err.log').exists())
        self.assertNotIn('secret-value', json.dumps(result))

    def test_binding_or_runtime_drift_is_blocked(self):
        result = self.run_story(fake_story(mutate_binding=True))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['canonical_summary']['status'], 'BLOCKED')

    def test_permissions_fresh_state_and_exact_confirmation_are_required(self):
        for field, value in (('allow_local_processes', False),
                             ('allow_fixture_write', False), ('allow_write', False),
                             ('confirm_run_id', 'different')):
            with self.subTest(field=field):
                kwargs = dict(self.kwargs)
                kwargs[field] = value
                with self.assertRaises(RunStateError):
                    with patch('bcs_e2e.provider_subbots.run.OwnedBcsProcess',
                               FakeOwnedBcs), \
                            patch('bcs_e2e.provider_subbots.run._launch_story',
                                  side_effect=fake_story()):
                        local_provider_subbots_run(**kwargs)
        self.state.mkdir()
        with self.assertRaisesRegex(RunStateError, 'fresh state directory'):
            with patch('bcs_e2e.provider_subbots.run.OwnedBcsProcess', FakeOwnedBcs), \
                    patch('bcs_e2e.provider_subbots.run._launch_story',
                          side_effect=fake_story()):
                local_provider_subbots_run(**self.kwargs)

    def test_marker_parser_requires_exactly_one_complete_marker(self):
        self.assertEqual(_parse_story_marker(GOOD_MARKER.decode()), (True, STORY_CHECKS))
        self.assertEqual(_parse_story_marker('noise\n' + GOOD_MARKER.decode()),
                         (True, STORY_CHECKS))
        self.assertEqual(_parse_story_marker('Provider subbots story: 131 checks'),
                         (False, 0))
        self.assertEqual(_parse_story_marker(
            GOOD_MARKER.decode() + '\n' + GOOD_MARKER.decode()), (False, 0))

    def test_child_environment_contains_only_the_owned_signing_key(self):
        owned = SimpleNamespace(inputs=SimpleNamespace(
            environment={PRINCIPAL_ENV: OWNED_KEY}))
        environment = _story_environment(owned, self.base)
        self.assertEqual(environment[PRINCIPAL_ENV], OWNED_KEY)
        self.assertEqual(set(environment) - {PRINCIPAL_ENV},
                         {'PATH', 'HOME', 'TMPDIR', 'LANG', 'TZ',
                          'PYTHONIOENCODING'})
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', environment)


if __name__ == '__main__':
    unittest.main()
