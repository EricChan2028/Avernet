# Retained pilot: existing execution observation audit

Date: 2026-10-11. Task 10.2 increment, **not live Story acceptance**.

## Evidence and limits

- Runner source at start: `08bdfbfc0439f9a0ad91c00dbed2370b6dc82191`,
  `codex/e2e-multi-env-spec`, clean dedicated worktree.
- Product semantics below were read from the immutable public source object
  `ee9f4f8af4dbe4b768469df9043215093a74da58`. It matches the build reported in the
  earlier 2026-10-10 audit, **not a freshly verified deployment today**. The source
  mirror checkout was clean, detached at `990a8c59fe61a4a2bc2ff6aba90d959350eb413c`;
  analysis used Git objects, not that checkout or the current development branch.
- Live metadata was read through the operator-configured shared-database observer:
  database/table discovery and eight candidate table descriptions. The newly
  implemented runner probe was then exercised against the same source: eight
  metadata calls, six required projections present, zero business-row queries.
- No target API writes, resource deletion, Bot trigger, credential refresh,
  business-row read or log-content download occurred. Private raw DDL, datasource
  identifiers and probe output remain in ignored local evidence storage.
- Column presence does not prove active delivery policy, deployment identity,
  current execution state, log ingestion or authorization for new row queries.
  The probe explicitly reports `execution_status=UNCONFIRMED` and no readiness.

## Available state surfaces

| Surface | Minimal correlation/state fields | What it can establish | What it cannot establish alone |
| --- | --- | --- | --- |
| Group Session | `env, group_id, session_id, status, completed_at, callback_status` | Exact persisted parent binding and business Session state | All chat/delivery work ended; callbacks finished |
| Message | `env, group_id, session_id, message_id, session_seq, sender_id, message_type, status, run_id` | Canonical message identity and scoped sequence | Execution completed or recipient inventory is complete |
| Message delivery | `env, group_id, session_id, source_message_id, delivery_id, target_bot_id, kind, status, state_version, attempt_no, request_id, run_id, downstream_run_id, bound_to_delivery_id, may_have_been_sent, available_at_ms, terminal_at_ms` | Per-recipient delivery state, attempt and carrier/downstream correlation | No later relay, all producers closed, or direct/non-managed work absent |
| Chat run | `env, run_id, bot_uuid, session_key, state, version, delivery_id, source_message_id, completion_policy, delivery_ack_at_ms, completed_at_ms` | Exact known run state and completion-policy interpretation | ACK means model completion; one run means whole Session |
| Downstream run | `run_id, bot_id, status, completed_at, gmt_modified` | Exact BaaS result state for a proven downstream ID | Environment isolation: this table has **no `env` column** |
| Downstream queue | `env, run_id, bot_id, session_id, status, last_heartbeat, gmt_modified` | Pending/running/done state for exact correlated work | Callback delivery success or absence of descendants |

These are candidate projections, not an enabled SQL allowlist. Existing resource
observation receipts do not authorize these new rows. No content, metadata JSON,
original requests, transport context, token, key prefix or credentials are needed
for the projections. Never guess that a BCS logical run ID equals a BaaS run ID.
A fast Final may arrive before a downstream ACK/alias; missing alias is an evidence
gap, not proof of no execution or a reason to retry.

## State interpretation from source

| Observation | Interpretation |
| --- | --- |
| Managed Send `completed` with exact scope/run binding | This known logical Send committed a successful terminal result; recheck descendants separately |
| Delivery `queued/dispatching/running`, Chat run `pending/submitted/running` | Not naturally completed; ACK/submission is not a terminal result |
| Send `failed/cancelled/rejected_capacity` | Not natural success; distinguish functional failure from execution termination and inspect provenance |
| `unknown/cancel_unknown`, or `expired` after `may_have_been_sent` | Cannot conclude downstream execution stopped |
| Inject `pending_context/bound` | Residual context or active carrier linkage; not a successful run |
| Inject `consumed/discarded_context` | Context disposition, not successful Bot execution; consumed may follow carrier failure/cancellation/uncertain expiry |
| Session `completed` | Business completion only; can coexist with an active Manager result or originate from timeout |
| Incoming Final event log | Event received; it precedes terminal persistence and may include sensitive content in the raw log |
| Managed delivery committed log plus matching DB state/version | Stronger evidence for that transition, not complete Session coverage |
| BaaS `COMPLETED` plus exact queue `DONE` | Known result/worker disposition; callback exceptions can be swallowed before DONE, so verify BCS receipt separately |
| BaaS `TIME_OUT/ABORTED/FAILED` | Stored result terminal, not by itself proof that the engine and all callbacks stopped |
| No rows/new logs, WS close or HTTP 404 | Insufficient terminal evidence |

