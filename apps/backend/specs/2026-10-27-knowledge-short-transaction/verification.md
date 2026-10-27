# Short-transaction knowledge publication verification

Date: 2026-10-28 (Asia/Shanghai).

## Delivery boundary

- Remote GitHub PR #3177 was re-resolved to
  `3ee3f5b18f43a190969c8422b9f95e114136a2c4` before this refactor and
  confirmed unchanged at closeout on 2026-10-28.
- The accepted implementation supersedes the local rejected projector commit;
  neither its architecture nor its older test counts are acceptance evidence.
- Native publishing models match both the original PR Head and its Base
  `4f11a31528da4304a582cfcb71670c5aa50ef61d`. DI matches the original PR Head; the publication CAS protocol only adds a
  backward-compatible optional observed-workflow proof argument. There is no
  knowledge lifecycle registration or new task-queue integration. SQL scripts match the original PR Head. Only the
  original knowledge-owned `online_enabled` migration remains in scope.
- No push, merge, deployment, schema operation or remote permission change ran.

## Acceptance matrix

| Requirement | Evidence |
| --- | --- |
| Draft/verify share working state, production separate | Stage, personal, editability and lifecycle repository contracts |
| Bounded admission and stale generation fence | 100-ID cap, new generation on redraft; real flow rejects old build result |
| Same Session / real rollback | ProductionContractDatabase ordinary AUTOCOMMIT vs explicit transaction; failure after second online binding UPDATE restores status/ext/flags; retry succeeds once |
| Native progress retry does not redeploy | Real PublishProgressPollHandler + real flow/repositories: observed BaaS SUCCESS, injected binding-write failure, same payload redelivery completes; terminal replay is a no-op and no BaaS mutator is called |
| Business UNKNOWN differs from SQL failure | Configured/unconfigured legacy rollback, missing/corrupt/foreign snapshot, removed binding, Owner/scope changes; SQL read/write failures still raise |
| Unknown does not inherit flags | Scoped flag clearing; structured top-level latest-observed-result predicate is part of the SAME flags SQL; nested opaque metadata, similar keys and compact/reordered JSON tested |
| UNKNOWN lifetime and admission isolation | Real default-tenant progress poll of legacy external publication, failed real rollback submission, native DRAFT and recapture retain UNKNOWN; older UNKNOWN target is not effective until actual success |
| UUID/Owner replacement | Actual progress-poll late callback with different target UUIDs cannot overwrite newer observed selection; old Owner callback cannot revoke new Owner flags |
| Native rollback not gated by knowledge history | Native paired flip can target a pre-feature publication; flags change only on observed target deployment success |
| Restore native ledger guards remain | Operation identity/kind/stage/env, latest attempt, source, current draft, terminal state and persisted timezone deadline; recheck after lock wait; completion/result/selection atomic; replay preserves later edits |
| Unknown restore does not reject native completion | Missing source snapshot or binding completes native ledger, preserves working edits, records UNKNOWN restore outcome |
| Tenant and Owner scope | Snapshot includes Owner/Bot/env/tenant/space/publication identity; external-tenant background completion restores snapshot context with normal guards, then resets caller context; unresolved old Owner never revokes current Owner flags |
| Native stable identity after handoff | Actual BotPublishService.create_publish retains immutable entity owner while snapshot/result capture current Owner; late callback cannot overwrite the newer result; A→B→A cannot resurrect older state |
| Actual restart proof | Real progress handler prefers restart ledger300 even when release slot77 and restart dual-write is absent/stale299; result stamps300 without changing native handles |
| Current reads after lock waits | MySQL SQL-compilation contract requires FOR UPDATE on mutable selection and observed-completion reads; SQLite two-thread tests serialize draft edit/admission |
| No remote I/O under added transaction | Fault-injected changed artifact PUT asserts no explicit transaction is open; unchanged oversized artifact not PUT again; PUT failure cannot advance status |
| Immutable artifact/CAS ownership | Known stale/missing status/ext or rollback pair does not PUT; delete racing a pre-staged successful PUT is reaped under the existing prefix |
| Default-off compatibility | Missing binding schema build/restore remains native; ordinary native sessions/uploads retained; no binding SQL |
| No native schema/worker expansion | Source/schema comparisons and schema regression; no remaining projector, journal DDL, lifecycle or retry CLI; optional proof argument is compatible and creates no field |

## Commands and results

The shared Python installation is used without corporate editable `.pth` files;
public locked dependencies are supplied by the existing isolated dependency
folder. No shared environment was modified for this refactor.

