> **2026-10-11 plan revision:** the approved next increment is the serial,
> default-retain observation subset described in `retained-pilot-plan.md`.
> The separate supervised gateway `retained-run` attachment is implemented;
> required reviews and limits are in `retained-remote.md`. Terminal observation
> and live qualification are still pending. Remote PRE-01/02/03
> remain blocked; historical task 9.2 decisions below are not the new roadmap.

# First preproduction pilot: explicit remote command composition

This guide covers the same PRE-01/02/03 Web API Stories used locally. It does
not authorize any real environment, create a Human/Bot identity, or qualify a
release. Production/cloud profiles and automatic remote recovery are not enabled.

## What is implemented, and what still needs real confirmation

`prepare --observer-binding` freezes an environment-owner review receipt.
`run --mcporter-binary --allow-observer-read` constructs the existing bounded
MCP client and supplies it to the same Story dispatcher. The ordinary profile
alone does not silently launch a credentialed tool.

The initial implementation uses **operator-attested mapping**, permitted by the
spec's environment-owner confirmation path. This is not a live configuration
probe or a digital-signature approval service. The runner checks the review
record, target, complete profile digest, validity interval and frozen integrity.
It **does not** fetch the referenced evidence, authenticate the named reviewer,
verify the evidence hashes against their source, or prove that the deployment
has not changed since review. Do not describe `BOUND` (the internal provider
admission result) as live verification. Reports say `OPERATOR_ATTESTED` and
`live_verification=NOT_PERFORMED`.

Only an authorized reviewer who has actually compared the independent sources
should issue this receipt. If automatic live verification is required by an
environment, this manual mode is insufficient; use an approved embedding
integration or implement the environment-specific verifier first.

## 1. Prepare the real environment outside the runner

The environment owner/operator must arrange:

- A verified preproduction API origin, route bindings, expected BCS `env`,
  deployed commit and BaaS/signed-file-URL behavior.
- One legitimate Human session and two distinct, dedicated Bots owned by that
  Human. First-pilot friendship is preserved, not created or required. Avoid
  operational Bots and unintended notification/model/channel side effects.
- Human auth **environment-variable references** in the private profile;
  credentials are supplied by the approved local login/secret process. Do not
  paste credentials into commands, source control, receipts or chat.
- The configured MCP server/database and authorization for the three fixed,
  current-Case group/session/file projections. No direct DB write permission is
  needed or supported. The selected `mcporter` installation/configuration and
  its credential environment belong to the operator; they are not discovered
  or modified by the runner.
- A dedicated fixture pool used from this one local OS account/machine only,
  write approval, resource budgets and a named retention/failure-cleanup owner.
  A local lease is not multi-runner fencing or server-side authorization.

`preprod.example.json` is a template, not an approved target. Keep resolved
profiles and review records in private files outside version control.

## 2. Record the independently reviewed mapping

Use the offline `plan` command to obtain `profile_digest` for the **final,
validated** profile. This step contacts no server:

```bash
python3 "$RUNNER/runner.py" plan --profile "$PRIVATE_PROFILE"
```

Review the actual deployed BCS DB configuration and the actual MCP datasource
mapping. Record separate evidence references/hashes that an environment owner
can later resolve. The evidence must establish the same physical database and
`env` partition as the API target; table/column existence alone is insufficient.
Do not copy raw service configuration, secrets or authenticated URLs into the
receipt. References are bounded identifiers (for example, internal review-record
IDs), not URLs or filesystem paths. Hash sanitized, approved evidence snapshots
and retain those snapshots through the organization's existing review process.

Receipt shape (all angle-bracket values and zero timestamps below are deliberately
**invalid placeholders**, not an executable approval):

```json
{
  "schema_version": 1,
  "verification_mode": "OPERATOR_ATTESTED",
  "receipt_id": "<review-record-id>",
  "reviewer": "<authorized-reviewer-id>",
  "reviewed_at": 0,
  "expires_at": 0,
  "profile_digest": "<64-lowercase-hex-from-plan>",
  "target": {
    "environment_id": "<profile-environment-id>",
    "api_origin": "<profile-entrypoint-origin>",
    "server": "<profile-observer-server>",
    "database": "<profile-observer-database>",
    "env": "<profile-expected-bcs-env>"
  },
  "deployment_commit": "<40-lowercase-hex-observed-by-reviewer>",
  "source_reviews": {
    "deployment_config": {
      "reference": "<deployment-evidence-record-id>",
      "sha256": "<64-lowercase-hex-of-reviewed-evidence>"
    },
    "mcp_datasource": {
      "reference": "<distinct-datasource-evidence-record-id>",
      "sha256": "<64-lowercase-hex-of-reviewed-evidence>"
    }
  },
  "query_authorized": true
}
```