## Source anchors

Paths are relative to the fixed public object above, not the current checkout.

- `apps/bcs/crates/services/bcs-message-flow/src/message_delivery.rs:110-116,167-202`:
  Accepted versus Completed/Failed/Aborted; terminal Send consumes or releases
  bound context based on `may_have_been_sent`.
- `apps/bcs/crates/services/bcs-message-flow/src/managed_delivery.rs:360-415,429-478`:
  terminal timestamps, Inject settlement, CAS updates, awaited repository commit,
  then transition instrumentation/log. A terminal timestamp alone does not mean
  successful completion.
- `apps/bcs/crates/services/bcs-message-flow/src/queued_admission.rs:281-418`:
  routed reply constructs new admission targets and commits them with the
  predecessor's terminal transition. Predecessor completed can coexist with a
  newly queued successor.
- `apps/bcs/crates/services/bcs-message-flow/src/bot_event.rs:118-121,393-402` and
  `bot_event/protocol.rs:497-525`: incoming event logging precedes terminal
  processing; raw log includes content, so use a whitelisted projection.
- `apps/bcs/crates/services/bcs-message-flow/src/task_flow.rs:303-356` and
  `apps/bcs/crates/services/bcs-session-store/src/mysql_operations/operations_1.rs:178-210`:
  business Session completion does not join/check all deliveries or Chat runs.
- `apps/bcs/crates/bootstrap/bcs/src/timeout_scanner.rs:85-109`: timeout can
  complete a Session with an error and then start its callback.
- `apps/baas/src/secbaas/community/core/repository/bot_run/_record.py:12-30` and
  `_orm_repository.py` (`update_result`, `update_timeout`, `update_aborted`): stored
  result terminal vocabulary and updates; these are not engine-stop barriers.
- `apps/baas/src/secbaas/community/core/repository/bot_run_queue/_record.py`:
  PENDING/RUNNING/DONE vocabulary; queue rows are transient and TTL eligible.
- `apps/baas/src/secbaas/community/core/service/bot_run/_worker.py` (`_run_one`,
  `_post_run`, `_mark_queue_done`, `_requeue_pending`): execute, callback, then
  queue DONE; callback errors are logged/caught, session-busy requeues instead.

## Next implementation input / remaining live checks

1. Preserve actual request, Group and every initial/explicit Session ID. Identify
   the initial per-target deliveries and discover routed successors within exact
   Session scope and finite pagination/query budgets; truncation stays unknown.
2. Confirm the deployed revision and **effective** managed/direct delivery path.
   The presence of a delivery table does not prove this request uses it. Missing
   rows in managed mode cannot certify a direct/legacy route as inactive.
3. Bind read-only row/log access with source freshness and exact ownership. For
   the no-env downstream result table, independently establish downstream-ID,
   Bot and parent/environment provenance; otherwise block that query.
4. Keep `known task completed` separate from `scope confirmed ended`. Required
   producer, Inject, retry or descendant evidence gaps stay visible; never create
   synthetic terminal effects to satisfy the existing local execution ledger.
5. Implement retained slice admission/execution and its separate report/reuse
   policy. Safe supervised functional execution may retain unconfirmed results;
   it need not wait for a new product stop API. Overall PASS and another round
   still require sufficient scoped completion evidence and retention quota checks.

Task 10.2 remains open for current deployment/log bindings and actual exact-run
state observations. Metadata/source audit is usable implementation input, not a
new blanket product blocker and not permission to skip the runner hold.

## Implemented delivery read increment

The retained gateway path now supports the explicitly authorized
`bcs-session-deliveries-v1` read described in `retained-remote.md`. It uses the
existing state columns above rather than inventing a stop API. The implemented
query is current-Run/current-Case, held-lease and exact parent-chain guarded,
content-free and limited to two bounded Session snapshots. It records known Send
completion and residual/unknown work, but does not yet establish complete producer,
non-managed or downstream scope coverage. No actual delivery rows were read as
part of this implementation increment; regression uses synthetic external replies.

