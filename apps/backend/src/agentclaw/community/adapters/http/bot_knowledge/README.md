# Stage-aware knowledge selection HTTP

## Context Boundary

```yaml
purpose: Adapt authenticated requests for stage-aware application knowledge selection.
provides:
  - router
consumes:
  - BotKnowledgeServiceProtocol
internal_dependencies:
  - agentclaw.community.adapters.http.auth
  - agentclaw.community.api
  - agentclaw.community.di
  - agentclaw.community.kernel
  - agentclaw.community.utils
```

### Change impact
No grant writes or client-controlled publication/version inputs. GET accepts
`selection_stage=draft|online` (default draft); verification shares draft. PUT
only modifies working selection and rejects production fields. Production
advancement is projected from the successful Bot publication intent, independently
of native advancement. GET includes `projection_state` (`legacy`, `pending`,
`applied`, `unknown`, `failed`, `superseded`). An opted-in service online view
reports disabled items while projection is not applied; personal/current views
retain their existing selection. No HTTP retry/write-publication API is added.
