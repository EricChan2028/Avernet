# Service Bot knowledge selection — approved short-transaction contract

## Outcome

Draft and verification share working selection; production is separate. The user
explicitly accepts bounded same-database transaction coupling. Do not add tables,
columns/indexes on native publishing/infrastructure tables, custom workers,
queue integration or a second publication state machine. Preserve the original
PR's knowledge-owned `online_enabled` column and existing publication `ext`
snapshot. Previous journal/projector design is rejected and must be removed.

## Acceptance

1. Build admission captures the bounded selection and attempt identity in ext;
   ordinary knowledge business states (missing/invalid historical snapshot,
   removed binding, revoked external grant) must not reject native advancement.
   Snapshot UNKNOWN is not empty and never recaptures a later draft.
2. Actual online-success CAS and online_enabled replacement use the SAME Session
   and real commit/rollback transaction. Database/SQL errors propagate and undo
   local completion. Retrying completion never initiates another deployment.
3. Knowledge-specific transactions contain DB operations only; no OSS/RPC/grant
   lookup under their locks. Existing immutable artifact/CAS behavior is retained;
   any raced pre-staged immutable artifact is owned by existing prefix cleanup.
4. Native rollback admission does not validate historical KB configuration;
   target snapshot is applied only on actual target deployment success.
5. Native draft restore ledger identity/attempt/deadline/source checks remain;
   selection and completion are atomic. Completed replay never overwrites edits.
6. Missing/foreign snapshot or binding produces an explicit UNKNOWN result in
   existing ext, with no production grant inherited from the previous version.
7. Tenant/env/Bot/Owner/space and generation/actual deployment fences remain.
   No tenant-guard bypass and no SQL schema/backfill on ac_bot_publish/ac_task_queue.
8. Default-off native SQL requires no knowledge schema and uses the original
   native session behavior. No merge/push/deploy/production DDL is authorized.
