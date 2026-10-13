# `agentclaw.community.core.bot_public`

Bot publication / discoverability domain — public bot listing, friend/relationship state, publish approvals.

## Context Boundary

```yaml
purpose: "Bot publication / discoverability domain — public bot listing, friend/relationship state, publish approvals."
provides:
  - "BotPublicService"
  - "BotDiscoverService"
  - "BcsFuseRuntimeDispatcher"
  - "Bot-public SQLAlchemy models"
consumes:
  - "BotManagement repo + service"
  - "BotCatalogMetadata port (BCS search page or exact recommendation candidates, joined by (bot_id, entity_id))"
  - "AntProcess plugins (auth_relationship, bot_publish_approval, antprocess)"
  - "PassportPlugin"
  - "SkillCenter factories"
internal_dependencies:
  - agentclaw.community.core.bot_public.catalog_metadata
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.operator_context
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.plugin_api.approval_workflow
  - agentclaw.community.plugin_api.auth_relationship
  - agentclaw.community.plugin_api.bot_publish_approval
  - agentclaw.community.plugin_api.device_sync_dispatcher
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
```

### Change impact

Owns visible-to-other-users state; bugs here can leak unpublished bots or block legitimate publication. The approval callback path crosses with core/antprocess.

### BCSFuse publication synchronization

- Legacy `POST /api/bots/{bot_id}/public` syncs user publication through
  `PUT /v1/workers/{worker_id}/{online|offline}`. Community defaults to the
  bare bot ID; internal Backend overlays must set
  `user_config.bcsfuse.worker_id_with_owner: true` for `bot_id:owner_id`.
- `POST /openapi/v1/collaboration/bots/{bot_uuid}/public` uses the exact BCS
  UUID, independently of that legacy ID configuration. For `public_scope=user`,
  Backend syncs runtime state only after the BCS attribute PATCH succeeds:
  private becomes offline, public/protected become online. Approval submission,
  rejection, cancellation, and skipped/failed BCS writes do not change runtime.
- For `public_scope=agent`, the BCS Provider attributes PATCH dispatches the
  existing visibility sync after persisting `visibility`. BCS sends
  `POST /v1/workers/{bot_uuid}/sync` with `availability` set to the persisted
  visibility; it does not overwrite user-publication runtime state. This needs
  BCS's `bcsfuse.enabled` configuration and deployment of the updated BCS runtime.
- Backend schedules lifecycle HTTP calls after publication persistence succeeds.
  Approval callbacks and direct publication return their existing results without
  waiting for BCSFuse. The application-owned dispatcher permits four concurrent
  calls across Workers and serializes calls per downstream Worker ID, across tenant contexts. While a
  call is queued or running, only the latest pending state and its request context
  are retained; intermediate pending notifications are coalesced.
- Admission is capped at 1,024 tracked Worker identities, with one pending state
  per identity. Existing Workers may replace their pending state at capacity;
  new identities are rejected with a warning. Both online and offline retain their
  existing HTTP path, method, identity mapping, and 15-second timeout.
- The dispatcher is a discovered Lifecycle participant. Shutdown stops admission,
  discards pending states, and cancels queued executor tasks without waiting for
  the queue to drain. At most four in-flight HTTP calls finish under their existing
  timeout. Notifications remain best-effort and are not durable across restarts.
- BCSFuse failure remains best-effort and is logged. User-publication logs use
  `[_sync_bcsfuse_runtime_state]` with the exact `worker_id`; Agent-publication
  logs live in BCS (`Worker synced to bcsfuse` / `Worker sync failed, retrying`).
  Successful publication is not proof of downstream synchronization; use these
  logs and the BCSFuse worker record when verifying a rollout.
