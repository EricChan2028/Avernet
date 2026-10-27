# Plan

## Approach

使用发布原记录的可选、非 ORM 映射日志列，而不是第二张 outbox 表。原生状态 CAS 同时写入完整的投影意图，避免 enqueue/crash 窗口。知识库绑定的错误只在独立事务中重试，不回滚 Bot 的成功。

## Data Model Changes

```sql
-- sql/2026_10_27_bot_knowledge_projection.sql (review only)
-- knowledge_tenant: native authenticated tenant routing (historical FK backfill)
-- knowledge_snapshot: admitted immutable selection, or explicit capture error
-- knowledge_projection: actual online completion intent + state
-- knowledge_restore: native draft restore completion intent + revision fence
-- knowledge_due: indexed incremental discovery, NULL = no work
-- knowledge_deployment_id: existing globally monotonic BaaS deployment ID
-- knowledge_revision: draft edit epoch (increments with selection mutation)
```

新增列从原生 mapper 排除，disabled 原生 SQL 不包含这些列。开启前必须先提供 schema。生产 migration 未授权执行。

## Key Files & Functions

```python
# repository/implementations/publishing/knowledge_projection.py
online_intent(session, publish_id, ext)  # only native row data, same CAS
complete_restore(db, ...)               # native operation + intent, no binding I/O
apply(db, publish_id)                    # Bot -> publication lock, DB-only atomic effect
run_batch(db, limit=50)                  # due-only scan, DB-clock retry, finite attempts
retry_failed(db, publish_id)             # explicit scoped repair; never recapture
```

```python
# service_bot/services/knowledge_projection_lifecycle.py
# Inject repository Protocol; reconcile every 5 seconds only when enabled.
# Cold restart discovers persisted intents without queue dependency.
```

## Fencing and Reads

使用现有部署机制已定义的全局递增 `baas_publish_id`（不是版本号或 publication 主键），因此旧版本回滚的部署仍是新的序号。对同 Bot 的实际完成记录查最高部署序号；迟到旧意图只变为 SUPERSEDED。`pending/failed/unknown` 的生产读取返回空且 bindings 显示 projection_state，不把旧标志当作当前授权。原生权限与平台 grants 仍独立。

草稿投影在完成时采样 publication edit epoch；编辑与投影都在同一个 Bot 锁序列下递增/比较 epoch。后来的编辑胜出。

## Risks & Mitigations

- 开启前 journal schema 必须存在；关闭不使用可选列。知识库绑定 schema 缺失不阻断主流程。
- 所有部署走现有单调 BaaS ID 合约；缺失或不合法为 UNKNOWN，不能猜测顺序。
- DB 故障投影不确认，原生记录已成功。重试到上限后 FAILED；修复原因后显式 retry。
- 本地 SQLite 锁验证不等于 OceanBase 多实例证明。

## Test Strategy

原生推进、绑定故障/缺表、重启恢复、重复/乱序、旧版本回滚、未知快照、草稿编辑 epoch、故障事务回滚、DB-clock retry、索引查询、disabled SQL 隔离和真实 DI lifecycle 发现。


### Claim and operator recovery

Before taking a Bot lock, each worker atomically claims a due row by comparing
the journals and moving `knowledge_due` to DB-now + 30 seconds. Only the winner
runs the projection. The apply/failed-result write checks the same lease; a
process crash is recovered by ordinary due discovery after expiry. No new lease
column/table or in-memory ownership is required. MySQL latest-deployment and
restore fences use locking/current reads, not a pre-lock RR snapshot.

The narrowly scoped recovery entrypoint is
`apps/backend/scripts/retry_knowledge_projection.py`. From an **authorized
deployment shell**, with its normal profile/config/DB privileges, run from
`apps/backend` (only after separate operations approval):

```sh
PYTHONPATH=src python scripts/retry_knowledge_projection.py \
  --env <matching-deployment-env> --tenant <explicit-tenant> \
  --publish-id <failed-publication-id> --confirm-retry
```

No credentials are accepted/printed; this does not grant DB permissions, run
DDL, start lifecycles or call deployment APIs. The policy must be enabled and
`--env` must match the configured deployment. Repository guards enforce tenant
and environment. Exit 0 means requeued, 2 means no matching FAILED intent was
requeued, and validation/persistence errors fail the command. It reuses the
immutable snapshot; UNKNOWN cannot be reconstructed this way. Production use of
this command has not been executed or verified by this local repair.


### Native tenant lineage and failed admission token

`knowledge_tenant` persists native routing independently of optional snapshots.
Opted-in inserts record the authenticated tenant and successful capture rechecks
it from the guarded source Bot. The review-only migration backfills historical
routing by the existing source Bot PK and environment; it does **not** backfill
any selection or snapshot. Apply this reviewed metadata backfill before opt-in;
orphan sources cannot be attributed and remain untrusted. Background completion
uses this native routing column, never the default task tenant or missing legacy
snapshot. No runtime tenant-guard bypass is introduced.

Capture failure still rotates a small generation-only ext token in the native
admission transaction, alongside the UNKNOWN journal capture. Thus a previous
build worker cannot adopt a later BUILDING state even when recapture failed.
The marker contains no enabled IDs and cannot grant access.

If there was no preceding ext token and even the minimal token would overflow a
valid native TEXT, admission leaves that native payload unchanged and records
UNKNOWN in the journal only. No optional token may introduce a native capacity
failure. Existing admitted generation tokens are rotated, not retained.