Use integer Unix seconds for the review interval. The reviewer chooses an
appropriate expiry; the runner requires `reviewed_at <= now < expires_at`.
The profile must separately declare `environment_binding_confirmed=true`; that
boolean alone is never sufficient. Changing any profile field requires a review
record for the new digest. `deployment_commit` is an operator-recorded fact,
not a live version response from the API.

A still-valid record can be reused for successive new Runs using the same
profile. If deployment, datasource routing or authorization changes, stop using
the old receipt immediately and re-review it; timestamps cannot detect such a
change. The runner does not invent or renew approvals.

## 3. Check both targets before reserving a Run

Once the receipt exists, explicitly check API identity/actors and the required
MCP schemas before preparing a Run:

```bash
python3 "$RUNNER/runner.py" check \
  --profile "$PRIVATE_PROFILE" --allow-read \
  --observer-binding "$REVIEW_RECEIPT" \
  --mcporter-binary "$MCPORTER" --allow-observer-read
```

This creates no Run directory, ledger, fixtures or pool reservation. It first
validates the input review and explicit executable, then reads the API environment,
Human identity and two Bots. A failed HTTP discovery prevents all MCP tool calls.
After successful HTTP discovery, MCP admission performs at most five metadata
calls: database list, table list and the three required table descriptions.
**No business-row query is allowed**, even if an observer accidentally requests
one during admission. The receipt is checked before each tool and after the final
metadata call; expiry or input changes during the check cause a blocker.

The result remains `ready_to_execute=false`. `DISCOVERY_PASSED` plus
`observer_preflight.status=METADATA_ADMITTED` only means these read checks passed
under the recorded operator attestation. It does not prove write permissions,
BaaS/file URL behavior, business-row read permission, quotas, cleanup or current
deployed SHA. A failed metadata call is a sanitized `BLOCKED`, not an empty
successful result. Cancellation retains exit 130. Ordinary `check --allow-read`
without these three observer options keeps its previous HTTP-only behavior.

This input-file check is not a substitute for Run preparation: `prepare` freezes
a new copy, and each actual Story still performs its own admission. Do not treat
a saved discovery result as permanent authorization.

## 4. Run the entire pilot in one foreground command

For an approved environment, `remote-run` performs the reviewed read-only check,
freezes a new Run, executes PRE-01/02/03 in order, and writes the reports. It
requires a **nonexistent** output directory; it never resumes or replaces one.

```bash
python3 "$RUNNER/runner.py" remote-run \
  --profile "$PRIVATE_PROFILE" \
  --observer-binding "$REVIEW_RECEIPT" \
  --mcporter-binary "$MCPORTER" \
  --state-dir "$STATE_DIR" --run-id "$RUN_ID" \
  --allow-read --allow-observer-read \
  --allow-write --confirm-run-id "$RUN_ID"
```

All three permissions are explicit: HTTP reads, MCP metadata/current-Case reads,
and Story writes. Inputs, review scope, executable and the fixed three-Case
inventory are checked before either target is contacted. A failed preflight
prevents preparation and Story writes. The binding frozen by preparation must be
the same one that passed preflight. Each actual Case still rechecks its admission.

The command stops on the first non-PASS Case or cancellation. Existing Story
`finally` blocks perform their exact API cleanup; the orchestrator never invokes
standalone remote recovery, repairs fixtures, releases another Run's lease or
starts/stops any target process. It uses existing **per-Case** pool ownership,
not full-suite fencing: if another Run acquires the pool between Cases, the next
Case blocks rather than stealing it. The explicit MCP tool subprocess is the
only external command selected by this remote path.

Artifacts in the new private output directory:

- `profile.json`: validated profile/credential references, not resolved secrets.
- `preflight.json`: discovery result when preflight returned.
- `run.json`, ledger, manifest and frozen mapping receipt: when preparation
  reached the corresponding checkpoint.
