"""Only fresh bounded local context inputs may drive product simple fusion."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_workspace_context import FUSION_CONTEXT, prepare_workspace_context, verify_workspace_context
from bcs_e2e.run_state import RunStateError, read, write


class WorkspaceContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root/'bots').mkdir()
        config = self.root/'config.json'
        config.write_text(json.dumps({'bots_base_dir': str(self.root/'bots'),
                                     'bcsfuse': {'enabled': False}, 'llm': {'type': 'none'}}))
        fixture = self.root/'fixture.json'
        write(fixture, {'schema_version': 1, 'run_id': 'context-test'})
        self.identities = object.__new__(OwnedLocalRoleIdentity)
        self.identities.fixture_set = 'workspace'
        self.identities._require_roles = Mock()
        self.identities._entries = {'PM': ('bot-pm', 'not-written'), 'ENG': ('bot-eng', 'not-written')}
        self.identities.run_id = 'context-test'
        self.identities.runtime = SimpleNamespace(root=self.root, inputs=SimpleNamespace(config_path=config))
        self.identities.fixtures = SimpleNamespace(path=fixture)

    def test_explicit_context_bytes_and_receipt_without_credentials(self):
        result = prepare_workspace_context(self.identities)
        self.assertEqual(result, verify_workspace_context(self.identities))
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['mode'], 'PRODUCT_LOCAL_SIMPLE_FUSION_NO_LLM')
        self.assertEqual(len(result['files']), 4)
        self.assertNotIn('not-written', json.dumps(result))
        for role, files in FUSION_CONTEXT.items():
            for name, content in files.items():
                path = self.root/'bots'/self.identities._entries[role][0]/name
                self.assertEqual(path.read_text(), content)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_repeated_prepare_cannot_overwrite_or_adopt_existing_inputs(self):
        prepare_workspace_context(self.identities)
        target = self.root/'bots'/'bot-pm'/'SOUL.md'
        target.write_text('operator-changed')
        with self.assertRaises(RunStateError):
            prepare_workspace_context(self.identities)
        with self.assertRaises(RunStateError):
            verify_workspace_context(self.identities)
        self.assertEqual(target.read_text(), 'operator-changed')

    def test_existing_or_symlink_bot_directory_blocks_before_receipt(self):
        external = self.root/'do-not-touch'
        external.mkdir()
        (external/'SOUL.md').write_text('original')
        (self.root/'bots'/'bot-pm').symlink_to(external)
        with self.assertRaises(RunStateError):
            prepare_workspace_context(self.identities)
        self.assertFalse((self.root/'workspace-fusion-context.json').exists())
        self.assertEqual((external/'SOUL.md').read_text(), 'original')

    def test_partial_creation_remains_unready_and_cannot_retry(self):
        original = Path.mkdir
        def fail(path, *args, **kwargs):
            if path.name == 'bot-eng':
                raise OSError('synthetic fixture failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', fail), self.assertRaises(OSError):
            prepare_workspace_context(self.identities)
        self.assertEqual(read(self.root/'workspace-fusion-context.json')['status'], 'INTENDED')
        with self.assertRaises(RunStateError):
            verify_workspace_context(self.identities)
        with self.assertRaises(RunStateError):
            prepare_workspace_context(self.identities)

    def test_remote_fusion_config_or_wrong_identity_type_is_rejected(self):
        with self.assertRaises(RunStateError):
            prepare_workspace_context(object())
        self.identities.runtime.inputs.config_path.write_text(json.dumps({
            'bots_base_dir': str(self.root/'bots'), 'bcsfuse': {'enabled': True}, 'llm': {'type': 'none'}}))
        with self.assertRaises(RunStateError):
            prepare_workspace_context(self.identities)
        self.assertFalse((self.root/'workspace-fusion-context.json').exists())


if __name__ == '__main__':
    unittest.main()
