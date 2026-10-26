# Separate persistent environment facts from one-shot read permission

## What changed

The original `observer-binding.json` v1 combined two different things:

1. an owner's confirmation of which environment/API and MCP database correspond;
2. a time-limited permission to read data for a test.

Its one-hour limit was a **runner-local rule**, not an alisddbquery platform
approval, database credential expiry or Cookie lifetime. Repeatedly asking an
operator to restate stable database mapping because of that local timer is not
necessary for continuously running environments.

The new path separates these concerns. It is deliberately limited to the existing
**database-only retained recheck**. Legacy receipts, new business execution,
cleanup, pool release and the separate native `policy-check` command are not
silently migrated or given new permissions.

| Input/control | Meaning | Lifetime |
| --- | --- | --- |
| `environment-database-mapping-v1` | Owner-attested target mapping facts; no SQL permission, credential or deployment proof | Until revoked or changed; no hourly expiry |
| `retained-source-read-plan-v2` | Exact original Run/Case/Profile/evidence, selected projections and execution budget | One observation ID; source/plan drift invalidates it |
| `--allow-observer-read --confirm-run-id` | Explicit operator/authorized automation invocation of that bounded read | This invocation only; not permission for writes or another scope |
| MCP/platform authentication | Actual service-side access control | Controlled by the platform, not these local files |

This avoids a separate manual one-hour approval renewal for every readonly
recheck. It does not remove runtime deadlines or permit endless queries. The
local files/CLI flags are operator controls, not signed capabilities or protection
against a hostile process that can edit all local code and files.

## Persistent mapping confirmation

Record a real environment-owner statement once, in a private file. This example
is intentionally not executable until actual facts are supplied and confirmed:

```json
{
  "schema_version": 1,
  "contract": "environment-database-mapping-v1",
  "mapping_id": "reviewed-preprod-mapping",
  "revision": 1,
  "status": "PROPOSED",
  "confirmed_at": 0,
  "confirmed_by": "<actual-environment-owner>",
  "target": {
    "environment_tier": "preprod",
    "environment_id": "<profile-environment-id>",
    "api_origin": "<profile-api-origin>",
    "server": "<profile-mcp-server>",
    "database": "<profile-database>",
    "env": "<profile-expected-bcs-env>"
  },
  "database_sharing": "preprod-and-production",
  "evidence": {
    "reference": "<actual-owner-statement-reference>",
    "sha256": "<actual-evidence-sha256>"
  }
}
```

For a real confirmed mapping, record its actual confirmer/time/evidence and use
`status=ACTIVE`. The only sharing choices are `preprod-only` and
`preprod-and-production`; neither implies every table has environment isolation.
There is no `query_authorized`, `expires_at` or full Profile fingerprint in this
fact record. Cookie reference, timeout or actor changes alone do not alter the
physical mapping, but **cannot reuse an old Run read plan**: that plan separately
pins the complete immutable Run Profile and exact ledger resources.

Set status to `REVOKED`, remove the input, or publish a changed revision when the
mapping is withdrawn or changes. The reader checks status/target and its pinned
content digest before and after queries and refuses an in-flight replacement;
it never adopts a new revision automatically. A same-name MCP alias remapped to
a different physical database requires owner reconfirmation even if its text did
not change. The runner cannot discover hidden alias drift from table existence.
Reports say OWNER_CONFIRMED_ENVIRONMENT_MAPPING and live verification NOT_PERFORMED;
they do not fabricate a deployed SHA or independent configuration inspection.

## Plan one bounded observation offline

```bash
python3 runner.py retained-recheck \
  --profile /private/path/original-profile.json \
  --state-dir /private/path/original-run \
  --run-id original-run-id --case RETAIN-GROUP-01 \
  --mapping-confirmation /private/path/environment-mapping.json \
  --dry-run
```

