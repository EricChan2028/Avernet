# Preproduction retained serial admission — accepted revision, 2026-10-14

**Status: serial admission and reporting implemented; two live Group rounds and two live Session/WS rounds accepted. Original full pilot/deletion acceptance remains pending.**
This is the tracked counterpart of OpenSpec decision D40. It supersedes the
historical retained plan's requirement to prove complete remote execution closure
before admitting another retained Run. It does not retroactively change results.

## Decision

Next-run admission requires **process-guaranteed serial triggering and a fresh
successful dependency check**, not deletion, pool cleanup, or proof that all work
from the previous Run has stopped. Historical remote execution UNCONFIRMED alone
is not an admission failure. Retained resources are the default; cleanup and full
termination capability are lower-priority independent work.

The designated runner owns the trigger sequence. An active test command, an
unclosed local triggering channel or a concrete conflicting Case baseline blocks
a new trigger. The flow must not redefine historical unknown closure as a failed
preflight to recreate the old universal blocker. This single-runner flow does not
claim to enforce cross-machine mutual exclusion.

## Per-run dependency checks

Acquire exclusive trigger authority, then freshly validate:

- Target environment, route/deployment binding, current authenticated Human.
- Bound dedicated Bot identities, ownership, availability and Case-specific
  required/forbidden relationships and permissions; do not silently repair them.
- Required services and the selected Case's current baseline/state.
- Evidence sources needed for business assertions. Complete producer cutoff and
  complete remote termination evidence are diagnostic for this control-plane
  subset, not universal prerequisites. Discover current instances whenever a
  log check needs instance selection; do not reuse a historical instance list.
- Retained-resource accounting and available limits, including prior ambiguous
  creates; keep existing quota consumption, never assume missing records mean zero.

Hold trigger authority through execution and local channel finalization. On lost
identity/authority or a failed dependency, stop new actions. No automatic retry
of ambiguous creation and no fallback identity, route or global stop is added.

## Reservation and historical artifacts

Separate the active command guard from a historical resource reservation.
Transfer the scheduling reservation with an append-only old/new Run receipt;
preserve old ledgers, reports, observation receipts and quota charges. Transfer
is not resource deletion, cleanup completion, historical RELEASED, or proof of
remote quiescence. Do not implement it as manual unlinking of a lock file.
A partial transfer must remain recoverable without admitting duplicate triggers.

## Results

Keep distinct fields for:

1. Functional result and required business evidence.
2. Execution observation (including UNCONFIRMED).
3. Exact retained resources and accounting.
4. Next-run admission from the new serial guard and fresh preflight.

Functional PASS, execution UNCONFIRMED and resources RETAINED can coexist.
The next Run can be admitted after its checks pass; an old report is not a reusable
readiness certificate. A new versioned policy must expose these semantics rather
than silently making old BLOCKED reports green. Completion/stop remains mandatory
when it is a Story's actual business assertion. Delete assertions omitted from
retained slices remain uncovered; the original release denominator is unchanged.

## Delivery order and validation

1. Implement versioned serial admission and reservation transfer in retained
   orchestration, reusing existing preflight, pool guard, quota and shared steps.
2. Integrate separate reporting without changing old results or default cleanup.
3. Test unknown historical activity + passing dependencies admits; a held guard,
   failed/expired dependency or exhausted quota denies; failed/interrupted/replayed
   transfer cannot execute twice; no path deletes resources or refunds quota.
4. Run the first and second same-version preproduction slices serially, with a
   new preflight each time. Record real functional and persistence assertions.

Without `--serial-preflight`, `retained-run` keeps its previous behavior. The
opt-in scheduling policy now supports a fresh first Run and an explicitly bound
handoff from the previous retained Run. New-policy Runs publish a selected-Case result independently of complete
remote closure. Existing historical reports and default-policy Runs keep their
old result and exit semantics. No historical result is rewritten and no existing real
reservation is transferred by installing this code. Real first/second preproduction acceptance in tasks 10.6–10.7 remains pending. Full cleanup/termination work and production/cloud policy
remain separate; do not prioritize further local Story expansion over this path.