## Group bootstrap is not necessarily a ChatRun row

A subsequent read of the same fixed public object above confirms that
`bcs_chat_runs` must not be a universal required row for the first Group/Session
slices. Source anchors (object-relative, not checkout line references):

- `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/chat_run.rs:1-10,63-73`
  explicitly scopes `ChatRunRecord` to Direct Chat asynchronous runs.
- `apps/bcs/crates/services/bcs-message-flow/src/a2a_chat/dispatch.rs:102-114`
  (`chat_with_queue_mode`) and `a2a_chat/queued.rs:77-82` (`admit_direct`) create
  ChatRun records. This is the Direct A2A path.
- `apps/bcs/crates/services/bcs-system-message/src/producers/session_context.rs:71-87`
  chooses Send for the driver (or manager) and Inject for others; the dispatcher
  (`dispatcher.rs:200-206`) passes these to system queue admission.
- `apps/bcs/crates/services/bcs-message-flow/src/queued_system.rs:97-109`
  admits `DeliveryFlowKind::System` deliveries.
- `apps/bcs/crates/services/bcs-message-flow/src/queued_group.rs:125-149,237-264`
  branches only DirectA2a to direct preparation; Group/System paths register
  BotRunContext/ActiveBotRunContext, not a ChatRun record.

Thus an observed `run_id` on a bootstrap delivery is not evidence of a corresponding
ChatRun table row. Missing ChatRun rows cannot mean bootstrap failure or no work.
The implemented next increment is bounded delivery-state progression observation
(`bcs-session-deliveries-wait-v1`), not a mandatory query against the wrong run
surface. This remains a source-contract conclusion, not a fresh deployed-version
or live-execution assertion.


## BaaS correlation increment (2026-10-12)

The following mapping was checked against the same immutable public object
`ee9f4f8af4dbe4b768469df9043215093a74da58`, not inferred from today's deployed
configuration. The mirror remains a clean detached checkout at
`990a8c59fe61a4a2bc2ff6aba90d959350eb413c`. Source paths below are relative to
that public object. This adds no live business-row observations or deployment
verification.

| Mapping | Source evidence | Admission consequence |
| --- | --- | --- |
| HTTP Provider `chat.send` uses canonical Run ID | `apps/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs:347-358` assigns `body.id = cmd.run_id`; BaaS BCN handler passes it as `message_id` | `bcn-canonical` is explicit; never substitute per-attempt request ID |
| BaaS persists the input Run ID and logical Bot ID | `apps/baas/src/secbaas/community/core/service/bcn/_bcn_service.py:300-307` uses `to_bot.provider_bot_ref`; `bot_run/_runner.py:323-330,406-426` persists/dispatches the original IDs | Map each BCS actor to its reviewed logical BaaS Bot, not a guessed device ID |
| Queue preserves dispatched Run/Bot/Session | `apps/baas/src/secbaas/community/core/service/bot_run/_queue_task_message_dispatcher.py:423-440` | Exact env/run/Bot queue row plus matching owned Session precedes the no-env result query |
| Explicit BCS Session can survive the BCN route | Provider `lib.rs:1180-1192` prefers `bcs_session_id`; BaaS `_bcn_service.py:567-572` passes metadata Session; `_runner.py:1288-1338` preserves explicit Session | `session_binding=bcs-session-id` needs runtime review; it is not true of every BaaS entrypoint |
| Queue dispatch is configuration-dependent | BaaS `_runner.py:871-926` selects queue/non-queue dispatcher from defaults and overrides | Require confirmed queue-enabled route; an absent queue is not a functional failure or permission to query unscoped results |

A separately accepted WS `downstream_run_id` can be a valid alias without being
a BaaS Run. The alternative `accepted-alias` mode therefore requires independent
runtime mapping review; the observer never auto-detects/falls back between modes.

The runner now implements `baas-correlated-run-v1`, selecting five queue fields
and three result fields without content/metadata. It follows only Sends from the
same client's validated delivery snapshot and live owned scope, with frozen
reviews, a held pool lease, at most four runs/eight extra reads and the original
shared deadline. The result table's missing env column is handled by a required
fresh exact queue binding, not by pretending the table is environment isolated.

