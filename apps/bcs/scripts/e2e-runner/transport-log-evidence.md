# Minimal transport evidence for a retained delivery chain

This is the source-reviewed acquisition plan and actual access-check result for
retained task 10.2. It does not add a remote log adapter, change Case outcomes,
authorize a new Story or release a pool. Source citations below refer to fixed
public object `ee9f4f8af4dbe4b768469df9043215093a74da58`; paths are relative to
`apps/bcs/crates/`, not the mirror checkout's current branch or a fresh deployment.

## Preserve canonical and attempt identity separately

Use the existing exact owned delivery projection; do not read transport JSON,
headers, prompt content or credentials. For each known Send freeze:

- delivery_id, canonical run_id, request_id (the sending attempt), downstream_run_id;
- target_bot_id, exact Group and Session, env, attempt_no, state_version;
- observed status and terminal timestamp, source observation identity/digest.

`services/bcs-message-flow/src/delivery_runtime.rs:391-420` changes the outgoing
chat.send frame ID to the attempt request_id. The HTTP Provider adapter then
changes its request body ID back to the canonical run_id
(`adapters/http/bcs-provider-http/src/lib.rs:347-358`). Consequently, searching
only canonical run_id can miss the WS acceptance record. Do not substitute
source_message_id or a freshly generated request ID for either identity.

## Two narrow observation surfaces

| Evidence | Actual source and match | What it proves |
| --- | --- | --- |
| WS accepted response | `adapters/ws/bcs-ws/src/bot/dispatcher.rs:575-584,788-846`: bcs_message target, bot_accept event, accepted status; log run_id equals the attempt request_id. Check exact Bot/Group/Session. | The WS adapter received a successful ResponseFrame for that attempt. Not engine completion or the plugin's downstream implementation. |
| HTTP Provider acceptance | `adapters/http/bcs-provider-http/src/lib.rs:493-514,566-587,638-665`: respectively SSE stream accepted, JSON ack accepted (callback transport), or deliver acked. Check canonical run_id and exact target Bot; where present, method must be chat.send. | HTTP Provider accepted the request through the stated branch, not BaaS identity or execution completion. |
| Optional terminal cross-check | `services/bcs-message-flow/src/managed_delivery.rs:456-495`: managed delivery run settled is emitted after the repository commit. Match exact delivery_id/run_id/Bot/Session. | BCS terminal commit; this does not identify the transport. Existing bounded DB evidence already supplies this fact. |

The Provider's `deliver start` record (lib.rs:365-373) can supply the exact
provider_id/provider_bot_ref and method for pairing, but is not a successful
send. `posting webhook` is a request preference and can contain a URL; do not use
it as the primary proof or return its raw line. The Provider's HTTP message_id
is a different transport identity, not the delivery source_message_id.

The WS `accepted_run_id` is response data, while its `delivery_request_id` log
field is from optional state-machine correlation; ordinary bootstrap may leave
that field empty. Do not require it to equal the queue attempt ID. WS logging
also returns early if both runtime context and state-machine correlation are
absent (dispatcher.rs:727-744), so missing ACK logs do not exclude WS transport.

## Query and result boundary

Discover the actual application/region/collection and format first; source
filenames alone are not deployment bindings. Use only the original controlled
Run's time range with a small explicit clock margin, exact three canonical or
attempt IDs, and the corresponding Bot/Group/Session where those fields exist.
Do not widen to Bot-wide logs. A shared collection or a `prod` project suffix does
not independently prove the event environment; preserve the original preproduction
ID provenance and require any available environment metadata before qualification.

Request only whitelisted scalar identity/status fields through a server-side
projection. For the WS query these are run_id, bot_id, group_id, session_id,
accepted_run_id, event_type and status; timestamp may be added only using the
verified collection format. Never return content, URL, headers, error text or
request/response bodies. The reviewed initial window uses a maximum of 31 rows
(30 plus an incompleteness sentinel), a ten-second server wait and bounded CLI
runtime. Parse failure, truncation, timeout, ACL error or empty rows remain
unresolved, not proof of absent work.

