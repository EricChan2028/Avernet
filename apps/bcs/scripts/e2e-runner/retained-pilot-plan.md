> **Superseded admission rule (2026-10-14):** see [retained-serial-admission.md](retained-serial-admission.md). The accepted next-run rule is process-guaranteed serial triggering plus fresh successful dependency checks, not historical whole-scope closure or cleanup. The text below records the previous design and implementation; its closure-before-reuse requirements are historical, not the current target. Runtime migration is pending.

# Preproduction retained-observation pilot

Decision accepted: 2026-10-11. **Shared steps and local/mock retained execution
implemented; separately reviewed supervised gateway attachment is implemented.
Terminal observation and live qualification remain pending.** See `retained-remote.md`
for required private inputs; current results cannot qualify or release a pool.
This scoped plan supersedes the earlier requirement to implement a new product
suppression/stop API before attempting any Session pilot. It does not disable
current runner admission, change production policy, or certify existing logs and
database projections as complete execution evidence.

## Scope and first slices

Use one authorized Human and two distinct, dedicated owned test Bots. Preserve
existing friendship and Bot configuration. Run serially on the one designated
runner; do not claim that its local lock fences other machines. Confirm exclusive
use before admission. Freeze route, deployment evidence, roles, Case version,
allowed implicit effects, resource/request/observation budgets and the operator
responsible for unresolved activity.

Reuse the existing Story steps and business invariants; do not fork their business
logic by environment. A reduced selection has a separate manifest and denominator:

| Proposed slice | Assertions | Explicitly not covered |
| --- | --- | --- |
| RETAIN-GROUP-01 | Create/read/rename/read one normal Group; verify exact membership and persistence; observe its implicit initial Session and dispatch | Group DELETE and deletion propagation; Bot reply quality |
| RETAIN-SESSION-01 | In its own Group, create/read/update one Session, obtain the authorized token, actually connect/bind/close owned WS; verify API and DB parent binding; observe both implicit and explicit Session work | Session/Group DELETE; full chat semantics; model quality |

These IDs are registered in the separate retained manifest for local/mock execution;
remote execution requires the separate scoped review and observer binding. Start with the first
slice; admit the second only after the first's scoped activity is confirmed ended
and its retained resources cannot generate later work. Each slice owns its own
Group and records every observed Session. Original PRE-01/02/03 and their deletion
assertions remain visible and unqualified. NATIVE-GROUP-01 evidence is unchanged.
PRE-03 remains later: file completion notifications, sharing and storage effects
need their own review. Real Bot stories must still execute their asserted behavior.

## Observation before product changes

First inspect the deployed-version semantics of existing API, logs and database
states. Record exact request/Group/Session and available delivery/run/attempt IDs,
state meanings, retries/queues/callbacks actually applicable to this slice, source
freshness, completeness and known blind spots. Do not fabricate effect IDs or infer
zero work from an empty mapping. A new stop API is not mandatory for natural
completion; only demonstrated gaps require a separate product change.

Read-only queries require reviewed schemas, minimum projections, exact owned
identifiers, bounded rows/polls and source binding. Where a shared table lacks an
environment column, require separately reviewed exact-ID provenance and parent/
owner correlation; otherwise refuse that query. Existing receipts do not authorize
new tables or broaden scope. Never write the database, scan broadly by Bot/name,
read credentials, or mistake truncation/tool failure for an empty result.

Distinguish terminal evidence for a known task from coverage of all relevant work.
No new logs, an idle Bot, disconnected WS, HTTP 404 or a missing Session row alone
proves neither. Unknown correlation, delayed evidence and source errors remain
UNCONFIRMED. A reviewed existing terminal contract may establish completion;
a quiet interval alone is only diagnostic. Retention need not await proof in
order to record functional results, but pool reuse and overall success do.

## Lifecycle and safety

1. Read-only preflight: identity, route, dedicated actors, permissible effects,
   required observers, quota and named manual incident owner. Reject uncontrolled
   business writes, operational channels, global mutations or unbounded autonomous
   scenarios; retaining data is not permission for harmful execution.
2. Reserve the test pool, recheck baseline, persist intent and actual request IDs,
   then execute one explicitly admitted low-risk slice with finite trigger budgets.
3. Assert API behavior and gather scoped log/DB evidence with bounded waits.
4. Stop new runner triggers. Wait for natural completion; do not manufacture cancel
   requests. Close only owned client connections, preserving observation while it
   is needed within budget. Closing them does not stop remote work.
5. Retain created Group/Session/File resources by policy on success or failure.
   No automatic teardown DELETE, parent cascade, DB write or broad Bot stop.