DONE + COMPLETED only confirms the observed stored queue/result pair. Callback
failure can be caught by the worker; later producers or unobserved recipients
remain uncovered. Missing rows, timeout, non-queue deployments or incomplete
mapping remain explicit gaps. Complete scope observation, actual callback
evidence, runtime configuration validation and safe pool reuse remain pending.

## Pending context and producer closure (2026-10-12)

This audit uses immutable public object
`ee9f4f8af4dbe4b768469df9043215093a74da58`, not today's unverified deployed
configuration. Paths and line numbers below refer to that object. No live API or
DB call, cancellation, completion mutation or cleanup was performed.

### Pending Inject is retained input, not an autonomous engine task

- `apps/bcs/crates/services/bcs-message-flow/src/message_delivery.rs:30-54,93-103`
  gives Inject its own lifecycle: pending_context → bound → consumed, or release
  back to pending_context, cancellation/expiry. Valid Inject states have
  `may_have_been_sent=false`; they are distinct from Send execution states.
- The scheduler in that file (`:229-238`) only admits Send entries. A pending
  Inject is not itself a scheduled Send or proof that an engine is running.
- `apps/bcs/crates/services/bcs-message-store/src/delivery.rs:583-586` separates
  `LanePending` (unfinished **Send** only) from `LanePendingContextCarrier`
  (unexpired pending Inject). Both are Bot/Session-scoped lookups.
- `apps/bcs/crates/services/bcs-message-flow/src/queued_admission.rs:70-82,228-235`
  keeps future Send admission available as a context carrier even after policy
  is disabled. A retained context can affect later input in that lane; it is not
  automatically harmless or evidence that no future producer exists.
- `apps/bcs/crates/services/bcs-message-flow/src/managed_delivery.rs:370-424`
  consumes/discards bound contexts when their carrier settles, or releases them
  and may bind them to a later queued Send for the same Bot/Session. Disposition
  and carrier association matter; merely finding a terminal context does not
  independently prove its carrier's runtime/callback has stopped.

Therefore, **pending_context alone must not be described as an active Bot**.
Conversely it must not be silently removed from the retained inventory or made
terminal just to make a test green. The runner now includes `snapshot_activity`
with separate active/uncertain/terminal Send and pending/bound/terminal context
counts. `NO_ACTIVE_SEND_IN_SNAPSHOT` is deliberately weaker than quiescence.
Here “active Send” includes queued/runnable work, not only an executing engine.
Empty snapshots remain NO_EVIDENCE; Send expiry remains uncertain because the
lifecycle permits expiry after an uncertain possible-send attempt. Inconsistent
Inject shapes are unclassified rather than described as passive context.

### Session or Group completion is not a universal producer barrier

| Surface | Fixed-source finding | Required interpretation |
| --- | --- | --- |
| Explicit Session scope | `apps/bcs/crates/services/bcs-message-flow/src/group_flow.rs:592-622` checks existence, Group and participants, not status | Session completed alone cannot prove all future explicit-SID messages are rejected |
| Service callback recovery | `apps/bcs/crates/services/bcs-session-store/src/mysql_operations/operations_2.rs:116-145` selects completed service-invocation Sessions with pending eligible callback lease | Completed can be a prerequisite for more callback work, not proof of its absence |
| Group callback | `apps/bcs/crates/services/bcs-message-flow/src/group_flow/callback_abort.rs:3-21,63-81` accepts an existing Group, routes targets and constructs `session_id=None` with new Run IDs | A SID-only observer cannot cover all callback-derived Group work; exclude this producer with evidence or track its accepted work |
| State-machine timeout | `apps/bcs/crates/services/bcs-collaboration-store/src/lib.rs:3451-3470` selects running Run/Node deadlines | A Session row alone does not cover state-machine retries; enumerate applicable Runs rather than assume “latest” is all |

For the **normal/chat** retained subset, do not impose unrelated service/state-
machine requirements blindly. First establish the exact Group strategy, Session
kind and actual configured producer/transport paths; record irrelevant producers
as not applicable with evidence. Applicable producers need their own exact Run,
activation, callback and successor correlation. Completion assertions must state
the no-new-external-input assumption and observation cutoff. A quiet interval,
Group/Session completed, empty query or all currently seen Sends completed does
not replace that inventory.

