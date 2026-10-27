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
business Bot/owner IDs alone. The optional projection journal described below
does not introduce a new publication ownership identity.

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
the bounded enabled KB-ID set into its existing ext and authoritative journal.
Actual online success atomically records an intent, not binding effects. The
independent projector atomically replaces production selection and acknowledges
that intent. Failed deployment
or rollback admission is not production success. Snapshots cannot cross source
Bot, tenant, environment or space. Reclaiming a binding after a space move clears
its old production flag. Platform grants and native user ACL remain live gates,
not snapshot fields. Actual MCP consumption wiring is outside this change.

Fresh schemas use the updated create script. Existing databases must apply the
review-only `apps/backend/sql/2026_10_26_bot_knowledge_online_selection.sql` before
deploying the new code. It initializes production to false, not from draft:
historical service publications without a trustworthy snapshot must republish.
No migration is executed at startup or against any running environment here.

### Publication integration opt-in

`user_config.bot_knowledge_publication.enabled` is false by default. It controls
only publication snapshot/promotion/restore effects, not knowledge management
APIs. Disabled publication never queries the binding table; enabled integration
requires `online_enabled` and the reviewed, additive publication journal schema
`apps/backend/sql/2026_10_27_bot_knowledge_projection.sql` **before opt-in**.
The journal columns are excluded from the native ORM mapper so disabled native
SELECT/INSERT/UPDATE does not require this migration. No startup migration runs.
Do not toggle off until publication/restore operations and pending projections
are drained. Retain additive columns when rolling back the feature.

### Non-blocking projection contract (2026-10-27)

Knowledge capture failure is explicit UNKNOWN; native build admission continues
using its original status CAS. Missing/invalid historical snapshots never borrow
the current draft. Successful online deployment and native draft-device restore
persist immutable intents alongside their native completion; neither accesses
binding rows. Native ledger/CAS/DB failures still propagate normally.

A lifecycle worker discovers only indexed due rows (50 per batch, 5 seconds
between batches). It runs DB-only effects and acknowledgement in one transaction
under Bot → publication locks. Failures retry after 30 DB-clock seconds, up to
10 attempts, then retain FAILED with no further automatic work. After repairing
the cause, the tenant/environment-scoped repository method
`retry_knowledge_projection(publish_id)` requeues the same intent; it never
recaptures. UNKNOWN requires a new trustworthy publication, not retry from draft.
Malformed journals are durably UNKNOWN rather than silently acknowledged.

Ordering uses the existing globally monotonic BaaS deployment ID, not version or
publication ID, so rollback to an older version is still a newer deployment.
Missing deployment IDs fail closed. Delayed restore effects compare the draft
edit epoch and latest native restore attempt; subsequent edits win.

Online `list_enabled` is empty while the latest projection is pending, failed or
unknown. `list_bindings` adds `projection_state` and reports disabled flags in
those states; absence of a journal is `legacy`, not evidence of new capture.
Readers must not interpret prior flags as the current production selection.
Platform grants remain live gates. Personal Bot behavior is unchanged.

Local SQLite/autocommit/fault-injection tests are not MySQL/OceanBase, multi-pod,
remote CI, Singlebox or production acceptance. Review target schema/index plans
and obtain separate migration/deployment approval before enabling.


### Claim and operator recovery

Before taking a Bot lock, each worker atomically claims a due row by comparing
the journals and moving `knowledge_due` to DB-now + 30 seconds. Only the winner
runs the projection. The apply/failed-result write checks the same lease; a
process crash is recovered by ordinary due discovery after expiry. No new lease
column/table or in-memory ownership is required. MySQL latest-deployment and
restore fences use locking/current reads, not a pre-lock RR snapshot.

The narrowly scoped recovery entrypoint is
`apps/backend/scripts/retry_knowledge_projection.py`. From an **authorized
deployment shell**, with its normal profile/config/DB privileges, run from
`apps/backend` (only after separate operations approval):

```sh
PYTHONPATH=src python scripts/retry_knowledge_projection.py \
  --env <matching-deployment-env> --tenant <explicit-tenant> \
  --publish-id <failed-publication-id> --confirm-retry
```

No credentials are accepted/printed; this does not grant DB permissions, run
DDL, start lifecycles or call deployment APIs. The policy must be enabled and
`--env` must match the configured deployment. Repository guards enforce tenant
and environment. Exit 0 means requeued, 2 means no matching FAILED intent was
requeued, and validation/persistence errors fail the command. It reuses the
immutable snapshot; UNKNOWN cannot be reconstructed this way. Production use of
this command has not been executed or verified by this local repair.


### Native tenant lineage and failed admission token

`knowledge_tenant` persists native routing independently of optional snapshots.
Opted-in inserts record the authenticated tenant and successful capture rechecks
it from the guarded source Bot. The review-only migration backfills historical
routing by the existing source Bot PK and environment; it does **not** backfill
any selection or snapshot. Apply this reviewed metadata backfill before opt-in;
orphan sources cannot be attributed and remain untrusted. Background completion
uses this native routing column, never the default task tenant or missing legacy
snapshot. No runtime tenant-guard bypass is introduced.

Capture failure still rotates a small generation-only ext token in the native
admission transaction, alongside the UNKNOWN journal capture. Thus a previous
build worker cannot adopt a later BUILDING state even when recapture failed.
The marker contains no enabled IDs and cannot grant access.

If there was no preceding ext token and even the minimal token would overflow a
valid native TEXT, admission leaves that native payload unchanged and records
UNKNOWN in the journal only. No optional token may introduce a native capacity
failure. Existing admitted generation tokens are rotated, not retained.
