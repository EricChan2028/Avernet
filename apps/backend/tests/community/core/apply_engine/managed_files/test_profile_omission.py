"""A protected profile never replays an old managed copy when this Apply omits it."""
from __future__ import annotations

import pytest

import agentclaw.community.core.services.identity as identity_module
from agentclaw.community.core.apply_engine.managed_files.ports import PlatformIdentity
from agentclaw.community.core.apply_engine.managed_files.reader import ManagedFilesComposeReader
from agentclaw.community.core.config_compose.models import ComposeOccasion, ComposeRequest
from .test_store_ports import _store, _identity, _run, _apply, _ctx


class _RuntimeFiles:
    def __init__(self):
        self.files = {"identity/IDENTITY.md": b"old profile"}

    async def write_file(self, path, content):
        self.files[path] = content


@pytest.mark.parametrize("identity", [None, [], [{"type": "AGENTS.md", "content": "new rules"}]])
def test_profile_edited_through_identity_service_is_not_replayed_when_omitted(identity, monkeypatch):
    store, _ = _store()
    materialiser = _identity(PlatformIdentity(store))
    _run(_apply(materialiser, _ctx(), [{"type": "IDENTITY.md", "content": "old profile"}]))
    runtime = _RuntimeFiles()
    service = identity_module.IdentityService.__new__(identity_module.IdentityService)
    service._bot_repo = object()
    service._identity_device_fs = lambda **_: runtime
    monkeypatch.setattr(identity_module, "resolve_runtime_engine_for_bot", lambda *_, **__: "teclaw")
    _run(service.update_bot_file("staff", "u_owner", "b_1", "IDENTITY.md", "user edited new", "u_owner", "teclaw"))
    if identity is not None:
        _run(_apply(materialiser, _ctx(), identity))
    reader = ManagedFilesComposeReader(store=store, declared_state_provider=lambda: None)
    request = ComposeRequest(entity_type="staff", entity_id="u_owner", bot_id="b_1", user_id="u_owner", engine_type="teclaw", occasion=ComposeOccasion.MANIFEST_APPLY)
    assert all(ref.name != "IDENTITY.md" for ref in reader.identity_files(request))
    assert runtime.files["identity/IDENTITY.md"] == b"user edited new"


@pytest.mark.parametrize("profile", ["", "explicit profile"])
def test_only_current_apply_selection_can_emit_a_managed_profile(profile):
    from dataclasses import replace
    store, objects = _store()
    materialiser = _identity(PlatformIdentity(store))
    _run(_apply(materialiser, _ctx(), [{"type": "IDENTITY.md", "content": profile}]))
    reader = ManagedFilesComposeReader(store=store, declared_state_provider=lambda: None)
    request = ComposeRequest(entity_type="staff", entity_id="u_owner", bot_id="b_1", user_id="u_owner", engine_type="teclaw", occasion=ComposeOccasion.MANIFEST_APPLY)
    # The current stored document is irrelevant; selecting another category
    # cannot replay this stored profile, even on subsequent redeliveries.
    for selected in (None, frozenset(), frozenset({"AGENTS.md"})):
        assert reader.identity_files(replace(request, applied_identity_files=selected)) == []
    refs = reader.identity_files(replace(request, applied_identity_files=frozenset({"IDENTITY.md"})))
    assert len(refs) == 1 and refs[0].name == "IDENTITY.md"
    assert objects.objects[f"teclaw/dev/bolt_data/{refs[0].path}"] == profile.encode()
    # First provisioning still receives the newly prepared files without an
    # Apply selection; normal runtime ownership remains with the engine.
    provision = replace(request, occasion=ComposeOccasion.PROVISION)
    assert reader.identity_files(provision) == refs
    runtime = replace(request, occasion=ComposeOccasion.RUNTIME)
    assert reader.platform_owns(runtime) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("only_resources", [False, True])
async def test_bound_compiled_input_and_only_selection_control_profile_redelivery(only_resources):
    from dataclasses import replace
    from datetime import datetime, timezone
    from agentclaw.community.core.apply_engine.capabilities import Category
    from agentclaw.community.core.apply_engine.delivery import TeclawPlatformDelivery
    from agentclaw.community.core.apply_engine.materialisers.identity import IdentityMaterialiser
    from agentclaw.community.core.apply_engine.materialisers.resources import ResourcesMaterialiser
    from agentclaw.community.core.apply_engine.managed_files.ports import PlatformResource
    from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
    from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
    from agentclaw.community.core.bot_config_manifest.binding import bound_manifest_state, manifest_digest
    from tests.community.core.bot_config_manifest.compiler.test_compile import _compiler
    from tests.community.core.apply_engine._fakes import resource_rows_for

    store, objects = _store()
    profile = PlatformIdentity(store)
    await profile.update_bot_file("staff", "u_owner", "b_1", "IDENTITY.md", "compiled A", "u_owner")
    document = 'schema_version: 1\nmanifest:\n  identity:\n    - type: IDENTITY.md\n      content: compiled A\n  resources: []\n'
    compiler, content = _compiler(document)
    ctx = _ctx()
    compiled = await compiler.compile(ctx, bound_manifest_state(manifest_digest(document)), only={Category.RESOURCES} if only_resources else None)
    # A different input B is now stored. Delivery must use the successful
    # entries from compiled A, and must not re-read B to decide profile scope.
    compiler._manifests.get = lambda **_: (_ for _ in ()).throw(AssertionError("unexpected second input read"))
    delivered = []
    reader = ManagedFilesComposeReader(store=store, declared_state_provider=lambda: None)
    request = ComposeRequest(entity_type="staff", entity_id="u_owner", bot_id="b_1", user_id="u_owner", engine_type="teclaw", occasion=ComposeOccasion.MANIFEST_APPLY)
    async def redeliver(delivery_ctx):
        delivered.extend(reader.identity_files(replace(request, applied_identity_files=delivery_ctx.applied_identity_files)))
    strategy = TeclawPlatformDelivery(ports=lambda: None, redeliver=redeliver)
    orchestrator = ApplyOrchestrator({
        Category.IDENTITY: IdentityMaterialiser(profile, content),
        Category.RESOURCES: ResourcesMaterialiser(PlatformResource(store), content, resource_rows_for()[0]),
    }, steps=strategy.steps_for)
    report = await orchestrator.apply(ctx, compiled.state, apply_id="review_apply", trigger="explicit", started_at=datetime.now(timezone.utc))
    assert report.status is ApplyStatus.SUCCEEDED
    assert await strategy.finish(ctx, report) is None
    assert [ref.name for ref in delivered] == ([] if only_resources else ["IDENTITY.md"])
    for ref in delivered:
        assert objects.objects[f"teclaw/dev/bolt_data/{ref.path}"] == b"compiled A"
