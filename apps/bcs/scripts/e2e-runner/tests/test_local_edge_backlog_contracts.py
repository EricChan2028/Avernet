"""Focused guards for the two migrated backlog Edge-permission slices.

These tests are hermetic: they validate frozen shell/route evidence and pure
model/parser behavior.  No BCS process, portfolio runtime, mock server or
network transport is created here.
"""
import base64
import copy
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_edge_backlog import (
    DEPT_CASE_ID,
    DEPT_EXPECTED_VALUES,
    DEPT_PARENT,
    DEPT_OWNED_LOCAL_PROFILE,
    DEPT_REMOTE_PUBLIC_PROFILE,
    DEPT_ROUTE_PROFILES,
    MIXED_SCOPE,
    SCOPED_SHAPE,
    SUITE,
    TC_APPROVER_SUBJECT_ID,
    TC_BOT_API_ID,
    TC_CASE_ID,
    TC_OWNED_LOCAL_PROFILE,
    TC_OWNER_WORK_NO,
    TC_PARENT,
    TC_REMOTE_PUBLIC_PROFILE,
    TC_ROUTE_PROFILES,
    allocate_state_dir,
    assert_dept_owned_local_transcript,
    assert_dept_scope_parse,
    assert_tc_owned_local_transcript,
    assert_tc_sync_view,
    friend_ext_scope_deps,
    load_dept_contract,
    load_tc_contract,
    parse_friend_ext_scopes,
    scope_entry_label,
    EdgeBacklogFailure,
    RemoteDeptScopeFriendExtAdapter,
    RemoteTcFriendAuthSyncAdapter,
)


