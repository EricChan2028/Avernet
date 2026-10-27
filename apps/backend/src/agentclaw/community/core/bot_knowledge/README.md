# Bot knowledge

External platforms own grants. The platform catalog uses the existing business
Bot ID and target user identifier; no new UUID contract. Service Bots edit only
in draft and have separate working (`enabled`) and production (`online_enabled`)
selection. Draft and verification share the working selection. Personal Bots
keep their immediately effective current selection. Knowledge content is not
version-frozen and no independent knowledge publication workflow is introduced.

## Context Boundary

```yaml
purpose: Persist working and production knowledge selection independently of platform grants.
provides:
  - BotKnowledgeService
  - BotKnowledgeRepository
  - BotKnowledgeAgentsMdSync
consumes:
  - DatabasePlugin
  - KnowledgeCatalogPlugin
  - KnowledgeCatalog
internal_dependencies:
  - agentclaw.community.kernel
  - agentclaw.community.plugin_api
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_knowledge
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.knowledge_management
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.services
  - agentclaw.community.core.spaces
  - agentclaw.community.log
  - agentclaw.community.utils
```

Only native Owner/explicit Editor may edit. Enabling consults the platform with
the business Bot ID, existing owner/user identifier and current space; disabling does not require a platform call. Revision CAS and
native editor/draft checks run in the same transaction as the current selection.
Re-read on stale revision; no request history is replayed. Deployments must apply
the reviewed current-state schema before enabling integration.

Space-mismatched selections are not inherited. A new-space write uses revision
zero (the absent view); enabling rechecks that space's external catalog. Reusing
an old row keeps its monotonically increasing revision. Only current-space rows
consume the enabled limit. Disabling an absent relation returns disabled/revision
zero without creating a row. Enabled reads/counts and cursor pages have matching
indexes; current service-Bot draft checks retain their transactional recheck.


## Native editability and tenant lineage

Personal Space Owners and explicit Editors do not need Space-member rows.
Team Space edits still require ACTIVE owner and actor membership as well as
ownership or an explicit editor grant. Space existence, editor standing and
current service-Bot draft state are rechecked inside the write transaction.

`ac_bot_publish` has no tenant column. For legacy `default` Bots, publication
candidates are restricted to source Bot rows in the current tenant/environment
before selecting the latest version. A retained historical source PK remains
valid even if it differs from the current Bot PK; it is never silently rebound.
A matching publication whose source was hard-deleted cannot be attributed to a
tenant and fails closed (`BOT_DRAFT_REQUIRED`), including when ignoring it would
otherwise reveal an older draft. No new publication ownership is inferred from
business Bot/owner IDs alone, and no publication schema or DDL is changed.

## AGENTS.md projection

`PUT /bots/{bot_id}/knowledge-bases/{kb_id}/enablement` remains the only write;
after a successful enablement change the router schedules
`BotKnowledgeAgentsMdSync` (see `agents_md_sync.py`) as a background task. The
binding table stays the single source of truth: the sync rereads the current
selection, resolves display names through the `KnowledgeCatalog.details` port
(batches of 20; a failed batch degrades to ids only), and rewrites one fenced
`KNOWLEDGE_TOOLS` section inside the Bot's draft `AGENTS.md` through
`IdentityService`'s provider-blind identity-file I/O.

The fence markers are the only anchor for locating an existing section and
must never be renamed after release — renaming would orphan every section
already written into existing bots' `AGENTS.md` files and the next sync would
append a second, stale copy. The sync is best-effort: it never fails the HTTP
response, never mutates the stored selection, and skips the update when a
broken fence (start marker without its end marker) suggests hand-editing
damage rather than risk eating user-written content. An empty selection removes
the section; an unchanged projection is not written back.

## Live local acceptance

The unified Singlebox manifest registers all production Core files, both HTTP
routes and both declared `KnowledgeCatalogPlugin` methods. `list_authorized`
is exercised; `list_in_space` currently has no production caller, remains in the
denominator and is not manufactured into coverage through test-only business
calls. The initial Plugin API threshold is the measured 1/2 (50%), not a claim
that both methods were called. The three live HTTP/DB stories cover
selection/CAS/pagination, Personal editor access, Team revocation, draft lifecycle
and tenant-isolated legacy publication selection.

