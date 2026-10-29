# Execution safety: implementation evidence and remote admission gap

## Current next-Run checkpoint — 2026-10-21

User approved Group max10→11, other limits unchanged. Explicit history-preserving
amendment support and70 focused tests are complete; historical real report
compatibility passed. Actual quota remains10 pending fresh admission. Observ is
ready, but configured Cookie identity returned an exact ACE login challenge at
15:48:01. Planned second File Run has not been invoked; no resource or quota
change occurred. [Amendment contract and evidence](retained-file.md).

## Latest real PRE checkpoint — 2026-10-21

Retained File Case v4 passed once with19/19 assertions and an independent offline
report. Exact File DB projection and original/authenticated/shared bytes matched;
issued capability without Cookie received ACE denial. Resources remain retained
and execution UNCONFIRMED; not full PRE-03/release acceptance. Group reservation
usage is now10/10, so no additional Run is admitted without a separate quota
decision. [Evidence and resource IDs](retained-file-v4-acceptance-20261021.md).
Earlier expiry/NOT_RUN checkpoints below are historical.

## Latest checkpoint — 2026-10-20 22:04 +08:00

Observ renewal and fresh topology/source checks succeeded. One retained File
invocation passed identity/Bot/DB metadata checks but stopped before preparation:
no-Cookie invalid share capability returned the exact BCS404 envelope rather
than the v3 login-denial expectation. Exact-request logs corroborate Gateway→BCS
forwarding. No resource writes or quota change occurred. This is neither the
previous Observ expiry nor the withdrawn ownership blocker. See the
[evening execution and evidence](retained-file-v3-attempt-20261020-evening.md).
Earlier dated sections remain historical, not current instructions.

This document records the current boundary, not a release qualification. Passing
runner tests does not imply that a deployed service can expose or terminate the
same effects as a controlled local fake.

## What the runner now verifies locally

| Requirement | Implementation | Focused regression evidence |
|---|---|---|
| Exact execution effect journal | `execution_ledger.py`, controlled Group/Session/run/delivery/subscription binding | `test_execution_ledger.py`, `test_local_execution_effects.py` |
| Producer shutdown before observation socket close | `case_scope.py`, shared PRE-02, controlled adapter channels | `test_termination_channels.py` |
| No late success or deadline renewal | Persisted termination-history budget, wall/monotonic checks | `test_termination_deadline.py` |
| No cleanup/release based only on deleted resource rows or PASS text | Shared read-only journal gate in cleanup/reporting/release | `test_execution_safety.py` |
| Lease/credential loss closes ordinary operation admission | Held guard/Run/profile/token rechecks; structured 401/403 invalidation | `test_operation_authority.py`, real owned-runtime denial tests in `test_local_pilot_live.py` |
| Cancellation cannot initiate ordinary target cleanup | Adapter and finally-unwind cancellation fence, owned-socket release only | `test_cancellation_fence.py`, signal contracts in `test_remote_run.py` and owned-runtime cancellation tests |
| Explicit recovery does not fabricate convergence | `recover-local` uses existing terminal proof and original reservation; separate receipts | `test_local_recovery.py`, including fresh-interpreter recovery after real child exit |

These tests cover local framework contracts, synthetic boundary clients, and
selected real owned-local BCS/CLI behavior. Their method count is not the count
of migrated product user stories. Resource-only local tests without an execution
ledger remain `NOT_TRACKED` for execution safety.

## What is still unproven

- The deployed product's complete mapping from a request to every implicit
  context, delivery, queue/retry, run, subscription and downstream effect.
- Human-owner authority to terminate all exact effects, including system-sender
  bootstrap and queued/Inject work, without a broad Bot stop or queue drain.
- Product-side suppression, or a producer/terminal barrier that prevents new
  work from appearing after observation or resource deletion.
- Truly bounded remote I/O/stop/observation and authorized recovery of unknown
  activity. Runner deadline checks are not hard preemption.