6. Persist independent outcomes and a resource manifest with exact IDs, parents,
   owner, reason, review deadline, quota and manual handling instructions. A review
   deadline is not an automatic TTL or authority to delete.
7. Unknown/active work, lost credentials/lease, cancellation, lost create response
   or incomplete observation stops the sequence and retains pool quarantine.
   Do not automatically retry creation, start another Run or clear locks on expiry.
   Reuse requires reviewed completion, no residual producer, and quota/baseline
   checks; resource retention alone must not permanently prevent safe reuse.

An operator must be available for the first supervised run. Manual escalation is
not proof of termination or permission to change production. Recovery/cleanup is
a later explicit, exact-scope action after ownership, authorization and execution
state checks. Production and cloud admission remain separate. Local owned-runtime
cleanup and existing release/coverage gates remain unchanged.

## Results and acceptance

Target outcome axes (partial local reports now implemented; terminal qualification
and quota-aware pool reuse still pending):

- Functional: PASS / FAIL / NOT_RUN / ERROR / CANCELLED, preserving API assertions.
- Execution: CONFIRMED_ENDED / UNCONFIRMED / TIMED_OUT / NOT_STARTED, with sources,
  timestamps, completeness and unresolved activity.
- Resources: RETAINED_BY_POLICY / RETAINED_UNRESOLVED / CLEANED / CLEANUP_FAILED.
- Qualification: success only when the selected manifest's functional and required
  evidence checks pass, scoped execution is confirmed ended, client handles are
  closed and retention/quota policy is satisfied. Retention by itself is not failure.

Functional PASS plus execution UNCONFIRMED is useful partial evidence, but MUST
NOT become overall PASS, a successful exit code or green JUnit qualification.
Keep the original functional result visible. Missing delete assertions are NOT_RUN
in the original inventory, not quietly removed or credited as CRUD success.
Two serial same-version successful retained-slice rounds qualify only this subset,
not PRE-01/02/03, full release, remote coverage or production safety.

## Implementation order

1. Audit real state semantics and available scoped read-only sources; identify gaps.
2. Add reviewed retained-slice admission, shared-step selection and no-delete
   finalization. Preserve legacy/local safety gates; no boolean bypass.
3. Separate report/exit/JUnit outcomes and quota-aware reservation disposition.
4. Test unknown IDs, active queues, quiet logs, stale/truncated/error evidence,
   credential/lease loss, SIGTERM, no DELETE, retained quota and pool reuse.
5. Admit one supervised live slice, retaining resources and evidence; then a second
   same-version round only if the first safely qualifies. Do not refresh expired
   credentials or observer authorization implicitly.

## Shared-step implementation increment

`control_steps.py` now owns the Group create/read/update/persistence assertions
and the Session create/read/update/token/WS-connect assertions. Mutable progress
keeps exact resource handles and socket ownership even when a step fails. The
legacy PRE-01/02 executors call these same steps and retain their original
termination, deletion and report behavior. This extraction introduces no new
remote admission, command or claim of retained-pilot qualification.


## Retained executor increment

`retained-run` selects one frozen Case from `retained-manifest.json` and uses the
shared control steps. Its composition binds the Run, Case, resource ledger and
held pool lease; effect declarations contain retention but no deletion authority.
It writes the durable execution requirement before effects, retains resource
intents/IDs in `retention.json`, closes only its owned WS, and writes the Case report
with independent functional, execution and resource outcomes.

The local/mock and separately reviewed gateway adapters validate functional steps, but the retained
terminal observer is not connected. Therefore even functional PASS yields
`status=BLOCKED`, `execution_status=UNCONFIRMED`, `cleanup=RETAINED`, a quarantined
reservation and CLI exit 2. Cancellation yields exit 130. This is intentionally
not a completed retained pilot or safe pool-reuse implementation. Remote profiles
without the scoped gateway effect review, supervisor, protected test actors and
current observer mapping are rejected before target writes. Native identity is
not admitted by this command. Do not
modify the profile/lock to bypass this boundary.


## Retention-aware reports and manual handoff

The read-only report path now validates retained metadata against the original
Run/Profile, frozen Case/report and the exact local resource ledger. It projects
independent functional, execution and resource-disposition axes in JSON, JUnit and
Markdown, rather than forcing the operator to infer execution state from cleanup.
A missing report for an attempted Case remains execution UNCONFIRMED, not NOT_RUN.