def _jwt(claims):
    def encode(value):
        raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    return '.'.join((encode({'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}),
                     encode(claims), 'ci-signature'))


def _tc_transcript(subject_id=TC_APPROVER_SUBJECT_ID):
    principal = _jwt({
        'iss': 'bcs',
        'aud': 'backend',
        'principals': [{'type': 'user', 'subject': {'id': subject_id}}],
    })
    return {
        'ensure_status': 200,
        'create_status': 200,
        'create_response': {
            'data': {'request_ids': ['request-1'], 'status': 'pending'},
        },
        'accept_status': 200,
        'accept_response': {'data': {'edge_ids': ['edge-1']}},
        'sync_view': {
            'requests': [{
                'body': {
                    'bot_id': TC_BOT_API_ID,
                    'owner_work_no': TC_OWNER_WORK_NO,
                    'action': 'grant',
                },
                'principal': principal,
            }],
        },
    }


def _dept_transcript():
    return {
        'ensure_status': 200,
        'mixed_patch_status': 200,
        'mixed_scope': MIXED_SCOPE['friend_ext'],
        'no_check_status': 200,
        'scoped_patch_status': 200,
        'scoped_scope': SCOPED_SHAPE['friend_ext'],
        'human_scoped_status': 403,
        'bot_scoped_status': 403,
    }


class LocalEdgeBacklogContractTests(unittest.TestCase):

    def test_both_cases_are_independent_owned_local_slices(self):
        tc, tc_profiles = load_tc_contract()
        dept, dept_profiles = load_dept_contract()
        self.assertEqual(tc['case_id'], TC_CASE_ID)
        self.assertEqual(tc['parent'], TC_PARENT)
        self.assertEqual(tc['suite'], SUITE)
        self.assertEqual(tc['requirements'][0]['method'], 'POST')
        self.assertEqual(
            tc['requirements'][0]['path'],
            '/collaboration/friend-connections/requests/{request_id}/accept')
        self.assertEqual(dept['case_id'], DEPT_CASE_ID)
        self.assertEqual(dept['parent'], DEPT_PARENT)
        self.assertEqual(dept['suite'], SUITE)
        self.assertEqual(dept['requirements'][0]['method'], 'PATCH')
        self.assertEqual(
            dept['requirements'][0]['path'],
            '/openapi/v1/collaboration/bots/{bot_id}')
        for contract in (tc, dept):
            self.assertIs(contract['independent_slice'], True)
            self.assertIs(contract['release_parent_denominator'], False)
            self.assertIs(contract['grants_release_credit'], False)
            self.assertEqual(contract['remote_validation'], 'NOT_RUN')
            self.assertEqual(contract['environment']['local']['status'],
                             'LOCAL_MODEL_READY')
            self.assertEqual(contract['environment']['remote']['status'],
                             'REMOTE_PLACEHOLDER')
        self.assertEqual(set(tc_profiles), {'owned-local', 'remote-public'})
        self.assertEqual(set(dept_profiles), {'owned-local', 'remote-public'})

    def test_route_profiles_make_the_station_face_divergence_explicit(self):
        tc_native = TC_OWNED_LOCAL_PROFILE.route('create')
        tc_public = TC_REMOTE_PUBLIC_PROFILE.route('create')
        self.assertEqual(tc_native.path, '/collaboration/friend-connections/requests')
        self.assertEqual(tc_native.success_statuses, (200,))
        self.assertEqual(
            tc_public.path,
            '/openapi/v1/collaboration/friend-connections/requests')
        self.assertEqual(tc_public.success_statuses, (201,))
        self.assertEqual(
            TC_REMOTE_PUBLIC_PROFILE.route('ensure').availability,
            'STATION_NATIVE_ONLY')

        dept_native = DEPT_OWNED_LOCAL_PROFILE.route('no_check_create')
        dept_public = DEPT_REMOTE_PUBLIC_PROFILE.route('no_check_create')
        self.assertEqual(dept_native.path, '/collaboration/friend-connections/requests')
        self.assertEqual(dept_native.success_statuses, (200,))
        self.assertEqual(
            dept_public.path,
            '/openapi/v1/collaboration/friend-connections/requests')
        self.assertEqual(dept_public.success_statuses, (201,))
        self.assertEqual(
            DEPT_REMOTE_PUBLIC_PROFILE.route('ensure').availability,
            'STATION_NATIVE_ONLY')
        self.assertEqual(
            DEPT_OWNED_LOCAL_PROFILE.route('mixed_patch').path,
            '/openapi/v1/collaboration/bots/{bot_id}')

    def test_station_evidence_names_both_native_and_openapi_routers(self):
        tc, _ = load_tc_contract()
        dept, _ = load_dept_contract()
        tc_files = {source['file'] for row in tc['station_path_evidence']
                    for source in row['sources']}
        dept_files = {source['file'] for row in dept['station_path_evidence']
                      for source in row['sources']}
        self.assertIn('apps/bcs/crates/adapters/http/bcs-http/src/router.rs', tc_files)
        self.assertIn(
            'apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs',
            tc_files)
        self.assertIn('apps/bcs/crates/adapters/http/bcs-http/src/router.rs', dept_files)
        self.assertIn(
            'apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs',
            dept_files)
        tc_pairs = {(row['operation'], row['profile'])
                    for row in tc['station_path_evidence']}
        self.assertIn(('create', 'owned-local'), tc_pairs)
        self.assertIn(('create', 'remote-public'), tc_pairs)
        dept_pairs = {(row['operation'], row['profile'])
                      for row in dept['station_path_evidence']}
        self.assertIn(('mixed_patch', 'owned-local'), dept_pairs)
        self.assertIn(('no_check_create', 'remote-public'), dept_pairs)

    def test_tc_model_reproduces_the_upstream_expected_approver(self):
        receipt = assert_tc_owned_local_transcript(_tc_transcript())
        self.assertEqual(receipt['status'], 'LOCAL_MODEL_PASS')
        self.assertEqual(receipt['request_id'], 'request-1')
        self.assertEqual(receipt['sync']['sync_call_count'], 1)
        self.assertEqual(receipt['sync']['issuer'], 'bcs')
        self.assertEqual(receipt['sync']['audience'], 'backend')
        self.assertEqual(receipt['sync']['approver_subject_id'], '153998')
        self.assertIs(receipt['grants_release_credit'], False)

        wrong = _tc_transcript(subject_id='999999')
        with self.assertRaises(EdgeBacklogFailure) as raised:
            assert_tc_sync_view(wrong['sync_view'])
        self.assertEqual(raised.exception.code, 'SYNC_PRINCIPAL_MISMATCH')
        self.assertEqual(raised.exception.expected, '153998')
        self.assertEqual(raised.exception.observed, '999999')

        duplicate = copy.deepcopy(_tc_transcript()['sync_view'])
        duplicate['requests'].append(copy.deepcopy(duplicate['requests'][0]))
        with self.assertRaises(EdgeBacklogFailure) as raised:
            assert_tc_sync_view(duplicate)
        self.assertEqual(raised.exception.code, 'SYNC_CALL_COUNT_MISMATCH')
        self.assertEqual(raised.exception.expected, 1)
        self.assertEqual(raised.exception.observed, 2)

    def test_dept_parser_prefers_name_keeps_strings_and_drops_garbage(self):
        self.assertEqual(
            scope_entry_label({'deptNo': 'L3480',
                               'deptName': '蚂蚁集团-信贷事业群'}),
            '蚂蚁集团-信贷事业群')
        self.assertEqual(scope_entry_label({'deptNo': 'L3480'}), 'L3480')
        self.assertEqual(scope_entry_label('  F4858  '), 'F4858')
        self.assertIsNone(scope_entry_label({'priority': 7}))
        self.assertIsNone(scope_entry_label('   '))

        parsed = parse_friend_ext_scopes(MIXED_SCOPE['friend_ext'])
        self.assertEqual(
            parsed['scopes']['no_check_scope_friend_deps'],
            ['F4858', '蚂蚁集团-信贷事业群'])
        self.assertEqual(parsed['scopes']['view_scope_user_friend_deps'], [])
        self.assertEqual(parsed['scopes']['view_scope_agent_friend_deps'], ['L3480'])
        self.assertEqual(parsed['dropped']['no_check_scope_friend_deps'], [])
        self.assertEqual(parsed['dropped']['view_scope_user_friend_deps'], [])
        self.assertEqual(parsed['dropped']['view_scope_agent_friend_deps'], [1])
        self.assertEqual(friend_ext_scope_deps(MIXED_SCOPE['friend_ext'],
                                               'view_scope_agent_friend_deps'),
                         ['L3480'])

        receipt = assert_dept_owned_local_transcript(_dept_transcript())
        self.assertEqual(receipt['status'], 'LOCAL_MODEL_PASS')
        self.assertEqual(receipt['scoped_scopes']['view_scope_user_friend_deps'],
                         ['L3480'])
        self.assertEqual(receipt['scoped_dropped']['view_scope_user_friend_deps'], [1])
        self.assertEqual(receipt['scoped_dropped']['view_scope_agent_friend_deps'], [1])

        with self.assertRaises(EdgeBacklogFailure) as raised:
            assert_dept_scope_parse(MIXED_SCOPE['friend_ext'], {
                'no_check_scope_friend_deps': ['WRONG'],
                'view_scope_user_friend_deps': [],
                'view_scope_agent_friend_deps': ['L3480'],
            })
        self.assertEqual(raised.exception.code, 'FRIEND_EXT_PARSE_MISMATCH')

    def test_each_contract_failure_code_is_defined_and_referenced(self):
        for loader in (load_tc_contract, load_dept_contract):
            contract, _ = loader()
            defined = [row['code'] for row in contract['failure_semantics']]
            referenced = {code for row in contract['obligations']
                          for code in row['failure_codes']}
            self.assertEqual(len(defined), len(set(defined)))
            self.assertEqual(set(defined), referenced)
            self.assertTrue(all(contract['expected_values']))

    def test_remote_adapters_are_explicit_not_run_placeholders(self):
        for adapter_cls in (RemoteTcFriendAuthSyncAdapter,
                            RemoteDeptScopeFriendExtAdapter):
            adapter = adapter_cls()
            receipt = adapter.preflight()
            self.assertEqual(receipt['status'], 'REMOTE_PLACEHOLDER')
            self.assertEqual(receipt['remote_validation'], 'NOT_RUN')
            self.assertIs(receipt['grants_release_credit'], False)
            self.assertIn('ensure', receipt['unavailable_operations'])
            with self.assertRaises(EdgeBacklogFailure) as raised:
                adapter.execute()
            self.assertEqual(raised.exception.code, 'REMOTE_UNSUPPORTED')

    def test_state_dirs_use_the_edge_backlog_prefix_and_are_unique(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first = allocate_state_dir(parent, TC_CASE_ID)
            second = allocate_state_dir(parent, DEPT_CASE_ID)
            self.assertTrue(first.name.startswith('edge-backlog-'))
            self.assertTrue(second.name.startswith('edge-backlog-'))
            self.assertNotEqual(first, second)
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(second.stat().st_mode), 0o700)

    def test_route_profile_tamper_fails_before_any_model_runs(self):
        original, _ = load_tc_contract()
        mutated = copy.deepcopy(original)
        mutated['route_profiles']['remote-public']['routes']['create'][
            'success_statuses'] = [200]
        with patch('bcs_e2e.local_edge_backlog.read', return_value=mutated):
            with self.assertRaises(EdgeBacklogFailure) as raised:
                load_tc_contract()
        self.assertEqual(raised.exception.code, 'ROUTE_PROFILE_DRIFT')

    def test_expected_values_keep_the_upstream_failure_facts(self):
        tc, _ = load_tc_contract()
        dept, _ = load_dept_contract()
        self.assertEqual(tc['expected_values']['approver_subject_id'], '153998')
        self.assertEqual(tc['expected_values']['readdressed_issuer'], 'bcs')
        self.assertEqual(tc['expected_values']['readdressed_audience'], 'backend')
        self.assertEqual(dept['expected_values'],
                         json.loads(json.dumps(DEPT_EXPECTED_VALUES,
                                               ensure_ascii=False)))
        self.assertEqual(
            dept['expected_values']['mixed_no_check_scope_friend_deps'],
            ['F4858', '蚂蚁集团-信贷事业群'])


if __name__ == '__main__':
    unittest.main()
