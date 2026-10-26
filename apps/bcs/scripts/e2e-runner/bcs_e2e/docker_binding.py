"""Offline review gate for task 7.2 Aliyun Docker target binding.

This module never starts a container, reads a registry, resolves a secret, or
contacts a network endpoint.  It turns the deployment draft into a strict
machine-readable contract and reports the exact blockers needed before a live
run can be admitted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .profile import ConfigError, unique_keys

_CONTRACT = "bcs-aliyun-docker-binding-review-v1"
_TOP_KEYS = {
    "schema_version", "environment", "target", "runner", "mounts",
    "network", "security", "observer", "runtime",
}
_ENVIRONMENT_KEYS = {"lifecycle", "deployment", "provider"}
_TARGET_KEYS = {"kind", "resource_id", "image_digest"}
_RUNNER_KEYS = {"image_digest"}
_MOUNT_KEYS = {"source", "target", "read_only"}
_NETWORK_KEYS = {"api_origin", "callback", "protocols"}
_SECURITY_KEYS = {"tls"}
_TLS_KEYS = {"min_version", "verification"}
_OBSERVER_KEYS = {"kind", "server", "database", "environment", "binding_confirmed"}
_RUNTIME_KEYS = {"termination_signal", "kill_signal", "timeout_seconds"}
_TARGET_KINDS = {"kubernetes-namespace", "aliyun-ecs", "managed-container"}
_PROTOCOLS = {"HTTPS", "WEBSOCKET", "MCP"}


def _keys(
    value: object, keys: set[str], blocker: str, blockers: list[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != keys:
        blockers.append(blocker)
        return None
    return value


def _text(value: object, blocker: str, blockers: list[str], *, maximum: int = 512) -> str | None:
    if (not isinstance(value, str) or not value.strip()
            or len(value) > maximum
            or any(ord(char) < 32 for char in value)
            or not value.isascii()
            or value.startswith("REPLACE_WITH_")):
        blockers.append(f"{blocker}:invalid")
        return None
    return value


def _path(value: object, blocker: str, blockers: list[str]) -> str | None:
    if (not isinstance(value, str) or not value.startswith("/")
            or len(value) > 512 or any(ord(char) < 32 for char in value)
            or not value.isascii()
            or value.startswith("/REPLACE_WITH_")):
        blockers.append(f"{blocker}:invalid")
        return None
    return value


def _image_digest(value: object, blocker: str, blockers: list[str]) -> str | None:
    if (not isinstance(value, str) or not value.isascii()
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*(?::[0-9]{1,5})?"
                r"(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+@sha256:[0-9a-f]{64}",
                value,
            )):
        blockers.append(f"{blocker}:invalid")
        return None
    return value


def _https_origin(value: object, blocker: str, blockers: list[str]) -> str | None:
    if not isinstance(value, str) or not value.isascii() or len(value) > 512:
        blockers.append(f"{blocker}:invalid")
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        blockers.append(f"{blocker}:invalid")
        return None
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        blockers.append(f"{blocker}:invalid")
        return None
    return value.rstrip("/")


def _bool(value: object, blocker: str, blockers: list[str]) -> bool | None:
    if type(value) is not bool:
        blockers.append(blocker)
        return None
    return value


def _mount(value: object, blocker: str, blockers: list[str]) -> dict[str, Any] | None:
    mount = _keys(value, _MOUNT_KEYS, f"{blocker}.keys", blockers)
    if mount is None:
        return None
    source = _path(mount["source"], f"{blocker}.source", blockers)
    target = _path(mount["target"], f"{blocker}.target", blockers)
    read_only = _bool(mount["read_only"], f"{blocker}.read_only", blockers)
    if source is None or target is None or read_only is None:
        return None
    return {"source": source, "target": target, "read_only": read_only}


def review_docker_binding(raw: object) -> dict[str, Any]:
    """Review the offline contract; live execution evidence is separate."""
    blockers: list[str] = []
    contract = _keys(raw, _TOP_KEYS, "binding.keys", blockers)
    if contract is None:
        return _blocked_binding(blockers)

    if type(contract["schema_version"]) is not int or contract["schema_version"] != 1:
        blockers.append("binding.schema_version:expected 1")

    environment = _keys(
        contract["environment"], _ENVIRONMENT_KEYS, "environment.keys", blockers,
    )
    if environment is not None:
        if environment["lifecycle"] != "remote-attach":
            blockers.append("environment.lifecycle:expected remote-attach")
        if environment["deployment"] != "docker":
            blockers.append("environment.deployment:expected docker")
        if environment["provider"] != "aliyun":
            blockers.append("environment.provider:expected aliyun")

    target = _keys(contract["target"], _TARGET_KEYS, "target.keys", blockers)
    if target is not None:
        if target["kind"] not in _TARGET_KINDS:
            blockers.append("target.kind:unsupported")
        if not _text(target["resource_id"], "target.resource_id", blockers):
            pass
        _image_digest(target["image_digest"], "target.image_digest", blockers)

    runner = _keys(contract["runner"], _RUNNER_KEYS, "runner.keys", blockers)
    if runner is not None:
        _image_digest(runner["image_digest"], "runner.image_digest", blockers)

    mounts_value = _keys(
        contract["mounts"], {"profile", "secret", "report"},
        "mounts.keys", blockers,
    )
    if mounts_value is not None:
        profile = _mount(mounts_value["profile"], "mounts.profile", blockers)
        secret = _mount(mounts_value["secret"], "mounts.secret", blockers)
        report = _mount(mounts_value["report"], "mounts.report", blockers)
        if profile is not None and secret is not None and report is not None:
            if profile["read_only"] is not True or secret["read_only"] is not True:
                blockers.append("mounts.read_only:profile and secret must be read-only")
            if report["read_only"] is not False:
                blockers.append("mounts.report:must be writable")
            targets = {profile["target"], secret["target"], report["target"]}
            if len(targets) != 3:
                blockers.append("mounts.target:must be distinct")
            mounts = {"profile": profile, "secret": secret, "report": report}

    network = _keys(contract["network"], _NETWORK_KEYS, "network.keys", blockers)
    if network is not None:
        _https_origin(network["api_origin"], "network.api_origin", blockers)
        _https_origin(network["callback"], "network.callback", blockers)
        protocols = network["protocols"]
        if not isinstance(protocols, list) or not set(protocols) <= _PROTOCOLS or not protocols:
            blockers.append("network.protocols:unsupported")
        elif "HTTPS" not in set(protocols):
            blockers.append("network.protocols:HTTPS required")

    security = _keys(contract["security"], _SECURITY_KEYS, "security.keys", blockers)
    if security is not None:
        tls = _keys(security["tls"], _TLS_KEYS, "security.tls.keys", blockers)
        if tls is not None:
            if tls["min_version"] not in {"TLS1_2", "TLS1_3"}:
                blockers.append("security.tls.min_version:unsupported")
            if tls["verification"] != "SYSTEM_TRUSTED_CA":
                blockers.append("security.tls.verification:expected SYSTEM_TRUSTED_CA")

    observer = _keys(contract["observer"], _OBSERVER_KEYS, "observer.keys", blockers)
    if observer is not None:
        if observer["kind"] != "alisddbquery":
            blockers.append("observer.kind:expected alisddbquery")
        if not _text(observer["server"], "observer.server", blockers):
            pass
        if not _text(observer["database"], "observer.database", blockers):
            pass
        if observer["environment"] not in {"preprod", "production"}:
            blockers.append("observer.environment:unsupported")
        if observer["binding_confirmed"] is not True:
            blockers.append("observer.binding_confirmed:expected true")

    runtime = _keys(contract["runtime"], _RUNTIME_KEYS, "runtime.keys", blockers)
    if runtime is not None:
        if runtime["termination_signal"] != "SIGTERM":
            blockers.append("runtime.termination_signal:expected SIGTERM")
        if runtime["kill_signal"] != "SIGKILL":
            blockers.append("runtime.kill_signal:expected SIGKILL")
        if (type(runtime["timeout_seconds"]) is not int
                or not 1 <= runtime["timeout_seconds"] <= 3600):
            blockers.append("runtime.timeout_seconds:integer outside bounds")

    # Keep the final policy explicit even when the offline contract is valid.
    ready = not blockers
    return {
        "schema_version": 1,
        "contract": _CONTRACT,
        "status": "READY" if ready else "BLOCKED",
        "ready_to_execute": ready,
        "scope": "OFFLINE_REVIEW_ONLY",
        "binding": contract if ready else None,
        "blockers": blockers,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "requires_live_validation": True,
    }


def review_docker_binding_file(path: Path) -> dict[str, Any]:
    """Load one private binding file and perform the same offline review."""
    if path.is_symlink() or not path.is_file():
        raise ConfigError("binding: file not found")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except UnicodeError:
        raise ConfigError("binding: file must be UTF-8 JSON") from None
    except json.JSONDecodeError:
        raise ConfigError("binding: file is not valid JSON") from None
    except OSError:
        raise ConfigError("binding: file cannot be read") from None
    return review_docker_binding(raw)


def _blocked_binding(blockers: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract": _CONTRACT,
        "status": "BLOCKED",
        "ready_to_execute": False,
        "scope": "OFFLINE_REVIEW_ONLY",
        "binding": None,
        "blockers": blockers,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "requires_live_validation": True,
    }
