"""CLI snapshots copy independently of the engine root and retry cleanly."""

import shutil
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildMigrationError,
    BotBuildService,
)
from agentclaw.community.core.service_bot.services import bot_build_service as module
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan
from agentclaw.community.core.service_bot.services.local_bin_artifact import (
    _resolve_cli_source,
    restore_local_bin_artifact,
    sync_local_bin_artifact,
)


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_node_npm_harness_multi_hop_links_map_to_bot_home(migration, monkeypatch, caplog):
    caplog.set_level("INFO")
    source, artifact, commands, migrate = migration
    home = source.parent
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    node_root = home / ".local/node24"
    (node_root / "bin").mkdir(parents=True)
    (home / ".local/share/runtime").mkdir(parents=True)
    (home / "launchers").mkdir()
    npm_dir = node_root / "lib/node_modules/npm/bin"
    npm_dir.mkdir(parents=True)
    harness_dir = node_root / "lib/node_modules/td-harnessctl/bin"
    harness_dir.mkdir(parents=True)
    targets = {
        "node": home / ".local/share/runtime/node-real",
        "npm": home / "launchers/npm-real",
        "td-harnessctl": harness_dir.parent / "real-harness",
    }
    for name, target in targets.items():
        target.write_text(f"#!/bin/sh\nprintf '{name} bot'\n")
        target.chmod(0o755)
    (bin_dir / "node").symlink_to("/home/admin/.local/node24/bin/node")
    (node_root / "bin/node").symlink_to("../../share/runtime/node-real")
    (bin_dir / "npm").symlink_to("/home/admin/.local/node24/bin/npm")
    (node_root / "bin/npm").symlink_to("../lib/node_modules/npm/bin/npm-cli.js")
    (npm_dir / "npm-cli.js").symlink_to("/home/admin/launchers/npm-real")
    (bin_dir / "td-harnessctl").symlink_to(
        "/home/admin/.local/node24/lib/node_modules/td-harnessctl/bin/harness",
    )
    (harness_dir / "harness").symlink_to("../real-harness")
    before = {str(path.relative_to(home)): str(path.readlink())
              for path in home.rglob("*") if path.is_symlink()}
    original_lstat = Path.lstat

    def no_host_lstat(path):
        assert not path.is_relative_to("/home/admin")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", no_host_lstat)
    assert migrate()
    for name, target in targets.items():
        published = artifact.parent / ".local/bin" / name
        assert not published.is_symlink()
        assert published.read_bytes() == target.read_bytes()
        assert subprocess.check_output([str(published)], text=True) == f"{name} bot"
    after = {str(path.relative_to(home)): str(path.readlink())
             for path in home.rglob("*") if path.is_symlink()}
    assert before == after
    assert not (artifact.parent / ".local/node24").exists()
    assert not (artifact.parent / "launchers").exists()
    stage = Path(commands[-1][-2]).parent
    assert not stage.exists()
    assert "service_bot.local_bin.mapping.request" in caplog.text
    assert "service_bot.local_bin.mapping.success" in caplog.text
    assert "raw=/home/admin" in caplog.text and "resolved=" in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