Each delivery must match its own recorded identity tuple; duplicate/conflicting
transport claims remain explicit. For attempt_no greater than one, Provider
canonical Run logs do not necessarily distinguish the final attempt. Do not pick
whichever record makes the result green. Only add a BaaS observation when the
actual transport/provider mapping establishes that BaaS is applicable.

## Dynamic instance discovery for every acquisition round

Instance addresses are ephemeral observation results, not environment configuration.
Before **each** remote acquisition round, discover the application's server instances
from the authoritative platform, consume all pages within an explicit discovery
budget, and select the exact target environment and eligible instance status. Save
observation time, application/environment, returned instance identity and pagination
completeness in a private snapshot. Do not cache a prior round's IP list as current
or put live addresses into the public profile, Story, or tracked documentation.
An empty or incomplete discovery cannot establish complete environment coverage.

For every selected instance, discover its log files using the explicit preferred
host. Require the returned target `data.ip` to equal that discovered instance and
use only returned file paths. The default application log discovery may select a
production instance; never accept that fallback for a preproduction query. Backend
IP/hostname fields describe the observation service, not the business-log target.
Validate the actual query echo against the frozen target, path, time and selectors.

Freeze this round's discovered targets and bounded query plan together. If an
instance disappears, the agent is unreachable, the target changes, or acquisition
must resume in a later round, stop using the stale target and rediscover. Replanning
must retain the same original Run/identity scope, preserve partial evidence and
account for calls already spent; retries cannot reset the total budget. Never
silently query the replacement/default instance and claim continuous coverage.

Current topology is not historical topology. A retired or replaced instance may
have held the original Run's logs, and even an unchanged IP need not prove the
same process lifetime. Missing logs therefore remain incomplete evidence, not
proof of no dispatch or completion. Where historical coverage is required, use
independently verified historical instance metadata or an authorized centralized
log source with exact Run and environment correlation; current instance discovery
alone cannot establish it. Positive ACKs still require their exact identity tuple.

These are collector requirements. The existing `retained-transport` command is
still offline-only: it does not dynamically discover instances or certify the
provenance of a supplied projection. The manual Observ acquisition below follows
this discovery process; automatic collector enforcement was not yet implemented at that point; the
subsequent `retained-observ-ws` implementation is described below.

## Actual access check (2026-10-12)

- The configured AntLogs CLI was authenticated. Application and collection
  metadata were discoverable. The first candidate region returned no collections;
  the selected region was subsequently confirmed by live metadata, not assumed.
- The message-log collection has a content/full-text index. An index-export call
  returned only an empty configuration document; the later direct metadata read
  established the actual index. No environment field was inferred from that index.
- One exact three-attempt WS query, with a server-side scalar projection, was
  submitted. The platform rejected it with **AE2511000370008 / application READ
  permission required**, before any usable log result. No Provider query, query
  retry, broad scan or alternate identity was attempted. Query syntax/wire-row
  shape is therefore not live-validated by this failed call.
- The separate Observ diagnostic reported **renewal_required**, effective source
  none, with server identity probe NOT_RUN. It did not supply alternate log data;
  no refresh, login, configuration write or credential copying was performed.
- The operator action is `observ auth renew` in their terminal. After confirmation,
  verify readiness and actual target-log access before issuing any exact query.
  Successful renewal alone is not proof of log READ permission. Alternatively,
  obtain the explicitly named application's READ grant on AntLogs; do not create
  or grant it automatically.

Private artifacts retain the query, fixed source IDs/digest, bounded time window,
platform error/trace and original artifact hashes. The original Run/ledger/results/
reservation/guard stayed byte-identical. Business writes, stops, deletes and
releases were zero. This real platform ACL denial is distinct from a runner-local
review timer or the native Cookie, which succeeded in the preceding policy read.

Validation for this plan/access-record increment: 31 log-observer/local-log and
terminal-delivery regressions passed (0.526s), plus strict OpenSpec and diff checks.
These existing tests cover bounded/partial/error handling and preserve qualification
gates; they do not validate AntLogs query syntax, live rows, platform authorization
or a new remote log adapter. The prior 963-test complete runner result is unchanged.