- `reports/summary.json`, `summary.md`, `junit.xml`: after preparation successfully
  returned, even when subsequent Cases fail or are cancelled (unless report
  persistence itself fails).
- `remote-run.json`: orchestration outcome and all three Case IDs, including
  NOT_RUN entries. It is written only after this process claimed the directory;
  failed initial input validation or a lost directory-creation race writes nothing
  into an unowned directory and returns a blocker to stdout instead.

`REMOTE_PILOT_PASS`/exit 0 requires all three returned Case outcomes plus a valid
`PILOT_PASS` summary and successful report/receipt publication. It never grants
full release qualification. A report, checkpoint or receipt error prevents
success even if individual API assertions passed. A Case interrupted before its
executor returns is marked CANCELLED/BLOCKED with UNKNOWN outcomes in the
lifecycle receipt; a partial saved Case report alone is not proof that checkpoint
and lease finalization finished. `prepared=false` is **not** a claim that no pool
was acquired: `preparation_status=UNCONFIRMED` requires inspection of partial
preparation artifacts and the reservation, not automatic cleanup.

Ctrl-C and SIGTERM stop new work, preserve the existing Case cleanup/quarantine
semantics, write available reports, and restore previous handlers. Exit 130 means
cancellation; exit 2 means blocked/failed qualification or finalization. Repeated
signals do not repeatedly interrupt finalization. A pre-existing failed Case
remains the primary result. Report/receipt failures are sanitized and never
silently retried. SIGKILL cannot run finalizers; retain the partial Run for manual
reconciliation. If a propagated MCP cancellation carries
`child_shutdown_status=UNCONFIRMED`, the lifecycle failure retains that warning
about the locally spawned tool child; the orchestrator does not retry its cleanup
or confuse it with stopping a remote service. No foreground command can guarantee evidence after hard kill or
unwritable storage.

## 5. Alternatively, execute individual lifecycle steps

The variables below must refer to actual operator-selected paths/values.
`RUNNER` is this directory, `PRIVATE_PROFILE` is the resolved private profile,
`REVIEW_RECEIPT` is the reviewed file above, `STATE_DIR` is a fresh private Run
directory, and `MCPORTER` is an absolute executable path. Placeholders from this
guide are not ready-to-run values.

```bash
python3 "$RUNNER/runner.py" prepare \
  --profile "$PRIVATE_PROFILE" \
  --run-id "$RUN_ID" --state-dir "$STATE_DIR" \
  --observer-binding "$REVIEW_RECEIPT"

# Execute only after prepare succeeded and the environment's write approval exists.
# Stop this loop immediately on a failed, blocked or cancelled Case.
for CASE in PRE-01 PRE-02 PRE-03; do
  python3 "$RUNNER/runner.py" run \
    --profile "$PRIVATE_PROFILE" \
    --run-id "$RUN_ID" --state-dir "$STATE_DIR" --case "$CASE" \
    --mcporter-binary "$MCPORTER" --allow-observer-read \
    --allow-write --confirm-run-id "$RUN_ID" || break
done

# Offline: no MCP executable, credentials or read permission needed to render.
python3 "$RUNNER/runner.py" report \
  --profile "$PRIVATE_PROFILE" --state-dir "$STATE_DIR" --format all
```

Preparation is offline and does not validate live services. It reserves the pool,
freezes `observer-binding.json` at mode 0600, and records its canonical digest in
`run.json`. Existing Run/receipt artifacts are preserved, not overwritten. The
external source file can subsequently change without changing the frozen Run.

Execution requires both explicit MCP selection/read permission and the existing
write confirmation. No shell command/profile plugin is imported, no fallback
identity is selected, and no service is built, started or stopped remotely.
Before **every** MCP metadata/business call, the frozen review is rechecked.
The profile response budget also caps combined MCP stdout/stderr and must be at
least 1,024 bytes for this client; smaller budgets are rejected during preparation,
never silently raised. The profile timeout is the per-tool execution deadline;
the existing bounded subprocess cleanup allowance remains separate.
MCP tool operations, query projections, environment/exact-ID filters and limits
remain those of the existing bounded client. Case ledger scope is independently
checked before/after semantic observations. No extra product DB queries are used
to infer a mapping.

