"""Slice-owned configuration bridge for the Provider subbots owned runtime."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
from unittest.mock import patch

from ..run_state import RunStateError
from .contract import CONFIG_REQUIRED


def merge_provider_subbots_config(config_path: Path) -> None:
    """Merge the Provider control-plane pin into an already-private config.

    The shared ``local_config`` deliberately owns no Provider rollout policy.
    The merge runs immediately after ``prepare_local_bcs_inputs`` created its
    0600 regular file, refuses conflicting pre-existing values, and writes the
    complete object back through an ``O_NOFOLLOW`` descriptor.
    """
    path = Path(config_path)
    try:
        metadata = path.lstat()
    except OSError:
        raise RunStateError('provider-subbots runtime config is missing') from None
    if (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1):
        raise RunStateError('provider-subbots runtime config must be a regular unshared file')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RunStateError('provider-subbots runtime config is unreadable') from None
    if not isinstance(value, dict):
        raise RunStateError('provider-subbots runtime config must be an object')
    for key, required in CONFIG_REQUIRED.items():
        if key in value and value[key] != required:
            raise RunStateError('provider-subbots runtime config conflicts at ' + key)
        value[key] = required if not isinstance(required, list) else list(required)
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


@contextmanager
def provider_subbots_config_overlay():
    """Temporarily merge the fragment after the shared config generator runs."""
    import bcs_e2e.owned_bcs as owned_bcs

    original = owned_bcs.prepare_local_bcs_inputs

    def prepare_with_subbots(*args, **kwargs):
        inputs = original(*args, **kwargs)
        merge_provider_subbots_config(inputs.config_path)
        return inputs

    with patch.object(owned_bcs, 'prepare_local_bcs_inputs', prepare_with_subbots):
        yield