- File completion's notification/Inject effects, downstream BaaS persistence,
  and share/storage cleanup under the actual remote deployment contract.
- The two consecutive full remote pilot runs and the unchanged unified
  singlebox coverage gate. Neither can be substituted by local mock success.

## Revised next step: retained observation pilot (2026-10-11)

The owner accepted a serial preproduction pilot that observes existing logs/DB
states, waits for natural completion, and retains resources by default. See
`retained-pilot-plan.md` for scope, slice IDs, evidence semantics,
independent report outcomes, acceptance and implementation order.

A new product suppression or stop API is no longer a universal prerequisite for
this supervised, low-risk subset. First audit existing evidence; propose product
changes only for demonstrated gaps. Unknown activity remains non-success and
quarantines the pool. Retention never means stopped. Real Bot stories cannot use
suppression as a substitute for their assertions; delete assertions remain pending.

**Original remote suite remains held:** PRE-01/02/03 are blocked before bootstrap
writes. The separately reviewed supervised retained gateway attachment is
implemented; it requires its own effect/supervisor receipt and current observer
binding, and is not live-qualified. Shared steps and local/mock retained slices
are implemented with no DELETE, independent functional results, unresolved
execution and a quarantined pool. Terminal observation, retention-aware successful
qualification and reuse are not implemented or live-qualified. Do not bypass
admission by editing a profile or removing a lock.
Keep local cleanup, original pilot denominators and release gates unchanged.


## Existing-state audit increment (2026-10-11)

`execution-observation-audit.md` records fixed-source state semantics and live
metadata availability. `probe db-metadata --scope execution` has been exercised
against the configured observer: all six projections present, eight metadata
calls, zero business queries. It explicitly does not confirm execution, target
version, effective delivery mode or row/log admission. The no-env downstream
result table is exposed rather than treated as environment isolated.

## Scoped delivery observation increment

The separately reviewed retained gateway path can now query exact owned Session
delivery rows under an additional explicit permission, with query-boundary ledger,
parent and held-pool validation. See `retained-remote.md`. Observed known task
completion is exposed in JSON/JUnit/Markdown, not promoted to whole-Run quiescence.
Uncovered producers/downstream work remain visible and block pool reuse. Real
preproduction delivery observation and full retained qualification are not yet
accepted; metadata and synthetic transport success do not replace that evidence.

## Natural-completion waiting increment

An explicitly reviewed `bcs-session-deliveries-wait-v1` permission adds at most
three rounds/six exact delivery queries under the original observation deadline.
It retains last-valid per-Session evidence and bounded progression history on
failure or cancellation. This can observe running-to-completed progression but
never equates polling completion with scope quiescence or pool reuse. Ordinary
Group/System bootstrap does not necessarily create a Direct ChatRun record;
`execution-observation-audit.md` records the fixed-source evidence rather than
making that table a false mandatory prerequisite.


## Correlated downstream observation increment (2026-10-12)

The optional `baas-correlated-run-v1` contract follows an actually observed BCS
Send to a reviewed BaaS Run/Bot mapping. An exact environment-scoped queue row
and owned Session match are prerequisites to reading the result table without
an env column. Missing/ambiguous rows and mappings, stale provenance, expired
reviews and lost leases do not broaden the query. Four attempts/eight additional
reads share the delivery deadline; failure/cancellation retains validated partial
evidence. See `retained-remote.md` for authorization and query details.

The 28 focused contract tests and full 816-test runner regression passed using
the owned local BCS/CLI binaries. These are framework and local runtime results,
not 816 preproduction stories. No real BaaS business rows were read for this
increment. Actual queue-enabled route, logical Bot mapping, Session propagation,
fresh credentials/reviews, callback coverage and retained reuse remain unverified.
DONE/COMPLETED never grants whole-scope PASS or pool release. Original PRE
admission, legacy singlebox/coverage gates and product services are unchanged.


## Public Bot visibility contract correction (2026-10-12)

