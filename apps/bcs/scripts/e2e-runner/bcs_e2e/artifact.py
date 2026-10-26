"""Capture source identity before importing runner implementation modules."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def disk_digest() -> str:
    paths = [ROOT / 'runner.py', *sorted((ROOT / 'bcs_e2e').glob('*.py'))]
    # Keep the executor inventory small, but leave room for focused modules;
    # the old hard cap blocked splitting oversized adapters while still
    # enforcing the stricter per-file 1,000-line CI limit. The dual
    # Workspace round-trip and proposal confirmation contracts each
    # add one focused Case-executor module, the structured-collaboration
    # core slice adds its Case contract and remote adapter, and the
    # register-onboard register core slice adds its Case contract and
    # remote adapter, so the focused-module allowance grows by exactly
    # those six files. The dual Channel product-CLI slice adds its Case
    # Profile, product-CLI runner and remote adapter, so the allowance
    # grows by exactly those three files. The fixed-Loop per-Run sandbox
    # isolation slice adds one focused module, and the fixed-Loop case
    # executor adds its owned-local seam implementation, so the
    # allowance grows by exactly those two files. The fixed-Loop owned
    # executor adds its run lifecycle and offline report projection, so
    # the allowance grows by exactly those two files. The edge
    # friend-mutation executor adds its case contract, owned adapter,
    # run lifecycle and offline report projection, so the allowance
    # grows by exactly those four files. The human delivery-policy
    # CAS executor adds its source contract, owned adapter, run
    # lifecycle and offline report projection, so the allowance grows
    # by exactly those four files. The operator CLI parent executor
    # adds its source contract, owned adapter, run lifecycle and
    # offline report projection, so the allowance grows by exactly
    # those four files. The provider-free owned runtime holds one
    # fixture Bot streaming connection per chat delivery window, so
    # the operator CLI slice adds one focused Bot WebSocket seam,
    # and the allowance grows by exactly that one file. The retained
    # Session/WS offline closure projection adds one focused review module, so
    # the allowance grows by exactly that one file. Splitting the crowded
    # Workspace command mapping into its focused command contract adds
    # exactly one more file. The Session Service lifecycle slice is also
    # split into its own focused Case module, and the completion plus
    # service-roundtrip slice adds one further focused module.
    # The collaboration-team cross-environment product-CLI slice adds its
    # Profile, bounded Runner, remote Adapter and Case contract, and its
    # owned-local run/report lifecycle, so the focused-module allowance grows
    # by exactly those six files. Its focused transport
    # reader adds one further file. Joining the three custom-collaboration
    # obligations into one owned-local parent case adds one focused module, so
    # the allowance grows by exactly that one file. The complete Session
    # Service parent case similarly combines its four focused slices in one
    # focused composition module, so the allowance grows by exactly one file.
    # The custom-collaboration and Session Service owned-local lifecycles add
    # four focused execution/report modules, and the offline 6.6 entry-switch
    # review adds one focused module, so the allowance grows by exactly
    # those five files. The reviewed remote-reconcile lifecycle is also a
    # single focused module, so the allowance grows by exactly one file.
    # The 7.2 Aliyun target binding adds one focused offline contract module.
    # The dual Workspace status/termination and Group case command lifecycles each add
    # one further focused module, and the remaining product-CLI case lifecycle adds
    # one more, so the focused-module allowance grows by those three files.
    # The dual Session, Channel, Session-file and friendship product-CLI
    # command lifecycles each add one focused module, so the focused-module
    # allowance grows by exactly those four files. The remaining Workspace
    # incident/roundtrip/proposal and collaboration-team command lifecycles
    # add two further focused modules.
    # The Channel-binding, Edge read, actor-status, register,
    # structured-core, structured-templates, friend-decision,
    # friend-public-accept and network-read command lifecycles add nine
    # further focused modules. Splitting the crowded cross-environment command
    # registration out of the CLI adds one more focused module. The dual Event
    # Webhook subscription command lifecycle adds one further focused module.
    # The dual public Session JWT/WS command lifecycle adds one further focused
    # module while reusing its existing admission and owned-local executor.
    # The dual Group Context product-CLI story adds one focused Case contract,
    # one remote Adapter and one command Run lifecycle, so the allowance grows
    # by exactly those three files.
    if len(paths) > 297:
        raise ValueError('executor artifact exceeds its source-file budget')
    digest = hashlib.sha256()
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError('executor artifact must contain bounded regular source files')
        data = path.read_bytes()
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0')
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


# Imported by package __init__ before cli, adapters, pilot or other leaf modules.
PROCESS_EXECUTOR_DIGEST = disk_digest()


def process_digest() -> str:
    if disk_digest() != PROCESS_EXECUTOR_DIGEST:
        raise ValueError('runner source changed after process import; start a fresh process and Run')
    return PROCESS_EXECUTOR_DIGEST
