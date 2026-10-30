"""A Teclaw artifact includes the platform's full current identity state."""
from dataclasses import replace

import pytest

from agentclaw.community.core.apply_engine.managed_files.ports import PlatformIdentity
from agentclaw.community.core.apply_engine.managed_files.reader import ManagedFilesComposeReader
from agentclaw.community.core.config_compose.models import ComposeOccasion, ComposeRequest
from .test_store_ports import _store, _identity, _run, _apply, _ctx


@pytest.mark.parametrize("identity", [None, [], [{"type": "AGENTS.md", "content": "new rules"}]])
def test_apply_snapshot_includes_profile_retained_in_platform_state(identity):
    store, objects = _store()
    materialiser = _identity(PlatformIdentity(store))
    _run(_apply(materialiser, _ctx(), [{"type": "IDENTITY.md", "content": "platform profile"}]))
    if identity is not None:
        _run(_apply(materialiser, _ctx(), identity))
    reader = ManagedFilesComposeReader(store=store, declared_state_provider=lambda: None)
    request = ComposeRequest(entity_type="staff", entity_id="u_owner", bot_id="b_1", user_id="u_owner", engine_type="teclaw", occasion=ComposeOccasion.MANIFEST_APPLY)
    refs = {ref.name: ref for ref in reader.identity_files(request)}
    assert "IDENTITY.md" in refs
    assert objects.objects[f"teclaw/dev/bolt_data/{refs['IDENTITY.md'].path}"] == b"platform profile"
    if identity:
        assert "AGENTS.md" in refs


@pytest.mark.parametrize("profile", ["", "explicit profile"])
def test_explicit_profile_is_present_in_apply_and_provision_snapshots(profile):
    store, objects = _store()
    _run(_apply(_identity(PlatformIdentity(store)), _ctx(), [{"type": "IDENTITY.md", "content": profile}]))
    reader = ManagedFilesComposeReader(store=store, declared_state_provider=lambda: None)
    request = ComposeRequest(entity_type="staff", entity_id="u_owner", bot_id="b_1", user_id="u_owner", engine_type="teclaw", occasion=ComposeOccasion.MANIFEST_APPLY)
    refs = reader.identity_files(request)
    assert len(refs) == 1 and refs[0].name == "IDENTITY.md"
    assert objects.objects[f"teclaw/dev/bolt_data/{refs[0].path}"] == profile.encode()
    assert reader.identity_files(replace(request, occasion=ComposeOccasion.PROVISION)) == refs
    assert reader.platform_owns(replace(request, occasion=ComposeOccasion.RUNTIME)) is False
