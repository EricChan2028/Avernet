"""Create-once context inputs for the product's owned-local simple fusion.

These are explicit test fixture files, not fabricated fusion responses or an
engine runtime. No remote Bot filesystem or user context is ever adopted.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat

from .inventory import executor_digest
from .local_role_identity import OwnedLocalRoleIdentity
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

FUSION_CONTEXT = {
    'PM': {'SOUL.md': 'Workspace PM: coordinate the release window.\n',
           'MEMORY.md': 'Workspace PM evidence: acceptance criteria reviewed.\n'},
    'ENG': {'SOUL.md': 'Workspace ENG: validate the implementation.\n',
            'MEMORY.md': 'Workspace ENG evidence: regression tests completed.\n'},
}
CONTRACT = 'owned-local-workspace-simple-fusion-fixture-v1'


def _bound(identities):
    if type(identities) is not OwnedLocalRoleIdentity or identities.fixture_set != 'workspace':
        raise RunStateError('Workspace contexts require fresh owned workspace identities')
    identities._require_roles(('PM', 'ENG'))
    root = identities.runtime.root
    base = root/'bots'
    config = json.loads(identities.runtime.inputs.config_path.read_text(), object_pairs_hook=unique_keys)
    if (root.is_symlink() or base.is_symlink() or not base.is_dir()
            or config.get('bots_base_dir') != str(base)
            or config.get('bcsfuse') != {'enabled': False}
            or config.get('llm') != {'type': 'none'}):
        raise RunStateError('Workspace contexts require the exact owned local simple-fusion configuration')
    roles = {role: identifier(identities._entries[role][0], 'fusion fixture Bot ID') for role in FUSION_CONTEXT}
    return root, base, roles


def _binding(identities, roles):
    return {'schema_version': 1, 'contract': CONTRACT, 'run_id': identities.run_id,
            'executor_digest': executor_digest(), 'roles': roles,
            'fixture_digest': fingerprint(read(identities.fixtures.path)),
            'mode': 'PRODUCT_LOCAL_SIMPLE_FUSION_NO_LLM',
            'engine_execution': 'NOT_PROVEN', 'remote_fusion': 'NOT_TESTED'}


def verify_workspace_context(identities) -> dict:
    root, base, roles = _bound(identities)
    receipt = read(root/'workspace-fusion-context.json')
    expected = _binding(identities, roles)
    if any(receipt.get(key) != value for key, value in expected.items()) or receipt.get('status') != 'READY':
        raise RunStateError('Workspace fusion context receipt is not ready or bound')
    files = []
    for role, bot_id in roles.items():
        directory = base/bot_id
        if directory.is_symlink() or not directory.is_dir():
            raise RunStateError('Workspace context directory is not owned regular data')
        for name, text in FUSION_CONTEXT[role].items():
            path = directory/name
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
                    raise RunStateError('Workspace context file is not bounded private data')
                data = stream.read(4097)
            if data != text.encode():
                raise RunStateError('Workspace fusion input changed')
            files.append({'role': role, 'name': name, 'sha256': hashlib.sha256(data).hexdigest()})
    if receipt.get('files') != files:
        raise RunStateError('Workspace fusion context evidence changed')
    return receipt


def prepare_workspace_context(identities) -> dict:
    root, base, roles = _bound(identities)
    receipt_path = root/'workspace-fusion-context.json'
    if (receipt_path.exists() or receipt_path.is_symlink()
            or any((base/bot_id).exists() or (base/bot_id).is_symlink() for bot_id in roles.values())):
        raise RunStateError('Workspace contexts require fresh paths; never overwrite or adopt')
    receipt = _binding(identities, roles) | {'status': 'INTENDED', 'files': []}
    # A partial fixture stays INTENDED. Never retry or silently complete it.
    fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    write(receipt_path, receipt)
    for role, bot_id in roles.items():
        identities._require_roles(('PM', 'ENG'))
        directory = base/bot_id
        directory.mkdir(mode=0o700)
        for name, text in FUSION_CONTEXT[role].items():
            data = text.encode()
            fd = os.open(directory/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            receipt['files'].append({'role': role, 'name': name, 'sha256': hashlib.sha256(data).hexdigest()})
    receipt['status'] = 'READY'
    write(receipt_path, receipt)
    return verify_workspace_context(identities)
