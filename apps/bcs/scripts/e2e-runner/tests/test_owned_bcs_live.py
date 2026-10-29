"""Explicitly opt-in live BCS boot tests, not a substitute for pilot Stories."""
from pathlib import Path
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.ledger import Ledger
from bcs_e2e.local_sqlite import OwnedSqliteEvidenceProvider
from bcs_e2e.local_fixtures import OwnedLocalFixtures, LocalFixtureError

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'),
                     'set BCS_E2E_TEST_BINARY and SHA256 with lsof for live BCS tests')
class LiveOwnedBcsTests(unittest.TestCase):
    def test_live_cli_start_health_and_graceful_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = main(['runtime', 'smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                    '--state-dir', str(Path(tmp) / 'state'), '--binary', BINARY, '--binary-sha256', DIGEST,
                    '--run-id', 'live-bcs-smoke', '--allow-local-processes'])
            self.assertEqual(rc, 0, output.getvalue())
            report = json.loads(output.getvalue())
            self.assertEqual(report['status'], 'LOCAL_RUNTIME_SMOKE_PASS')
            self.assertEqual(report['shutdown']['returncode'], 0)
            self.assertFalse(report['ready_to_execute'])
            root = Path(report['runtime_dir'])
            self.assertTrue((root / 'bcs.sqlite').is_file())
            self.assertTrue((root / 'console.log').is_file())
            self.assertFalse((Path(tmp) / 'state/run.json').exists())

    def test_live_fixture_smoke_uses_generated_actors_and_retains_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = main(['runtime', 'fixture-smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                    '--state-dir', str(Path(tmp) / 'state'), '--binary', BINARY, '--binary-sha256', DIGEST,
                    '--run-id', 'live-fixture-smoke', '--allow-local-processes', '--allow-fixture-write'])
            self.assertEqual(rc, 0, output.getvalue())
            report = json.loads(output.getvalue())
            self.assertEqual(report['status'], 'LOCAL_FIXTURE_SMOKE_PASS')
            self.assertEqual(report['fixtures']['status'], 'VERIFIED')
            self.assertEqual(len(report['fixtures']['roles']), 2)
            self.assertEqual(report['shutdown']['returncode'], 0)
            self.assertFalse(report['ready_to_execute'])
            saved = json.loads((Path(report['runtime_dir']) / 'smoke-report.json').read_text())
            self.assertEqual(saved, report)
            self.assertEqual((Path(report['runtime_dir']) / 'smoke-report.json').stat().st_mode & 0o777, 0o600)
            self.assertNotIn('bot_token', json.dumps(saved))
            self.assertIn('pilot Stories', saved['not_verified'])

    def test_live_expanded_fixture_sets_create_distinct_owned_bots_and_stop(self):
        from bcs_e2e.local_fixtures import FIXTURE_SETS
        for selected in ('workspace', 'trusted-team', 'cli-file'):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as tmp:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = main(['runtime', 'fixture-smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                        '--state-dir', str(Path(tmp) / 'state'), '--binary', BINARY, '--binary-sha256', DIGEST,
                        '--run-id', 'live-' + selected, '--fixture-set', selected,
                        '--allow-local-processes', '--allow-fixture-write'])
                self.assertEqual(rc, 0, output.getvalue())
                report = json.loads(output.getvalue())
                self.assertEqual(report['status'], 'LOCAL_FIXTURE_SMOKE_PASS')
                self.assertEqual(report['shutdown']['status'], 'STOPPED')
                self.assertEqual(report['shutdown']['returncode'], 0)
                fixture = report['fixtures']
                self.assertEqual(fixture['fixture_set'], selected)
                self.assertEqual(set(fixture['roles']), set(FIXTURE_SETS[selected]))
                self.assertEqual(len({v['bot_id'] for v in fixture['roles'].values()}), len(FIXTURE_SETS[selected]))
                self.assertTrue(all(v['owner_id'] == fixture['human']['user_id'] for v in fixture['roles'].values()))
                self.assertEqual(fixture['relationship_baseline'], 'NOT_VERIFIED')
                self.assertEqual(fixture['story_admission'], 'NOT_EVALUATED')
                self.assertFalse(report['ready_to_execute'])
                root = Path(report['runtime_dir'])
                saved = json.loads((root / 'local-fixtures.json').read_text())
                self.assertEqual(saved['status'], 'APPLIED')
                self.assertNotIn('bot_token', json.dumps(saved) + output.getvalue())
                self.assertFalse((Path(tmp) / 'state/run.json').exists())

    def test_live_expanded_receipt_cannot_activate_pilot_profile_or_signer(self):
        from bcs_e2e.local_adapter import resolve_owned_profile
        from bcs_e2e.local_auth import OwnedLocalAuth
        from bcs_e2e.profile import load
        with tempfile.TemporaryDirectory() as tmp:
            with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                 root=Path(tmp) / 'fixtures', run_id='expanded-auth-denial') as runtime:
                handler = OwnedLocalFixtures(runtime, fixture_set='workspace')
                handler.apply(allow_write=True)
                state = json.loads(handler.path.read_text())
                profile = load(ROOT / 'profiles/local.singlebox.example.json')
                profile['auth'] = {'kind': 'owned-local'}
                profile['entrypoint']['origin'] = runtime.inputs.origin
                profile['human']['user_id'] = runtime.inputs.human_id
                profile['environment']['expected_bcs_env'] = 'local'
                profile['actors'] = {role: {'bot_id': state['roles'][source]['bot_id'],
                    'owner_id': runtime.inputs.human_id, 'dedicated': True}
                    for role, source in (('driver', 'CEO'), ('member', 'PM'))}
                for relabel in (False, True):
                    if relabel:
                        state['roles'] = {role: {**state['roles'][source],
                            'name': f'e2e-{role}-{runtime.inputs.human_id}'}
                            for role, source in (('driver', 'CEO'), ('member', 'PM'))}
                        handler.path.write_text(json.dumps(state))
                    with patch('bcs_e2e.local_fixtures.OwnedLocalFixtures._request') as request, \
                         patch('bcs_e2e.local_auth.hmac.new') as signer:
                        with self.assertRaises(LocalFixtureError): resolve_owned_profile(runtime)
                        with self.assertRaises(LocalFixtureError): OwnedLocalAuth(runtime, profile).headers(profile)
                        request.assert_not_called(); signer.assert_not_called()
            self.assertEqual(runtime.child.poll(), 0)

    def test_live_cancel_during_expanded_provisioning_retains_progress_and_reaps_child(self):
        original_request = OwnedLocalFixtures._request
        original_start = OwnedBcsProcess.start
        started = []
        registered = 0
        def start(runtime):
            result = original_start(runtime)
            started.append(runtime)
            return result
        def cancel(handler, operation, **kwargs):
            nonlocal registered
            if operation == 'register':
                registered += 1
                if registered == 3:
                    raise KeyboardInterrupt()
            return original_request(handler, operation, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(OwnedBcsProcess, 'start', start), \
                 patch.object(OwnedLocalFixtures, '_request', cancel), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                rc = main(['runtime', 'fixture-smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                    '--state-dir', str(Path(tmp) / 'state'), '--binary', BINARY, '--binary-sha256', DIGEST,
                    '--run-id', 'cancel-expanded', '--fixture-set', 'workspace',
                    '--allow-local-processes', '--allow-fixture-write'])
            self.assertEqual(rc, 130, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['status'], 'CANCELLED')
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0].child.poll(), 0)
            root = started[0].root
            saved = json.loads((root / 'local-fixtures.json').read_text())
            self.assertEqual(saved['status'], 'BLOCKED')
            self.assertEqual(set(saved['roles']), {'CEO', 'PM'})
            self.assertEqual(saved['pending_operation'], 'register-ENG')
            self.assertFalse((root / 'smoke-report.json').exists())
            self.assertEqual(json.loads((root / 'owned-bcs.json').read_text())['status'], 'STOPPED')

    def test_live_multi_role_identity_smoke_clears_credentials_and_stops(self):
        for selected in ('workspace', 'trusted-team', 'cli-file'):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as tmp:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = main(['runtime', 'fixture-smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                        '--state-dir', str(Path(tmp) / 'state'), '--binary', BINARY, '--binary-sha256', DIGEST,
                        '--run-id', 'identities-' + selected, '--fixture-set', selected,
                        '--verify-bot-identities', '--allow-local-processes', '--allow-fixture-write'])
                self.assertEqual(rc, 0, output.getvalue())
                result = json.loads(output.getvalue())
                identities = result['bot_identities']
                self.assertEqual(identities['status'], 'PASS')
                self.assertEqual(identities['credential_memory'], 'CLEARED')
                self.assertEqual(identities['state'], 'CLOSED')
                self.assertEqual(set(identities['verified_roles']), set(result['fixtures']['roles']))
                for role in identities['roles']:
                    self.assertEqual(role['bot_id'], result['fixtures']['roles'][role['role']]['bot_id'])
                    self.assertEqual(role['authentication'], 'OWNED_LOCAL_BOT')
                    self.assertFalse(role['atomic_identity_attestation'])
                self.assertEqual(result['shutdown']['returncode'], 0)
                self.assertFalse(result['ready_to_execute'])
                root = Path(result['runtime_dir'])
                self.assertEqual(json.loads((root / 'smoke-report.json').read_text()), result)
                self.assertNotIn('bot_token', output.getvalue() + (root / 'local-fixtures.json').read_text())

    def test_live_invalid_token_and_foreign_bot_cannot_use_human_fallback_as_identity(self):
        from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity, LocalRoleIdentityError
        from urllib.error import HTTPError
        import uuid
        for mode in ('invalid', 'foreign'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=Path(tmp) / 'runtime', run_id='negative-' + mode) as runtime:
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='cli-file')
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                        fixtures.apply(allow_write=True, role_identity=identities)
                        bot_id, real_token = identities._entries['PM']
                        wrong = 'unmapped-' + uuid.uuid4().hex if mode == 'invalid' else identities._entries['ENG'][1]
                        # Characterize the two independently insufficient endpoints.
                        if mode == 'invalid':
                            fallback = identities._request('friends', bot_id, wrong)
                            self.assertIs(fallback['success'], True)
                        else:
                            visible = identities._request('visibility', bot_id, wrong)
                            self.assertIs(visible['success'], True)
                            self.assertEqual(visible['data']['bot_uuid'], bot_id)
                        identities._entries['PM'] = (bot_id, wrong)
                        original = identities.opener.open
                        calls, denials = [], []
                        def observe(request, **kwargs):
                            calls.append(request.full_url.rsplit('/', 1)[-1])
                            try:
                                return original(request, **kwargs)
                            except HTTPError as error:
                                # Store only the status; never print request/auth or raw body.
                                denials.append(error.code)
                                raise
                        with patch.object(identities.opener, 'open', side_effect=observe), \
                             self.assertRaises(LocalRoleIdentityError) as caught:
                            identities.verify('PM')
                        self.assertEqual(calls, ['visibility'] if mode == 'invalid' else ['visibility', 'friends'])
                        self.assertEqual(denials, [401 if mode == 'invalid' else 403])
                        self.assertEqual(len(identities._entries), 0)
                        self.assertEqual(identities.summary()['verified_roles'], [])
                        self.assertTrue(real_token not in str(caught.exception) and wrong not in str(caught.exception))
                        self.assertTrue(real_token not in fixtures.path.read_text())
                self.assertEqual(runtime.child.poll(), 0)

    def test_live_cancelled_capture_clears_all_role_tokens_before_runtime_exit(self):
        from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity, LocalRoleIdentityError
        with tempfile.TemporaryDirectory() as tmp:
            runtime = OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                      root=Path(tmp) / 'runtime', run_id='cancel-role-capture')
            with runtime:
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                identities = OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles)
                original = fixtures._request
                count = 0
                def cancel(operation, **kwargs):
                    nonlocal count
                    if operation == 'register':
                        count += 1
                        if count == 3: raise KeyboardInterrupt()
                    return original(operation, **kwargs)
                with identities:
                    with patch.object(fixtures, '_request', side_effect=cancel), self.assertRaises(KeyboardInterrupt):
                        fixtures.apply(allow_write=True, role_identity=identities)
                    self.assertEqual(len(identities._entries), 0)
                    self.assertEqual(identities.summary()['state'], 'CLOSED')
                    with self.assertRaises(LocalRoleIdentityError): identities.verify('CEO')
                    self.assertEqual(json.loads(fixtures.path.read_text())['status'], 'BLOCKED')
            self.assertEqual(runtime.child.poll(), 0)

    def test_live_trust_baseline_cli_observes_api_and_exact_sqlite_without_relationship_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc = main(['runtime', 'fixture-smoke', '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                    '--state-dir', str(Path(tmp) / 'state'), '--binary', BINARY, '--binary-sha256', DIGEST,
                    '--run-id', 'trust-baseline-live', '--fixture-set', 'trusted-team',
                    '--verify-bot-identities', '--check-trust-baseline',
                    '--allow-local-processes', '--allow-fixture-write'])
            self.assertEqual(rc, 0, output.getvalue())
            result = json.loads(output.getvalue())
            baseline = result['trust_baseline']
            self.assertEqual(baseline['status'], 'BASELINE_OBSERVED')
            self.assertEqual(baseline['storage_completeness'], 'LEGACY_INCIDENT_ROWS_VERIFIED')
            self.assertEqual(baseline['storage']['status'], 'OBSERVED')
            self.assertEqual(baseline['storage']['storage_writes'], 0)
            self.assertEqual(baseline['business_writes'], 0)
            self.assertFalse(baseline['ready_to_execute'])
            self.assertEqual(len(baseline['roles']), 5)
            self.assertEqual(len(baseline['required_absent_pairs']), 4)
            self.assertEqual(baseline['full_story_status'], 'NOT_MIGRATED')
            self.assertEqual(result['bot_identities']['credential_memory'], 'CLEARED')
            self.assertEqual(result['shutdown']['returncode'], 0)
            root = Path(result['runtime_dir'])
            self.assertEqual(json.loads((root / 'smoke-report.json').read_text()), result)
            self.assertEqual(json.loads((root / 'local-fixtures.json').read_text())['relationship_baseline'], 'NOT_VERIFIED')
            with sqlite3.connect((root / 'bcs.sqlite').as_uri() + '?mode=ro', uri=True) as db:
                for table in ('bcs_friendships', 'bcs_friend_requests'):
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table + " WHERE env='local'").fetchone()[0], 0)

    def test_live_false_empty_friend_api_is_not_a_clean_baseline(self):
        from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
        from bcs_e2e.trust_baseline import TrustBaselineError
        for failure in ('deleted-peer', 'missing-table'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=Path(tmp) / 'runtime', run_id='false-empty-' + failure) as runtime:
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='trusted-team')
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                        fixtures.apply(allow_write=True, role_identity=identities)
                        for role in fixtures.roles: identities.verify(role)
                        bot_id, value = identities._entries['CEO']
                        # Fault injection only in the freshly owned local test database.
                        with sqlite3.connect(runtime.root / 'bcs.sqlite') as db:
                            if failure == 'deleted-peer':
                                db.execute('INSERT INTO bcs_friendships(left_bot,right_bot,env) VALUES (?,?,?)',
                                           (bot_id, 'unregistered-test-peer', 'local'))
                            else:
                                db.execute('DROP TABLE bcs_friendships')
                        observed = identities._request('friends', bot_id, value)
                        self.assertIs(observed['success'], True)
                        self.assertEqual(observed['data'], [])
                        with self.assertRaises(TrustBaselineError): identities.trust_baseline()
                        self.assertEqual(identities.summary()['credential_memory'], 'CLEARED')
                self.assertEqual(runtime.child.poll(), 0)

    def test_real_fixture_apply_once_and_read_only_recheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                      root=Path(tmp) / 'fixtures', run_id='live-fixtures')
            with runtime:
                handler = OwnedLocalFixtures(runtime)
                applied = handler.apply(allow_write=True)
                self.assertEqual(applied['status'], 'APPLIED')
                self.assertEqual(len({v['bot_id'] for v in applied['roles'].values()}), 2)
                snapshot = handler.path.read_bytes()
                with patch.object(handler, '_request', wraps=handler._request) as request:
                    checked = handler.check()
                    self.assertEqual([call.args[0] for call in request.call_args_list],
                                     ['identity', 'read-bot', 'read-bot'])
                self.assertEqual(checked['status'], 'VERIFIED')
                self.assertEqual(handler.path.read_bytes(), snapshot)
                with self.assertRaises(LocalFixtureError):
                    handler.apply(allow_write=True)
                self.assertNotIn('bot_token', handler.path.read_text())
                for binding in checked['roles'].values():
                    self.assertEqual(binding['owner_id'], runtime.inputs.human_id)
            self.assertEqual(runtime.child.poll(), 0)

    def test_live_sqlite_schema_admission_is_read_only_and_distinct_from_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with Ledger(root / 'ledger.sqlite', 'live-db', 'a' * 64, create=True):
                pass
            with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                 root=root / 'runtime', run_id='live-db') as runtime:
                observer = OwnedSqliteEvidenceProvider(runtime, ledger_path=root / 'ledger.sqlite',
                                                        profile_digest='a' * 64, case_id='PRE-03')
                result = observer.admit(('group.persisted', 'session.persisted', 'file.persisted'))
                self.assertEqual(result['status'], 'READY')
                self.assertEqual(result['source'], 'local-sqlite')
                self.assertEqual(result['environment'], 'local')
                with observer._connection() as db:
                    with self.assertRaises(sqlite3.OperationalError):
                        db.execute('DELETE FROM bcs_groups WHERE 1=0')
            self.assertEqual(runtime.child.poll(), 0)

    def test_two_live_bcs_instances_have_private_databases_and_independent_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            processes = [OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                root=Path(tmp) / f'run-{n}', run_id=f'live-{n}') for n in range(2)]
            try:
                for process in processes:
                    self.assertEqual(process.start()['status'], 'RUNNING')
                self.assertNotEqual(processes[0].port, processes[1].port)
                self.assertEqual(processes[0].stop()['status'], 'STOPPED')
                self.assertTrue(processes[1]._owns_listener(1))
                self.assertIsNone(processes[1].child.poll())
                self.assertEqual(processes[1].stop()['status'], 'STOPPED')
                for process in processes:
                    with sqlite3.connect((process.root / 'bcs.sqlite').as_uri() + '?mode=ro', uri=True) as db:
                        self.assertGreater(db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)
            finally:
                for process in processes:
                    process._reap()