### Next acceptance work, not an automatic product-change prerequisite

1. Observe minimal Session kind/status/activation/callback and Group strategy
   projections in the same reviewed owned scope, without reading lease tokens or
   credential/content blobs. Freeze explicit read permissions and bounded costs.
2. Establish applicable producer coverage from the actual configuration and
   source/version; follow exact callback/Run edges when applicable. Missing
   scope remains a named gap, not a green not-applicable assumption.
3. Only after known work and applicable producers close, classify leftover
   pending contexts as retained lane data under a documented no-reuse/no-input
   policy; enforce retained resource quota before pool reuse.
4. If an existing observation surface cannot prove one of those facts, describe
   that precise missing capability. Do not require a new universal stop API or
   manufacture terminal transitions, nor make retention a permanent lock merely
   because a passive context row exists.

This increment changes diagnostics only. Existing polling settlement, total
qualification and quarantine rules are intentionally not relaxed until the above
producer/retention contract is implemented and validated.

## Producer inventory and terminal ordering audit (2026-10-12)

Source remains immutable public object `ee9f4f8af4dbe4b768469df9043215093a74da58`,
inspected with `git show`, not the mirror's detached checkout HEAD
`990a8c59fe61a4a2bc2ff6aba90d959350eb413c` or a newly verified deployment.
No remote requests, credentials, state changes or pool release were used.

### Positive coverage and its limits

`queued_admission.rs:366-405` builds the original delivery's terminal transition
with its selected reply deliveries. `managed_delivery.rs:452-465` submits them
through one repository commit; `bcs-message-store/src/delivery.rs:490-547` commits
reply messages/new deliveries and original CAS changes in a database transaction.
Thus a committed managed reply is not deliberately split into “parent terminal,
then later insert its selected managed children.” This is useful positive evidence,
not justification to assume all paths are managed.

In `queued_admission.rs:221-236`, the live policy path selects every target for an
already managed original; the static reply-limit path need not. The relay loop
(`bot_event/relay.rs:275-277`) can still send targets not handled by managed
admission. `bot_event.rs:393-414` calls outbound/active-context cleanup and terminal
observers **after** the managed terminal commit. The deferred state-machine
observer (`bootstrap/bcs/src/server.rs:3167-3208`) spawns asynchronous continuation.
A delivery terminal snapshot therefore does not, by itself, prove no callbacks or
unmanaged successors remain. Likewise, completion of the HTTP handler need not
join the spawned continuation.

The existing Human-authorized `GET /admin/message-delivery/policy` handler is at
`adapters/http/bcs-http/src/routes/delivery_policy.rs:66-70`; the old versioned
path is removed. `services/bcs-message-flow/src/delivery_policy.rs:82-88` compares
stored and live policy and fails on mismatch, whereas a database policy row alone
cannot prove the live runtime has loaded it. Gateway exposure, identity binding,
deployment and read authorization remain to be verified; this audit does not add
or call a guessed route, GET fallback, or environment-wide policy mutation.

### Necessary producer facts, rather than blanket exclusions

| Fact | Fixed-source authority | Consequence |
| --- | --- | --- |
| Chat one-shot may exist | `bcs-collaboration-runtime/src/runtime.rs:2683-2793,3009-3040` admits inline one-shot in running normal chat; its `session_activation_count` is NULL | Neither chat Group strategy, absence of a default binding, nor NULL activation excludes a state-machine Run |
| Legacy Task eligibility | `bcs-message-flow/src/task_flow/support.rs:151-166` admits `service_mode=master_slave` or ManagerWorker | Add the Group scalar; present configuration alone cannot erase previously admitted work |
| Legacy Task storage | `bcs-message-flow/src/task_store.rs:50-54` stores tasks in an in-process map | Do not invent a durable `bcs_tasks` table or treat delivery rows as the whole legacy Task ledger |
| Service callback scope | `bcs-callback/src/dispatch.rs:414-415` gates service completion by service-invocation Session kind | Chat kind can exclude that callback for that SID, not Group callbacks or other Sessions |
| Interaction state | `bootstrap/bcs/src/server.rs:3113-3140` wires MemoryInteractionStore; `bcs-interaction/src/terminal_observer.rs:26-42` invalidates terminal-run interactions | No reviewed SQL snapshot establishes interaction absence; invalidation errors are logged, not proof of completion |

