"""Publish the draft home's CLI directory as an engine-adjacent artifact."""

from collections import deque
from collections.abc import Callable
from pathlib import Path
import stat
import tempfile
import time
from typing import Any

from agentclaw.community.core.service_bot.services.build_ignore_rules import (
    extra_root_rules,
    is_excluded,
)
from agentclaw.community.log import get_logger

logger = get_logger()

# CodeFuse creates and upgrades these launchers in the runtime home. Their
# symlinks can be absolute while the draft home is mounted under a merge-NAS
# path, so materializing them during a host-side build can reference the
# build host rather than the published bot. Excluding only these fixed
# runtime-managed names lets unrelated broken symlinks still fail loudly.
_RUNTIME_MANAGED_LOCAL_BIN_NAMES = (
    "cfuse",
    "cfuse-claude",
    "cfuse-codex",
)


def _validate_home_dirs(home: Path) -> None:
    # COSEC: fixed home metadata directories must not redirect copying outside
    # the selected artifact/home through an existing symbolic link.
    for directory in (home, home / ".local"):
        if directory.is_symlink():
            raise ValueError(f"home artifact directory is a symlink: {directory}")
        if directory.exists() and not directory.is_dir():
            raise NotADirectoryError(f"home artifact path is not a directory: {directory}")


def _resolve_cli_source(path: Path, source_home: Path) -> Path:
    """Resolve components within one Bot Home, including container absolute links."""
    home = source_home.absolute()
    pending = deque(path.absolute().relative_to(home).parts)
    current = home
    hops = 0
    raw = ""
    started = time.monotonic()
    seen = set()
    try:
        # COSEC: the chosen Home itself must be a real directory, not a link
        # into another Bot. Its trusted host ancestors are outside this scope.
        home_mode = home.lstat().st_mode
        if not stat.S_ISDIR(home_mode):
            raise ValueError(f"Bot Home root must be a real directory: {home}")
        while pending:
            if not current.is_dir():
                raise NotADirectoryError(str(current))
            part = pending.popleft()
            # Path.parts removes '.' but leaves '..' for component-wise use.
            if part == "..":
                # COSEC: consume .. only after preceding links were resolved;
                # never allow it to move outside the selected Bot's Home.
                if current == home:
                    raise ValueError(f"CLI path escapes Bot Home: {path}")
                current = current.parent
                continue
            candidate = current / part
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                state = (candidate, tuple(pending))
                if state in seen or hops >= 40:
                    raise ValueError(f"CLI symlink cycle or hop limit: {candidate}")
                seen.add(state)
                hops += 1
                raw = str(candidate.readlink())
                target = Path(raw)
                if target.is_absolute():
                    # COSEC: do not use host resolve(). Map every absolute hop
                    # by components, rejecting Host/other-Bot absolute targets.
                    if target.is_relative_to("/home/admin"):
                        parts = target.relative_to("/home/admin").parts
                    elif target.is_relative_to(home):
                        parts = target.relative_to(home).parts
                    else:
                        raise ValueError(f"CLI absolute link escapes Bot Home: {candidate} -> {raw}")
                    current = home
                else:
                    parts = target.parts
                pending.extendleft(reversed(parts))
                logger.info(
                    "service_bot.local_bin.mapping.request source=%s target=%s raw=%s resolved=%s hop=%s",
                    candidate, current.joinpath(*pending), raw, current, hops,
                )
            else:
                current = candidate
        mode = current.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"CLI source is not a regular file or directory: {current}")
    except Exception as exc:
        logger.error(
            "service_bot.local_bin.mapping.failure source=%s target=%s raw=%s resolved=%s "
            "error_type=%s error=%s elapsed_ms=%s",
            path, current, raw, current, type(exc).__name__, str(exc),
            round((time.monotonic() - started) * 1000),
        )
        raise
    if hops:
        logger.info(
            "service_bot.local_bin.mapping.success source=%s target=%s raw=%s "
            "resolved=%s hops=%s elapsed_ms=%s",
            path, current, raw, current, hops, round((time.monotonic() - started) * 1000),
        )
    return current


def _stage_cli_bin(source_bin: Path, stage_bin: Path, source_home: Path,
                   excludes: tuple[str, ...]) -> None:
    """Stage only bin entries; rsync dereferences links to validated NAS files."""
    def visit(source: Path, output: Path, relative: str, ancestors: set) -> None:
        if relative and is_excluded(relative, excludes):
            return
        resolved = _resolve_cli_source(source, source_home)
        metadata = resolved.stat()
        if stat.S_ISDIR(metadata.st_mode):
            identity = (metadata.st_dev, metadata.st_ino)
            # COSEC: per-branch ancestry detects directory cycles while allowing
            # independent aliases to the same directory to retain their files.
            if identity in ancestors:
                raise ValueError(f"CLI directory cycle: {source}")
            output.mkdir()
            for child in resolved.iterdir():
                name = f"{relative}/{child.name}" if relative else child.name
                visit(child, output / child.name, name, ancestors | {identity})
            output.chmod(stat.S_IMODE(metadata.st_mode))
        else:
            output.symlink_to(resolved)

    visit(source_bin, stage_bin, "", set())


