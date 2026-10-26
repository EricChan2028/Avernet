from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.runtime import RuntimeContractError


class LocalConfigTests(unittest.TestCase):
    def test_private_paths_no_environment_inheritance_and_separate_secrets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'HTTP_PROXY': 'http://proxy.invalid', 'SERVER_ENV': 'prod',
            'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE': 'existing-secret',
        }):
            a = prepare_local_bcs_inputs(Path(tmp) / 'a', run_id='run-a', port=45001)
            b = prepare_local_bcs_inputs(Path(tmp) / 'b', run_id='run-b', port=45002)
            config = json.loads(a.config_path.read_text())
            self.assertEqual(config['bind'], '127.0.0.1')
            self.assertEqual(config['llm']['type'], 'none')
            self.assertFalse(config['bcsfuse']['enabled'])
            self.assertFalse(config['eventing']['dispatcher_enabled'])
            self.assertEqual(a.environment['SERVER_ENV'], 'local')
            self.assertEqual(a.environment['TZ'], 'UTC')
            self.assertEqual(config['logging']['outputs'][0]['format'], 'json')
            self.assertEqual(config['logging']['outputs'][0]['targets'], ['bcs_http_access'])
            self.assertEqual(config['logging']['outputs'][0]['max_keep_days'], 0)
            self.assertNotIn('HTTP_PROXY', a.environment)
            self.assertNotEqual(a.human_id, b.human_id)
            for key in a.environment:
                if 'SECRET' in key:
                    self.assertNotEqual(a.environment[key], b.environment[key])
                    self.assertNotIn(a.environment[key], a.config_path.read_text() + repr(a))
                    self.assertNotEqual(a.environment[key], 'existing-secret')
            paths = [config['bots_base_dir'], config['database']['sqlite']['path'],
                     config['session_files']['backend']['data_dir'],
                     config['collaboration']['templates']['base_dir'], a.environment['HOME']]
            for path in paths:
                self.assertIn(a.root, Path(path).parents)
            self.assertEqual(a.config_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(a.root.stat().st_mode & 0o777, 0o700)

    def test_message_storage_is_explicit_and_default_stays_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = prepare_local_bcs_inputs(Path(tmp)/'default', run_id='default', port=45001)
            enabled = prepare_local_bcs_inputs(Path(tmp)/'enabled', run_id='enabled', port=45002,
                                               store_messages=True)
            self.assertIs(json.loads(default.config_path.read_text())['store_messages'], False)
            self.assertIs(json.loads(enabled.config_path.read_text())['store_messages'], True)
            for value in (1, 'true', None):
                with self.subTest(value=value), self.assertRaises(RuntimeContractError):
                    prepare_local_bcs_inputs(Path(tmp)/'invalid', run_id='bad', port=45003,
                                             store_messages=value)
            self.assertFalse((Path(tmp)/'invalid').exists())

    def test_loop_execution_is_explicit_and_default_stays_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = prepare_local_bcs_inputs(Path(tmp)/'default', run_id='default', port=45001)
            enabled = prepare_local_bcs_inputs(Path(tmp)/'enabled', run_id='enabled', port=45002,
                                               loop_execution_enabled=True)
            self.assertIs(json.loads(default.config_path.read_text())['collaboration']['loop_execution_enabled'], False)
            self.assertIs(json.loads(enabled.config_path.read_text())['collaboration']['loop_execution_enabled'], True)
            for value in (1, 'true', None):
                with self.subTest(value=value), self.assertRaises(RuntimeContractError):
                    prepare_local_bcs_inputs(Path(tmp)/'invalid', run_id='bad', port=45003,
                                             loop_execution_enabled=value)
            self.assertFalse((Path(tmp)/'invalid').exists())

    def test_friend_work_order_base_url_requires_exact_owned_loopback_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default = prepare_local_bcs_inputs(root / 'default', run_id='default', port=45001)
            self.assertIsNone(json.loads(default.config_path.read_text())[
                'friend_work_order_base_url'])
            self.assertEqual(json.loads(default.config_path.read_text())['security']['outbound_url'],
                            {'block_private_networks': True, 'allow_loopback': False})
            inputs = prepare_local_bcs_inputs(
                root / 'enabled', run_id='enabled', port=45002,
                friend_work_order_base_url='http://127.0.0.1:45123')
            config = json.loads(inputs.config_path.read_text())
            self.assertEqual(config['friend_work_order_base_url'],
                             'http://127.0.0.1:45123')
            self.assertEqual(config['security']['outbound_url'],
                            {'block_private_networks': True, 'allow_loopback': True})
            invalid = {
                'non-http': 'https://127.0.0.1:45123',
                'non-loopback': 'http://localhost:45123',
                'private-host': 'http://10.0.0.1:45123',
                'path': 'http://127.0.0.1:45123/path',
                'query': 'http://127.0.0.1:45123?q=1',
                'userinfo': 'http://user@127.0.0.1:45123',
                'port': 'http://127.0.0.1:80',
                'type': True,
            }
            for name, value in invalid.items():
                with self.subTest(value=name), self.assertRaises(RuntimeContractError):
                    prepare_local_bcs_inputs(root / ('invalid-' + name), run_id=name,
                                             port=45003, friend_work_order_base_url=value)
                self.assertFalse((root / ('invalid-' + name)).exists())

    def test_local_identity_can_be_disabled_without_enabling_ambient_inheritance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            enabled = prepare_local_bcs_inputs(root/'enabled', run_id='enabled', port=45001)
            disabled = prepare_local_bcs_inputs(root/'disabled', run_id='disabled', port=45002,
                                               allow_ambient_local_identity=False)
            enabled_auth = json.loads(enabled.config_path.read_text())['auth']
            disabled_auth = json.loads(disabled.config_path.read_text())['auth']
            self.assertEqual(enabled_auth['chain'], ['local', 'session'])
            self.assertIn('mock_user_id', enabled_auth)
            self.assertEqual(disabled_auth, {'chain': ['local', 'session'],
                                             'require_authentication': False,
                                             'allow_mock_headers': True})
            self.assertNotIn('mock_user_id', disabled_auth)
            self.assertNotIn('mock_user_name', disabled_auth)
            self.assertTrue(disabled.environment['HOME'].startswith(str(disabled.root)))
            for value in (None, 1, 'false'):
                with self.subTest(value=value), self.assertRaises(RuntimeContractError):
                    prepare_local_bcs_inputs(root/'invalid', run_id='bad', port=45003,
                                             allow_ambient_local_identity=value)
                self.assertFalse((root/'invalid').exists())

    def test_loopback_provider_requires_matching_live_owned_sink(self):
        from bcs_e2e.local_provider_sink import OwnedLocalProviderSink
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default = prepare_local_bcs_inputs(root/'default', run_id='default', port=45001)
            self.assertEqual(json.loads(default.config_path.read_text())['security']['outbound_url'],
                             {'block_private_networks': True, 'allow_loopback': False})
            for invalid in (True, 'http://127.0.0.1:1234', object()):
                with self.subTest(invalid=type(invalid).__name__), self.assertRaises(RuntimeContractError):
                    prepare_local_bcs_inputs(root/'invalid', run_id='local-provider', port=45002, provider_sink=invalid)
                self.assertFalse((root/'invalid').exists())
            sink = OwnedLocalProviderSink('local-provider')
            with self.assertRaises(RuntimeContractError):
                prepare_local_bcs_inputs(root/'not-started', run_id='local-provider', port=45002, provider_sink=sink)
            self.assertFalse((root/'not-started').exists())
            with sink:
                with self.assertRaises(RuntimeContractError):
                    prepare_local_bcs_inputs(root/'foreign', run_id='foreign', port=45002, provider_sink=sink)
                self.assertFalse((root/'foreign').exists())
                inputs = prepare_local_bcs_inputs(root/'valid', run_id='local-provider', port=45002, provider_sink=sink)
                config = json.loads(inputs.config_path.read_text())
                self.assertEqual(config['security']['outbound_url'],
                                 {'block_private_networks': True, 'allow_loopback': True})
                self.assertEqual(config['allowed_switch_provider_ids'], [])
                self.assertFalse(config['eventing']['enabled'])
                self.assertEqual(config['llm'], {'type': 'none'})
            with self.assertRaises(RuntimeContractError):
                prepare_local_bcs_inputs(root/'closed', run_id='local-provider', port=45002, provider_sink=sink)
            self.assertFalse((root/'closed').exists())

    def test_existing_directory_symlink_and_invalid_port_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'existing'
            root.mkdir()
            (root / 'keep').write_text('untouched')
            link = Path(tmp) / 'link'
            link.symlink_to(root)
            for target, port in ((root, 45001), (link, 45001), (Path(tmp) / 'bad', 0),
                                 (Path(tmp) / 'bad', True), (Path(tmp) / 'bad', 65536)):
                with self.subTest(target=target, port=port), self.assertRaises(RuntimeContractError):
                    prepare_local_bcs_inputs(target, run_id='run', port=port)
            self.assertEqual((root / 'keep').read_text(), 'untouched')
            self.assertFalse((Path(tmp) / 'bad').exists())