The runner sets `SINGLEBOX_ACCEPTANCE_KNOWLEDGE_FIXTURE_FILE` to the reviewed JSON
catalog. Only the `SINGLEBOX` composition profile reads it; exact Bot/owner/env/
space keys select grants through the existing local Plugin API. Missing catalog
configuration still fails closed; production and unit-test profiles ignore the
fixture variable. This is an external-catalog double, not real remote-platform
integration evidence. Full shared-stack acceptance and artifact verification
must be reported separately from an isolated Backend run.

## Reviewed schema and deferred indexes (2026-10-26)

The approved OceanBase MySQL-mode table definition is
`apps/backend/sql/2026_10_22_bot_knowledge.sql`. It adds database-defaulted
`gmt_create TIMESTAMP`, uses `TIMESTAMP` for `gmt_modified`, retains signed
`BIGINT` IDs/revision and the original business columns/index keys, and supplies
Chinese comments plus OB `GLOBAL` secondary indexes. The portable ORM mirrors
these columns, timestamp defaults and BIGINT revision; it does not emit OB-only
`GLOBAL` syntax. The deployment script supplies database-side `ON UPDATE
CURRENT_TIMESTAMP`; the ORM also sets modification time for local SQLite writes.
Neither timestamp is added to the HTTP response contract.

The two existing-table publication indexes are **deferred and not executed**.
They are recorded separately in
`apps/backend/sql/2026_10_22_bot_knowledge_publish_indexes.sql`, not in the table
creation script. Inspect actual target indexes and query plans and obtain
separate deployment approval before applying them. The ORM still declares these
indexes for fresh local schemas; that is not evidence they exist in an already
running database. Deferring them does not change draft-check correctness, but
production query/lock cost without them remains unverified. No automatic
existing-database migration, production DDL, or deployment is performed here.

## Draft and production selection (2026-10-26)

`GET /api/bot-knowledge/{bot_id}?selection_stage=draft|online` explicitly selects
the view; the default remains draft for compatibility. `items[].enabled` is the
selected value. A service online view reports `runtime_selection=online`; personal
Bots report current in both views. `list_enabled_current` remains a compatibility
working-selection API; new consumers use `list_enabled(..., selection_stage=...)`.
Production state is never writable through the settings API.

`ac_bot_knowledge_binding` retains its identity and draft revision CAS. Production
advancement does not increment that editor revision. Publication owns freezing
the bounded enabled KB-ID set into its existing ext, and atomically replacing
production selection when actual online deployment succeeds. Failed deployment
or rollback admission is not production success. Snapshots cannot cross source
Bot, tenant, environment or space. Reclaiming a binding after a space move clears
its old production flag. Platform grants and native user ACL remain live gates,
not snapshot fields. Actual MCP consumption wiring is outside this change.

Fresh schemas use the updated create script. Existing databases must apply the
review-only `apps/backend/sql/2026_10_26_bot_knowledge_online_selection.sql` before
deploying the new code. It initializes production to false, not from draft:
historical service publications without a trustworthy snapshot produce UNKNOWN
knowledge state (no draft fallback); native publication/rollback may still finish.
Republishing with a trustworthy snapshot restores an explicit selection.
No migration is executed at startup or against any running environment here.

### Publication integration opt-in

`user_config.bot_knowledge_publication.enabled` is false by default. It controls
only publication snapshot/promotion/restore effects, not knowledge management
APIs. Disabled publication never queries the binding table; enabled integration
requires the additive `online_enabled` column and fails closed on DB errors.
Do not toggle off in the middle of active publication or restore operations.

### Short local completion transaction (approved 2026-10-27)

The implementation adds no fields/indexes to `ac_bot_publish`, no publication
side-table, no worker, and no queue integration. `knowledge_selection_snapshot`
and the small `knowledge_selection_result` use the existing publication `ext`.
The result distinguishes a valid empty selection from UNKNOWN history.

