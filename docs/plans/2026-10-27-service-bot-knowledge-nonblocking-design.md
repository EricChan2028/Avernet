# Service Bot knowledge projection: non-blocking lifecycle

## Contract

Draft and verification share one working selection, because editing is allowed only in DRAFT. Production is separate. Capture the selection for the specific build attempt; a successful online deployment (including a rollback) requests projection of that attempt's immutable snapshot. Production selection must never be inferred from a later draft. Existing service Bot build, promotion, rollback and draft restore must not depend on knowledge storage, validation or knowledge background work succeeding.

The publication lifecycle's own durable ledger remains authoritative. A knowledge projection is independent, observable and retryable. An attempt whose selection could not be captured, or a historical publication without a trustworthy snapshot, is explicitly unknown and requires intervention; it is not an empty selection and cannot safely be reconstructed from a later draft. A projection must check bot, owner, env, tenant, space and exact active publication/attempt under an appropriate fence. Replays are idempotent; stale work cannot overwrite newer production selection. Unknown snapshots do not grant knowledge access.

## Design constraints

- Do not make queue insertion, knowledge reads/writes, snapshot validation or object storage latency a prerequisite for native publication completion. Avoid remote I/O while holding knowledge-related locks.
- Existing publication/operation records must serve as durable discovery of work even if enqueue fails or the process crashes between completion and enqueue; a queue-only best-effort callback is not an outbox.
- Use bounded, indexed incremental discovery/claim and existing queue or an explicit reviewable migration as warranted by the real persistence constraints. Avoid unbounded historic rescans and cross-instance duplicate effects. Failed and terminal tasks must remain visible and manually or automatically recoverable.
- Preserve the native CAS, optimistic-concurrency and offload contracts; do not weaken them to make knowledge best effort.
- For historical rollback with no snapshot, native rollback proceeds and the knowledge result is explicitly unknown, not silently guessed.
- Keep feature enablement and deployment/schema migration separately controlled. No production DDL or deployment is authorized by this change.

## Alternatives

1. A single synchronous transaction gives instant consistency but blocks the Bot on knowledge errors; rejected.
2. Catch and log knowledge errors after the Bot transition is simple but loses work on enqueue failure or process crash; rejected.
3. Persist/discover a version-fenced projection intent and process independently, with an explicit unknown state when capture is impossible; chosen subject to validating the existing ledger and schema.

## Validation

Cover first publication, retries, stale/out-of-order completions, repeated execution, configured and unconfigured historical rollback, draft restore, snapshot-capture failure, queue/write failure and restart at every handoff. Inject slow artifact PUT and check that knowledge-specific Bot locks are not held for its duration. Exercise SQLite integration and describe separately any MySQL/OceanBase concurrency and deployed validation not performed.