This reads local source artifacts and mapping facts only. It returns a `read_plan`
object; save that object as private JSON. Select a unique `observation_id` and the
required supported projections/budget, including `scope_observation` and/or
separately scoped downstream mapping if needed. The existing optional contracts
remain unchanged. Do not change the source hashes or mapping digest to hide a
validation failure. The generated plan is not an authorization token: without the
explicit command flags it causes no MCP call.

Unlike the legacy review, v2 does not contain `reviewed_at`, `expires_at` or
`reviewer`. Adding those fields is rejected, rather than creating a confusing
hybrid approval. A plan cannot replace a different original Run/Profile/Case,
claim new resources or reconstruct unknown create IDs.

## Execute explicitly, once

```bash
python3 runner.py retained-recheck \
  --profile /private/path/original-profile.json \
  --state-dir /private/path/original-run \
  --run-id original-run-id --case RETAIN-GROUP-01 \
  --mapping-confirmation /private/path/environment-mapping.json \
  --read-plan /private/path/read-plan.json \
  --mcporter-binary /absolute/path/to/mcporter \
  --allow-observer-read --confirm-run-id original-run-id
```

The operator or an already authorized automation can perform this explicit
invocation without another hourly chat confirmation. A mapping file alone does
not authorize the automation. Actual MCP credential failures are still service
errors and cannot be bypassed by changing mapping status.

The existing reservation/guard must remain present and match the original Run;
there is no guard recreation or ownership adoption. The Run-local ledger is
readonly. `observations/<observation_id>` is created exclusively before calls;
success, failure or interruption consumes that ID. Reusing it does not reset the
query/deadline budget. There is no automatic retry with another ID.

Only the selected fixed schema projections are described. Broad database/table
catalogue discovery, generic resource queries and arbitrary SQL are refused by
the specialized mapping client. Every row query still verifies env, exact owned
resource chain, full Profile, source plan, unchanged mapping and held lease.
The single wait-v2 observation deadline, bounded rows/rounds and 32-call phase cap
are unchanged. Optional producer/downstream observations cannot reset those caps.

Evidence goes into the new observation directory as `read-plan.json`,
`mapping-confirmation.json` and `report.json`. The report uses
`read_authorization_basis=EXPLICIT_COMMAND_FLAGS_AND_SOURCE_PLAN` and
`read_plan_digest`, not a claim of a renewed manual approval. The mapping summary
continues to say `query_authorization=NONE`: permission comes from the explicit
bounded invocation, not from the environment fact.

The outer result remains BLOCKED/exit 2, or CANCELLED/130, while whole-scope
execution is unconfirmed. New evidence does not overwrite the original timeout,
delete resources, upgrade qualification or release fixtures.

## Legacy compatibility and rollout

The existing `--review` plus `--observer-binding` v1 path keeps its expiry and
integrity checks. It does not accept persistent mapping files. V2 requires
`--mapping-confirmation` instead; both binding forms together are rejected.
`--read-plan` aliases the existing input option, but JSON contract and binding
selection—not the option spelling—determine the semantics. Generic `check`,
`prepare`, remote business execution and cleanup do not accept the new mapping
as a legacy query receipt. Native `policy-check` is a different origin/identity
workflow and keeps its current explicit source-review contract.

Do not extend timestamps, rewrite original receipts or automatically turn old
permissions into permanent authority. Establish the new fact record from actual
owner evidence and select the new path explicitly. This implementation increment
does not migrate any real input, execute a remote read or grant a new business
operation. Real end-to-end acceptance remains a separate step.

## Validation record

2026-10-12: 19 focused mapping/read-plan tests and 71 legacy/composition regression
tests passed. The complete 954-test runner regression passed with owned BCS/CLI
runtime enabled (165.142 seconds; no failures/errors/skips). Coverage includes
facts older than an hour, stable-target reuse versus old-Profile-plan rejection,
revocation during a query, old/fresh contract mixing, catalogue/generic-query
refusal, producer projections, cancellation, runtime deadline and one-shot replay
refusal. Independent readonly review found no required correction. Case guard,
strict OpenSpec and diff checks passed. No real profile/receipt was migrated and
no remote request, policy change, resource cleanup or legacy coverage gate ran.
