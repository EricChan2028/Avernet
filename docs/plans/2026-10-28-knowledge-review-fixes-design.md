# Plan: Knowledge publication review fixes

## Approach
按批准的五项复审建议逐项做最小修复。复用现有短事务、原生 CAS 和 artifact offloader，不新增发布子系统。测试先复现再修复。

## Changes / Propagation
```python
# di/container.py: build_injector -- pure configuration resolution on every profile
_app_injector.get(KnowledgePublicationPolicy)
# publishing/bot_publish.py -- applies only when integration is enabled
if online_success and observed_workflow_id is None:
    raise KnowledgeError("KNOWLEDGE_OBSERVATION_REQUIRED")
```
```python
# config_artifact_offload.py -- choose offload from the complete inline ext budget
# prepare remains pure and immutable/content-addressed; preserve old oversized rule.
# publishing/knowledge_selection.py -- separate preparing a frozen candidate from committing it
# publishing/bot_publish.py -- fast path commits in one transaction; if a new OSS PUT
# is necessary, release locks, upload, reacquire and recapture the same-generation
# candidate; commit only when status/ext/selection/scope still match. Bounded retry.
# Existing markers normalize via GET outside locks. Supplied ext with a changed
# baseline is a CAS miss, not retried against newer metadata. Completion preflight
# reserves 2 KiB for its compact result; final pending keys must match staged keys.
```
```python
# knowledge_selection.py -- use existing source PK and preserve shared writer lock
# restore -- enabled-index seek (limit 101) + exact selected-bindings seek (<=100)
```
Service/Repository protocols and README describe observed completion; internal callers and fixtures forward observed workflow explicitly. No HTTP body field exposes that authority. Native disabled writes retain behavior. Existing retry endpoints remain, but may not synthesize success while opted in.

## Data / Dependencies
None: no package, ORM, DDL, table or index change.

## Alternatives Considered
- Only filter the HTTP adapter: rejected; leaves other shared write callers inconsistent.
- Lower artifact threshold globally / always offload: rejected; unnecessary I/O and canonicalization changes to old records.
- Upload while freezing under locks: rejected; breaks the short local-only transaction contract.
- Add indexes: unnecessary when source PK and existing binding indexes suffice.

## Risks / Mitigations
- Admission/upload race: bounded recapture with native exact ext and selection checks; cleanup immutable orphan if record was deleted.
- Generic success consumers: explicit intentional enabled-mode refusal; real observer already supplies workflow; fix test fixtures to exercise that contract.
- Alternate SQL dialects: compile checks plus SQLite bounded-work witnesses; MySQL/OceanBase plans remain rollout evidence, not local claims.

## Test Strategy
Focused config/DI, real handler/service/repository status tests, below-threshold artifact with maximum multibyte selections, OSS failure/CAS/delete races, same-name/disabled-history VM tests, all affected publishing/knowledge/services/config/architecture suites and lint.