Missing/expired/mismatched review or command arguments fail before client
construction/Case writes. Metadata/schema/admission failure creates a BLOCKED
Case with no business intents. If the review expires after resources were
created, the next MCP call is refused; required evidence remains non-success,
but API cleanup still runs and the failure reservation is retained. Cancellation
is not retried and quarantines the Run through the existing lifecycle.

## 6. Interpret results and handle failures

- `PASS` is the selected Story's actual result, including its required evidence
  and cleanup. The full three-Story denominator is retained in summary reports;
  a missing PRE-03 is not a successful full pilot.
- JSON, JUnit and Markdown retain the frozen receipt digest, validity interval,
  manual provenance and **declared** deployed commit. They omit private reviewer
  and source-reference details; those remain in the private receipt.
- `target.deployment_version` stays `unknown`: a reviewed SHA is not a live probe.
  `full_release_status` stays `NOT_QUALIFIED`; three Stories do not cover the
  legacy suite. Missing optional remote logs remain visible diagnostics.
- Historical replay validates receipt integrity but does not re-run admission or
  reject past results just because the review expired today. New execution does
  reject expiry. Editing a Case report's receipt binding blocks integrity.
- Normal in-Story cleanup uses exact registered IDs and supported APIs. It does
  not prove history purge or physical storage erasure. **Standalone destructive
  remote recovery remains unsupported.** Do not use DB deletion, bulk cleanup,
  force-unlock, modified receipts or a new Run to conceal unresolved resources.
  Retain the ledger/reports and involve the environment's cleanup owner.

The command-to-provider conformance tests use synthetic Gateway responses and
MCP process replies. They are not real preproduction qualification. Real approval,
identity, routing, BaaS behavior and two consecutive preproduction Runs still
require their own retained evidence.


## Known file-capability contract gap

The initial planning document mentions querying file capabilities in PRE-03.
The actual V1 contract currently **excludes**
`/api/v1/collaboration/sessions/{session_id}/files/capabilities`; the existing
endpoint is legacy `/sessions/{sid}/files/capabilities`, protected by Session
membership. The contract test `tests/openapi/test_session_file_v1_contract.py`
explicitly asserts the V1 route's absence. This is not a prefix typo.

The runner does not call the legacy endpoint as a fallback, probe an operational
Session, or pretend the capability read passed. The current PRE-03 manifest
validates backend/metadata/bytes through the actual file lifecycle. Its success
is not evidence of a standalone capability-read check, maximum configured upload
size, or preflight BaaS health. The legacy CLI capability assertion remains part
of the unmigrated full file Story. Resolving the planning discrepancy requires an
explicit pilot-scope clarification or separately approved V1 product contract;
this increment neither changes that contract nor silently adds a bypass.

## Native direct-service identity is an explicit binding

A web page origin, a V1 API gateway origin and a direct BCS origin are different
bindings. Do not derive their paths or authentication compatibility by adding a
prefix or following a redirect. For an approved direct BCS target, the Profile
can explicitly select the service's native identity contract:

```json
"identity_binding": {
  "method": "GET",
  "path": "/auth/user",
  "response": "plain-user-info"
}
```

This binding is accepted only with `entrypoint.kind=bcs-direct`. Native identity
returns a plain object with a bounded, nonempty string `user_id` at the top level;
it is not `{code, data, ...}`. Preflight and the one-operation identity probe
compare that returned ID to `human.user_id` before reporting identity success.
The probe emits request metadata, not the returned profile/name/avatar or auth
headers. HTML, a V1 envelope, a missing/malformed ID, 401/403 or a redirect is a
failure, not a reason to try a second route or identity.

Existing profiles without `identity_binding` keep their prior behavior and
fingerprint: remote identity uses `/openapi/v1/auth/user` with the V1 envelope;
owned-local auth uses its native `/auth/user`. The example remote profile is a
V1 template, **not evidence of any deployed company's route table**. Configure
from the target's deployment overlay and verify the selected identity endpoint.
The offline plan displays the selected identity path and response contract.

Changing this field changes the complete Profile digest, invalidating an old
Run/mapping receipt. Prepare a newly reviewed Profile/Run rather than editing a
frozen one. Selecting native identity changes **only identity discovery**: it
does not rewrite Bot/Group/Session/File/WS paths, bypass gateway principals, prove
Cookie acceptance on V1 business endpoints, or map a frontend/backend Bot ID to a
BCS UUID. Those contracts must be checked independently before business writes.