The new `retained-producer-inventory-v1` recheck projection adds only
`bcs_groups.service_mode` and a bounded exact `env/group_id/session_id` inventory
from `bcs_state_machine_runs`. The initial DDL (`migrations/mysql/001_init_schema.sql:626-653`)
provides `idx_sm_runs_session(env,session_id)`; lineage migration
`017_state_machine_rerun_lineage.sql:3-12` adds root/rerun/activation fields.
These are source index/schema facts, not a live EXPLAIN or migration-health claim.
The query reads all record statuses rather than hiding soft-deleted/history rows,
and does not filter NULL activation, take only latest, read definitions/content,
or follow lineage into other scopes. At most 20 rows are accepted; the 21st marks
incomplete inventory instead of truncating it into a false absence assertion.

A complete zero-row result is only NO_RECORDED_RUNS_IN_SNAPSHOT for the precise
SID and observation source. Nonzero rows require review; even completed Runs do
not certify child runtime completion. Source replication delay, concurrent new
input, configuration history, cross-scope calls, legacy Task state and interaction
state remain independent requirements. No-new-input/unchanged-fixture claims need
actual environment-owner evidence, not generated approvals or prompt assurances.
The runner still makes no whole-scope completion or automatic pool-release claim.

## Normal-chat producer applicability: narrow the remaining evidence (2026-10-12)

This refines the preceding inventory audit; it does not change admission or
release any retained pool. Evidence is still the immutable public source object
`ee9f4f8af4dbe4b768469df9043215093a74da58`, not a newly verified deployment.
The main reviewer independently checked the following paths after the read-only
subagent review. All paths below are relative to `apps/bcs/crates/` at that object.

The real retained recheck observed normal/chat Group, NULL service mode, chat
Session with activation 1 and NULL callback status, no recorded state-machine
Runs in the exact Session inventory, and three completed Sends plus consumed and
expired Injects. These facts narrow applicability; they do not alone prove the
entire delivery chain, configuration history or absence of outside input.

| Potential producer | Source condition and operation | Implication for this slice |
| --- | --- | --- |
| State-machine terminal continuation | `services/bcs-collaboration-runtime/src/runtime.rs:4095-4123` first resolves the exact delivery correlation. No correlation returns `consumed=false`; otherwise an existing Run/Node, running state, attempt, request and Bot must match. `bootstrap/bcs/src/server.rs:3167-3208` spawns this handler; it does not create an uncorrelated Run. | Do not assume every chat Final creates another state machine. An exact fresh zero inventory is useful, but excluding this path also depends on proving the observed delivery chain belongs to this Session, with no cross-scope correlation or new one-shot input. |
| Interaction terminal invalidation | `services/bcs-interaction/src/terminal_observer.rs:26-42` calls `invalidate_run`; `management.rs:514-532` delegates to the store; `memory_store.rs:207-229` only invalidates active records and clears local in-flight state. | Invalidation failure is not, by itself, an autonomous new Bot producer. Do not require a new interaction SQL table solely because the store is in memory. It is not proof that an already accepted external resolution has stopped. |
| Interaction resolution | `services/bcs-interaction/src/management.rs:187-237` handles provider notification by recording resolution and publishing a frontend event. The separate `resolve` entry at 240-367 requires an explicit authenticated command, authorization and a claim before calling the Provider. | Exclude autonomous continuation from notification/invalidation, not explicit Human/Bot/integration resolution or previously accepted in-flight resolution. |
| Admin invocation terminal callback | `bootstrap/bcs/src/server.rs:2332-2342` wires this observer. `adapters/http/bcs-http/src/admin_invocation_terminal.rs:121-132` and `state.rs:381-396` require an already registered exact Run and matching Bot before dispatching its configured callback. | A bootstrap Final is not automatically an admin invocation. The chain's provenance and exclusion of separately accepted admin invocations remain required; no blanket callback absence follows from Session kind alone. |
| Service completion callback | `services/bcs-callback/src/dispatch.rs:408-415` returns unless the Session is `ServiceInvocation`. | For a verified unchanged chat Session, this particular callback is not applicable. Do not wait for its NULL callback state to become a made-up terminal status. |
| Group callback | Production call-site search for `handle_group_callback` leads from the explicit HTTP handler (`adapters/http/bcs-http/src/routes/group_messages.rs:172-197`), through metrics/application forwarding, to `services/bcs-message-flow/src/group_flow/callback_abort.rs:3-81`, which creates fresh work without a Session ID. No automatic bootstrap/Final observer call edge was found at this source object. | This is an explicit input relative to the bootstrap chain, not an automatic completion callback. A Bot tool or external integration can still supply that input. Dedicated Bots and no runner POST alone do not establish its absence or rule out an already accepted request. |

