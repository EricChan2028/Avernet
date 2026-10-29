"""Slice-owned configuration bridge for the Draft delivery owned runtime."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
from unittest.mock import patch

from ..run_state import RunStateError
from .contract import CONFIG_REQUIRED


def merge_draft_config(config_path: Path) -> None:
    """Merge the slice-owned Draft enablement into an already-private config.

    The shared local_config intentionally has no collaboration_drafts section;
    integration is kept here so the slice never edits another module's config
    generator. The file must still be the regular 0600 file just created by
    prepare_local_bcs_inputs, and the merge is written atomically in place.
    """
    path = Path(config_path)
    try:
        metadata = path.lstat()
    except OSError:
        raise RunStateError('draft-delivery runtime config is missing') from None
    if (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1):
        raise RunStateError('draft-delivery runtime config must be a regular unshared file')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RunStateError('draft-delivery runtime config is unreadable') from None
    if not isinstance(value, dict):
        raise RunStateError('draft-delivery runtime config must be an object')
    existing = value.get('collaboration_drafts')
    if existing not in (None, CONFIG_REQUIRED['collaboration_drafts'], CONFIG_REQUIRED):
        raise RunStateError('draft-delivery runtime config contains a conflicting Draft section')
    value['collaboration_drafts'] = dict(CONFIG_REQUIRED['collaboration_drafts'])
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
def draft_delivery_config_overlay():
    """Temporarily merge the fragment after the shared config generator runs."""
    import bcs_e2e.owned_bcs as owned_bcs

    original = owned_bcs.prepare_local_bcs_inputs

    def prepare_with_drafts(*args, **kwargs):
        inputs = original(*args, **kwargs)
        merge_draft_config(inputs.config_path)
        return inputs

    with patch.object(owned_bcs, 'prepare_local_bcs_inputs', prepare_with_drafts):
        yield