Live read-only discovery exposed a runner/fixture mismatch: the public-v1
PhysicalBot contract has a required top-level `visibility`, while the retained
preflight incorrectly read `capabilities.visibility`. Confirmed against immutable
public source `ee9f4f8af4dbe4b768469df9043215093a74da58`,
`apps/bcs/api-contracts/v1/domain-models.yaml:133-176` and
`apps/bcs/crates/service-api/bcs-service-api/src/application/v1/bot.rs:68-92`.
The runner and synthetic HTTP fixture now use the actual response field; nested
protected metadata cannot admit a missing/public/invalid top-level value.

The corrected fixture reproduced NOT_RUN before the fix. Afterward 37 focused
retained tests passed. The first 817-test full run failed an unrelated local CLI
subprocess output-budget test when its one-second deadline won; the failure log
was retained, no deadline/assertion was weakened, and both the isolated test and
unchanged full 817-test rerun passed. Static independent review found no remaining
issue. These results do not constitute a real retained Story PASS.


## First live retained Group diagnostic (2026-10-12)

After fresh explicit owner consent and scoped receipts, `RETAIN-GROUP-01`
executed through the configured preproduction gateway with real Cookie identity
and two dedicated Bots. Seven functional/persistence assertions passed: preflight,
owner, participants/roles, read, rename, read-after-update and exact-environment
Group DB binding. One Group and its implicit initial Session were retained; no
DELETE, create retry, explicit Session, WebSocket or second Case was executed.
Private IDs, receipts and reports remain in ignored run storage.

The original ten-second observation budget accepted a first snapshot containing
one running Send and one pending Inject, then timed out on the second MCP query.
The original result remains functional PASS, evidence PASS, overall BLOCKED,
execution UNCONFIRMED and pool QUARANTINED. This is an observation-window/tool
budget outcome, not proof of a product functional failure or execution completion.

One separately recorded operator read of the same exact Session later observed
three completed Sends, no active Send in the returned snapshot, one consumed
Inject and one pending_context Inject. It does not overwrite the original report,
resume the runner or release the pool. Pending context alone is not proof of an
actively executing Bot; its producer/reuse implications still require review.
Successor rows demonstrate that bootstrap coverage cannot stop at the first Send.

The fixture DB connection_mode is marked plugin; matching provider IDs/references
alone does not prove its effective downstream path or BaaS Run correlation. No
BaaS queue/result lookup was authorized by inference or performed in this Run.
Complete callback/producer coverage, independent observation/recheck budget,
retained quota/reuse and same-version second-round acceptance remain pending.
The original PRE cases and full release denominator remain unqualified.


## Independent observation budget (2026-10-12)

`bcs-session-deliveries-wait-v2` now requires a separately reviewed and frozen
observation schedule (1–300 total seconds, 2–6 rounds, 1–30 seconds between
rounds). HTTP/MCP per-request limits remain unchanged. Old snapshot/wait-v1
receipts cannot acquire the larger budget implicitly; an old Run's timeout is
not extended or rewritten. The v2 query boundary requires the shared absolute
deadline, rejects missing/invalid/expired deadlines before transport and refuses
late provenance. Downstream shares the same observation window.

Twenty focused contract tests and the full 837-test owned-runtime runner suite
passed; the old 42 delivery tests also passed. Worst-case composition is tested
at seven preflight metadata calls plus 31 execution calls for two Sessions,
six rounds and four downstream Runs, under the unchanged 32-call phase cap.
JSON/JUnit/Markdown keep the schedule independent of whole-scope qualification.
This increment performs no remote call or resource operation, and does not
release the existing quarantined Run. Independent retained recheck, complete
producer/callback observation and quota-aware safe reuse remain outstanding.


## Read-only retained recheck (2026-10-12)

`retained-recheck` now supports a separately reviewed, one-shot evidence read of
a known-owned quarantined retained Case, with an offline `--dry-run` plan. Fresh
read authority binds original artifact digests and the current observer receipt;
old expiry never becomes implicit renewal. The command uses read-only SQLite,
an existing-only held guard and exclusively created observation evidence, with
no HTTP adapter, Cookie resolution, Story replay, deletion, stop or pool release.
Original result, Run state, reservation and journal remain unchanged. See
`retained-remote.md` for input and result contracts.

