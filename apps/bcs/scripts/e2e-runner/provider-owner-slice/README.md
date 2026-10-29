# Provider owner-isolation — owned-local slice candidate (staged payload)

Migrates upstream legacy story `story_provider_owner_isolation`
(`apps/bcs/scripts/e2e-test/provider_scope_story.{sh,py}`, frozen at git tip
`f181ba448a8bd5844c9517d8543ff3b6bb9d66bb`, provider-scoped owner
collaboration #2654) into the owned-local slice pattern, without touching any
shared registry file.

## Status (truthful)

| Dimension | Value |
| --- | --- |
| Case | `LOCAL-PROVIDER-OWNER-01` (suite `owned-local-provider-owner-v1`) |
| Parent | `story_provider_owner_isolation` — known-unmapped legacy backlog (registered 2026-10-29), NOT in the reviewed 30-Story release denominator |
| Environment | `local: LOCAL_EXECUTOR_ONLY`, `preprod: REMOTE_UNSUPPORTED` |
| Remote validation | `NOT_RUN`; `grants_release_credit=false`; independent slice (`independent_slice=true`, `release_parent_denominator=false`) |
| Upstream accounting | 45 live HTTP/WebSocket checks (marker), 24 frozen obligations |
| Live owned-local replay | BLOCKED at story request 8 by upstream #2625 bot-authority drift (documented gap `OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT`) |
| Preprod | Provider admin face and `provider_admin_token` credential channel absent from every route binding (documented gap `PREPROD_PROVIDER_ADMIN_FACE_ABSENT`) |

No release credit is claimed anywhere in this slice.

## Why staged (artifact budget hard-blocks landing from this line)

`bcs_e2e/artifact.py::disk_digest()` rejects the whole executor artifact when
`len(paths) > 324` where `paths = runner.py + bcs_e2e/*.py`. Current tree:
`323 bcs_e2e modules (including the parallel authority-slice untracked files)
+ runner.py = 324` — exactly at the cap. This slice's three modules would make
`327` and crash **the entire e2e-runner package at import time**. Empirically
observed in the rehearsal copy (cap left unpatched, modules landed):

```
ValueError: executor artifact exceeds its source-file budget
```

Editing `artifact.py` is editing an existing shared file, which this migration
line is forbidden to do. The payload therefore ships as staging; landing is a
pure move once the orchestrator grows the allowance.

## Landing recipe (orchestrator; no content edits needed)

1. Grow the allowance in `bcs_e2e/artifact.py` (shared freeze): `> 324` → a
   value covering all slices landing this session (this slice needs +3; the
   parallel authority slice needs its own +N; use one bump, e.g. `> 330`), and
   append the established allowance-comment line:
   "The Provider owner-isolation backlog slice adds its focused source binding
   contract, run lifecycle and offline report projection modules, so the
   allowance grows by exactly those three files."
2. Move, byte-identical (tests resolve their runner root dynamically, so they
   work staged and landed):

   ```bash
   cp provider-owner-slice/modules/provider_owner_contract.py bcs_e2e/
   cp provider-owner-slice/modules/local_provider_owner_run.py bcs_e2e/
   cp provider-owner-slice/modules/local_provider_owner_reporting.py bcs_e2e/
   cp provider-owner-slice/tests/test_provider_owner_contract.py tests/
   cp provider-owner-slice/tests/test_local_provider_owner_run.py tests/
   ```

3. Verify:

   ```bash
   PYTHONPATH=. /usr/local/bin/python3 -m unittest \
       tests.test_provider_owner_contract tests.test_local_provider_owner_run -v
   python3 -m compileall bcs_e2e/provider_owner_contract.py \
       bcs_e2e/local_provider_owner_run.py bcs_e2e/local_provider_owner_reporting.py
   ```

   Rehearsal result (scratch copy with the cap patch only): 37/37 focused
   tests OK; slice-30 canary (`tests.test_local_service_policy_contract`,
   `tests.test_local_service_policy_run`) 27/27 OK in the same environment.
4. Do NOT register the case from this line: `local_story_commands.py`,
   `inventory.py`, `migration_inventory.py`, the manifests and the count tests
   stay untouched. Listing under reviewed/unregistered registries happens only
   through the orchestrator promotion path.

## Upstream check accounting (frozen; marker = one print at story L159)

`Story.checks` increments only inside `request()` and `ws_connect()`; `bot()`
and `group()` each perform exactly one request; literal-tuple loops multiply
their bodies. 28 straight-line request sites (L102,103,104,110,120,121,123,124,
125,126,129,130,131,135,137,139,140,141,142,143,144,146,147,148,154,155,156,
158) + visibility loop L117 x3 + member-removal loop L153 x2 = 33 requests;
plus 4 `bot()` (L109,114,115,128), 5 `group()` (L119,132,133,134,138), 3
`ws_connect()` (L149,150,157) = **45** (`story_checks`). Not marker-counted:
the two explicit metadata asserts (L122, L125) and the 101-upgrade guards
(L69, per connect). Obligations: 24 (`requirements`,
`PROVIDER-OWNER.*`, story_lines cover [102,103]...[161,166] of 177).

## Live drift evidence (why the pass path is enforced but currently unreachable)

Replayed against the current dev binary
(`apps/bcs/target/debug/bcs`, sha256
`dcbc39f07cedb62c8764987caa9fb05983dd7b3b1e6185022281cf0daa73d8d3`) on
2026-10-30: requests 1-7 all HTTP 200 (ensure-human x2, restricted Provider
registration, owner provider Bot, standard Provider registration, standard
provider Bot, foreign provider Bot); request 8 — the first
`PUT /bots/{bot_uuid}/visibility` from the L117-118 loop — answered
`403 "owner or manager role required for bot bot_..."`, and bot reads answered
`403 "Not authorized to access bot"`. Root cause: story frozen 2026-10-22
(f181ba44) predates the #2625 bot-authority model (commit `c6f001209`,
2026-10-27); provider registration (`register_with_owner_and_token`) records
`created_by` but never seeds bcs-edge-permission owner/manager edges, and
`POST /me/ensure-human` backfills only `list_legacy_bots_for_owner` (legacy
registration path) — probe evidence: `matched_bots=[]`, `edges_created=0`.
The frozen authoritative map is unmodified; the drift lives in
`documented_gaps.OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT` (full machine-
readable record: `evidence/live-drift-evidence.json`). The executor still
enforces the exact pass path (strict marker + frozen 45-check accounting +
clean teardown + drift fences); it must simply not be reached by a re-scoped
or partially executed child. Resolving needs a product decision (seed owner
edges for provider-registered Bots, or re-scope the story) — DIMA candidate.

## Preprod face recon (why REMOTE_UNSUPPORTED)

- `profiles/preprod.example.json` and the live preprod profile declare
  `route_bindings` = `group.*` + `session.*` only; no `/providers`,
  `/providers/{id}/bots`, no `provider_admin_token` in any auth header env.
- The runner's own `bcs_e2e/route_bindings.py` routes provider administration
  to the BCS internal face, not the public-v1 gateway.
- The Workbench WebSocket 101 upgrade and `X-Mock-User-Id` loopback admission
  are owned-runtime capabilities only (documented gap
  `WORKBENCH_WS_MOCK_ADMISSION`).

## Probe hygiene

An earlier live probe printed a `bot_runtime_token` preview to stdout; no
token value was captured into this payload. Evidence files in this slice
contain redacted text only; the temporary probe directories under `/tmp` were
removed. The executor's redaction gate refuses `bot_token`,
`provider_admin_token` and `Bearer ` in any captured stream.