## Offline runner plan and projection matching

`retained-transport` now implements the local planning/matching part of this
workflow. It never runs AntLogs, Observ or MCP, creates an HTTP client, resolves a
Cookie, acquires/releases a pool or writes the original Run. It prints JSON only.
It is **not a live log adapter** and does not remove the access blocker above.

```bash
python3 runner.py retained-transport \
  --profile /private/path/original-profile.json \
  --state-dir /private/path/original-run \
  --run-id ORIGINAL_RUN_ID --case RETAIN-GROUP-01 \
  --observation-id EXISTING_SAVED_RECHECK_ID \
  --start-epoch "$START_EPOCH" --end-epoch "$END_EPOCH"
```

The source must be an existing saved, successful wait-v2 delivery observation in
that Run, bound to its frozen read-plan or legacy review and current immutable
original artifacts. A top-level BLOCKED retained result is expected; incomplete,
timed-out or inconsistent delivery snapshots cannot become complete input by
selecting their last few rows. Historical receipt expiry does not prevent offline
inspection, but its remote authority is never renewed. The command rechecks source
binding at the end and opens the resource ledger read-only; it does not validate a
current remote lease or grant any network permission.

Version 1 plans require **all observed Sends** to have completed, positive
version/attempt, nonempty canonical and attempt IDs, a terminal timestamp and
possible-send evidence. Every owned Session must be present; ambiguous IDs or more
than eight Sends reject the whole plan rather than choosing a prefix. Inject rows
are not Send transport candidates, and their presence/disposition still cannot
establish scope completion. The explicit half-open window is at most 900 seconds;
it is an operator-selected search boundary, not proof of the full execution period.
Plan output includes separate WS attempt and HTTP canonical selectors, exact tuples
and a digest, with `query_permission=NONE` and `ready_to_execute=false`.

After an independently authorized collector supplies a **normalized scalar-only**
projection, the same command may add `--projection /private/path/projection.json`.
The bundle contract is:

```json
{
  "schema_version": 1,
  "contract": "retained-transport-log-projection-v1",
  "plan_digest": "<exact-plan-digest>",
  "source_reference": "operator-export-001",
  "environment": "<exact-plan-environment>",
  "status": "OBSERVED",
  "error_code": null,
  "truncated": false,
  "records": []
}
```

This is not the raw AntLogs/Observ response format. A collector must independently
verify the source event and project the following strict schemas; extra/raw fields
are rejected, not echoed. Every record has `event`, integer `timestamp_ms`, `run_id`
and `bot_id`; its time must be within the plan's half-open window.

| Normalized event | Additional fields | Matching rule |
| --- | --- | --- |
| WS_ACK | group_id, session_id, accepted_run_id (nullable/empty) | attempt Q + exact Bot/Group/Session; accepted alias never substitutes for Q |
| HTTP_START | provider_id, provider_bot_ref, method | canonical R + exact Bot, method chat.send; never acceptance by itself |
| HTTP_ACK / HTTP_CALLBACK_ACK | provider_id, method | canonical R + exact Bot, method chat.send |
| HTTP_SSE_ACCEPTED | provider_id | canonical R + exact Bot, paired with same-provider HTTP_START to establish chat.send |

The maximum is thirty input rows **before** deduplication. A 31st row is incomplete
and rejected, not trimmed. Empty, truncated or duplicate projections cannot yield
complete matching. Conflicting accepted transports/providers/known aliases remain
CONFLICT. HTTP evidence on a canonical Run with multiple attempts stays Run-level
and cannot identify the final attempt. A fast Final may precede its ACK: the
matcher intentionally does not require ACK time to precede the terminal timestamp.

A failed query uses status ERROR, no records, and one of PERMISSION_DENIED,
AUTHENTICATION_REQUIRED, TIMEOUT, QUERY_ERROR or SCHEMA_ERROR as error_code. It
returns UNAVAILABLE rather than an empty successful observation. Missing actor or
scope matches, unknown events, invalid timestamps and mixed plans fail closed.

