"""Dual-environment Case Run entry for the session owner permissions slice.

One profile selects one tier. The owned-local Singlebox tier runs the full
in-process executor with strict per-request mock-header admission and
per-request minted gateway Principals. The preprod Gateway public-v1 tier
answers an offline NOT_RUN receipt: the reconnaissance conclusion frozen in
the migration contract (station bootstrap face absent, single-Human pilot
credential family, operator principal minting key, unbound participant
routes) means no honest preprod execution exists yet, and this module never
opens a transport to pretend otherwise.
"""
from __future__ import annotations

from pathlib import Path

from ..inventory import executor_digest
from ..profile import ConfigError, fingerprint, identifier, load
from ..run_state import RunStateError, write
from .local_session_owner_run import STATUS, local_session_owner_run
from .remote_session_owner_adapter import RemoteSessionOwnerAdapter
from .session_owner_contract import (CASE_ID, DOCUMENTED_GAPS, ENVIRONMENT, PARENT,
                                      session_owner_dual_adaptability)

SCHEMA_VERSION = 1
REPORT_CONTRACT = 'dual-session-owner-case-run-v1'
CREDENTIAL_KIND = 'owned_local_strict_mock_headers'
LOCAL_TIER_STATUS = 'LOCAL_PASS_PREPROD_DOCUMENTED_GAPS'
REMOTE_TIER_STATUS = 'PREPROD_NOT_RUN_DOCUMENTED_GAPS'


def load_session_owner_case_profile(path: Path) -> dict:
    """Load and structurally admit one tier profile; never resolve secrets."""
    requested = Path(path)
    if requested.is_symlink() or not requested.is_file():
        raise ConfigError('Session owner case profile must be a regular file')
    profile = load(requested)
    entrypoint = profile.get('entrypoint', {})
    environment = profile.get('environment', {})
    pair = (entrypoint.get('kind'), environment.get('tier'))
    if pair not in (('singlebox', 'local'), ('gateway', 'preprod')):
        raise ConfigError('Session owner case profile must be Singlebox-local or Gateway-preprod')
    if entrypoint.get('api_face') != 'public-v1':
        raise ConfigError('Session owner case profile requires the public-v1 face')
    auth = profile.get('auth', {})
    if pair == ('singlebox', 'local') and auth != {'kind': 'owned-local'}:
        raise ConfigError('Session owner local tier requires the owned-local auth declaration')
    if pair == ('gateway', 'preprod'):
        headers = auth.get('header_env', {}) if isinstance(auth, dict) else {}
        if 'Cookie' not in headers and auth.get('principal_signing_key_env') is None:
            raise ConfigError('Session owner preprod tier requires a declared credential reference')
    return profile


def dual_session_owner_adaptability() -> dict:
    return session_owner_dual_adaptability()


def _environment_slice(profile: dict) -> dict:
    return {'tier': profile['environment']['tier'], 'id': profile['environment']['id'],
            'entrypoint_kind': profile['entrypoint']['kind'],
            'api_face': profile['entrypoint']['api_face']}


def _fresh_state_dir(requested: Path) -> Path:
    if (not requested.is_absolute() or requested.is_symlink() or requested.exists()
            or not requested.parent.is_dir() or requested.parent.is_symlink()):
        raise ConfigError('Session owner case requires a fresh private state directory')
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError('Session owner case requires a fresh private state directory')
    state_dir.mkdir(mode=0o700, parents=False)
    return state_dir


def session_owner_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                           confirm_run_id: str, allow_write: bool,
                           allow_local_processes: bool = False,
                           allow_fixture_write: bool = False,
                           binary=None, binary_sha256: str = '') -> dict:
    """Execute one tier and return the honest dual-environment envelope."""
    run_id = identifier(run_id, 'Session owner case run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError('Session owner case requires explicit write permission and exact Run confirmation')
    profile = load_session_owner_case_profile(Path(profile_path))
    profile_digest = fingerprint(profile)
    artifact = executor_digest()
    adaptability = session_owner_dual_adaptability()
    requested = Path(state_dir)
    state_dir = _fresh_state_dir(requested)
    local_tier = profile['entrypoint']['kind'] == 'singlebox'

    write(state_dir / 'session-owner-profile.json',
          {'schema_version': SCHEMA_VERSION, 'profile_digest': profile_digest,
           'profile': profile})
    binding = {'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT, 'case_id': CASE_ID,
               'parent_id': PARENT, 'run_id': run_id, 'profile_digest': profile_digest,
               'executor_digest': artifact, 'credential_kind': CREDENTIAL_KIND,
               'adaptability': adaptability, 'documented_gaps': DOCUMENTED_GAPS,
               'environment': ENVIRONMENT, 'tier': 'local' if local_tier else 'preprod',
               'state_dir': str(state_dir)}
    write(state_dir / 'session-owner-case-binding.json', binding)

    local_run: dict
    remote_receipt: dict
    if local_tier:
        if not (allow_local_processes is True and allow_fixture_write is True):
            raise ConfigError('Session owner local tier requires process and fixture permissions')
        if binary is None or not binary_sha256:
            raise ConfigError('Session owner local tier requires the frozen BCS binary pair')
        local_run = local_session_owner_run(
            binary=Path(binary), binary_sha256=binary_sha256, state_dir=state_dir / 'local',
            run_id=run_id, confirm_run_id=run_id, allow_local_processes=True,
            allow_fixture_write=True, allow_write=True)
        remote_receipt = {'status': 'NOT_RUN', 'ready_to_execute': False,
                          'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
                          'reason': 'preprod public-v1 face carries the frozen documented gaps'}
        status = LOCAL_TIER_STATUS if local_run.get('status') == STATUS else 'BLOCKED'
        if local_run.get('status') == 'CANCELLED':
            status = 'CANCELLED'
    else:
        adapter = RemoteSessionOwnerAdapter(profile)
        remote_receipt = adapter.preflight()
        write(state_dir / 'session-owner-remote-gaps.json', remote_receipt)
        local_run = {'status': 'NOT_RUN', 'ready_to_execute': False,
                     'reason': 'owned-local executor requires the Singlebox-local tier profile'}
        status = REMOTE_TIER_STATUS

    envelope = {'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT, 'case_id': CASE_ID,
                'parent_id': PARENT, 'run_id': run_id, 'status': status,
                'adaptability': adaptability, 'documented_gaps': DOCUMENTED_GAPS,
                'credential_kind': CREDENTIAL_KIND, 'environment': _environment_slice(profile),
                'local_run': local_run, 'remote_receipt': remote_receipt,
                'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
                'ready_to_execute': False, 'state_dir': str(state_dir)}
    write(state_dir / 'session-owner-case-run.json', envelope)
    return envelope
