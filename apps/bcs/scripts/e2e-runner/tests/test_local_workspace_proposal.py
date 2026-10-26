"""Unit-only guards for the owned-local Workspace proposal adapter."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.ledger import Ledger
from bcs_e2e.local_workspace_proposal import (
    CASE_ID, OPERATIONS, REDACTED_CONFIRM_PATH, TOPIC,
    OwnedLocalWorkspaceProposal, validate_confirmation_html,
    validate_confirmation_url,
)
from bcs_e2e.run_state import RunStateError


ORIGIN = 'http://127.0.0.1:23123'
TOKEN = '123e4567-e89b-42d3-a456-426614174000'
PATH = '/groups/' + TOKEN + '/confirm'


def page(action=PATH, topic=TOPIC):
    return f'''<!DOCTYPE html><html><head><title>确认群聊</title><meta charset="utf-8"></head>
<body><h1>确认创建群聊</h1><p><strong>Driver：</strong>e2e-CEO-owner</p>
<p><strong>原因：</strong>{topic}</p><h3>参与者：</h3>
<pre>e2e-PM-owner\ne2e-QA-owner</pre>
<form action="{action}" method="post"><button type="submit">确认</button></form></body></html>'''


class ProposalUrlTests(unittest.TestCase):
    def test_accepts_exact_owned_origin_and_uuid_path(self):
        path, token, digest = validate_confirmation_url(ORIGIN, ORIGIN + PATH)
        self.assertEqual(path, PATH)
        self.assertEqual(token, TOKEN)
        self.assertEqual(len(digest), 64)
        self.assertNotIn(TOKEN, digest)

    def test_rejects_cross_origin_userinfo_query_and_fragment(self):
        values = (
            'http://127.0.0.1:23124' + PATH,
            'http://example.test:23123' + PATH,
            'http://user@127.0.0.1:23123' + PATH,
            ORIGIN + PATH + '?next=/groups',
            ORIGIN + PATH + '#confirm',
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(RunStateError):
                validate_confirmation_url(ORIGIN, value)

    def test_rejects_encoded_or_noncanonical_path(self):
        values = (
            ORIGIN + '/groups/%31' + TOKEN[1:] + '/confirm',
            ORIGIN + '/groups/' + TOKEN + '%2fconfirm',
            ORIGIN + '/groups/' + TOKEN.upper() + '/confirm',
            ORIGIN + '/groups/' + TOKEN + '/confirm/',
            ORIGIN + '/groups/not-a-uuid/confirm',
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(RunStateError):
                validate_confirmation_url(ORIGIN, value)


class ProposalHtmlTests(unittest.TestCase):
    def test_structural_preview_accepts_one_same_token_post_form(self):
        result = validate_confirmation_html(
            page(), expected_path=PATH,
            expected_text=(TOPIC, 'e2e-CEO-owner', 'e2e-PM-owner', 'e2e-QA-owner'))
        self.assertEqual(result['action'], REDACTED_CONFIRM_PATH)
        self.assertEqual(result['method'], 'POST')

    def test_mismatched_action_or_method_is_rejected(self):
        cases = (
            page('/groups/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/confirm'),
            page().replace('method="post"', 'method="get"'),
            page('http://evil.example/groups/' + TOKEN + '/confirm'),
            page() + f'<form action="{PATH}" method="post"></form>',
        )
        for html in cases:
            with self.subTest(html=html[:80]), self.assertRaises(RunStateError):
                validate_confirmation_html(html, expected_path=PATH,
                                           expected_text=(TOPIC, 'e2e-CEO-owner'))

    def test_unsafe_injected_tag_is_rejected(self):
        with self.assertRaisesRegex(RunStateError, 'unsafe'):
            validate_confirmation_html(page(topic='<script>alert(1)</script>'),
                                       expected_path=PATH, expected_text=(TOPIC,))


class ProposalJournalTests(unittest.TestCase):
    def make_case(self):
        case = object.__new__(OwnedLocalWorkspaceProposal)
        case.state = {'operations': []}
        case._journal_digest = ''
        case._invalidated = False
        case._capability_path = PATH
        case._capability_token = TOKEN
        case._active = Mock()
        case._save = Mock()
        return case

    def test_confirmation_intent_and_implicit_resources_precede_dispatch(self):
        case = self.make_case()
        opened = []
        case.identities = Mock()
        case.identities.origin = ORIGIN
        case.identities.opener.open.side_effect = lambda *_args, **_kwargs: opened.append(True)
        with tempfile.TemporaryDirectory() as tmp:
            with Ledger(Path(tmp)/'resources.sqlite', 'proposal-unit', 'a'*64, create=True) as ledger:
                case.ledger = ledger
                request_id = 'proposal-confirm-intent'
                handles = ledger.group_creation_intent(CASE_ID, 1, request_id)
                event = case._record_intent('proposal-create', 'POST', '/groups/request',
                                            role='CEO', request_id='create-intent')
                event['status'] = 'ACKNOWLEDGED'
                case._record_intent('proposal-preview', 'GET', REDACTED_CONFIRM_PATH,
                                    role='TOKEN_CAPABILITY', request_id='preview-intent')
                case.state['operations'][-1]['status'] = 'ACKNOWLEDGED'
                event = case._record_intent('proposal-confirm', 'POST', REDACTED_CONFIRM_PATH,
                                            role='TOKEN_CAPABILITY', request_id=request_id,
                                            handles=handles)
                rows = ledger.resource_snapshot()
        self.assertEqual(event['status'], 'INTENDED')
        self.assertEqual(event['path'], REDACTED_CONFIRM_PATH)
        self.assertEqual([row['kind'] for row in rows], ['group', 'session'])
        self.assertEqual([row['state'] for row in rows], ['intended', 'intended'])
        self.assertEqual(event['resource_handles'], {'group': handles[0], 'initial_session': handles[1]})
        self.assertEqual(opened, [])

    def test_capability_never_persists_in_state(self):
        case = self.make_case()
        case.state = {
            'operations': [{'operation': 'proposal-preview', 'path': REDACTED_CONFIRM_PATH}],
            'url_validation': {'confirm_url_sha256': 'a'*64, 'path': REDACTED_CONFIRM_PATH},
        }
        serialized = json.dumps(case.state)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(ORIGIN + PATH, serialized)
        self.assertIn('<redacted-capability>', serialized)

    def test_save_rejects_accidental_token_or_path_leak(self):
        case = self.make_case()
        case.path = Path('/unused')
        case.state = {'leak': TOKEN}
        # Use the real implementation while preventing filesystem access: leak
        # detection happens first.
        case._save = OwnedLocalWorkspaceProposal._save.__get__(case)
        with self.assertRaisesRegex(RunStateError, 'capability leaked'):
            case._save()
        case.state = {'leak': PATH}
        with self.assertRaisesRegex(RunStateError, 'capability leaked'):
            case._save()

    def test_adapter_exposes_create_confirm_only_and_never_claims_pass(self):
        self.assertTrue(hasattr(OwnedLocalWorkspaceProposal, 'run_create_confirm'))
        self.assertFalse(hasattr(OwnedLocalWorkspaceProposal, 'run'))
        source = (ROOT/'bcs_e2e'/'local_workspace_proposal.py').read_text()
        self.assertIn("{'id': 'WORKSPACE.proposal', 'status': 'NOT_RUN'}", source)
        self.assertIn('LOCAL_WORKSPACE_PROPOSAL_CONFIRMED_AWAITING_DELETE', source)
        for forbidden in ("method='DELETE'", 'method="DELETE"',
                          'INSERT INTO bcs_', 'UPDATE bcs_', 'DELETE FROM bcs_',
                          'subprocess', 'shell=True', '/openapi/', 'Cookie'):
            self.assertNotIn(forbidden, source)

    def test_operation_order_is_exact_original_create_preview_confirm_get(self):
        self.assertEqual(OPERATIONS, (
            'proposal-create', 'proposal-preview', 'proposal-confirm', 'proposal-group-get'))


if __name__ == '__main__':
    unittest.main()
