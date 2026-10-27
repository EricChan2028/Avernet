#!/usr/bin/env python3
"""Requeue one FAILED knowledge projection from an authorized deployment shell.

Uses the deployment's existing profile/configuration/DB privileges. This is not
an HTTP API, grants no permissions, performs no DDL and starts no lifecycles.
Run from apps/backend with PYTHONPATH=src. Production execution requires the
normal, separately approved operations access and change authorization.
"""

from __future__ import annotations

import argparse
import json


def retry(args, repo, *, enabled: bool, current_env: str) -> int:
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    if not enabled or args.env != current_env:
        raise ValueError("Integration must be enabled and --env must match deployment")
    if args.publish_id <= 0 or not args.tenant.strip():
        raise ValueError("A positive publication ID and explicit tenant are required")
    with avernet_tenant_scope(args.tenant):
        requeued = repo.retry_knowledge_projection(args.publish_id)
    print(json.dumps({"publish_id": args.publish_id, "requeued": requeued}))
    return 0 if requeued else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-id", type=int, required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument(
        "--confirm-retry",
        action="store_true",
        required=True,
        help="Explicitly authorize requeue of this one FAILED intent",
    )
    args = parser.parse_args(argv)
    if args.publish_id <= 0 or not args.tenant.strip():
        parser.error("Positive publication ID and nonempty tenant required")

    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.di.config_bootstrap import register_config_provider
    from agentclaw.community.core.repository.protocols.publishing import (
        BotPublishRepositoryProtocol,
    )
    from agentclaw.community.kernel.knowledge import KnowledgePublicationPolicy
    from agentclaw.community.utils.env_utils import get_current_env

    if args.env != get_current_env():
        parser.error("--env does not match deployment environment")
    profile = DeployProfile.detect()
    register_config_provider(profile)
    injector = build_injector(profile=profile)
    return retry(
        args,
        injector.get(BotPublishRepositoryProtocol),
        enabled=injector.get(KnowledgePublicationPolicy).enabled,
        current_env=get_current_env(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