@pytest.mark.parametrize("root_link", [".local", ".local/bin"])
def test_source_bin_root_and_parent_absolute_links_are_mapped(migration, root_link):
    source, artifact, _, migrate = migration
    home = source.parent
    actual = home / "payload/bin"
    actual.mkdir(parents=True)
    (actual / "cli").write_text("mapped parent")
    link = home / root_link
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("/home/admin/payload" if root_link == ".local" else "/home/admin/payload/bin")
    assert migrate()
    assert (artifact.parent / ".local/bin/cli").read_text() == "mapped parent"
    assert link.is_symlink()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_directory_aliases_and_linked_parent_dotdot_preserve_output_paths(migration):
    source, artifact, _, migrate = migration
    home = source.parent
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    actual = home / "tree/deep/tools"
    actual.mkdir(parents=True)
    actual.chmod(0o750)
    (home / "tree/deep/sibling").mkdir()
    (home / "tree/deep/sibling/cli").write_text("resolved parent dotdot")
    (actual / "nested").symlink_to("/home/admin/tree/deep/sibling/cli")
    (actual / "ignored").symlink_to("/home/admin/missing")
    (home / "alias").symlink_to("/home/admin/tree/deep/tools")
    (bin_dir / "first").symlink_to("/home/admin/alias")
    (bin_dir / "second").symlink_to("/home/admin/alias")
    (bin_dir / "dotdot").symlink_to("/home/admin/alias/../sibling/cli")
    assert migrate((".local/bin/first/ignored", ".local/bin/second/ignored"))
    published = artifact.parent / ".local/bin"
    assert (published / "dotdot").read_text() == "resolved parent dotdot"
    for name in ("first", "second"):
        assert (published / name / "nested").read_text() == "resolved parent dotdot"
        assert (published / name).stat().st_mode & 0o777 == 0o750
        assert not (published / name / "ignored").exists()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_excluded_broken_links_are_never_resolved(migration):
    source, artifact, _, migrate = migration
    bin_dir = source.parent / ".local/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "valid").write_text("keep")
    for name in ("cfuse", "cfuse-claude", "cfuse-codex", "ignored"):
        (bin_dir / name).symlink_to("/etc/not-a-cli")
    (bin_dir / "cycle").symlink_to("cycle")
    assert migrate((".local/bin/ignored", ".local/bin/cycle"))
    assert sorted(path.name for path in (artifact.parent / ".local/bin").iterdir()) == ["valid"]


@pytest.mark.parametrize("target", ["/etc/passwd", "/home/administrator/cli", "../../../outside"])
def test_external_or_escaping_links_fail_before_rsync(tmp_path, target, caplog):
    caplog.set_level("INFO")
    home = tmp_path / "home"
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "cli").symlink_to(target)
    run = MagicMock()
    with pytest.raises(ValueError, match="escapes"):
        sync_local_bin_artifact(
            source_home=home, target_dir=tmp_path / "versions/3/openclaw",
            device_id="bot", version="3", build_ignore_paths=(), run_command=run,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.mapping.failure" in caplog.text
    assert "service_bot.local_bin.failure" in caplog.text


@pytest.mark.parametrize("kind", ["symlink-cycle", "directory-cycle", "dangling", "fifo"])
def test_invalid_source_entries_fail_without_copy(tmp_path, kind):
    import os

    home = tmp_path / "home"
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    if kind == "symlink-cycle":
        (bin_dir / "cli").symlink_to("other")
        (bin_dir / "other").symlink_to("cli")
    elif kind == "directory-cycle":
        (bin_dir / "cli").symlink_to(".")
    elif kind == "dangling":
        (bin_dir / "cli").symlink_to("/home/admin/missing")
    else:
        os.mkfifo(bin_dir / "cli")
    run = MagicMock()
    with pytest.raises((ValueError, FileNotFoundError)):
        sync_local_bin_artifact(
            source_home=home, target_dir=tmp_path / "versions/3/openclaw",
            device_id="bot", version="3", build_ignore_paths=(), run_command=run,
        )
    run.assert_not_called()


@pytest.mark.parametrize("hops", [40, 41])
def test_symlink_hop_boundary(tmp_path, hops):
    home = tmp_path / "home"
    home.mkdir()
    (home / "real").write_text("final")
    for index in range(hops):
        (home / f"link{index}").symlink_to(f"link{index + 1}" if index < hops - 1 else "real")
    if hops == 40:
        assert _resolve_cli_source(home / "link0", home) == home / "real"
    else:
        with pytest.raises(ValueError, match="hop limit"):
            _resolve_cli_source(home / "link0", home)


def test_same_container_absolute_link_uses_each_bots_home(tmp_path):
    for bot in ("bot-a", "bot-b"):
        home = tmp_path / bot
        home.mkdir()
        (home / "real").write_text(bot)
        (home / "cli").symlink_to("/home/admin/real")
        assert _resolve_cli_source(home / "cli", home).read_text() == bot


def test_already_mapped_absolute_link_stays_in_selected_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "real").write_text("NAS target")
    (home / "cli").symlink_to(home / "real")
    assert _resolve_cli_source(home / "cli", home) == home / "real"