For the current admitted contracts, execution remains UNCONFIRMED and the pool
remains quarantined. There is no completed-scope proof consumer or retained reuse
operation in this increment. Editing report fields to claim CONFIRMED_ENDED,
RETAINED_BY_POLICY or RELEASED is rejected as an unsupported claim; a future
implementation must establish those outcomes from authoritative evidence, not a
profile flag. Local controlled quiesce/delete callbacks are not invoked to make
a retained mock run appear naturally complete.

The handoff section includes the bound Human owner, exact retained Group/Session
IDs and parent handles, intended resources whose IDs are unknown, and the original
review-after epoch. DUE means manual review is due; it is not TTL, an automatic
reaper, permission to delete or permission to release the pool. The report is a
handoff artifact, not evidence that a human has accepted it or that a notification
was sent. Suggested next actions are fixed safe labels, never commands copied
from arbitrary retention-file free text.

A verified inventory means **local artifact binding only**, not a fresh remote
existence check. Missing/mismatched/symlinked retention files, altered ownership,
unsupported dispositions, incorrect parent trees, incomplete successful-Case
resource counts and observed source drift block integrity. Unknown creation
intents can still be displayed as UNKNOWN_RESOURCES when they match the ledger;
they never gain deletion authority. Source corruption hides unbound resource IDs
and does not erase the separately recorded functional outcome.

If a quota receipt is recorded, it must agree across Run, Case and retention
metadata and match the exact reservation. Reports display accounting and limits
**at reservation**, not current remaining capacity. A missing historical quota is
NOT_RECORDED, not a zero baseline; no receipt is fabricated and no automatic refund
occurs. Retention metadata and the read-only resource snapshot are compared again
before publication. This detects observed drift, not malicious rewriting by the
local file owner or an atomic snapshot across independent files.

The added inventory reads are local SQLite snapshots only (the retained slice
allows at most one Group and two Sessions); no database-platform query, HTTP,
Cookie resolution, product mutation or pool operation is introduced. Non-retained
pilot reporting keeps its existing cleanup qualification and does not read the
retained inventory. The original legacy release denominator is unchanged.


Prepared-state reporting also accounts for the pre-dispatch quota crash window:
PREPARED alone does not mean no handoff is needed. Only a profile-bound Run with
no quota record, no attempted Cases and an already-read empty ledger can display
NOT_REQUIRED. Missing/uncertain quota receipts, unreadable resources or attempted
work keep manual handoff REQUIRED. If Run/Profile binding fails, the owner is
UNKNOWN/UNVERIFIED, not the caller-supplied Human. JUnit emits retention status and
handoff status under separate property names to avoid one overwriting the other;
quota and exact resource projections are present at suite and applicable Case
levels, including the suite-only crash path with no Case report.

The historical report view does not silently replace the original Case's timeout
or merge later standalone database/log acquisitions by filename or timestamp.
Those receipts remain independent evidence; a future closure consumer must bind
and evaluate them explicitly. Reading this report therefore does not perform a
fresh environment check or assert that its original observation is the newest one.

### Handoff-report validation (2026-10-12)

Eighteen dedicated retention-report tests cover real temporary ledgers and Run
files, Group/Session slices, lost-response intents, source mismatch/drift, future
and due deadlines, missing reports, reservation uncertainty, owner binding and
all-format consistency. Forty-four focused report/retained regressions passed;
the final complete runner suite passed **1027 tests in 169.776 seconds**, with
owned BCS/CLI enabled and no failures/errors/skips. Case guard, strict OpenSpec
and diff checks passed; the old all-module singlebox coverage gate was not run.

Independent read-only review identified three corrected gaps: PREPARED quota
crashes had been incorrectly classified as no handoff, wrong Profiles supplied
an unverified owner fallback, and JUnit lacked quota snapshot fields. Each now has
negative regressions and passed re-review. Additional tests caught unknown-origin
creation intents being hidden and a retention/handoff JUnit property collision;
these were corrected without weakening uncertainty or source-binding checks.

Offline replay of the original preproduction Run produced JSON/Markdown/JUnit
with two exact retained resources, bound owner and pending review, functional
PASS, execution UNCONFIRMED, resource disposition RETAINED_UNRESOLVED and pool
QUARANTINED. Its historical quota receipt was absent and remained NOT_RECORDED,
not an invented empty baseline. All sixteen original Run files were byte-identical
before and after reporting. There were zero network/DB-platform calls, native
Cookie reads, business writes, stops, deletes or pool releases. Later independent
ACK/Final acquisitions were not merged into or used to rewrite the old timeout.
Task 10.4 remains partially implemented; whole-scope proof and safe reuse still
require their own evidence consumption and admission implementation. Spec: 30/54.