Nineteen focused recheck tests and the full 856-test runner suite passed with
owned local BCS/CLI enabled. Tests cover original-byte preservation, replay,
source/binding mismatch, historical expiry with fresh authority, unsafe paths,
guard contention and deletion races, query cancellation, unknown MCP-child
shutdown, SIGTERM during publication and combined cancellation/disk failure.
Three independent-review findings were fixed and statically rechecked closed.
The existing real retained Run also passed offline source/inventory validation;
that command performed no network call and generated no effective authorization.

Live recheck still needs a new current scoped read review and observer binding.
No old authorization was refreshed or real resource state changed in this
increment. Even an OBSERVED recheck remains overall BLOCKED / exit 2 (130 for
cancellation), execution UNCONFIRMED and pool QUARANTINED. Complete producer,
callback and pending-context semantics, retention quotas and safe reuse remain
separate unfinished acceptance requirements.


## Retained context versus active Send (2026-10-12)

Fixed-source audit confirms pending Inject is retained input for a future carrier
Send, not itself an active engine invocation. Session completed also does not
uniformly prevent future producers: explicit-SID scope checks and Group callback
routing have distinct boundaries. See `execution-observation-audit.md` for exact
source anchors and applicability checks required before closing a normal/chat
retained scope. No new universal stop API is presumed necessary.

Raw per-Session observations now expose `snapshot_activity`: active/uncertain/
terminal Send counts and pending/bound/terminal context counts. Empty data means
NO_EVIDENCE; expired Send remains uncertain. NO_ACTIVE_SEND_IN_SNAPSHOT never
means producer/callback closure or pool reuse. Existing settlement, timeout,
original reports and quarantine decisions are unchanged. Nine focused tests and
the full 865-test owned-runtime runner regression passed; independent read-only
review found no actionable issue. No remote request was made in this increment.

## Owned Group/Session facts in recheck (2026-10-12)

An optional, separately reviewed `retained-group-session-facts-v1` recheck
projection now records Group strategy/kind/status and Session kind/status/
activation/callback/completion, without reading content or callback lease tokens.
It requires exact owned IDs, environment and Group owner/driver agreement. Original
retained execution reviews cannot enable this read surface. No facts permission
means no extra schema or row reads.

Facts are read once before delivery polling, using the same deadline and client
budget. They are not an atomic snapshot or terminal producer evidence. Missing,
foreign, ambiguous or late facts cannot be accepted; earlier valid facts survive
later query failure/cancellation. The outer report remains BLOCKED and the pool
quarantined, with producer_coverage explicitly NOT_VERIFIED.

Ten focused tests and the full 875-test owned-runtime runner suite passed. The
first focused failure was an incorrect timeout test fixture: the MCP subprocess
seam must raise ProcessCaptureTimeout, not a generic OS TimeoutError. An independent
review found an outer post-authority deadline gap; the fix and a dedicated
regression reject that late final Session row while retaining earlier Group facts.
The full run interrupted before producing evidence is not credited; the resumed
full regression log contains 875 tests / OK. No live API, DB read, authorization
refresh or resource mutation occurred for this increment.

## Read-only acceptance handoff checkpoint (2026-10-12)

Implementation now includes pool-local retained-resource quota admission,
explicit producer inventory and independent native effective-policy discovery.
The latest complete implementation regression contained 935 tests, all passing.
These additions have not established live preproduction completion or safe pool
reuse; do not keep adding observation surfaces as a substitute for collecting the
already-defined evidence.

The local original diagnostic artifacts were rechecked: resource inventory is
complete, functional assertions remain PASS, overall status remains BLOCKED,
execution UNCONFIRMED and pool QUARANTINED. The original observation approval has
expired. There is no effective new authorization for producer-inventory or native
policy reads. Cookie validity was not probed or inferred from file existence.
No Cookie content was read during this handoff.