def sync_local_bin_artifact(
    *,
    source_home: Path,
    target_dir: Path,
    device_id: str,
    version: str,
    build_ignore_paths: tuple[str, ...],
    run_command: Callable[..., Any],
) -> None:
    """Copy only .local/bin; a present directory must copy successfully.

    The sibling bin snapshot is cleaned independently of the engine migration,
    so an absent/ignored CLI source cannot retain a previous retry's snapshot.
    """
    source_bin = source_home / ".local/bin"
    artifact_home = target_dir.parent
    target_bin = artifact_home / ".local/bin"
    started = time.monotonic()
    fields = "device_id=%s version=%s source=%s target=%s"
    context = (device_id, version, source_bin, target_bin)
    logger.info(
        "service_bot.local_bin.request " + fields + " ignore_paths=%s",
        *context, build_ignore_paths,
    )
    try:
        _validate_home_dirs(artifact_home)
        if target_bin.is_symlink():
            raise ValueError("home artifact bin is a symlink")
        if target_bin.exists():
            if not target_bin.is_dir():
                raise NotADirectoryError("home artifact bin is not a directory")
        reason = ""
        if is_excluded(".local/bin", build_ignore_paths):
            reason = "excluded"
        else:
            _resolve_cli_source(source_home, source_home)
            # Inspect .local without following its link in the host namespace.
            try:
                source_bin.parent.lstat()
            except FileNotFoundError:
                reason = "source_missing"
            else:
                parent = _resolve_cli_source(source_bin.parent, source_home)
                try:
                    (parent / "bin").lstat()
                except FileNotFoundError:
                    reason = "source_missing"
                else:
                    resolved_bin = _resolve_cli_source(source_bin, source_home)
        if reason:
            if target_bin.exists():
                # Only the validated bin snapshot is synchronized against an
                # empty source; the version root and other Home data stay put.
                logger.info(
                    "service_bot.local_bin.cleanup.request " + fields
                    + " reason=%s cleanup_method=rsync",
                    *context, reason,
                )
                with tempfile.TemporaryDirectory(prefix="service-bot-cli-empty-") as empty_dir:
                    run_command(
                        cmd=[
                            # Empty-source cleanup needs deletion only; do not
                            # apply the temporary directory's mode/owner to bin.
                            "sudo", "rsync", "-r", "--delete", "--delete-excluded",
                            f"{empty_dir}/", f"{target_bin}/",
                        ],
                        command_name="rsync local bin artifact cleanup",
                        error_message="rsync local bin artifact cleanup failed",
                    )
                logger.info(
                    "service_bot.local_bin.cleanup.success " + fields
                    + " reason=%s cleanup_method=rsync elapsed_ms=%s",
                    *context, reason, round((time.monotonic() - started) * 1000),
                )
            logger.info(
                "service_bot.local_bin.skip " + fields + " reason=%s elapsed_ms=%s",
                *context, reason, round((time.monotonic() - started) * 1000),
            )
            return
        if not resolved_bin.is_dir():
            raise NotADirectoryError("draft .local/bin is not a directory")
        exclude_paths = (
            *extra_root_rules(build_ignore_paths, ".local/bin"),
            *_RUNTIME_MANAGED_LOCAL_BIN_NAMES,
        )
        with tempfile.TemporaryDirectory(prefix="service-bot-cli-stage-") as stage:
            stage_bin = Path(stage) / "bin"
            _stage_cli_bin(resolved_bin, stage_bin, source_home, exclude_paths)
            target_bin.mkdir(parents=True, exist_ok=True)
            run_command(
                cmd=[
                    "sudo", "rsync", "-avL", "--delete", "--delete-excluded",
                    *(f"--exclude=/{path}" for path in exclude_paths),
                    f"{stage_bin}/", f"{target_bin}/",
                ],
                command_name="rsync local bin artifact",
                error_message="rsync local bin artifact failed",
            )
    except Exception as exc:
        logger.error(
            "service_bot.local_bin.failure " + fields + " error_type=%s error=%s elapsed_ms=%s",
            *context, type(exc).__name__, str(exc),
            round((time.monotonic() - started) * 1000),
        )
        raise
    logger.info(
        "service_bot.local_bin.success " + fields + " elapsed_ms=%s",
        *context, round((time.monotonic() - started) * 1000),
    )


def restore_local_bin_artifact(
    *,
    artifact_dir: Path,
    target_home: Path,
    bot_id: str,
    version: int,
    run_command: Callable[..., Any],
    timeout_seconds: float,
) -> None:
    """Merge the allowlisted bin snapshot into the draft home without deletion."""
    artifact_home = artifact_dir.parent
    source_bin = artifact_home / ".local/bin"
    target_bin = target_home / ".local/bin"
    started = time.monotonic()
    fields = "bot_id=%s version=%s source=%s target=%s"
    context = (bot_id, version, source_bin, target_bin)
    logger.info("service_bot.local_bin.restore.request " + fields, *context)
    try:
        _validate_home_dirs(artifact_home)
        if not source_bin.exists() and not source_bin.is_symlink():
            logger.info(
                "service_bot.local_bin.restore.skip " + fields
                + " reason=source_missing elapsed_ms=%s",
                *context, round((time.monotonic() - started) * 1000),
            )
            return
        if source_bin.is_symlink() or not source_bin.is_dir():
            raise ValueError("home artifact bin must be a directory, not a symlink")
        _validate_home_dirs(target_home)
        if target_bin.is_symlink():
            raise ValueError("draft home bin is a symlink")
        target_bin.mkdir(parents=True, exist_ok=True)
        run_command(
            cmd=[
                "sudo", "rsync", "-avL", "--chown=1000:1000",
                *(f"--exclude=/{path}" for path in _RUNTIME_MANAGED_LOCAL_BIN_NAMES),
                f"{source_bin}/", f"{target_bin}/",
            ],
            command_name="rsync draft restore local bin",
            error_message="restore draft local bin failed",
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        logger.error(
            "service_bot.local_bin.restore.failure " + fields
            + " error_type=%s error=%s elapsed_ms=%s",
            *context, type(exc).__name__, str(exc),
            round((time.monotonic() - started) * 1000),
        )
        raise
    logger.info(
        "service_bot.local_bin.restore.success " + fields + " elapsed_ms=%s",
        *context, round((time.monotonic() - started) * 1000),
    )
