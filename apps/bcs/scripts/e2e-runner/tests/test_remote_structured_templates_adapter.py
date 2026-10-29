"""Public station catalog template slice and adapter contracts."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_structured_public_contract import (
    CASE_ID,
    PUBLIC_DETAIL_PATH,
    PUBLIC_LIST_PATH,
    execute_structured_templates_case,
    structured_templates_adaptability,
    structured_templates_case_plan,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_network_adapters import RemoteStructuredTemplatesAdapter
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ReadClient

COOKIE = 'human-cookie-private-value'
DRIVER = 'driver-bot'


def base_profile(*, gateway=False):
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment'].update(id='preprod-templates' if gateway else 'local-singlebox-templates')
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id=DRIVER, owner_id='owner-1')
    value['actors']['member'].update(bot_id='member-bot', owner_id='owner-1')
    value['auth']['header_env'] = {
        'Authorization': 'BCS_E2E_DRIVER_BOT_TOKEN',
        'Cookie': 'BCS_E2E_HUMAN_COOKIE',
    }
    value['edge_case_bindings'] = {
        'structured_templates.list': {
            'method': 'GET', 'path': PUBLIC_LIST_PATH, 'api_face': 'internal-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled',
        },
        'structured_templates.detail': {
            'method': 'GET', 'path': PUBLIC_DETAIL_PATH, 'api_face': 'internal-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled',
        },
    }
    if gateway:
        value['environment'].update(tier='preprod', expected_bcs_env='pre',
                                  lifecycle='remote-attach', deployment='managed',
                                  provider='internal')
        value['entrypoint'].update(kind='gateway', origin='https://teamclawgw.example.com')
        value['observer'].update(kind='alisddbquery', server='observ',
                                 database='agentclawdb',
                                 environment_binding_confirmed=True)
    return validate(value)


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.code = code
        self.headers = headers or {}


def envelope(data, code=20000):
    return Reply(json.dumps({
        'code': code, 'message': 'OK', 'request_id': 'server-request', 'data': data,
    }).encode())


def template_row(template_id, *, selected=False):
    return {
        'id': template_id,
        'name': ('Single-Bot Guided Answer' if selected else 'Judge Review'),
        'description': 'Reviewed catalog entry',
        'participants': {'driver': {'required': True}},
        'tags': ['guided-answer'],
        'priority': 0,
        'available_languages': ['zh-CN'] if selected else ['en'],
    }


def list_data():
    return {
        'templates': [
            template_row('judge_review'),
            template_row('single-bot-guided-answer', selected=True),
        ],
        'tag_labels': {},
        'default_language': 'zh-CN',
        'supported_languages': ['zh-CN'],
    }


def detail_data(**overrides):
    value = {
        'id': 'single-bot-guided-answer',
        'lang': 'zh-CN',
        'name': 'Single-Bot Guided Answer',
        'yaml': 'version: 1\nkind: guided_answer\n',
        'definition': {'kind': 'guided_answer', 'nodes': {}},
    }
    value.update(overrides)
    return value


class RemoteStructuredTemplatesTests(unittest.TestCase):
    def test_case_guard_accepts_the_template_slice(self):
        guard = ROOT.parents[3] / 'scripts/ci/e2e_runner_case_guard.py'
        files = (ROOT / 'bcs_e2e/local_structured_public_contract.py',
                 ROOT / 'bcs_e2e/remote_network_adapters.py')
        result = subprocess.run([sys.executable, str(guard), *[str(path) for path in files]],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_bindings_reject_route_face_and_identity_drift(self):
        mutations = {
            'list-method': lambda v: v['edge_case_bindings']['structured_templates.list'].update(method='POST'),
            'detail-method': lambda v: v['edge_case_bindings']['structured_templates.detail'].update(method='POST'),
            'list-path': lambda v: v['edge_case_bindings']['structured_templates.list'].update(path=PUBLIC_LIST_PATH + '&x=1'),
            'detail-path': lambda v: v['edge_case_bindings']['structured_templates.detail'].update(path=PUBLIC_DETAIL_PATH + '&x=1'),
            'face': lambda v: v['edge_case_bindings']['structured_templates.list'].update(api_face='public-v1'),
            'auth': lambda v: v['edge_case_bindings']['structured_templates.list'].update(auth='authenticated_bot'),
            'manifest': lambda v: v['entrypoint'].update(manifest_face='legacy'),
            'cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
            'empty': lambda v: v.update(edge_case_bindings={}),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_the_slice_preserves_two_source_requirements_without_credit(self):
        plan = structured_templates_case_plan()
        adaptability = structured_templates_adaptability()
        self.assertEqual(plan.case_id, CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_runs_structured_collaboration')
        self.assertEqual([row.requirement_id for row in plan.requirements],
                         ['STRUCTURED.templates-list', 'STRUCTURED.template-detail'])
        self.assertEqual(plan.requirements[0].source_path, '/collaboration/templates?lang=zh-CN')
        self.assertEqual(plan.requirements[1].source_path,
                         '/collaboration/templates/single-bot-guided-answer?lang=zh-CN&format=json')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        inventory_row = next(row for row in migration_plan()['public_v1_case_slices']
                             if row['case_id'] == CASE_ID)
        self.assertEqual(inventory_row['selected_obligations'], ['list', 'detail'])
        self.assertEqual(inventory_row['public_paths'], [PUBLIC_LIST_PATH, PUBLIC_DETAIL_PATH])
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])
        self.assertEqual(adaptability['blocker'], inventory_row['blocker'])

    def run_case(self, responses=None, profile_value=None):
        if responses is None:
            responses = (envelope(list_data()), envelope(detail_data()))
        requests = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': 'driver-token',
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            iterator = iter(responses)
            def open(request, timeout=None):
                requests.append(request)
                try:
                    return next(iterator)
                except StopIteration:
                    raise AssertionError('unexpected template request count')
            factory.return_value.open.side_effect = open
            adapter = RemoteStructuredTemplatesAdapter(
                profile_value if profile_value is not None else base_profile())
            return execute_structured_templates_case(adapter), requests

    def test_catalog_reads_exact_station_routes_with_human_cookie_only(self):
        execution, requests = self.run_case()
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual([request.method for request in requests], ['GET', 'GET'])
        self.assertEqual([request.full_url.split('http://127.0.0.1:21000', 1)[1]
                          for request in requests], [PUBLIC_LIST_PATH, PUBLIC_DETAIL_PATH])
        for request in requests:
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertNotIn('authorization', headers)
            self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual([step['status'] for step in snapshot['steps']], ['PASS', 'PASS'])
        self.assertEqual(snapshot['steps'][0]['data'], {
            'template_id': 'single-bot-guided-answer', 'item_count': 2})
        self.assertEqual(snapshot['steps'][1]['data'], {'template_id': 'single-bot-guided-answer'})
        self.assertNotIn(COOKIE, json.dumps(snapshot, sort_keys=True))
        self.assertNotIn('human-cookie-private-value', json.dumps(snapshot, sort_keys=True))

    def test_gateway_profile_executes_the_same_station_contract(self):
        execution, requests = self.run_case(profile_value=base_profile(gateway=True))
        self.assertEqual(execution.status, 'PASS')
        self.assertTrue(all(request.full_url.startswith('https://teamclawgw.example.com')
                            for request in requests))

    def test_identity_or_schema_drift_blocks_without_partial_credit(self):
        cases = {
            'missing-template': (
                envelope({'templates': [template_row('judge_review')]}),
                envelope(detail_data())),
            'duplicate-template': (
                envelope({'templates': [template_row('single-bot-guided-answer'),
                                        template_row('single-bot-guided-answer')]}),
                envelope(detail_data())),
            'detail-id': (envelope(list_data()), envelope(detail_data(id='judge_review'))),
            'detail-lang': (envelope(list_data()), envelope(detail_data(lang='en'))),
            'detail-yaml': (envelope(list_data()), envelope(detail_data(yaml=''))),
            'detail-http-404': (envelope(list_data()), envelope(detail_data(), code=40400)),
        }
        for name, responses in cases.items():
            with self.subTest(case=name):
                execution, _ = self.run_case(responses=responses)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertTrue(all(step.status == 'NOT_RUN' for step in execution.steps))

    def test_invalid_profile_and_transport_operation_fail_before_network(self):
        for kind in ('mock', 'bcs-direct'):
            value = base_profile()
            value['entrypoint']['kind'] = kind
            with self.subTest(kind=kind), patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises((ConfigError, RunStateError)):
                    RemoteStructuredTemplatesAdapter(value)
                factory.return_value.open.assert_not_called()

        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': 'driver-token',
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(base_profile())
            for operation in ('structured.templates.group', 'structured.templates.catalog'):
                with self.subTest(operation=operation), self.assertRaises(Exception):
                    client.read(operation)
            factory.return_value.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
