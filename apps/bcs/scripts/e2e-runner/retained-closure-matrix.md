# Retained normal-chat observation and closure decision matrix

This is the task 10.2 audit result for the first retained Group slice, not a
completion certificate, new effect review or pool-release instruction. The audit
records observed facts, conditional source conclusions and remaining unknowns
separately. Subsequent task 10.3/10.4/10.6 admission must consume those distinctions;
a finished audit cannot turn an unknown cell into a passing execution result.

## Evidence and scope (2026-10-12)

- The original effect review contains an actual pre-Run health read at
  **10:36:21 +08:00 on October 12, 2026**, HTTP 200, reporting enterprise source
  `b4961722767e4778bb7e3d7df579ee1023a62107` and public source
  `ee9f4f8af4dbe4b768469df9043215093a74da58` by their short build identifiers.
  This is newer than the October 10 health audit. Its safe projection did not
  preserve the responding pod identity; it is not per-replica image attestation.
- The immutable original Run owns one Group and its implicit Session. The later
  exact database recheck observed a normal/chat Group, NULL service mode, chat
  Session with activation 1, and zero recorded state-machine Runs for that SID.
  Its complete delivery projection was three completed Sends, one consumed Inject
  and one expired Inject, with no active/uncertain Send or pending/bound context.
- The live `retained-observ-ws` acquisition dynamically discovered PRE instances
  and matched each attempt ACK to its Bot/Group/Session and accepted canonical Run.
  Its successful receipt is independent of prior schema/timeout failure receipts.
- A subsequent **manual, bounded Final collector** made thirteen read-only Observ
  calls: one complete instance discovery, three explicit log-list validations and
  nine exact canonical-Run + bot_event + final queries, each in that delivery's
  terminal minute. It returned exactly three matching Final rows. All were
  `chat.event`, `chat_event_state=final`, `status=responded`, `task_id=""`, with
  exact Bot/Group/Session and canonical Run identities. The first two modes were
  structured and the last free_chat; structured routing is not proof of a
  StateMachine Group. Their event seconds agree with the respective completed
  delivery terminal timestamps after accounting for second-vs-millisecond precision.
- The Final collector checked row source and query echo, decoded the actual
  HTML-escaped JSON, and retained only whitelisted scalars and raw-content hashes.
  Raw prompts/messages were neither printed nor saved. Original sixteen Run files
  were rehashed unchanged. No Cookie, database query, business write, Bot action,
  deletion, second Story or pool release was performed during this acquisition.
  This manual script is not a new runner command or an automatic Final adapter.

## Corrected managed-to-direct coverage argument

All paths in the following proof are relative to `apps/bcs/crates/` at immutable
public object `ee9f4f8af4dbe4b768469df9043215093a74da58`. It is a conditional source
proof for the deployed-build report above, not a claim about arbitrary builders,
all future versions or every independent incoming request.

1. `bootstrap/bcs/src/server.rs:4931-4934` calls
   `message_delivery_wiring::wire_with_leader(...).await?` before exposing the flow;
   failure does not fall back to a legacy implementation.
2. `bootstrap/bcs/src/message_delivery_wiring.rs:65-95` loads the durable policy,
   constructs the managed service with the same live policy, assigns
   `flow.delivery_policy = Some(live.clone())`, then installs managed deliveries.
   Even the nondurable successful return occurs after both assignments.
3. `services/bcs-message-flow/src/queued_admission.rs:116-157` resolves the original
   managed delivery by exact Bot/Group/optional Session and canonical, request or
   downstream alias. Its lookup failure propagates rather than taking direct mode.
4. In that function's reply path (`198-244`), for a managed original the selection
   condition is `(original.is_some() || policy.manages_group(target))`. Since this
   bootstrap guarantees `Some(policy)`, **all routed reply targets are selected
   irrespective of the policy's current enable switches**. The static-limit
   fallback is not reached on this actual composition path.
5. `queued_admission.rs:281-305,366-418` returns handled target IDs even for
   capacity rejection; an already terminal original also returns all decision
   targets. Parent completion and managed reply admissions use the previously
   reviewed transaction. Admission failure propagates. The following direct loop
   (`bot_event/relay.rs:225-235,275-277`) skips those handled IDs.

Therefore a Final matched to an original managed Run on this normal-chat source
path cannot dispatch unregistered direct reply targets. **Historical policy flag
values are not an additional requirement for that specific implication.** The
prior audit's generic static-limit caveat remains valid for a separately assembled
flow or an unverified deployment, not this verified source composition. An
unmanaged original (`original=None`) is outside this conclusion.

The enterprise bootstrap at immutable object
`b4961722767e4778bb7e3d7df579ee1023a62107`,
`apps/bcs-internal/crates/bootstrap/bcs-internal/src/main.rs:41-53`, resolves secrets,
sets its build version and delegates directly to public `run_with_resolved_config`;
it does not replace the message flow. Its build script stamps the actual checkout
commits (`build.rs:6-39`), not necessarily the pinned submodule gitlink. Neither
source fact independently certifies that all replicas used that build at execution.

No dedicated log supplies managed-selected count, residual-direct count and policy
version for every Final. `route_decided` counts routing targets **before admission**;
`bot_deliver_result` records direct-loop outcomes. Do not derive direct absence
from a missing log. Here it follows conditionally from source control flow plus the
known managed identity chain, not from an empty grep or the later policy GET.

