# Verification: Knowledge publication review fixes

Date: 2026-10-28 (Asia/Shanghai).

## Pinned scope

- PR #3177 repair base: `7e5454c5d8c50db378566b735e686ab23974bc1a`.
- Final production/test source: `960f46d0a9bb5887c77250d67fc4b31fce0da14d`.
- This user-approved scope includes legacy status admission; the older
  current-summary verification's explicit exclusion is historical, not this repair's scope.
- Working selection remains shared by draft/verification; production is separate.
  No new Plugin, worker, table, column, index, DDL or package dependency.

## Red/green evidence

- Root configuration: 5 malformed-policy cases failed to reject before the fix;
  real YAML quoted string and environment interpolation now reject at root DI.
  A valid disabled policy discovers and bootstraps the real publish lifecycle,
  registering `PROGRESS_POLL_TASK`.
- Original SQLite cost witnesses failed both legacy same-name source and disabled-
  history restore bounds before the keyed query corrections.
- Four subthreshold-artifact/max-selection combinations (ASCII and UTF-8, artifact
  before/after admission) failed before complete-ext budgeting; all now complete
  with the exact intended production set. Upload failure leaves DRAFT/no snapshot.
- Generic success writers/legacy handler failed refusal checks before the observed
  completion guard. Explicit observed completion still applies the frozen set;
  disabled native status writes retain their behavior.
- Independent Spec review found three further defects despite the first 2,817-test
  green run: result-size growth could create an unstaged marker; retrying supplied
  ext could erase concurrent metadata; shrinking a selection with a stored marker
  could break exact ext CAS. All three were reproduced as failing positive regressions
  before fixing them. The final artifact suite also proves rollback on protected-
  metadata growth after preflight: **17 passed**.
- Real normal TEST DI resolves `BotPublishServiceProtocol` to its configured
  concrete singleton, using the normal repository binding and enabled policy.
  Only the external DatabasePlugin boundary is supplied with real fixture SQLite;
  the Service/Repository are not hand-rebound for this configured-provider witness.

## Final integrated gate

**2,822 passed, 44 warnings, 0 failed, 0 skipped**, 187.68 seconds, on the final
source above. The earlier 2,817 pass and an interrupted intermediate rerun are not
claimed as final acceptance of the healed source.

From `apps/backend` with the existing local Python environment:

```bash
DEPLOY_PROFILE=test SERVER_ENV=dev PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python -m pytest \
  tests/community/core/bot_knowledge \
  tests/community/repository/publishing \
  tests/community/core/service_bot/services \
  tests/community/core/service_bot/repository \
  tests/community/endpoints/test_bot_knowledge_router.py \
  tests/community/endpoints/test_bot_common_config_router.py \
  tests/community/architecture tests/community/core/common_config \
  tests/community/repository/config tests/community/di tests/community/config \
  -q --disable-warnings
```

Ruff, repository blocking `singlebox/ci/python_sast_local.sh`/Flake8 against the
repair base, and `git diff --check` passed. ORM model and SQL files have no diff
against the repair base. The pre-existing oversized router waiver remains; no
new waiver was added. New helper is 62 lines, publish repository 962 lines.

## Cost and race boundaries

- Source PK locking retains tenant/env/native identity checks and SQLite's shared
  writer reservation. Restore uses enabled-index and exact unique-key ID seeks.
- Independent SQLite negative controls on the initial query fixes detected old
  growth: address seek 80 -> 18,494 VM steps; restore OR seek 347 -> 18,761. Keyed
  no-PUT admission and changing restore were constant at 528 and 848 VM steps in
  that controlled 2/2,048-row probe. These are not production timings.
- No new periodic work. Build recapture is capped at three attempts. Inline/no-
  artifact admission stays one short transaction; PUT or stored-marker normalization
  releases locks for remote I/O, then revalidates. Supplied ext never replays over
  a changed baseline. A final pending-key fence prevents committing an unstaged
  reference, including paths not covered by the compact-result reserve.
- Completion preflight reserves 2 KiB for compact Owner/status/reason/workflow
  metadata; canonical stored JSON and expected-ext comparison use zero reserve.
- Immutable orphan objects retain native per-record prefix cleanup. This bounds
  per-request attempts, not lock wait/OSS latency or cross-request object retention.

## Independent closure

- Standards: final incremental review found no evidenced P2+; normal-provider and
  lifecycle-bootstrap evidence confirmed. Existing router waiver was not expanded.
- Spec: the three independent boundary findings are closed at `960f46d0a`;
  reviewer independently ran their three positive regressions plus the final
  staged-key guard regression: **4 passed**, no new evidenced P2+.
- Performance: **PASS WITH RISKS**, no new evidenced P2+. Incremental independent
  witnesses confirmed marker-stays-offloaded normalization at 16 SQL/2 windows/
  2 GET/0 PUT, marker-to-inline at 16 SQL/2 windows/1 GET/0 PUT; continuous marker
  churn stays at three normalization GET and six windows (42 request-owned SQL).
  Successful marker return can add a fourth GET at the three-attempt boundary.
  All observed GET/PUT were outside the added transaction windows.
- The 2-KiB preflight reserve can upload an object that final canonical JSON does
  not reference (a 65,400-byte fixture demonstrated this). It is at most one such
  PUT per completion call and uses existing prefix deletion cleanup; no new TTL
  or cross-request retention guarantee is claimed.

## Explicit limits and operations

TEST/SQLite and controlled OSS boundaries are not deployed MySQL/OceanBase plans,
lock contention, multi-instance load, actual BaaS/OSS/ECB/MCP consumption, full HTTP
permission-wrapper or corp/pre/prod startup acceptance. Production schema/index
presence remains an operator prerequisite. No full repository/Singlebox stack,
remote CI or production acceptance is claimed. No feature enablement, remote push,
PR comment/vote/merge, deployment, permission escalation or production DDL occurred.
