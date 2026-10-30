"""Shared published restart dispatches policies; it must not interpret coding state."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[3] / 'src/agentclaw/community/core'
_FLOW = 'service_bot/services/publish_flow/'
_SHARED_PATHS = (
    _FLOW + 'restart_mixin.py',
    _FLOW + 'operation_runner.py',
    _FLOW + 'tasks.py',
    'bot_management/engines/provisioning.py',
    'bot_management/engines/restart_contract.py',
)
_POLICY_KEYS = {'guard', 'precondition_error', 'fastdisk_targets', 'coding_restart'}
_POLICY_ERRORS = {'RestartPreconditionError', 'RestartBackupBlocked', 'RestartBackupError'}
_ENGINES = {'aicoding', 'claude_code'}


@pytest.mark.parametrize('relative', _SHARED_PATHS)
def test_shared_restart_does_not_interpret_coding_policy(relative):
    tree = ast.parse((_CORE / relative).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert '.engines.aicoding' not in (node.module or ''), (relative, node.lineno)
            assert not _POLICY_ERRORS.intersection(alias.name for alias in node.names)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in _POLICY_KEYS | _POLICY_ERRORS | _ENGINES, (relative, node.lineno)
        if isinstance(node, ast.Name):
            assert node.id not in _POLICY_ERRORS, (relative, node.lineno)


def test_instance_dispatch_does_not_assemble_coding_backup_dependencies():
    tree = ast.parse((_CORE / 'bot_management/engines/registry.py').read_text())
    dispatcher = next(node for node in tree.body if getattr(node, 'name', '') == 'prepare_instance_restart')
    assert not any(isinstance(node, (ast.If, ast.Try, ast.ImportFrom, ast.Import))
                   for node in ast.walk(dispatcher))
    assert any(isinstance(node, ast.Attribute) and node.attr == 'prepare_restart_async'
               for node in ast.walk(dispatcher))