Results always label evidence as **OFFLINE_SUPPLIED**, source/environment and
historical policy as **NOT_VERIFIED**, scope as **UNCONFIRMED**, and pool release
as false. `MATCHED` (CLI exit 0) means only that supplied projections match the
saved identities; it is not a real log-read success, authentic-origin proof,
engine-completion certificate, Case PASS or release qualification. Non-matching,
unavailable, conflicting and invalid results exit 2. A later source/plan change
invalidates an older bundle instead of silently rebinding it.

### Implementation validation (2026-10-12)

Twenty-two focused transport tests passed, including a real temporary two-Session
ledger/recheck composition, tuple-mixing refusal, canonical/attempt separation,
fast-Final order, multiple-attempt ambiguity, failed/truncated/duplicate/oversized
queries, alias conflicts and structured CLI rejection of non-scalar source fields.
The independent read-only review's two findings were fixed and regression-tested.
The complete runner suite passed **985 tests in 157.747 seconds**, with owned BCS/CLI
enabled and no failures/errors/skips. Case guard, strict OpenSpec and diff checks
passed. The legacy all-module singlebox coverage gate was not run.

On the unchanged real retained artifacts, the offline CLI generated exactly three
Send bindings with zero network calls and source/pool bytes unchanged. Importing
the already recorded platform READ denial as an explicit ERROR projection returned
UNAVAILABLE/PERMISSION_DENIED, exit 2, not empty successful evidence. The normalized
error mapping was operator-supplied; no platform success response or automatic
remote log adapter was exercised. This is not a second real log query or Story.
At that increment, the Observ access diagnostic still required operator renewal.
The subsequent renewed acquisition below supersedes that access status.


## Renewed Observ acquisition and dynamic discovery (2026-10-12)

After the operator renewed their identity, the live Observ diagnostic reported
ready with server identity verified (HTTP 200). This resolves the Observ credential
blocker, not the independent AntLogs application READ denial. No alternate identity
or credentials were substituted.

Fresh application/server discovery returned fifteen instances with no further page
and no truncation; exactly three were PRE/online. Each PRE instance's explicit
log-list call returned the requested target IP and message-log path. The initial
default application discovery had selected a production target; it was used only
as metadata and no production business logs were queried. Every subsequent
acquisition round refreshed the instance metadata and revalidated its log target.
Actual addresses and platform trace IDs remain private, not profile constants.

The frozen first search used eighteen exact attempt-ID plus bot_accept queries,
covering each Send's terminal minute and previous minute on the three discovered
PRE instances, with a five-row per-call limit. It returned three positive rows,
one for each known Send, and fifteen empty results. Empty results were not treated
as absence evidence. The query contract's minute-sized windows were recorded
explicitly, rather than represented as arbitrary timestamp ranges.

Two further, separately recorded three-query rounds inspected and decoded those
same positive records; cumulative log calls were twenty-four, not eighteen. Each
round rediscovered current instances. The first scalar extractor did not recognize
the content encoding and credited no identities. Actual content was HTML-escaped
JSON; decoding then JSON parsing recovered the reviewed scalar fields. Raw content
was handled in memory only, never printed or persisted; private evidence retains
scalar projections, content hashes, query echoes and source metadata. Observ grep
returns raw rows, so this manual path uses local projection, unlike the earlier
AntLogs server-side SELECT. It is not an implemented production log adapter.

All three final rows matched the requested source IP and discovered hostname,
message target, schema version, bot_accept event, accepted status and exact
Bot/Group/Session/attempt tuple. Their accepted Run aliases matched the three
canonical downstream Runs. Platform epoch seconds agreed with the event timestamps
interpreted in the deployment timezone; no millisecond precision was invented.
The existing offline matcher returned MATCHED for all three scalar WS_ACK records.
Its output correctly remains OFFLINE_SUPPLIED with source/environment NOT_VERIFIED:
actual CLI acquisition provenance is recorded separately, not certified by that
matcher or silently upgraded to a Case PASS.