### Current native-identity execution boundary

The explicit native binding is **discovery-only**, not a native Story adapter.
`plan.execution_admission` is `BLOCKED` for this binding. `prepare`, `run` and
`remote-run` reject it before creating Run artifacts, reserving the fixture pool,
composing an observer or executing a Story. Direct Python Case entrypoints apply
the same check, including when a caller supplies an adapter factory. The existing
owned-local path and profiles without this opt-in retain their prior behavior;
`NOT_EVALUATED` in their offline plan is not execution permission.

The one-operation `probe http --operation identity.read` remains available.
`check` can verify environment and Human identity, but then records
`route.business_adapter=BLOCKED` before Bot requests or MCP metadata. A separate
Bot probe also refuses this combination before sending a request. No business
route is rewritten and no credential, signing key or mock principal is generated.
An invalid environment or identity still stops the check first.

The source-level distinction to review against the actual deployment is:

| Surface | Existing pilot contract | Native direct-service contract |
| --- | --- | --- |
| Human identity | V1 identity envelope | Plain `/auth/user` response |
| Bot / Group / Session | V1 DTOs and gateway-signed principal | Legacy paths, DTOs and per-operation auth |
| Session WebSocket | V1 session connection token and session WS | Native human WS has a different authentication/protocol contract |
| Files | Internal V1 file API and scoped file lifecycle | Legacy file API, membership checks and storage capabilities |

This table records adapter boundaries, **not a deployed route inventory**. A
checked-in configuration or source revision is not deployment verification.
Identity success alone proves none of the other rows. In particular, some legacy
Session-list GET handlers can create/reactivate a default Session: HTTP GET is
not sufficient evidence that an operation is safe for read-only preflight.

To continue using direct BCS, implement and test native operation bindings for
the same Story invariants, including actor ID mapping, ownership, WS, initial
Session side effects, file contracts and exact cleanup. Use an owned local
runtime for contract regression first; retain this admission blocker until the
native adapter is implemented. Do not make a second copy of the Stories or
remove required assertions. Using a gateway instead requires an explicit,
independently verified gateway binding; it is not an authentication fallback.

## Browser-equivalent gateway selection and Cookie context

Choose the deployment's **assembled frontend** contract: the public frontend
controllers plus its internal deployment overlay, at matching revisions. Do not
use a legacy frontend's proxy table, an arbitrary local configuration, or a Bot
onboarding endpoint as a replacement. Checked-in routing establishes intent,
not the deployed version or successful authentication.

For a V1 gateway frontend, the runner profile selects `entrypoint.kind=gateway`
and its reviewed gateway origin. Keep these distinct routes (no global prefix
rewrite):

- Identity: `GET /openapi/v1/auth/user` (V1 envelope).
- Group and Session: `/openapi/v1/collaboration/...`.
- File: `/api/v1/collaboration/sessions/{session_id}/files...`.
- Session connection token: `POST /openapi/v1/collaboration/sessions/{session_id}/token`.
- Session WS: `/openapi/v1/collaboration/messages/ws?token=...` at the configured
  gateway, not a direct-service onboarding address.

Remove an explicit native `identity_binding` only when **deliberately selecting
this independently reviewed gateway profile**, never as an error fallback.
Existing native profiles retain their execution blocker. This is an API/WS E2E
path; it does not exercise browser rendering or the browser hosting proxy.

A Cookie-based gateway can require the genuine frontend's browser context for
SSO admission. Configure a non-secret, explicit profile field such as:

```json
"browser_origin": "https://workbench.example.test"
```

Only Cookie-authenticated gateway profiles accept it. The bounded origin must
have no credentials, path, query, whitespace or controls; remote origins require
HTTPS. The runner derives `Origin` and an origin-only `Referer`, sending both
only with authenticated requests to the product origin. These headers do not
select the destination, confer permissions, disable TLS checks, follow redirects
or establish SSO allowlist acceptance. Storage and token-only shared downloads
receive neither these headers nor the Cookie. Omitted context preserves older
profiles unchanged; adding it changes the frozen Profile digest.

Use `probe http --operation identity.read --allow-read` first. Only this
single-operation identity probe permits unresolved unrelated Bot/environment/DB
inputs; its endpoint, Human and authentication references must be resolved. The
full `check`, `prepare`, `run` and `remote-run` admission contracts remain strict.
Identity success neither verifies Bot ownership nor admits business writes.
Keep short-lived Cookies in an operator-private file, load the designated
environment reference only for the runner process, never put their values in
arguments or reports, and remove the temporary credential after use.