## Implemented serial scheduling entrypoint

Use the existing `retained-run` inputs and add `--serial-preflight`. For the next
Run also supply `--previous-state-dir /private/path/previous-run`; the new Run ID
and state directory must be fresh. Remote read/write permissions, reviewed routes,
actor binding, observer configuration and retention limits remain required.

The fixed pool guard is held continuously from fresh dependency checks through
preparation, business steps, local connection closure and evidence publication.
A pool-local `.serial-history` receipt records transfer intent before the atomic
reservation replacement. `serial-admission.json` binds the new Run, previous
reservation digest and fresh preflight. These are scheduling evidence, not remote
completion or a reusable next-run readiness certificate.

A completed previous retained command can hand off regardless of historical
remote execution uncertainty. Confirmed pre-execution failures can also hand off
after dependencies are corrected: they require a persisted, bound NOT_STARTED /
NOT_RUN receipt under the guard. Missing/partial command evidence or unconfirmed
local connection closure still requires local-trigger inspection. Ambiguous
transfer persistence is not automatically retried or rolled back.

Validation uses real temporary file guards/ledgers and the actual runner CLI;
local mock and gateway-shaped HTTP/MCP replies are synthetic, not live preprod.
Tests cover two Runs, old-policy handoff, byte-identical old artifacts, fresh
preflight/held guard, failed dependencies, quota accumulation/exhaustion, failed
quota snapshot followed by a corrected third Run, and transfer failure before
and after reservation replacement. No remote deletion or quota refund is added.

## New-policy reports and exit status

Only new Runs with a frozen `retained-serial-preflight-v1` admission receipt use
the revised qualification. All declared business assertions and required evidence
must pass; cancellation, authority loss, failed assertions and unconfirmed local
WS closure remain non-success. Complete remote closure stays UNCONFIRMED and its
execution-safety findings remain diagnostic. Resource inventory, ownership,
observer binding and report integrity are still checked.

A successful selected Case emits `status=PASS` in its new Case report and
`status=RETAINED_CASE_PASS` in JSON/JUnit/Markdown summary, with exit 0 from both
`retained-run` and `report`. The summary explicitly names the one selected Case;
the other retained Case is unexecuted/skipped, the 29-parent legacy denominator is
unchanged and `full_release_status=NOT_QUALIFIED`. This is not PILOT_PASS or a
full-suite release result. Required Bot completion assertions in other Stories
are not reclassified as diagnostic.

`execution_status=UNCONFIRMED`, `cleanup=RETAINED`,
`pool_disposition=RETAINED_SERIAL` and `next_run_admission=FRESH_CHECK_REQUIRED`
remain visible. An offline report cannot certify current dependencies or current
trigger ownership, release the scheduling reservation, authorize cleanup or turn
old historical BLOCKED results green. Missing/mismatched policy/preflight receipts
or failed business assertions block successful qualification.

The runner writes the three report formats to the new Run's `reports/` directory.
Use a fresh Run and fresh read/write reviews each time; `--previous-state-dir`
only identifies scheduling provenance. Never reuse the previous Run ID or change
its files to adopt the new reporting policy.

Validation checkpoint (2026-10-14): 201 related retained/reporting/pool/prepare/
quota/authority/cancellation regressions passed in 17.766 seconds. This includes
real SIGTERM delivery to the test process, publication failures, lost authority,
ambiguous creates and review expiry using isolated synthetic service boundaries.
A pending/completion fence prevents a partially published PASS Case from yielding
successful offline qualification. No live preproduction request or historical
pool transfer was performed, and no full Singlebox coverage gate was run.

Full runner regression checkpoint (2026-10-14): **1329 tests passed in 983.315
seconds**, with approved owned-local BCS and product CLI binaries enabled, no
failures/errors/skips. This validates the runner suite including existing pilot,
reporting, restoration, cleanup, CLI and new serial retained tests. It is not the
legacy all-module Singlebox coverage gate or live preproduction acceptance.
The tested implementation is `cf7f96bc8` with runbook follow-up `51c5b84fe`.
The unrelated unfinished `local_file_setup.py` remains untracked and was not
wired into an executable command or claimed as completed coverage.

