#!/usr/bin/env python3
"""Resolve the immutable BBS Bot identity from trusted runtime inputs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass


class BotIdentityError(ValueError):
    """A deterministic Bot identity resolution failure."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class ResolvedBotIdentity:
    status: str
    bot_id: str
    node_id: str | None
    source: str


def parse_bcn_bot_uuid(raw_value: str) -> tuple[str, str]:
    """Parse BCN_BOT_UUID as bot_id:node_id, splitting on the first colon."""
    if not raw_value or ":" not in raw_value:
        raise BotIdentityError(
            "BOT_IDENTITY_FORMAT_INVALID",
            "BCN_BOT_UUID must use the bot_id:node_id format",
        )

    bot_id, node_id = raw_value.split(":", 1)
    if not bot_id or not node_id:
        raise BotIdentityError(
            "BOT_IDENTITY_FORMAT_INVALID",
            "BCN_BOT_UUID bot_id and node_id must both be non-empty",
        )
    return bot_id, node_id


def resolve_bot_identity(
    explicit_bot_id: str | None,
    bcn_bot_uuid: str | None,
) -> ResolvedBotIdentity:
    """Resolve and cross-check explicit bot_id and BCN_BOT_UUID."""
    explicit = explicit_bot_id if explicit_bot_id else None
    env_bot_id: str | None = None
    node_id: str | None = None

    if bcn_bot_uuid:
        env_bot_id, node_id = parse_bcn_bot_uuid(bcn_bot_uuid)

    if explicit and env_bot_id and explicit != env_bot_id:
        raise BotIdentityError(
            "BOT_IDENTITY_CONFLICT",
            "explicit bot_id does not match BCN_BOT_UUID bot_id",
        )

    bot_id = explicit or env_bot_id
    if not bot_id:
        raise BotIdentityError(
            "BOT_IDENTITY_MISSING",
            "neither explicit bot_id nor BCN_BOT_UUID is available",
        )

    source = "explicit+BCN_BOT_UUID" if explicit and env_bot_id else (
        "explicit" if explicit else "BCN_BOT_UUID"
    )
    return ResolvedBotIdentity(
        status="ok",
        bot_id=bot_id,
        node_id=node_id,
        source=source,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--explicit-bot-id", default=None)
    args = parser.parse_args()

    try:
        result = resolve_bot_identity(
            explicit_bot_id=args.explicit_bot_id,
            bcn_bot_uuid=os.environ.get("BCN_BOT_UUID"),
        )
    except BotIdentityError as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": error.error_code,
                    "message": str(error),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 2

    print(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