## Internal gateway identity after explicit Human bootstrap

Do not assume that an assembled internal frontend activates the public OAuth
controller just because the controller exists in its source tree. An internal
login strategy can obtain presentation identity from its hosting platform and
call `bots/mine` during workspace startup. The latter is **not** a read-only
preflight operation: the service may create/backfill a Human registry record.
Treat such first-time bootstrap as a separate, explicitly authorized fixture
operation. Never silently call it from `check` or infer authentication from a
Cookie's user-id field or a UI fallback such as `me`.

For a deployment whose reviewed V1 Bot contract enforces the authenticated
User's exclusive Human perspective, a Profile may instead explicitly select:

```json
"identity_binding": {
  "method": "GET",
  "path": "/openapi/v1/collaboration/bots/{human_actor_id}/candidates?offset=0&limit=1",
  "response": "authorized-human-view"
}
```

This is a finite **gateway-only** contract. The runner resolves the target to
`human_<expected-user>` and sends a bounded GET using the ordinary credentials,
with no identity/principal injection. The runner first reads the exact Human actor and requires matching actor ID,
`kind=human`, owner and environment; a prefix alone is not sufficient. The
server must require an authenticated User, load an **existing** Human actor,
and authorize that perspective only when
its ID equals `human_<caller-user>`. This is an authorization proof, **not a
response containing the caller's user_id**. Reports explicitly label
`identity_basis=server-authorized-human-view` and
`identity_returned_directly=false`; candidate rows are discarded, not used as
identity data or saved in reports. A strict successful envelope and bounded page
are required. Wrong-owner/unauthenticated/missing-Human errors stop admission;
no creation, alternate route, pagination, or credential fallback occurs.

Selecting this contract does not waive full preflight, pool exclusivity, observer
review, server-side write authorization or cleanup. Do not enable it against an
implementation with different Human-perspective authorization semantics. Old
profiles retain their previous defaults; native direct identity remains
separately discovery-only. Private profiles should select the reviewed contract
before preparing a fresh Run, rather than changing a frozen one.

The Human-record check and perspective authorization are fresh but are not one
atomic server assertion. This adapter relies on the reviewed server contract;
it does not introduce a new server-enforced identity or resource-isolation
boundary. `limit=1` bounds returned candidates, not the server's friendship
lookup or candidate-count work. Only request evidence is retained.

## Explicit owner confirmation of a shared database

Some environments intentionally share a physical database between preproduction
and production. A database alias is not an environment boundary, and successful
metadata discovery alone still never admits a business-row query.

When the actual environment owner explicitly confirms the API-to-MCP database
mapping and authorizes this pilot's scoped observations, a receipt may instead
use `verification_mode=OWNER_CONFIRMED_SHARED_DATABASE` (schema version remains
1). This is a **different provenance contract**, not a fabricated independent
configuration review. Keep all common receipt fields, but:

- Set `deployment_commit` to `unknown`; this mode does not verify deployment
  provenance or qualify a release version.
- Replace `source_reviews` with `owner_confirmation` containing:
  `reference` (the actual owner statement's bounded record ID), `sha256` (its
  retained sanitized snapshot), `database_sharing=preprod-and-production`,
  `mapping_confirmed=true`, `tables_without_env_excluded=true`,
  `read_scope=current-case-resource-id-and-env`, and `allowed_assertions` (an
  explicit subset of `group.persisted`, `session.persisted`, `file.persisted`).
- Retain `query_authorized=true`, reviewer, complete profile/target binding and
  integer validity timestamps; this confirmation lasts at most one hour.

Record the owner's real statement rather than inventing two independent sources
or a deployed commit. The runner validates the receipt but does not authenticate
the reviewer or retrieve the source. JSON/JUnit/Markdown disclose the owner basis,
`independent_config_review=NOT_PERFORMED`, `live_verification=NOT_PERFORMED` and an
unknown deployment. No old receipt is silently upgraded to this mode.