Capture is bounded to 100 enabled IDs and contains Bot PK, Owner, environment,
tenant, space, publication ID and a fresh generation token. A durable callback
may restore the snapshot tenant context, but all Bot/Binding tenant guards and
source identity checks still apply. Missing or invalid historical snapshots and
removed bindings produce UNKNOWN, never a later draft selection or an implicit
inheritance of previous production flags. Flags are changed only under a
trustworthy current source/Owner scope; unresolved scopes never revoke a new
Owner's selection. The online reader denies the latest observed UNKNOWN result
in the same SQL statement that reads flags, even if source resolution failed.
Results carry the actual native workflow ID observed at successful completion,
are retained across capture/redraft, and are selected by workflow ID rather than
mutable publication status/version. Thus rollback admission cannot activate old
UNKNOWN state. A normal service read adds one scoped scalar subquery, not
per-binding queries.
Personal and draft reads do not query publication state.

The ONLINE_PUB → SUCCESS status/ext CAS and binding flag replacement use ONE
real transaction/Session. SQL/connection/commit errors propagate and roll back
both, unlike knowledge business states. The existing progress-poll task retries
local completion after observing the same external workflow; it does not invoke
another deploy. No knowledge-specific queue job or table is needed. Native
ledger/attempt/release gates remain unchanged. Current locking reads of observed
completion IDs fence late outcomes even when the target device UUID changed;
there is no new scan or lock over native operation history.

Rollback admission only performs the native two-record flip, with no knowledge
history validation. Selection changes on observed target deployment success.
Draft restore retains native operation identity, latest-attempt, source, draft
and deadline checks. Unknown history preserves working selection and records
UNKNOWN in the restore marker while completing the native ledger. Completed
restore replay cannot overwrite subsequent edits.

Changed content-addressed artifacts are staged before added row locks, unchanged
objects are reused, and PUT failure precedes any local transition. An exact CAS
that races after staging may leave an unreferenced immutable object under the
existing per-record cleanup prefix. Build/deploy/grant/OSS work is never wrapped
in a knowledge transaction. Disabled integration retains native session and
upload behavior and accesses no knowledge table. See
`specs/2026-10-27-knowledge-short-transaction/` for current acceptance evidence;
earlier stage verification counts are historical, not this refactor's result.

#### Production enablement gate

The feature remains disabled by default. The owner/source/env-scoped completion
subqueries inspect that Bot's publication history and may sort observed workflow
IDs; their CPU/scan cost can grow with that history even though statement count
and selected bindings are bounded. Before opting in on a deployed database,
inspect its actual existing indexes, history distribution and exact query plans,
and measure representative completion/read latency and lock waits. Fresh SQLite
indexes and passing unit tests are not this evidence. This refactor does not
apply the separately deferred publication index DDL or authorize enablement.

#### Native identity and completion proof compatibility

Native `ac_bot_publish.owner_id` is the immutable resource `entity_id`; it is not
rewritten on current-Owner handoff. Knowledge snapshots independently capture
`source_entity_id` and current `owner_id`. Completion results record the locked
current Owner. Reads first choose the Bot's latest observed workflow result,
then check Owner compatibility; filtering history by current Owner first would
resurrect an older result after A → B → A handoff. Native resource identity,
status, queue and deployment behavior are unchanged.

The real success observer forwards its `baas_publish_id` through a backward-
compatible optional `observed_workflow_id` CAS argument. This is required for
retry/restart completion: the ledger-preferred workflow actually observed may
be different from the old `ext.publish.online` or a missing/stale best-effort
restart marker. The argument is not a new DB field and does not rewrite native
release/restart handles. Only legacy direct repository callers may omit it when
the release slot already identifies the observed workflow.

The publication opt-in alone does not disable the history predicate for online
API reads. Measure online-consumer read cadence and cost separately before its
rollout; the local query-plan evidence records the remaining history-linear
work explicitly rather than claiming constant-time production access.
