"""Stable provider coordinates, separate from mutable BCN administration."""

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.bot_management.addressing import stable_resource_owner_id


def provider_address_kwargs(bot: Mapping[str, Any]) -> dict[str, str]:
    """Qualify service resources without changing personal/desktop registration.

    Existing non-service provider references use the Owner work number. Those
    Bots cannot be handed off, so retain that legacy contract, including default.
    Service references keep the entity suffix across Owner changes. Callers must
    supply the already-resolved Bot, never reconstruct it from request fields.
    """
    if bot.get("bot_type") != "service" or bot["bot_id"] == "default":
        return {}
    # A personal-to-service conversion retains the provider binding it already
    # switched. Its original staff-number suffix may differ from its entity.
    ext = bot.get("ext")
    existing = ext.get("bcn_provider_bot_ref") if isinstance(ext, Mapping) else None
    if existing is not None:
        prefix = f"{bot['bot_id']}:"
        if not isinstance(existing, str) or not existing.startswith(prefix) or not existing[len(prefix):].strip():
            raise ValueError("Invalid stored BCN provider reference")
        return {"provider_bot_ref": existing}
    return {"provider_bot_ref": f"{bot['bot_id']}:{stable_resource_owner_id(bot)}"}