An ignored private handoff package now contains unchanged gateway/native Profile
copies, the existing Run's source-bound offline recheck plan, and three explicitly
non-authorizing templates. Observer permission is false; approval times are zero;
unreviewed source evidence remains unfilled. Actual validators reject all three
templates. Before/after hashes confirm that original Run artifacts, SQLite ledger,
fixture reservation and command guard bytes are unchanged. No remote call or
historical authorization renewal occurred.

The proposed next action requires one fresh operator confirmation:

- DB-only recheck of the existing exact Group/initial Session and two test Bots,
  with the producer-inventory contract; no BaaS reads. Maximum 13 MCP calls:
  four schema descriptions, three scoped fact/inventory queries and up to six
  delivery reads. The 120-second observation window starts after schema admission;
  existing per-call limits remain in force.
- Independent native policy discovery: at most three explicitly approved,
  same-origin GETs. It uses the existing private credential reference; refresh
  credentials only if required, never paste values into reports or chat.
- No new Run, Story replay, messages, policy mutation, Bot stop, database write,
  resource deletion, original-result rewrite or fixture release. No new Human/Bot
  is required. Any newly collected evidence stays separate from the old timeout.

After actual confirmation, create separate fresh receipts from that authorization
and real source/mapping evidence; never change old timestamps or make templates
valid merely to bypass this checkpoint. If source artifacts have changed, inspect
why before generating a new plan. The diagnostic command's success still does not
qualify the retained subset or permit its second round.

Handoff validation: 59 relevant recheck, producer-inventory and native-policy tests
passed (4.504 seconds), including old-review isolation and no implicit admission.
This is a documentation/private-input preparation step, not a new full runtime
regression or a legacy singlebox coverage-gate run. Private profiles, target IDs,
credentials, source hashes and review templates remain ignored and uncommitted.

## Mapping/permission separation supersedes hourly readonly renewal (2026-10-12)

The preceding handoff's expired record remains an accurate **legacy v1** fact,
not evidence of a database-platform permission failure. The newly implemented
retained-recheck v2 path separates persistent, revocable environment facts from
an exact one-use read plan and explicit invocation flags. It does not require a
new chat confirmation solely because one hour passed. See `environment-mapping.md`.

Legacy Run artifacts/receipts are not edited or auto-converted. Actual platform
ACL and credentials still apply. Stable mapping alone never grants queries;
source/target/lease checks, projection whitelist, explicit flags, bounded calls,
monotonic runtime deadline and non-replay observation directory still apply.
This code increment passed 19 focused, 71 compatibility and 954 complete runner
tests; no real remote invocation or fixture disposition change occurred. Full
scope completion and multi-environment release acceptance remain unfinished.

## First real v2 database-only recheck (2026-10-12, 19:45–19:47 CST)

Using implementation `b9eaa6a10`, the ongoing E2E task performed one explicitly
invoked source-bound recheck of the original retained Group/initial Session.
Persistent mapping facts were established from the already recorded owner's
shared-database statement and exact historical target, without changing old
approval timestamps or claiming new platform privileges. No Cookie was read and
no HTTP request, business write, stop, delete or fixture release was performed.

All four required schemas and all scoped reads succeeded: four schema reads,
three Group/Session/state-machine inventory queries and six delivery snapshots,
**13 MCP calls total**. Platform DB access worked; the previous local one-hour
receipt expiry was not a platform permission failure.

The observed Group was normal/chat, active, service_mode NULL. Its initial chat
Session was running with activation_count 1, callback_status NULL and no completed
 timestamp. The exact Session state-machine inventory returned zero rows. The
last delivery snapshot contained three completed Sends, one consumed Inject and
one expired Inject: zero active/uncertain Sends and zero pending/bound contexts.
Session running is not proof of an active engine; the finite delivery snapshot
still is not proof of complete producer/callback/downstream coverage.

