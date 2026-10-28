"""Root-container validation must not lose native publish lifecycle silently."""

import pytest

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.modules import service_bot_module
from agentclaw.community.kernel.knowledge import KnowledgePublicationPolicy


@pytest.mark.parametrize("block", [None, [], "false", {"enabled": "false"}, {"enabled": 0}])
def test_invalid_policy_fails_container_construction(monkeypatch, block):
    real = service_bot_module.read_user_config
    monkeypatch.setattr(
        service_bot_module, "read_user_config",
        lambda: {**real(), "bot_knowledge_publication": block},
    )
    with pytest.raises(ValueError, match="bot_knowledge_publication"):
        build_injector(profile=DeployProfile.TEST)


def test_valid_disabled_policy_keeps_native_publish_handler(monkeypatch):
    from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
        PublishTaskLifecycle,
    )
    from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants

    real = service_bot_module.read_user_config
    monkeypatch.setattr(
        service_bot_module, "read_user_config",
        lambda: {**real(), "bot_knowledge_publication": {"enabled": False}},
    )
    injector = build_injector(profile=DeployProfile.TEST)
    assert injector.get(KnowledgePublicationPolicy).enabled is False
    assert any(isinstance(item, PublishTaskLifecycle)
               for item in discover_lifecycle_participants(injector))