## Observable / unknown / capability-needed matrix

| Boundary | Evidence available | Decision and remaining requirement |
| --- | --- | --- |
| Build/source identity | Same-day pre-Run health reports the reviewed public/enterprise objects; actual log schemas and identities agree | **Observed responding-service version**, not authenticated image identity for every handling process. Bind any closure decision to this provenance and its limitation; do not label it a fresh all-replica verification. |
| Known Sends | Complete exact delivery snapshot, three WS ACK tuples and three canonical Final tuples | **Known managed executions reached their reviewed BCS terminal state.** ACK alone was insufficient; Final alone also would not cover queued children. Bot process shutdown and model-quality assertions are not established or required by these rows. |
| Managed reply successors | Actual composition guarantees Some(policy); managed-parent reply transaction and direct-loop exclusion reviewed | **Conditionally covered by the same managed ledger**, without requiring history of policy enable flags. Requires applicability of that source and exact parent identity; not a claim about unmanaged originals. |
| Queue/retry/context inventory | Bounded same-Session DB snapshot includes all statuses and kinds, complete three-Send/two-Inject shape | **Observed settled snapshot**, not permanent absence. Retained passive consumed/expired Injects do not themselves require deletion or more polling. Freshness/cutoff and races with additional input remain explicit. |
| StateMachine / legacy Task / service callback | Zero exact SID inventory; normal/chat/NULL service mode; observed Finals have no task_id | **Conditional exclusions**, not broad zero-work claims. Unchanged configuration and no cross-scope admission must hold. Ordinary chat need not acquire a Session-completed or service-callback terminal flag. |
| External inputs and already accepted work | Real owner attestation states dedicated exclusive test Bots, isolated runtime and no operational channels; runner has made no second Story | **Attested isolation, not independently observed in-flight drain.** It does not by itself exclude Bot-tool/integration input already accepted elsewhere. Scope completion still requires an applicable bounded-input/cutoff contract or explicit evidence for relevant in-flight producers. |
| Shared database and no-env tables | Owner-confirmed shared DB mapping; actual metadata; exact owned parent/env/actor predicates for queried tables | **Observed exact-scope projections only.** No-env downstream tables remain gated on separately verified identity mapping. Do not query BaaS based only on a plugin Bot or fabricate a ChatRun requirement for bootstrap. |
| Log discovery, latency and absence | Fresh complete PRE instance pages, exact target/echo/source checks, successful positive records; timeout preserved separately | **Positive evidence validated.** Current topology does not prove historical coverage; no replication/ingestion SLA was measured. Empty or quiet logs never substitute for negative completeness. |
| Stop/cleanup/reuse | Existing observer reads work; resource ledger and retention/pool quarantine remain intact | **No new stop API is needed merely to observe natural completion of these known Sends.** If exact input closure/drain cannot be established with existing contracts, add a separately reviewed scope-specific admission barrier/drain capability; never infer it from deletion or use a global stop. Automated evidence consumption and quota/baseline-aware reuse remain task 10.4 work. |

## Decision for the next step

The known chain's ACK, Final and managed/direct reply questions now have concrete
answers. Stop repeating those queries or asking for historical policy flags to
prove the managed-parent implication. The actionable remaining closure boundary is
**a finite, explicit input cutoff covering relevant already-accepted work**, together
with source applicability and fresh complete inventory at that cutoff. It is not
"prove nothing can ever happen in a retained Session again".

A closure report must state the cutoff and assumptions, distinguish attested from
observed facts, and verify relevant producers; otherwise execution remains
UNCONFIRMED and the pool remains quarantined. Resource retention itself is not a
failure and must not create permanent lockout once those conditions and quota/
baseline checks are implemented. This audit neither changes the original timeout
nor turns its functional slice into full PRE-01/02/03 or release acceptance.


## Audit acceptance and regression

Task **10.2 is complete as an observation/capability audit**, using this matrix
alongside execution-observation-audit.md, transport-log-evidence.md and the private
source-bound receipts. Its requirement explicitly calls for observable, unknown
and capability-needed outcomes; it does not require every cell to be proven safe.
This acceptance does not complete task 10.4's closure/reuse implementation or task
10.6's live execution qualification. The original Case remains BLOCKED with
execution UNCONFIRMED and a quarantined pool. Overall Spec progress is **30/54**.

A read-only independent review verified the composition argument and the audit
requirement mapping; the main agent independently read the cited immutable source
and performed the live scalar acquisition. Ninety-four transport, Observ wire,
terminal-wait, producer-inventory and policy regressions passed (10.169 seconds).
Case guard, strict OpenSpec and diff checks passed. This increment changes only
documentation and private audit artifacts; the 1009-test full runner pass belongs
to the preceding collector implementation and was not rerun or relabelled here.
The legacy all-module singlebox coverage gate remains unrun. Regression results do
not substitute for either the source review or real Final evidence.

## Existing cutoff capability follow-up

[input-boundary-gap.md](input-boundary-gap.md) records the concrete check of
existing completion/status/abort/queue-drain contracts. No uniform exact-scope
cutoff/drain capability was found in the reviewed composition. The controlled-
input fixture route remains valid if its actual producer configuration supplies
the missing facts; otherwise a separate product change needs review. Resource
completed/closed labels are not an alternative proof and no such mutation was
attempted. The historical exclusive-runtime confirmation is not silently expanded
to certify unenumerated scheduled/integration/background work.