```sh
DEPLOY_PROFILE=test \
PYTHONPATH="src:/tmp/tc-knowledge-1027-public-deps:/Users/qiaoyifan/Antcode/ocb/src/backend/.venv/lib/python3.12/site-packages" \
/Users/qiaoyifan/Antcode/ocb/src/backend/.venv/bin/python -S -m pytest \
  tests/community/repository/publishing \
  tests/community/core/bot_knowledge \
  tests/community/core/service_bot/services \
  tests/community/endpoints/test_bot_knowledge_router.py \
  tests/community/architecture tests/community/di -q --disable-warnings --tb=short
```

- Initial implementation commit `e73515e0d`: 2457 passed, 43 warnings.
- Observed-result implementation `495e28771`: 2468 passed, 43 warnings.
- Final source `7b7c99222776e6708d4182d615f2890c0b628df0`:
  **2473 passed, 43 warnings, 0 skipped**, in 142.33 seconds.
  The final gate includes every suite listed above; the closeout commit changes
  documentation and the local query-plan evidence only.
- Changed Python: Ruff check and repository blocking Flake8 rules pass.
- `git diff --check` passes. New and modified production source remains below
  1000 lines except the pre-existing allowlisted `bot_publish_service.py`:
  it remains exactly 1563 lines, with only the optional proof argument and its
  forwarding changed. Its existing architecture-test allowlist is untouched;
  no new waiver, threshold change or unrelated large service split is added.

## Review and limitations

- Final Spec review on `7b7c99222`: no outstanding evidenced P2-or-higher
  correctness findings in the reviewed scope; all four reported defects closed.
- Final Standards review on `7b7c99222`: zero hard violations and zero
  consequential smells. Predicates compiled for SQLite, MySQL and
  MySQL-PyMySQL; the focused reviewer regression passed 13 tests.
- Performance review: no custom workers/queues or new operation-history scans.
  Remaining per-Bot publication-history query cost is measured locally below;
  production performance acceptance is explicitly **not established**.

The Standards review caught full-document JSON-text false positives; this is
corrected to a structured top-level JSON predicate with direct regressions.
MySQL locking reads and the concurrent-delete artifact-ownership gap were also
corrected before final acceptance. Spec review also exposed UNKNOWN lifetime,
rollback-admission and old-Owner/replacement-UUID callback defects; these were
corrected using retained ext results ordered by actual observed native workflow
ID, without introducing native fields or another worker/state machine.

The incremental performance correction removes the newly added native operation
history scan/locks. Remaining stable-Bot/source/env-scoped publication-history JSON
lookup/sort cost (with current Owner checked after timeline ordering) is
explicit: it can grow with per-Bot publication history.
Publication hooks remain default-off. Deployed plans/history/latency/lock
evidence is a prerequisite for publication-hook and online-consumer rollout
independently; the publication toggle does not disable explicitly requested
online-reader queries. This is not a claim of production performance
acceptance or authorization to apply deferred index DDL.

This is controlled local SQLite/in-memory dependency validation plus MySQL SQL
compilation, **not** deployed BaaS/OSS/ECB, MySQL/OceanBase transaction/lock testing,
production query-plan proof or multi-pod end-to-end acceptance. Existing native
publication indexes were previously deferred; actual deployed indexes/plans
remain a release-time check, and no index DDL was performed here. Native task
retry budgets/deadlines still apply; persistent DB outages are not claimed to
resolve themselves. A raced immutable object on an existing publication stays
under its original record's cleanup prefix, not a new cleanup worker.

## Exact local reader query-plan probe (final source)

`local-query-plan.json` records the emitted flags SQL (with tenant guard), SQLite
EXPLAIN output and VM-step counts on fresh in-memory fixtures at 1 / 1,000 /
10,000 publications for one source. Counts were 274 / 41,233 / 410,233 steps.
The source index constrains the native publication scan to that Bot/environment,
but JSON workflow ordering still uses a temporary B-tree. This is measured
history-linear local work, not O(1) and not an OceanBase benchmark. The source
index exists in the fresh fixture; its deployed presence is not established.
The fixture probe does not access an existing or production database.

Publication opt-in being disabled is **not** a kill switch for this online-reader
query when a consumer explicitly requests the online API view. Its actual
production cadence/history/concurrency must therefore be checked independently
of publication-hook enablement. No new timer/queue loop is introduced, and
neither publication nor online-consumer rollout is authorized by these tests.
