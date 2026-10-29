"""Shared preparation: freeze Run/profile binding and acquire its fixture pool."""
from __future__ import annotations

from pathlib import Path

from .inventory import current_manifest, executor_digest, freeze_manifest
from .ledger import Ledger
from .mock_state import MockStateStore
from .pool import PoolLease, pool_path, pool_scope
from .identity_binding import require_pilot_execution_binding
from .profile import ConfigError, fingerprint, require_resolved
from .retained_contract import retained_suites
from .run_state import read as read_state, write as write_state
from .remote_observer import FILENAME, validate_binding, binding_summary


def prepare_run(profile: dict, state_dir: Path, run_id: str, *, manifest: dict | None = None,
                observer_binding: Path | None = None, _held_lease: PoolLease | None = None) -> dict:
    if profile.get('schema_version') == 2 or manifest is not None and manifest.get('suite') == 'owned-local-trusted-team-v1':
        raise ConfigError('trusted-team registration is admitted only by the owned local-trust-run lifecycle')
    require_pilot_execution_binding(profile)
    require_resolved(profile)
    if profile["environment"]["tier"] not in {"local", "preprod"}:
        raise ConfigError("prepare: only local/preprod profiles are admitted")
    if profile["policy"]["exclusive_use_confirmed"] is not True:
        raise ConfigError("prepare: fixture-pool exclusive use is not confirmed")
    receipt = validate_binding(read_state(observer_binding), profile) if observer_binding is not None else None
    digest = fingerprint(profile)
    artifact_digest = executor_digest()
    if state_dir.exists() and (state_dir.is_symlink() or not state_dir.is_dir()):
        raise ConfigError("prepare: state-dir must be a regular directory")
    markers = ('run.json', 'run.sqlite', 'local-run.json', 'remote-run.json', 'cleanup.json', 'runtime.json', FILENAME)
    if (any((state_dir / name).exists() or (state_dir / name).is_symlink() for name in markers)
            or any(state_dir.glob('report-*.json')) or any(state_dir.glob('recovery-*.json'))):
        raise ConfigError('prepare: existing Run artifacts must be preserved; use a new state directory')
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_dir.is_symlink():
        raise ConfigError("prepare: state-dir cannot be a symlink")
    manifest_digest = freeze_manifest(state_dir, current_manifest() if manifest is None else manifest)
    scope = pool_scope(profile)
    if _held_lease is not None:
        _held_lease.require_held()
        if (_held_lease.path != pool_path(scope) or _held_lease.run_id != run_id
                or _held_lease.profile_digest != digest or manifest is None
                or manifest.get('suite') not in retained_suites()):
            raise ConfigError('held retained lease differs from preparation scope')
    lock = _held_lease or PoolLease.acquire(pool_path(scope), run_id, digest)
    try:
        ledger_path = state_dir / "run.sqlite"
        with Ledger(ledger_path, run_id, digest, create=True):
            pass
        if profile["entrypoint"]["kind"] == "mock":
            MockStateStore(state_dir, run_id, profile).create()
        if receipt is not None:
            write_state(state_dir / FILENAME, receipt)
        write_state(state_dir / "run.json", {
            **({"observer_binding": binding_summary(receipt)} if receipt is not None else {}),
            "schema_version": 1, "status": "PREPARED", "ready_to_execute": False,
            "run_id": run_id, "profile_digest": digest,
            "fixture_pool": profile["policy"]["fixture_pool"], "pool_scope": scope,
            "adapter_kind": profile["entrypoint"]["kind"],
            "manifest_digest": manifest_digest, "executor_digest": artifact_digest,
            "lock_path": str(lock.path), "ledger_path": str(ledger_path),
            "note": "Preparation reserves the pool; it is not readiness or release qualification.",
        })
    except Exception:
        if _held_lease is None:
            lock.release()
        raise
    finally:
        if _held_lease is None:
            lock.close()
    return {"schema_version": 1, "status": "PREPARED", "ready_to_execute": False,
              "run_id": run_id, "profile_digest": digest,
              "state_dir": str(state_dir), "lease": lock.public_view(),
              "ledger": str(state_dir / "run.sqlite"),
              "next": "run PRE-01 with explicit write confirmation, or release the lease"}