Business queries still require **both** a fresh Case-owned resource ID and the
reviewed `env`, matching the fixed projection and two-row limit exactly. Existing
Case ledger/parent-chain validation remains mandatory before provider calls.
The three approved tables must expose the required `env` columns during schema
admission. A missing column, unknown table, broader filter, different env or
undeclared assertion blocks; no fallback scans a shared table. Tables without
reviewed environment isolation are excluded, not treated as safe by assumption.
Database access remains read-only; cleanup exclusively uses the product APIs for
exact Run-owned objects. The fixture pool still requires explicit exclusive use.

## Execution safety hold: implicit normal-group bootstrap

**Remote PRE-01/02/03 execution is currently blocked before resource creation.**
Their shared normal Chat group setup can implicitly create a Session and dispatch
bootstrap `chat.send` to its driver and `chat.inject` to other Bots. It may also
create a subscription relation for a public Bot. These effects are not covered
by the original no-chat pilot scope, and successful resource deletion alone does
not prove that queued delivery or Bot execution has stopped.

A successful HTTP/MCP discovery report therefore does not authorize these writes.
The real remote adapter refuses execution before client construction or creation
intent. The Case report says `BLOCKED`, `functional=NOT_RUN`,
`cleanup=NOT_ATTEMPTED`, with an effect-admission reason; no group/session was
created. Its quiescence is confirmed only because execution never started.
Direct remote `group_create` also refuses. There is no profile boolean bypass.
Owned local runtime and explicit synthetic test adapters remain separate from
persistent remote admission.

Lifting this hold requires either a verified product contract that suppresses
bootstrap **before creation**, or an explicitly approved expanded Story scope
with termination/queued-delivery evidence and any relationship effects accounted
for. Do not use a different collaboration strategy, silence a Bot, alter shared
service policy, or infer termination from DELETE/404 just to make CRUD pass.

## Independent native sessionless group slice

`sessionless-run` is a separate **NATIVE-GROUP-01** experiment, not a replacement
or PASS for PRE-01/02/03. It uses native `/groups`, explicitly sets
`create_initial_session:false`, and never calls Session list/create, chat, WS,
file or Bot/relationship mutation APIs. The existing remote bootstrap block is
unchanged. The old singlebox/coverage entrypoints are unchanged.

Inputs are private files, outside version control:

- A native discovery profile: `bcs-direct`, `manifest_face:legacy`, explicit
  `GET /auth/user` / `plain-user-info`, **Cookie-only** auth, dedicated/exclusive
  owned Bots. No browser-origin header or Gateway fallback. Existing public-v1
  route fields remain discovery-only; the slice owns its finite native routes.
- `--source-binding`: schema version 1, contract `native-sessionless-group-v1`,
  complete `profile_digest`, exact `health_version`, full `ocb_commit` and
  `avernet_commit`, `source_review_sha256`, integer `reviewed_at`/`expires_at`
  with at most one-hour validity. `--source-review` is the matching reviewed
  source evidence file. A repository gitlink alone is not deployment evidence.
  `/health` must match the reviewed build **before each write**; this is a
  service self-report + source review, not signed binary attestation.
- The existing owner-confirmed shared-database receipt, explicitly bound to
  this native profile and its API origin, with `group.persisted` permission.
  The runner cannot renew expired receipts or infer authority from table existence.
- Explicit MCP executable and HTTP/MCP/write authorization, as for the pilot.

```bash
python3 runner.py sessionless-run \
  --profile "$PRIVATE/native-profile.json" \
  --source-binding "$PRIVATE/native-source-binding.json" \
  --source-review "$PRIVATE/native-source-review.md" \
  --observer-binding "$PRIVATE/native-db-binding.json" \
  --mcporter-binary "$MCPORTER" \
  --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --state-dir "$PRIVATE/runs/$RUN_ID" \
  --allow-read --allow-observer-read --allow-write
```

The runner rechecks identity/environment/build and both Bots' native
`bot_uuid/actor_kind/created_by/env/status/capabilities.visibility`. Both must
be `protected`; public Bots could cause implicit subscription edges. Missing
or unexpected fields fail closed. Create supplies no custom ID (the service's
custom-ID path is an upsert). Only Group + group-participant rows are expected;
normal group quota, indexes/cache, logs and metrics still change. Exclusive
use is an operator promise, not a server-side lock against manual activity.

