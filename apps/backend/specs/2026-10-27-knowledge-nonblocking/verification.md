# Non-blocking publication verification — 2026-10-27

## Scope and fixed source

Repair of PR #3177 at `3ee3f5b18f43a190969c8422b9f95e114136a2c4`,
base `4f11a31528da4304a582cfcb71670c5aa50ef61d`. The GitHub head was
re-resolved during closeout and still matched this source. Draft and verification
are mutually exclusive and intentionally share the working selection.

No deployment, merge, remote branch update, production DDL or permission change
was performed. Migration files are proposals, not applied-schema evidence.

## Acceptance evidence

| Contract | Executed local evidence |
| --- | --- |
| Knowledge cannot block native build/rollback/success | Capture-read failure, configured/unconfigured first rollback to a legacy version, missing snapshot, actual native success without binding or Bot-table SQL |
| Durable completion without enqueue gap | Native success CAS and journal share one write; new repository instance discovers failed pending work without redeployment |
| Effects and acknowledgement are atomic | AUTOCOMMIT-profile fault injection fails second binding update; both flag effects roll back, journal remains pending and native SUCCESS survives |
| Historical capture never uses a later draft | Failed capture followed by changed draft becomes UNKNOWN and production closes, rather than recapturing; failed recapture rotates an existing attempt token and rejects the old build worker |
| Duplicate/out-of-order and rollback fencing | Older delayed intent becomes superseded; older-version rollback with higher BaaS deployment ID becomes current; concurrent SQLite applications produce one effect |
| Multi-worker claim | Two workers discover the same candidate; only the atomic due-lease winner takes the Bot lock. Abandoned claim is recovered after simulated DB lease expiry |
| Restore independent of binding availability | Native ledger completes while binding reads fail; independent projection subsequently applies; later draft edits or restore attempts supersede old work |
| Tenant/environment/Owner/space | Captured-scope validation, non-default tenant background recovery, legacy no-snapshot background completion using native knowledge_tenant rather than default tenant, and tenant-scoped manual retry rejection |
| Retry bounds and observability | DB-clock 30-second delay, 10-attempt FAILED retention, explicit requeue of the same snapshot/generation, malformed journals durably UNKNOWN |
| Operator recovery entrypoint | CLI argument confirmation and composition handoff tested with injected runtime; real local repository executes FAILED → pending → applied. Disabled/env/tenant errors reject. No deployed CLI/profile bootstrap is claimed |
| Native capacity and missing-row compatibility | Generation-only failure marker has no enabled IDs; no token is added if it would overflow a valid native TEXT; missing-row online CAS retains None |
| Disabled compatibility | Real pre-journal SQLite table supports original native SELECT/INSERT/UPDATE; optional columns are excluded from ORM mapping; disabled lifecycle performs no work |
| Scheduling/shutdown | Lifecycle drains in-flight thread before shutdown; due-only scan uses its declared index in SQLite EXPLAIN |
| MySQL RR query shape | Latest deployment fence compiles to a locking/current `FOR UPDATE` read; this is not a real MySQL/OceanBase concurrency test |

The focused recovery and attempt/scope suites have **31 tests**. The six native/offload regression
tests preserve immutable upload behavior and rollback on changed-artifact upload
failure. Existing lifecycle/restore/autocommit tests now assert asynchronous
contracts without dropping native CAS, deadline, permission or failure checks.

## Full affected suite

The final run uses the public test profile and an isolated Python 3.12 import
path (ignoring corporate editable `.pth` files), plus the existing public lock's
PyMySQL in an external temporary dependency directory. No project dependencies,
shared virtual environment, CI exclusions or test assertions were weakened.

```sh
DEPLOY_PROFILE=test PYTHONPATH=src python -m pytest \
  tests/community/repository/publishing \
  tests/community/core/bot_knowledge \
  tests/community/core/service_bot/services \
  tests/community/endpoints/test_bot_knowledge_router.py \
  tests/community/architecture tests/community/di -q --disable-warnings
```

Final consolidated result: **2469 passed, 0 skipped, 43 warnings in 187.06s**.
This is the final complete affected selection after the tenant/attempt/capacity
fixes, not the earlier pre-closeout runs. The two last P2 triggers were each
observed failing before correction and independently rechecked passing.

Static checks: changed-file Ruff check, repository-equivalent Flake8 SAST blocking
rules, Python 3.12 compilation, all changed Python files ≤1,000 lines, and
`git diff --check` passed. Newly added source/test files were formatted; no
repository-wide formatting was performed.

## Review closeout and remaining evidence boundaries

- Spec review: two additional reproduced P2 paths (legacy external tenant routing and failed recapture reusing an old generation) were fixed and independently rechecked. The native routing migration includes a trusted source-PK/environment tenant-metadata backfill, **not** a knowledge-selection backfill, and was not executed. Also corrected MySQL RR stale-snapshot fences in latest-deployment,
  restore, retry and binding-presence reads. New native completion after an old
  projection's fence leaves a pending latest marker, which closes online reads;
  its worker serializes via the same Bot lock before applying and acknowledging.
- Standards review: repository Protocol methods remain abstract and services
  consume Protocols. The explicit operator recovery gap was closed without a new
  HTTP/product workflow.
- Performance review: no knowledge/Bot lock in native online success; immutable
  unchanged artifact is not uploaded again. Disabled rollback retains its
  original session. Due-only discovery and a pre-Bot atomic claim avoid ordinary
  multi-worker duplicate Bot writes. Projection has no remote I/O.
- Idle opt-in cost is approximately 17,280 due-index reads/day/instance (5 seconds
  after each completed batch). Maximum candidates per batch is 50. Overlapping
  workers can still make `I × N` claim attempts, each with a DB-clock read and
  conditional update. Claims do not mean zero contention.
- Long DB waits exceeding the 30-second claim lease and target timestamp
  precision require real MySQL/OceanBase fault-injection evidence. The timestamp
  lease is not a unique token; Bot locks, current reads and atomic effect/ack
  remain the correctness boundary. Do not infer production exactly-once cost
  from the local two-thread tests.
- No full-repository tests, Singlebox E2E, target MySQL/OceanBase EXPLAIN/locking,
  real multi-pod contention, deployed config/CLI bootstrap, remote CI or
  production acceptance ran. Real OSS latency/lock waits were not measured.
- Before opting in, separately review/apply both binding and journal migrations
  and inspect actual target indexes/plans. The switch remains default-off. Drain
  native operations and pending projections before disabling it; retain additive
  columns when rolling back. UNKNOWN requires a new trustworthy publication,
  never reconstruction from current draft.