def test_selected_home_root_link_to_another_bot_is_rejected(tmp_path):
    other = tmp_path / "other-bot"
    (other / ".local/bin").mkdir(parents=True)
    (other / ".local/bin/cli").write_text("other Bot content")
    home = tmp_path / "selected-home"
    home.symlink_to(other, target_is_directory=True)
    run = MagicMock()
    with pytest.raises(ValueError, match="Home root"):
        sync_local_bin_artifact(
            source_home=home, target_dir=tmp_path / "versions/3/openclaw",
            device_id="bot", version="3", build_ignore_paths=(), run_command=run,
        )
    run.assert_not_called()


def test_file_cannot_be_traversed_by_dotdot(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "file").write_text("regular file")
    (home / "cli").symlink_to("/home/admin/file/../file")
    with pytest.raises(NotADirectoryError):
        _resolve_cli_source(home / "cli", home)


@pytest.mark.parametrize("broken", [False, True])
def test_bin_only_staging_is_removed_after_success_or_failure(tmp_path, monkeypatch, broken):
    from agentclaw.community.core.service_bot.services import local_bin_artifact as module

    home = tmp_path / "home"
    (home / ".local/bin").mkdir(parents=True)
    (home / "dependencies").mkdir()
    (home / "dependencies/cli").write_text("entry only")
    (home / ".local/bin/cli").symlink_to(
        "/home/admin/missing" if broken else "/home/admin/dependencies/cli",
    )
    original_temp = module.tempfile.TemporaryDirectory
    stages = []

    def track_temp(**kwargs):
        directory = original_temp(dir=tmp_path, **kwargs)
        stages.append(Path(directory.name))
        return directory

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", track_temp)
    calls = []

    def inspect_stage(**kwargs):
        calls.append(kwargs)
        stage_bin = Path(kwargs["cmd"][-2])
        assert sorted(path.name for path in stage_bin.parent.iterdir()) == ["bin"]
        assert sorted(path.name for path in stage_bin.iterdir()) == ["cli"]
        assert (stage_bin / "cli").is_symlink()
        assert (stage_bin / "cli").readlink() == home / "dependencies/cli"

    arguments = dict(
        source_home=home, target_dir=tmp_path / "versions/3/openclaw",
        device_id="bot", version="3", build_ignore_paths=(), run_command=inspect_stage,
    )
    if broken:
        with pytest.raises(FileNotFoundError):
            sync_local_bin_artifact(**arguments)
        assert calls == []
    else:
        sync_local_bin_artifact(**arguments)
        assert len(calls) == 1
    assert len(stages) == 1 and all(not stage.exists() for stage in stages)


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_absolute_home_link_is_materialized_without_changing_source(migration):
    source, artifact, _, migrate = migration
    home = source.parent
    (home / ".local/bin").mkdir(parents=True)
    (home / "tools").mkdir()
    tool = home / "tools/absolute-cli"
    tool.write_text("#!/bin/sh\nprintf 'mapped bot CLI'\n")
    tool.chmod(0o755)
    link = home / ".local/bin/absolute-cli"
    link.symlink_to("/home/admin/tools/absolute-cli")
    assert migrate()
    published = artifact.parent / ".local/bin/absolute-cli"
    assert published.is_file() and not published.is_symlink()
    assert subprocess.check_output([str(published)], text=True) == "mapped bot CLI"
    assert link.readlink() == Path("/home/admin/tools/absolute-cli")
    assert tool.read_text() == "#!/bin/sh\nprintf 'mapped bot CLI'\n"