This establishes successful WS acceptance for the three known attempts. It does
not identify a downstream engine/BaaS implementation, certify all historical
replicas/policy, close all producers or prove whole-Session quiescence. No HTTP
Provider query was needed to identify these three positive WS records. The original
sixteen retained Run files were rehashed unchanged. No business writes, Bot triggers,
stops, deletions, pool release or second Story occurred; quarantine remains.

Validation for this documentation/real-evidence increment: twenty-two focused
transport regressions passed (4.102s), strict OpenSpec validation and diff checks
passed. No runtime source changed, and the prior 985-test full runner result was
not rerun or represented as new evidence. Dynamic-discovery automated enforcement
and the remaining scope-closure work are still pending; Spec remains 29/54.


## Optional live WS collector: `retained-observ-ws`

The runner now provides a separate, explicitly authorized acquisition command.
`retained-transport` remains offline and backward-compatible. No Story imports
Observ; the CLI composition root selects this deployment-specific adapter. The
normalized projection/matcher remains the replacement boundary. Local/mock and
owned-runtime execution require neither Observ nor company network access.

The private binding uses schema_version 1 and contract
`observ-retained-ws-binding-v1`, with **exactly** these remaining fields:
`environment`, `source_environment`, `app_name`, `from_uri`, `to_uri`,
`log_directory`, `log_file`, `timezone`. Use the original Run environment, PRE as
source_environment, and application/entity URIs previously discovered through
Observ metadata. The from_uri is an application instance; to_uri is the discovered
server entity set. The binding contains no instance IPs or cookies. It is an
operator-supplied application/environment mapping, not proof of historical coverage.
This command rejects non-preprod profiles and source environments other than PRE.

```bash
python3 runner.py retained-observ-ws \
  --profile /private/path/original-profile.json \
  --state-dir /private/path/original-run \
  --run-id ORIGINAL_RUN_ID --confirm-run-id ORIGINAL_RUN_ID \
  --case RETAIN-GROUP-01 --observation-id EXISTING_SAVED_RECHECK_ID \
  --start-epoch "$MINUTE_ALIGNED_START" --end-epoch "$MINUTE_ALIGNED_END" \
  --log-binding /private/path/observ-binding.json \
  --observ-binary /absolute/path/to/observ \
  --output-dir /private/path/new-acquisition-id --allow-observer-read
```

The source plan is rebuilt and compared before/after each remote command and at
completion. Original Run/ledger/pool are not written. The output must be a new
absolute directory outside the original Run, with an existing canonical parent;
symlink paths and replay of a used acquisition directory are rejected. Intent and
partial evidence are saved privately before/after reads. Interrupted output cannot
be resumed to reset the same acquisition budget. A new explicit invocation is a
new round with fresh discovery; it does not merge partial rounds into complete
historical coverage or automatically renew authorization.

Bounds: 120 seconds total, at most 64 CLI reads including metadata, at most three
100-row instance pages, eight eligible instances, 2 MiB combined process output
per call, 64 KiB per raw log row and thirty projected rows total. Every query uses
an exact source-bound attempt ID plus bot_accept; six returned rows is an
incompleteness sentinel, never trimmed. The complete discovered-instance/minute/
Send query matrix must fit the remaining budget before querying logs. The caller
must choose an explicit minute-aligned half-open window in the past 24 hours, at
most 900 seconds. The collector never expands it silently or switches to Bot-wide
logs. Empty/duplicate/conflicting/truncated rows cannot yield TRANSPORT_OBSERVED.

Each invocation discovers every instance page first, selects PRE/online instances,
verifies the explicit log-list target and then freezes all query targets. Response
echoes and row source IP/hostname must match, and decoded WS ACK fields must match
the exact original tuple. JSON and HTML-escaped JSON are supported with duplicate
key rejection. Event time in the declared deployment timezone must equal the
platform epoch; ambiguous timezone timestamps reject. Raw text, headers, prompts
and arbitrary remote errors never enter saved receipts or CLI output. Process
errors, malformed output, deadlines and cancellation preserve BLOCKED/CANCELLED
receipts and partial evidence; none retries or selects an alternative identity.
An unreachable/replaced/mismatched target aborts the round. A subsequent authorized
round rediscovers rather than internally resetting budgets to retry stale IPs.

