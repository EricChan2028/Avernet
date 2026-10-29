"""Run-frozen Case inventory and executor artifact identity.

Checksums detect drift, not malicious rewrites by someone who owns the Run files.
Historical reports can be read offline; executing with changed code is refused.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .artifact import process_digest
from .effect_policy import validate_effect_policy
from .profile import ConfigError, fingerprint, identifier
from .run_state import MAX_STATE_BYTES, RunStateError, read

ROOT = Path(__file__).resolve().parents[1]


def validate_manifest(value: dict) -> dict:
    try:
        if (set(value) != {'schema_version', 'baseline_commit', 'suite', 'legacy_inventory', 'cases'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['baseline_commit'], str)
                or len(value['baseline_commit']) != 40
                or any(c not in '0123456789abcdef' for c in value['baseline_commit'])):
            raise ValueError
        identifier(value['suite'], 'manifest suite')
        cases, legacy = value['cases'], value['legacy_inventory']
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
            raise ValueError
        if not isinstance(legacy, list) or not 1 <= len(legacy) <= 500:
            raise ValueError
        for items in (cases, legacy):
            ids = [identifier(item['id'], 'manifest item ID') for item in items]
            if len(set(ids)) != len(ids): raise ValueError
        for case in cases:
            if type(case['version']) is not int or case['version'] < 1: raise ValueError
            for field in ('assertions', 'effects', 'steps', 'roles', 'required_evidence'):
                items = case[field]
                if not isinstance(items, list) or not 1 <= len(items) <= 1000: raise ValueError
                if any(not isinstance(item, str) or not item or len(item) > 512 for item in items): raise ValueError
                if len(set(items)) != len(items): raise ValueError
            if any(not item.startswith(case['id'] + '.') for item in case['assertions']): raise ValueError
        return json.loads(json.dumps(value, allow_nan=False))
    except (KeyError, TypeError, ValueError, ConfigError):
        raise RunStateError('manifest schema, versions or inventory are invalid') from None


KNOWN_UNMAPPED_LEGACY_IDS = frozenset({
    'story_cli_operator_manages_group_context',
    'story_nested_fixed_loop_workflows',
    'story_state_machine_result_queue',
    'test_ep_dept_scope_parses_object_friend_ext_entries',
    'test_ep_tc_friend_auth_sync_readdressed_principal',
})
REVIEWED_RELEASE_PARENT_COUNT = 29


def reviewed_release_inventory(manifest: dict | None = None) -> list[dict]:
    """Return the reviewed 29-Story release denominator from the current 34.

    The post-rebase product inventory gained five stories outside the reviewed
    release-parent migration.  They are known backlog, not silently missing or
    absorbed into this release denominator.
    """
    value = current_manifest() if manifest is None else manifest
    legacy = value['legacy_inventory']
    ids = [row['id'] for row in legacy]
    reviewed = [row for row in legacy if row['id'] not in KNOWN_UNMAPPED_LEGACY_IDS]
    if len(ids) != len(set(ids)) or len(reviewed) != REVIEWED_RELEASE_PARENT_COUNT:
        raise RunStateError(
            'reviewed release denominator changed; review the five known '
            'unmapped stories separately')
    return reviewed


def assert_reviewed_parent(parent_id: str, *, family: str) -> str:
    """Fail closed unless a dual parent is a reviewed release parent.

    Known unmapped backlog parents stay executable and are named explicitly;
    anything outside the reviewed 29-story denominator or the unmapped backlog
    is rejected before any binding is frozen.
    """
    reviewed = {row['id'] for row in reviewed_release_inventory()}
    if not isinstance(parent_id, str) or (
            parent_id not in reviewed and parent_id not in KNOWN_UNMAPPED_LEGACY_IDS):
        raise RunStateError(
            family + ' parent is outside the reviewed release denominator')
    return parent_id


def current_manifest(suite: str = 'preprod-pilot-v1') -> dict:
    paths = {'preprod-pilot-v1': 'manifest.json', 'preprod-retained-v1': 'retained-manifest.json',
             'preprod-retained-file-v1': 'retained-file-manifest.json',
             'owned-local-trusted-team-v1': 'trusted-team-manifest.json'}
    if suite not in paths:
        raise RunStateError('unsupported executor suite')
    manifest = validate_manifest(read(ROOT / paths[suite]))
    if manifest['suite'] != suite:
        raise RunStateError('executor manifest has a different suite identity')
    validate_effect_policy(manifest)
    validate_evidence_policy(manifest)
    return manifest


def executor_digest() -> str:
    try:
        return process_digest()
    except (ValueError, OSError):
        raise RunStateError('executor artifact differs from the process-import snapshot') from None


def freeze_manifest(state_dir: Path, manifest: dict) -> str:
    manifest = validate_manifest(manifest)
    validate_effect_policy(manifest)
    validate_evidence_policy(manifest)
    path = state_dir / 'manifest.json'
    digest = fingerprint(manifest)
    if path.exists() or path.is_symlink():
        if fingerprint(validate_manifest(read(path))) != digest:
            raise RunStateError('existing Run manifest differs; never overwrite its inventory')
        return digest
    payload = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    if len(payload) > MAX_STATE_BYTES:
        raise RunStateError('manifest exceeds the supported size limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return digest


def frozen_manifest(state_dir: Path, state: dict) -> dict:
    manifest = validate_manifest(read(state_dir / 'manifest.json'))
    if state.get('manifest_digest') != fingerprint(manifest):
        raise RunStateError('Run manifest digest is missing or mismatched')
    artifact = state.get('executor_digest')
    if not isinstance(artifact, str) or len(artifact) != 64 or any(c not in '0123456789abcdef' for c in artifact):
        raise RunStateError('Run executor binding is missing or invalid')
    return manifest


def executable_case(state_dir: Path, state: dict, case_id: str) -> dict:
    manifest = frozen_manifest(state_dir, state)
    if fingerprint(current_manifest(manifest['suite'])) != state['manifest_digest']:
        raise RunStateError('Case inventory changed after prepare; start a new Run')
    if executor_digest() != state['executor_digest']:
        raise RunStateError('executor artifact changed after prepare; start a new Run')
    for case in manifest['cases']:
        if case['id'] == case_id:
            return case
    raise RunStateError('Case is absent from the frozen Run inventory')


# --- Finite evidence contracts (consolidated from evidence_policy.py) ---
# Admission only: declarations are not observation receipts or qualification.
# Historical snapshots remain readable without applying today's execution policy.

_REQUIRED = {
    'owned-local-trusted-team-v1': {
        'LOCAL-TRUST-API-CLI-01': {'api', 'cli', 'sqlite'},
    },
    'preprod-pilot-v1': {
        'PRE-01': {'api', 'ob'},
        'PRE-02': {'api', 'websocket', 'ob'},
        'PRE-03': {'api', 'bytes', 'ob'},
    },
    'preprod-retained-v1': {
        'RETAIN-GROUP-01': {'api', 'ob'},
        'RETAIN-SESSION-01': {'api', 'websocket', 'ob'},
    },
    'preprod-retained-file-v1': {
        'RETAIN-FILE-01': {'api', 'bytes', 'ob'},
    },
}
_DIAGNOSTIC = {'request_correlation', 'logs'}


def validate_evidence_policy(manifest: dict) -> None:
    """Reject unsupported new execution, not historical schema or rendering.

    ``ob`` names the semantic persistence requirement in the shared inventory;
    the owned-local adapter explicitly substitutes SQLite, never remote OB.
    New mandatory sources require an executor/preflight contract, not just JSON.
    """
    contracts = _REQUIRED.get(manifest['suite'])
    if contracts is None:
        raise RunStateError('unsupported suite evidence contract')
    for case in manifest['cases']:
        required = set(case['required_evidence'])
        diagnostic = case.get('diagnostic_evidence')
        if (case['id'] not in contracts or required != contracts[case['id']]):
            raise RunStateError('Case required evidence differs from its executor contract')
        if (not isinstance(diagnostic, list) or len(diagnostic) > len(_DIAGNOSTIC)
                or any(not isinstance(item, str) or item not in _DIAGNOSTIC for item in diagnostic)
                or len(set(diagnostic)) != len(diagnostic)
                or required.intersection(diagnostic)):
            raise RunStateError('Case diagnostic evidence is invalid or unsupported')