### The next two observations, in priority order

1. **Effective delivery policy and its applicable interval.** Use the existing
   explicitly bound native `policy-check`, not another producer-table observer.
   `services/bcs-message-flow/src/delivery_policy.rs:82-88` compares durable and
   responding-process live policy. Keep its version/fingerprint and the two test
   actors' effective Group/System policy, plus honest evidence of whether that
   policy applies to the original Run interval. A present-day GET is only present
   evidence; it cannot reconstruct historical configuration or all replica state.
2. **Actual transport and Run correlation for the known chain.** Determine the
   reviewed WS/HTTP Provider path and terminal meaning without collecting raw
   transport headers or credential blobs. Query BaaS only if the actual path and
   exact Run mapping establish that it is relevant; plugin registration alone
   does not do so. Do not require a ChatRun row for all bootstrap work.

Policy coverage matters because `services/bcs-message-flow/src/queued_system.rs:30-49`
selects managed bootstrap recipients. For an already managed original, the live
policy branch in `queued_admission.rs:201-235` selects all reply targets, while
static per-Bot limits need not. `bot_event/relay.rs:275-277` skips only targets
handled by managed admission; others can take the direct path. The previously
verified atomic parent-terminal/managed-child commit does not cover that direct
path. **This concrete coverage question takes priority over speculative universal
stop interfaces or waiting for an ordinary chat Session to become completed.**

Even with policy and transport evidence, a qualification decision must bind the
actual source/deployment, chain completeness, observation freshness/cutoff and a
real no-new-input/unchanged-fixture boundary. The boundary includes explicit calls
from Bots/integrations and already accepted requests, not just the human tester.
Unknown historical policy, cross-scope work or in-flight input stays a named gap;
none of the conditional exclusions above are automatically reported as verified
NOT_APPLICABLE by the runner. The current result remains UNCONFIRMED and the pool
QUARANTINED. No product change or new remote request was made for this audit.

Validation for this documentation increment: 67 existing/new terminal-wait,
native-policy, producer-inventory and mapping/recheck tests passed (5.327s), plus
strict OpenSpec and diff checks. These tests protect existing runner behavior;
they do not constitute live verification of the conditional source exclusions.
The preceding implementation increment's full 963-test run is unchanged; no
additional full-stack or remote acceptance run was performed for this audit.

Follow-up acquisition details and real access diagnostics are recorded in
[transport-log-evidence.md](transport-log-evidence.md). In particular, WS ACK
correlation uses the sending attempt request ID, while the HTTP Provider uses
the canonical Run ID. An actual log READ denial is not a zero-result snapshot.


## Managed composition and exact Final follow-up (2026-10-12)

The subsequent [closure decision matrix](retained-closure-matrix.md) supersedes
one over-broad unresolved question above: on the reviewed bootstrap path,
managed delivery and Some(live policy) are installed together. For a known managed
original, all reply targets enter managed admission regardless of historical
policy enable flags, and the direct relay loop skips them. The static-limit
fallback remains a concern for other compositions, not this source path.

The original effect review also contains a same-day 10:36:21 +08 pre-Run health
read, not merely the older October 10 audit. Thirteen fresh bounded Observ calls
then obtained all three exact canonical Final records (chat.event/final, empty
task_id) corresponding to the previously observed completed Sends and WS ACKs.
The matrix preserves the distinction between that evidence, conditional source
applicability, attested isolation and the still-unproven input-cutoff/drain boundary.
There is no new execution/PASS/release authority and no automated Final collector.