Exit 0 / TRANSPORT_OBSERVED means the independently acquired WS rows matched all
known Sends, not Case PASS or execution completion. Empty/partial/conflicting or
failed acquisition exits 2; cancellation exits 130. The nested matcher retains
OFFLINE_SUPPLIED / NOT_VERIFIED because it cannot certify its input provenance;
the enclosing acquisition receipt separately supplies live discovery and query
history. Scope/whole-execution status remain UNCONFIRMED and pool release false.
No automatic HTTP Provider fallback, policy inference, identity renewal, new Story,
Bot action or deletion is introduced. Source schema/pagination changes fail closed.

Contract tests exercise the real runner CLI and saved retained source artifacts
with a fake bounded process adapter, including instance changes across rounds,
PROD fallback rejection, pagination, malformed/oversized/incomplete output,
identity/time drift, source changes, cancellation, no replay and original-byte
preservation. Separate real-live validation is recorded below when executed.


### Collector implementation and real validation (2026-10-12)

Twenty-four focused collector tests passed (5.348s). Independent read-only review
found two issues, both corrected with regressions: a PRE profile could previously
select PROD through an unchecked source_environment, and malformed Unicode/deep
JSON could escape the safe BLOCKED receipt path. Review confirmed both fixes and
the later real-wire relation parsing correction. Initial test failures caused by
macOS's noncanonical temporary path and an unbound test alias were fixed in the
fixtures, not by relaxing output-path or alias validation.

The first real collector invocation issued one metadata read and rejected its
response: live relation records wrap instances in entityData rather than the flat
shape initially used by the fake. The adapter now validates the outer sourceUri /
targetUri / uri against the nested entity and configured application/entity set;
its fixture and wrong-parent/target tests use the observed relation shape. Existing
saved live metadata replay then selected the expected three PRE instances.

The next invocation freshly discovered three PRE instances, validated their log
paths and credited one ACK, then its tenth call timed out. It saved BLOCKED and
partial records without retrying, changing identity or querying another target.
One subsequent explicitly bounded operator revalidation preserved that failed
receipt and rediscovered instances from scratch. It completed **22 calls** (one
instance-page read, three log-path reads, eighteen exact WS queries), returned
**TRANSPORT_OBSERVED / MATCHED with three ACKs**, and exited 0. Across these three
collector invocations the actual call count was **33**, within the recorded
64-call operator revalidation ceiling. This is a new read-only acquisition, not
a resumed business Run; no failed acquisition was rewritten as successful.

Each live attempt rehashed the original sixteen Run files unchanged. No Cookie
resolution, DB query, business write, Bot trigger/stop, delete, second Story or
pool release occurred. The final receipt still records scope/execution UNCONFIRMED
and release=false. It proves the new collector's live WS path, not whole-Scope
completion, HTTP Provider coverage or release qualification.

The final complete runner regression passed **1009 tests** with owned BCS/CLI
enabled and no failures/errors/skips. This was rerun after the real-wire fix; the
prior 1008-test pass was not reused as validation of that correction. Case guard,
strict OpenSpec and diff checks passed. The legacy all-module singlebox coverage
gate was not run. Tasks 3.3/10.2 and overall Spec remain incomplete at 29/54:
the collector adds positive WS evidence, not the remaining producer-closure proof.

### Instance refresh regression (2026-10-13)

Every new acquisition must rediscover PRE instances, including when the previous
round succeeded. A focused regression now pins the failure-after-success case:
empty rediscovery blocks without any log query, cached IP reuse, or modification
to the prior receipt. Current topology remains `historical_coverage=NOT_VERIFIED`;
no-log results cannot prove that retired instances had no activity. Mid-round
target failure preserves partial evidence and ends that round; an explicitly
authorized new round rediscovers under a new bounded plan rather than silently
retrying old hosts. This is local contract validation, not a fresh live inventory.