## Live Group acceptance checkpoint — 2026-10-14

Two fresh, same-version `RETAIN-GROUP-01` Runs completed serially through the
reviewed preproduction gateway, using an authenticated Human and two dedicated
owned protected Bots. Each Run repeated dependency discovery while holding the
trigger guard. Each returned Case PASS and canonical `RETAINED_CASE_PASS`;
independent new-process `report` commands also returned exit 0.

Per round, all seven declared assertions passed: preflight, Human originator,
driver/member roles, exact Group read, name update, updated-name readback and
independent persisted Group projection. The real database observer used exact
Group ID plus environment and the fixed two-row limit; persisted Group ID,
driver, originator and active state matched. This was not just API response replay.

Each round created one Group and one implicit initial Session, with distinct IDs.
The second round ran while the first retained resources and reported execution
UNCONFIRMED. Neither round issued DELETE, a stop operation or a database write.
The old Run state remained unchanged; scheduling transfer was recorded separately.
Quota accounting included the old one-Group/one-Session baseline and both new
reservations: three Groups and three Sessions, with no refunds.

Current preproduction instances were discovered separately for each round. The
service's build self-report was checked for drift between rounds; reviewed Group
route/identity/create/bootstrap/name-patch source files were unchanged from the
previously reviewed version. This is source review plus deployment self-report,
not binary attestation or independently reverified runtime isolation. Actor
isolation remained based on the existing owner attestation.

Private IDs, credentials, endpoint bindings and raw runtime evidence remain in
ignored local Run/batch directories. JSON/JUnit/Markdown reports retain the
unexecuted retained Session Case and the original 29-parent release denominator;
full release remains NOT_QUALIFIED. These two Group rounds do not qualify
RETAIN-SESSION-01, PRE-01/02/03, omitted deletion assertions, model quality, remote
coverage or production/cloud execution. Session/WS is the next distinct slice.

## Live Session/WS acceptance checkpoint — 2026-10-14

The first Session attempt stopped during read-only database metadata discovery
with a timeout, before preparation, quota consumption or business writes. A
separate new Run reached successful Group/Session API and database checks and
token issuance, but failed WS Upgrade with HTTP 302. Its Case remains FAIL and
its resources/accounting are retained; neither failed attempt was overwritten.

The runner's raw Upgrade had omitted the gateway Cookie/browser context used by
its HTTP client. After the same-origin header fix in `e493d65f6`, two fresh
same-version `RETAIN-SESSION-01` Runs passed serially through the gateway.
Each had **12 observed passing assertions**, including all seven manifest-required
assertions. The real observer checked Group and explicit Session rows by exact
ID plus environment, including Session parent and updated title. Actual WS
Upgrade returned HTTP 101, the connect request received the matching accepted
response for the Group, and the owned local connection was closed. Close evidence
is local-handle closure, not proof that all remote Bot work has terminated.

Each Run owned a new Group, its implicit initial Session and one explicit Session.
The second Run freshly checked dependencies rather than requiring cleanup or
complete remote closure of the first. Both Case reports are PASS; independent
new-process JSON/JUnit/Markdown rendering returned RETAINED_CASE_PASS / exit 0.
No delete, stop, database write, identity fallback or redirect-following was used.

Accounting now includes the old baseline, two successful Group Runs, the failed
WS Run and these two successful Session Runs: **six Groups and nine Sessions**.
The initial metadata failure consumed none; the failed WS Run was not refunded.
Private resource IDs and credentials remain in ignored evidence storage.

Validation of the handshake fix: 154 WS/retained/probe/report/browser-context
regressions and 24 correlation regressions passed. This is focused post-fix
validation; the earlier 1329-test full-run result predates the handshake fix and
must not be presented as a fresh full-suite run of that commit.

This accepts the retained Session/WS slice only. PRE-02's deletion/full execution
closure assertions, PRE-03 storage/BaaS acceptance and full release qualification
remain open. Bot response quality and complete background execution closure were
not asserted or inferred from the successful connection.