@pytest.fixture
def migration(tmp_path):
    source = tmp_path / "home/.openclaw"
    source.mkdir(parents=True)
    (source / "config.json").write_text("{}")
    target = tmp_path / "artifacts/3/openclaw"
    service = BotBuildService.__new__(BotBuildService)
    service._sync_extra_include_files = MagicMock()
    commands = []

    def run_command(**kwargs):
        cmd = kwargs["cmd"]
        commands.append(cmd)
        assert cmd[1] != "rm"
        if cmd[1] == "chmod":
            return
        result = subprocess.run(cmd[1:], capture_output=True, text=True)
        if result.returncode:
            raise BotBuildMigrationError(result.stderr)

    service._run_local_command = run_command

    def migrate(rules=()):
        return service._migrate_bot_instance(
            device_id="device-cli-test",
            source_dir=source,
            target_dir=target,
            version_str="2",
            is_nas=True,
            nas_storage_id=source.parent,
            build_ignore_paths=rules,
        )

    return source, target, commands, migrate


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_migration_copies_only_bin_and_materializes_executable_link(migration, caplog):
    caplog.set_level("INFO")
    source, target, commands, migrate = migration
    local = source.parent / ".local"
    (local / "bin").mkdir(parents=True)
    tools = local / "share/tools"
    tools.mkdir(parents=True)
    tool = tools / "cli"
    tool.write_text("#!/bin/sh\nprintf 'cli works'\n")
    tool.chmod(0o755)
    (local / "bin/cli").symlink_to("../share/tools/cli")
    (local / "credentials").write_text("private-source-content")

    assert migrate()

    cli = target.parent / ".local/bin/cli"
    assert cli.is_file() and not cli.is_symlink()
    assert cli.stat().st_mode & 0o111 == 0o111
    assert subprocess.check_output([str(cli)], text=True) == "cli works"
    assert not (target.parent / ".local/share").exists()
    assert not (target.parent / ".local/credentials").exists()
    assert (target / "config.json").read_text() == "{}"
    assert any("-avL" in cmd for cmd in commands)
    for event in ("request", "success"):
        assert f"service_bot.local_bin.{event}" in caplog.text
    assert "device_id=device-cli-test" in caplog.text
    assert "version=2" in caplog.text
    assert "source=" in caplog.text and "target=" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "private-source-content" not in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
@pytest.mark.parametrize("rule", [".local", ".local/bin"])
def test_migration_directory_ignore_skips_cli_and_clears_previous_artifact(migration, rule):
    source, target, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "cli").write_text("published")
    assert migrate()
    assert (target.parent / ".local/bin/cli").exists()

    assert migrate((rule,))
    assert (target.parent / ".local/bin").is_dir()
    assert list((target.parent / ".local/bin").iterdir()) == []


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_migration_child_ignore_omits_only_selected_cli(migration):
    source, target, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    (source_bin / "private").mkdir(parents=True)
    (source_bin / "private/cli").write_text("skip")
    (source_bin / "cli").write_text("keep")
    (source_bin / "skip-cli").write_text("skip")
    assert migrate((".local/bin/private", ".local/bin/skip-cli"))
    assert (target.parent / ".local/bin/cli").read_text() == "keep"
    assert not (target.parent / ".local/bin/private").exists()
    assert not (target.parent / ".local/bin/skip-cli").exists()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
@pytest.mark.parametrize("name", ["cfuse", "cfuse-claude", "cfuse-codex"])
def test_migration_skips_runtime_managed_cfuse_wrapper(migration, name):
    source, artifact, _, migrate = migration
    source_home = source.parent
    source_bin = source_home / ".local/bin"
    source_bin.mkdir(parents=True)
    tool_dir = source_home / ".local/tools"
    tool_dir.mkdir()
    (tool_dir / "wrapped").write_text("runtime-managed")
    (source_bin / "keep").write_text("#!/bin/sh\nprintf 'keep'\n")
    (source_bin / name).symlink_to("../tools/wrapped")

    assert migrate()
    assert (artifact.parent / ".local/bin/keep").is_file()
    assert not (artifact.parent / f".local/bin/{name}").exists()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_missing_home_bin_never_republishes_stale_engine_snapshot(migration, caplog):
    caplog.set_level("INFO")
    source, target, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "cli").write_text("old")
    assert migrate()
    shutil.rmtree(source.parent / ".local")
    # The engine root might contain a snapshot from an older restore. It is
    # not the draft home's source and must never be reused.
    engine_bin = source / ".local/bin"
    engine_bin.mkdir(parents=True)
    (engine_bin / "cli").write_text("stale")
    assert migrate()
    assert (target.parent / ".local/bin").is_dir()
    assert list((target.parent / ".local/bin").iterdir()) == []
    assert (target / ".local/bin/cli").read_text() == "stale"
    assert "service_bot.local_bin.skip" in caplog.text
    assert "reason=source_missing" in caplog.text


