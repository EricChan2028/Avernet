"""Recover a missing package through the existing Local Skill identity."""

from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.skill_center.errors import LocalSkillStorageError
from tests.community.core.skill_center.test_local_skill_upload_service import (
    _DiskFilesystem,
    _DiskStorageFactory,
    _ReplacementRepo,
    _ReplacementRuntime,
    _PackageRuntime,
    _existing_skill,
    _replacement_service,
    _skill_md,
    _zip,
)

CANONICAL = "/private/skills-local/upload-skill"


def _recovery(tmp_path, *, active=True, filesystem=None, runtime=None):
    filesystem = filesystem or _DiskFilesystem(tmp_path)
    skill = _existing_skill(active=active)
    repo = _ReplacementRepo([skill])
    service = _replacement_service(
        filesystem,
        repo,
        runtime or _ReplacementRuntime([True]),
        factory=_DiskStorageFactory(filesystem),
    )
    return service, repo, skill, filesystem


async def _upload(service):
    return await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="collaborator",
        package=_zip(
            {
                "SKILL.md": _skill_md(description="restored"),
                "scripts/run.py": b"print('restored')\n",
            }
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_recovery_keeps_existing_identity_and_desired_state(tmp_path, active):
    service, repo, skill, filesystem = _recovery(tmp_path, active=active)

    result = await _upload(service)

    assert result["operation"] == "updated"
    assert result["skill"]["id"] == skill["id"] == "9"
    assert skill["active"] is active
    assert skill["user_id"] == "owner"
    assert skill["git_path"] == f"local://{CANONICAL}"
    assert skill["description"] == "restored"
    assert repo.created == []
    assert repo.atomic_replacements[0]["old_locator"] == CANONICAL
    assert repo.atomic_replacements[0]["new_locator"] == CANONICAL
    assert await filesystem.read_file(f"{CANONICAL}/SKILL.md") == _skill_md(
        description="restored"
    )
    assert await filesystem.read_file(f"{CANONICAL}/scripts/run.py") == (
        b"print('restored')\n"
    )
    assert not list(tmp_path.rglob("*.rollback-*"))
    assert not list(tmp_path.rglob("*.replacement-*"))


@pytest.mark.asyncio
async def test_recovery_keeps_verified_bytes_when_runtime_is_pending(tmp_path):
    runtime = _ReplacementRuntime([False])
    service, repo, skill, filesystem = _recovery(tmp_path, runtime=runtime)

    result = await _upload(service)

    assert result["runtime_projection"]["status"] == "PENDING"
    assert skill["active"] is True
    assert repo.created == []
    assert await filesystem.read_file(f"{CANONICAL}/SKILL.md") == _skill_md(
        description="restored"
    )


@pytest.mark.asyncio
async def test_missing_alias_restores_canonical_package_without_changing_locator(tmp_path):
    service, repo, skill, filesystem = _recovery(tmp_path)
    historical = "/historical/owner/bot/skills-local/upload-skill"
    skill["git_path"] = f"local://{historical}"
    service._skill_service_factory.aliases[historical] = CANONICAL

    result = await _upload(service)

    assert result["skill"]["git_path"] == f"local://{historical}"
    assert repo.atomic_replacements[0]["old_locator"] == historical
    assert repo.atomic_replacements[0]["new_locator"] == historical
    assert await filesystem.read_file(f"{CANONICAL}/SKILL.md") == _skill_md(
        description="restored"
    )


@pytest.mark.asyncio
async def test_runtime_created_package_still_replaces_existing_metadata(tmp_path):
    service, repo, skill, filesystem = _recovery(tmp_path)
    package_runtime = _PackageRuntime()
    service._package_runtime = package_runtime

    result = await _upload(service)

    assert result["operation"] == "updated"
    assert result["skill"]["id"] == skill["id"]
    assert skill["active"] is True
    assert repo.created == []
    assert len(package_runtime.calls) == 1
    assert repo.atomic_replacements[0]["old_locator"] == CANONICAL
    assert repo.atomic_replacements[0]["new_locator"] == CANONICAL
    assert not await filesystem.exists(CANONICAL)  # Backend did not use legacy I/O.


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["publish", "metadata", "audit", "cleanup"])
async def test_failed_recovery_restores_missing_package_and_original_metadata(
    tmp_path, failure
):
    filesystem = _DiskFilesystem(
        tmp_path,
        fail_writes=[f"{CANONICAL}/scripts/run.py"] if failure == "publish" else [],
    )
    filesystem.write_file = AsyncMock(wraps=filesystem.write_file)
    service, repo, skill, filesystem = _recovery(tmp_path, filesystem=filesystem)
    if failure == "metadata":
        repo.replace_bot_local_skill = Mock(side_effect=OSError("metadata failed"))
    elif failure == "audit":
        service._audit_log_repo.insert = Mock(side_effect=OSError("audit failed"))
    elif failure == "cleanup":
        delete_tree = filesystem.delete_tree
        fail_once = True

        async def fail_staged_cleanup(path):
            nonlocal fail_once
            if ".replacement-" in path and fail_once:
                fail_once = False
                return False
            return await delete_tree(path)

        filesystem.delete_tree = fail_staged_cleanup

    with pytest.raises(LocalSkillStorageError):
        await _upload(service)

    assert not await filesystem.exists(CANONICAL)
    assert skill["id"] == "9"
    assert skill["active"] is True
    assert skill["description"] == "old description"
    assert skill["git_path"] == f"local://{CANONICAL}"
    assert repo.created == []
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]
    assert any(
        call.args[0].startswith(f"{CANONICAL}/")
        for call in filesystem.write_file.await_args_list
    )  # The fault was reached after recovery actually attempted publication.


@pytest.mark.asyncio
async def test_failed_publication_probe_preserves_original_absence(tmp_path):
    filesystem = _DiskFilesystem(tmp_path)
    list_dir = filesystem.list_dir
    probes = 0

    async def fail_publication_probe(path, **kwargs):
        nonlocal probes
        if path == CANONICAL:
            probes += 1
            if probes == 2:
                raise OSError("publication probe failed")
        return await list_dir(path, **kwargs)

    filesystem.list_dir = fail_publication_probe
    service, repo, skill, filesystem = _recovery(tmp_path, filesystem=filesystem)

    with pytest.raises(LocalSkillStorageError):
        await _upload(service)

    assert probes >= 3  # Absence was rechecked before rollback deletion.
    assert not await filesystem.exists(CANONICAL)
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]
    assert skill["description"] == "old description"
    assert repo.atomic_replacements == []


@pytest.mark.asyncio
async def test_unreadable_package_is_not_recovered_or_modified(tmp_path):
    filesystem = _DiskFilesystem(tmp_path)
    await filesystem.write_file(f"{CANONICAL}/SKILL.md", b"original")
    list_dir = filesystem.list_dir

    async def unreadable(path, **kwargs):
        if path == CANONICAL:
            raise OSError("directory read denied")
        return await list_dir(path, **kwargs)

    filesystem.list_dir = unreadable
    service, repo, skill, filesystem = _recovery(tmp_path, filesystem=filesystem)

    with pytest.raises(OSError, match="directory read denied"):
        await _upload(service)

    assert await filesystem.read_file(f"{CANONICAL}/SKILL.md") == b"original"
    assert skill["description"] == "old description"
    assert repo.atomic_replacements == []
    assert repo.created == []
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1