The observation was saved separately with no tool errors. Its outer result stays
BLOCKED/exit 2, execution UNCONFIRMED and pool QUARANTINED. Byte hashes confirm the
original Run, ledger, frozen reviews, old result, reservation and guard did not
change. The original timeout was not rewritten, and no second Story was started.
Private evidence includes the exact query scope, original hashes and new report;
it is intentionally not committed.

This observation exposed a concrete polling inefficiency: the stopping predicate
accepts consumed/discarded context but not expired Inject, so it used all six rounds
and reported OBSERVATION_LIMIT despite no remaining active Send. The next code
increment should distinguish expired passive Inject from uncertain expired Send,
retain all full-scope qualification gates, and preserve this original observation.
Relevant local recheck/mapping/producer regression: 57 tests passed in 7.724s.


## Terminal passive Inject polling correction (2026-10-12)

The follow-up to the real v2 recheck corrects only the polling stop condition.
It reuses the existing activity classifier: all observed Injects must be valid
passive terminal contexts (consumed/discarded/cancelled/expired/failed with
`may_have_been_sent=false/0`). The existing per-Session completed-Send requirement
and active/unresolved/unsuccessful Send exclusions remain unchanged. Expired
Send is still uncertain, including when its possible-send flag is false.
History now rejects expired/rejected-capacity status reversal as well, matching
the fixed lifecycle source `ee9f4f8af4dbe4b768469df9043215093a74da58`.

A sanitized synthetic replay of the observed three-Send/two-Inject shape stops
at the first complete round. The source-bound producer recheck composition uses
**eight calls** (four schema, three scope/inventory, one delivery), not thirteen;
its result remains BLOCKED/UNCONFIRMED/QUARANTINED, with no product writes, HTTP,
Cookie reads, deletion, release or changes to the original artifacts/guard.
This query-count result is from a controlled regression, not another remote run.
The real six-round observation and original business timeout remain untouched.

Validation: 137 focused tests and 963 complete runner tests passed
(163.625 seconds, owned BCS/CLI enabled, no failures/errors/skips). The independent
read-only review found no implementation defect; its two test-strengthening
suggestions were incorporated before the final complete run. Case guard, strict
OpenSpec and diff checks passed. The legacy all-module singlebox coverage gate
was not run on this shared workstation, and no release qualification is claimed.

## Real native policy evidence (2026-10-12, 20:26 CST)

The existing policy-check completed three same-origin GETs (all HTTP 200) using
the current delegated Goal and existing private native credential reference.
Policy version 22 resolves both test actors to enforce with Group/System managed;
pause_dispatch=false, max_running=8, max_queued=20, interval=1000ms and
queue_ttl_ms=300000. The stored update time precedes the original Run, but that
alone cannot establish historical configuration, all replicas or transport.
Source provenance is historical, live build verification NOT_PERFORMED.

This closes the **current effective policy not yet read** gap, not whole-scope
completion. Original Run/ledger/reviews/results/pool/guard bytes were verified
unchanged; no DB calls, new Story, policy writes, stops, deletes or releases.
The existing Cookie was accepted for this invocation. Next evidence priority is
the known Sends' actual transport and historical managed/direct coverage, not
another identical delivery snapshot. See policy-check.md for the validation and
local pre-invocation/discovery errors. Fifty-eight related tests passed; the
preceding 963-test implementation regression remains the latest complete run.


## Known managed chain: exact Final and composition proof (2026-10-12)

See [retained closure matrix](retained-closure-matrix.md) for the current evidence
and remaining boundary. Three canonical chat.event Final records now match the
three completed Sends and prior WS ACKs. Fixed-source bootstrap wiring guarantees
Some(live policy) with managed delivery, so a matched managed original's reply
selection covers all routing targets and does not depend on historical policy
switch values. That narrows, rather than removes, the execution closure gap.
The existing same-day pre-Run health report is retained as responding-service
version evidence, not all-replica image attestation. No pending-input drain or
whole-scope completion is claimed; no pool release or second Story occurred.