def test_cli_copy_failure_propagates_with_context(tmp_path, caplog):
    caplog.set_level("INFO")
    source = tmp_path / "home"
    (source / ".local/bin").mkdir(parents=True)
    run = MagicMock(side_effect=BotBuildMigrationError("copy failed"))
    with pytest.raises(BotBuildMigrationError, match="copy failed"):
        sync_local_bin_artifact(
            source_home=source,
            target_dir=tmp_path / "artifact",
            device_id="device-cli-test",
            version="2",
            build_ignore_paths=(),
            run_command=run,
        )
    assert "service_bot.local_bin.request" in caplog.text
    assert "service_bot.local_bin.failure" in caplog.text
    assert "error_type=BotBuildMigrationError" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "service_bot.local_bin.success" not in caplog.text


def _restore_service(monkeypatch, artifact, target_home):
    plan = EngineBuildPlan(
        engine_type="openclaw",
        source_root_name=".openclaw",
        migration_subpath="openclaw",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=[],
    )
    service = BotBuildService.__new__(BotBuildService)
    service._resolve_sandbox_provider = MagicMock(
        return_value=MagicMock(get_build_plan=MagicMock(return_value=plan)),
    )
    service._sync_extra_include_files = MagicMock()
    commands = []

    def run_command(**kwargs):
        cmd = kwargs["cmd"]
        commands.append(cmd)
        if cmd[1] == "chmod":
            return
        # The host test is unprivileged; ownership mapping is asserted below
        # while the same rsync argv performs real archive/merge operations.
        result = subprocess.run(
            [arg for arg in cmd[1:] if not arg.startswith("--chown=")],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise BotBuildMigrationError(result.stderr)

    service._run_local_command = run_command
    monkeypatch.setattr(module, "get_bot_dir", lambda *args, **kwargs: artifact.parent.parent)
    monkeypatch.setattr(module, "get_bot_nas_dir", lambda *args, **kwargs: target_home)

    def restore():
        return service.restore_draft(
            bot={"bot_id": "restore-cli", "entity_id": "owner"},
            source_version=3,
            artifact_ext={"migration_path": str(artifact)},
        )

    return commands, restore


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_build_and_restore_keep_engine_and_home_cli_separate(migration, monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    source, artifact, _, migrate = migration
    engine_bin = source / ".local/bin"
    home_bin = source.parent / ".local/bin"
    engine_bin.mkdir(parents=True)
    home_bin.mkdir(parents=True)
    (engine_bin / "cli").write_text("engine-cli")
    (source / ".local/engine-data").write_text("engine state")
    (home_bin / "cli").write_text("#!/bin/sh\nprintf 'home-cli'\n")
    (home_bin / "cli").chmod(0o755)
    assert migrate()
    assert (artifact / ".local/bin/cli").read_text() == "engine-cli"
    assert (artifact / ".local/engine-data").read_text() == "engine state"

    restored_home = tmp_path / "restored-home"
    (restored_home / ".local/bin").mkdir(parents=True)
    (restored_home / ".local/bin/cli").write_text("previous home CLI")
    (restored_home / ".local/bin/target-only").write_text("keep target CLI")
    (restored_home / ".local/share").mkdir()
    (restored_home / ".local/share/state").write_text("keep home state")
    # Only bin can leave the version root, even if other Home data exists.
    (artifact.parent / ".local/share").mkdir()
    (artifact.parent / ".local/share/state").write_text("do not restore")
    commands, restore = _restore_service(monkeypatch, artifact, restored_home)
    assert restore()["restore_type"] == "migration_path"
    assert (restored_home / ".openclaw/.local/bin/cli").read_text() == "engine-cli"
    assert (restored_home / ".openclaw/.local/engine-data").read_text() == "engine state"
    assert subprocess.check_output([str(restored_home / ".local/bin/cli")], text=True) == "home-cli"
    assert (restored_home / ".local/bin/target-only").read_text() == "keep target CLI"
    assert (restored_home / ".local/share/state").read_text() == "keep home state"
    home_cmd = [cmd for cmd in commands if cmd[-1] == f"{restored_home / '.local/bin'}/"][0]
    assert "--chown=1000:1000" in home_cmd
    assert "--delete" not in home_cmd and "--delete-excluded" not in home_cmd
    assert "service_bot.local_bin.restore.request" in caplog.text
    assert "service_bot.local_bin.restore.success" in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_restore_old_artifact_leaves_home_cli_untouched(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifacts/3/openclaw"
    (artifact / ".local/bin").mkdir(parents=True)
    (artifact / ".local/bin/cli").write_text("old engine CLI")
    target_home = tmp_path / "restored-home"
    (target_home / ".local/bin").mkdir(parents=True)
    (target_home / ".local/bin/cli").write_text("home CLI")
    _, restore = _restore_service(monkeypatch, artifact, target_home)
    restore()
    assert (target_home / ".openclaw/.local/bin/cli").read_text() == "old engine CLI"
    assert (target_home / ".local/bin/cli").read_text() == "home CLI"
    assert "service_bot.local_bin.restore.skip" in caplog.text


@pytest.mark.parametrize("parent", [".", ".local"])
def test_restore_rejects_symlink_artifact_parents(tmp_path, parent, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "versions/3/openclaw"
    link = artifact.parent / parent
    link.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    run = MagicMock()
    with pytest.raises(ValueError, match="symlink"):
        restore_local_bin_artifact(
            artifact_dir=artifact, target_home=tmp_path / "home",
            bot_id="restore-cli", version=3, run_command=run, timeout_seconds=30,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.restore.failure" in caplog.text
    assert "error_type=ValueError" in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_restore_preserves_runtime_managed_cfuse_wrappers(tmp_path):
    artifact = tmp_path / "versions/3/openclaw"
    artifact.mkdir(parents=True)
    artifact_bin = artifact.parent / ".local/bin"
    artifact_bin.mkdir(parents=True)
    (artifact_bin / "keep").write_text("published")
    for name in ("cfuse", "cfuse-claude", "cfuse-codex"):
        (artifact_bin / name).write_text("published wrapper")

    target_home = tmp_path / "home"
    target_bin = target_home / ".local/bin"
    target_bin.mkdir(parents=True)
    (target_bin / "keep").write_text("originally local")
    for name in ("cfuse", "cfuse-claude", "cfuse-codex"):
        (target_bin / name).write_text("runtime wrapper")

    def run_command(**kwargs):
        cmd = kwargs["cmd"]
        result = subprocess.run(
            [arg for arg in cmd[1:] if not arg.startswith("--chown=")],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise BotBuildMigrationError(result.stderr)

    restore_local_bin_artifact(
        artifact_dir=artifact,
        target_home=target_home,
        bot_id="restore-cli",
        version=3,
        run_command=run_command,
        timeout_seconds=30,
    )
    assert (target_bin / "keep").read_text() == "published"
    for name in ("cfuse", "cfuse-claude", "cfuse-codex"):
        assert (target_bin / name).read_text() == "runtime wrapper"


def test_restore_copy_failure_propagates_and_logs(tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifact"
    (artifact.parent / ".local/bin").mkdir(parents=True)
    with pytest.raises(BotBuildMigrationError, match="copy failed"):
        restore_local_bin_artifact(
            artifact_dir=artifact, target_home=tmp_path / "home",
            bot_id="restore-cli", version=3,
            run_command=MagicMock(side_effect=BotBuildMigrationError("copy failed")),
            timeout_seconds=30,
        )
    assert "service_bot.local_bin.restore.failure" in caplog.text
    assert "service_bot.local_bin.restore.success" not in caplog.text


@pytest.mark.parametrize("parent", [
    ".", ".local", ".local/bin",
])
def test_build_rejects_symlink_artifact_parents(tmp_path, parent, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "versions/3/openclaw"
    link = artifact.parent / parent
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "outside", target_is_directory=True)
    source_home = tmp_path / "source"
    (source_home / ".local/bin").mkdir(parents=True)
    run = MagicMock()
    with pytest.raises(ValueError, match="symlink"):
        sync_local_bin_artifact(
            source_home=source_home, target_dir=artifact,
            device_id="device-cli-test", version="3", build_ignore_paths=(),
            run_command=run,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.failure" in caplog.text


@pytest.mark.parametrize("parent", [".", ".local"])
def test_restore_rejects_non_directory_artifact_parents(tmp_path, parent):
    artifact = tmp_path / "versions/3/openclaw"
    invalid = artifact.parent / parent
    invalid.parent.mkdir(parents=True)
    invalid.write_text("invalid")
    with pytest.raises(NotADirectoryError):
        restore_local_bin_artifact(
            artifact_dir=artifact, target_home=tmp_path / "home",
            bot_id="restore-cli", version=3, run_command=MagicMock(), timeout_seconds=30,
        )


@pytest.mark.parametrize("invalid", ["bin-file", "bin-link", "target-local-link", "target-bin-link"])
def test_restore_rejects_invalid_bin_or_target_parent(tmp_path, invalid, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifact"
    source_bin = artifact.parent / ".local/bin"
    source_bin.parent.mkdir(parents=True)
    target_home = tmp_path / "home"
    target_home.mkdir()
    if invalid == "bin-file":
        source_bin.write_text("not a directory")
    elif invalid == "bin-link":
        source_bin.symlink_to("missing")
    else:
        source_bin.mkdir()
        link = target_home / (".local" if invalid == "target-local-link" else ".local/bin")
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(tmp_path / "outside", target_is_directory=True)
    run = MagicMock()
    with pytest.raises(ValueError, match="symlink|directory"):
        restore_local_bin_artifact(
            artifact_dir=artifact, target_home=target_home,
            bot_id="restore-cli", version=3, run_command=run, timeout_seconds=30,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.restore.failure" in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_restore_empty_bin_preserves_target_only_cli(monkeypatch, tmp_path):
    artifact = tmp_path / "artifacts/3/openclaw"
    artifact.mkdir(parents=True)
    (artifact.parent / ".local/bin").mkdir(parents=True)
    target_home = tmp_path / "home"
    (target_home / ".local/bin").mkdir(parents=True)
    (target_home / ".local/bin/target-only").write_text("preserved")
    _, restore = _restore_service(monkeypatch, artifact, target_home)
    restore()
    assert (target_home / ".local/bin/target-only").read_text() == "preserved"


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_retry_cleans_only_version_bin_and_keeps_other_version_data(migration):
    source, artifact, commands, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "old-cli").write_text("old")
    assert migrate()
    version_local = artifact.parent / ".local"
    (version_local / "share").mkdir()
    (version_local / "share/state").write_text("preserve other version data")
    (source_bin / "old-cli").unlink()
    (source_bin / "new-cli").write_text("new")
    assert migrate()
    assert not (version_local / "bin/old-cli").exists()
    assert (version_local / "bin/new-cli").read_text() == "new"
    assert (version_local / "share/state").read_text() == "preserve other version data"
    assert all(cmd[1] != "rm" for cmd in commands)
    cli_copies = [cmd for cmd in commands if cmd[-1] == f"{version_local / 'bin'}/"]
    assert len(cli_copies) == 2
    assert all(not Path(cmd[-2]).exists() for cmd in cli_copies)


def test_retry_cleanup_failure_aborts_before_copy(tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "versions/3/openclaw"
    (artifact.parent / ".local/bin").mkdir(parents=True)
    source_home = tmp_path / "home"
    source_home.mkdir()
    run = MagicMock(side_effect=BotBuildMigrationError("cleanup failed"))
    with pytest.raises(BotBuildMigrationError, match="cleanup failed"):
        sync_local_bin_artifact(
            source_home=source_home, target_dir=artifact,
            device_id="device-cli-test", version="3", build_ignore_paths=(),
            run_command=run,
        )
    assert run.call_count == 1
    assert run.call_args.kwargs["command_name"] == "rsync local bin artifact cleanup"
    assert run.call_args.kwargs["cmd"][1] == "rsync"
    assert "service_bot.local_bin.cleanup.request" in caplog.text
    assert "cleanup_method=rsync" in caplog.text
    assert "service_bot.local_bin.failure" in caplog.text
    assert "service_bot.local_bin.success" not in caplog.text
    assert "service_bot.local_bin.skip" not in caplog.text
    assert "service_bot.local_bin.cleanup.success" not in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
@pytest.mark.parametrize("state", ["empty", "missing", "excluded"])
def test_retry_empty_or_absent_payload_clears_only_bin_using_rsync(migration, caplog, state):
    caplog.set_level("INFO")
    source, artifact, commands, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "old-cli").write_text("old")
    (source / ".local").mkdir()
    (source / ".local/state").write_text("engine state")
    assert migrate()
    version_local = artifact.parent / ".local"
    version_bin = version_local / "bin"
    version_bin.chmod(0o750)
    (version_local / "share").mkdir()
    (version_local / "share/state").write_text("keep other data")
    first_command = len(commands)
    if state == "empty":
        (source_bin / "old-cli").unlink()
    elif state == "missing":
        shutil.rmtree(source_bin)
    rules = (".local/bin",) if state == "excluded" else ()
    assert migrate(rules)
    assert version_bin.is_dir() and list(version_bin.iterdir()) == []
    assert (version_local / "share/state").read_text() == "keep other data"
    assert (artifact / ".local/state").read_text() == "engine state"
    assert all(cmd[1] != "rm" for cmd in commands)
    cli_sync = [cmd for cmd in commands[first_command:] if cmd[-1] == f"{version_bin}/"]
    assert len(cli_sync) == 1
    assert "--delete" in cli_sync[0] and "--delete-excluded" in cli_sync[0]
    if state == "empty":
        assert not Path(cli_sync[0][-2]).exists()
        assert "service_bot.local_bin.cleanup.request" not in caplog.text
    else:
        assert not Path(cli_sync[0][-2]).exists()
        assert version_bin.stat().st_mode & 0o777 == 0o750
        assert "service_bot.local_bin.cleanup.request" in caplog.text
        assert "service_bot.local_bin.cleanup.success" in caplog.text
        assert "cleanup_method=rsync" in caplog.text


@pytest.mark.parametrize("excluded", [False, True])
def test_absent_target_without_payload_does_not_create_bin_or_run_cleanup(tmp_path, excluded):
    source_home = tmp_path / "home"
    source_home.mkdir()
    if excluded:
        (source_home / ".local/bin").mkdir(parents=True)
        (source_home / ".local/bin/cli").write_text("ignored")
    artifact = tmp_path / "versions/3/openclaw"
    run = MagicMock()
    sync_local_bin_artifact(
        source_home=source_home, target_dir=artifact,
        device_id="device-cli-test", version="3",
        build_ignore_paths=(".local/bin",) if excluded else (), run_command=run,
    )
    run.assert_not_called()
    assert not (artifact.parent / ".local/bin").exists()


def test_build_rejects_non_directory_version_bin(tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "versions/3/openclaw"
    (artifact.parent / ".local").mkdir(parents=True)
    (artifact.parent / ".local/bin").write_text("not a directory")
    run = MagicMock()
    with pytest.raises(NotADirectoryError, match="bin"):
        sync_local_bin_artifact(
            source_home=tmp_path / "home", target_dir=artifact,
            device_id="device-cli-test", version="3", build_ignore_paths=(),
            run_command=run,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.failure" in caplog.text


@pytest.mark.parametrize("dangling_link", [False, True])
def test_non_directory_cli_source_fails(tmp_path, dangling_link):
    source = tmp_path / "home"
    (source / ".local").mkdir(parents=True)
    if dangling_link:
        (source / ".local/bin").symlink_to("missing-directory")
    else:
        (source / ".local/bin").write_text("not a directory")
    with pytest.raises(FileNotFoundError if dangling_link else NotADirectoryError):
        sync_local_bin_artifact(
            source_home=source,
            target_dir=tmp_path / "artifact",
            device_id="device-cli-test",
            version="2",
            build_ignore_paths=(),
            run_command=MagicMock(),
        )


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_cli_broken_file_link_fails_migration(migration, caplog):
    caplog.set_level("INFO")
    source, _, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "cli").symlink_to("missing-cli-target")
    with pytest.raises(BotBuildMigrationError):
        migrate()
    assert "service_bot.local_bin.failure" in caplog.text
    assert "service_bot.local_bin.success" not in caplog.text