Lifecycle: shared single-machine pool reservation → read-only admission →
durable one-Group intent → create → exact participant/owner readback → rename
and readback → scoped DB observation → exact API delete → API 404 + exact DB
absence. Native create/read/PATCH have different response DTOs and are asserted
separately. Latest-running-session null is not proof that all historical
Sessions are absent; suppression assurance comes from the reviewed build and
explicit create response. Never call Session list to verify absence.

**Native DELETE limitation:** the reviewed legacy handler authorizes a query
actor but does not bind that actor to the Cookie. This slice rechecks the real
Human identity, uses only `human_<authenticated ID>`, and validates the exact
Run ledger plus originator/owner/participants before deletion. These are runner
misuse guards, **not a service-side scoped credential or authorization guarantee**.
This limitation precludes claiming production readiness. No Bot impersonation,
DB DELETE, prefix cleanup, ID adoption or blanket relation cleanup is allowed.

Outputs: `sessionless-report.json`, `sessionless-junit.xml`,
`sessionless-summary.md`, `ledger.sqlite`, and `sessionless-state.json`. The
one-case denominator and full-release NOT_QUALIFIED are explicit. Functional,
DB evidence and cleanup outcomes remain independent. DB reads use only the
existing fixed `bcs_groups` projection + exact Group ID + env, LIMIT 2; the
second read checks absence after deletion, not a broad scan.

A lost create response, unexpected Session, assertion failure, cancellation or
uncertain delete retains/quarantines the pool; **no automatic rerun or cleanup**.
Inspect this independent ledger and reconcile the exact resource before a
separately reviewed recovery. Standard `release`/`cleanup` are not recovery
commands for this independent state format. A clean run releases its pool.

Validation checkpoint (2026-10-10): one explicitly authorized native sessionless
preproduction attempt completed with functional PASS, scoped database evidence
PASS, cleanup PASS and pool RELEASED. The two fixed-resource database reads
verified creation and subsequent absence; no database writes were used. Private
Run IDs, service endpoints, identities, source-review receipts and HTTP/MCP
artifacts remain outside Git. This checkpoint does not qualify the original
three-Case pilot or full release suite. Runner regression: 615 tests passed,
including owned local BCS/CLI integration, with no skips. The old unified
singlebox coverage gate was not rerun against the persistent/shared environment.

Stability checkpoint (2026-10-10): a subsequent explicitly authorized batch ran
NATIVE-GROUP-01 three times serially against the same reviewed deployment, using
the same runner source and profile digests. All three attempts passed without
retry (46s, 44s, 51s; timings are not a benchmark). Each used a distinct Run ID,
Group ID and ledger, verified the scoped persisted Group row, then verified API
404 and database absence after API deletion. Each ledger ended deleted and each
pool reservation was released before the next attempt; final pool state was
AVAILABLE. The batch performed 54 bounded HTTP requests and six exact-resource
business-row reads, plus observer metadata admission. Private per-run reports
and a separate batch summary preserve the original results. No framework code
or credentials changed during the batch. This is initial repeatability evidence
for the independent group-only slice, not long-term stability, Session/WS/file
coverage, production isolation, or full-release qualification.

## Task 9.2 route freeze (2026-10-10)

The read-only Session/Bot effects audit is complete. Route decisions are now
frozen in the OpenSpec worktree:

- `NATIVE-GROUP-01`: existing `NO_EXECUTION` control-plane slice; already
  validated in preproduction and three serial repeatability rounds. It remains
  a separate denominator.
- `PRE-01` through the current V1 normal-group route: `BLOCKED` until a
  product-reviewed pre-dispatch suppression contract covers both the implicit
  group Session and explicit Session creation. Runner does not guess a request
  field.
- `PRE-02` Session/WS: future `BOUNDED_EXECUTION` candidate, currently
  `BLOCKED`; current abort, delivery-cancel and deletion contracts do not prove
  full execution/queue convergence.
- `PRE-03` files: independently `BLOCKED` for remote rollout until file
  completion Inject/queue/share propagation and storage/notification cleanup
  are covered; Session/WS acceptance does not automatically admit files.

The product dependency contract is recorded in
`openspec/changes/bcs-multi-environment-e2e/product-dependency-session-execution.md`.
It is not a product API change and does not grant remote write authority. The
2026-10-11 retained-observation plan supersedes this historical implementation
order for its limited subset. Current commands remain held until that admission,
observation and reporting path is implemented and tested; this document does not
authorize bypassing the hold.
