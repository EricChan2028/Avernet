# Provider-owned WebSocket coordination

## Requirement and design

An authenticated V3 bridge Bot must use its own server-stored Provider's
coordination mode and exact tool mapping, including when its connection is WS
and it has no HTTP delivery binding. A tool result with a stored v2 intent must
reach the worker through the existing claim, role, run and Session checks.

Resolve Bot-owned Provider metadata first and retain legacy gateway binding
compatibility. Missing/disabled/deleted or unreadable affiliated Providers fail
closed; they must not fall back to client-declared profiles. No affiliation keeps
the existing deployment-authorized built-in profile behavior. Provider identity
never comes from a tool result or bot.connect argument. Negotiate after runtime
authentication, using V3 only; preserve the existing event adapter and cached
start identity checks. Resolve the exact configured mapping through the same
Provider resolver during message-flow processing.

This requires a BCS release and a bridge reconnect, but no bridge wire change,
database migration or Provider config rewrite. An existing HTTP gateway route is
an operational alternative only if its endpoint/auth and registration are ready;
the WS admission bug cannot be repaired by changing tool_name_mapping alone.

## Plan

1. Add failing service tests for upstream Provider configuration resolution,
   V3 capability negotiation without client_kind, deny modes and disabled data.
   Run the tests before implementation.
2. Add a narrow core contract for Provider-owned coordination resolution; share
   the resolver with runtime negotiation and existing tool mapping. Extract the
   affected oversized application file by responsibility to meet 1,000 lines.
3. Add a bootstrap test using real manager/worker WebSockets, upstream metadata,
   custom MCP names and the v2 cache contract. Verify worker receipt, rejection
   of unmapped sources and no repeat dispatch for an applied intent.
4. Run relevant bot, WS, message-flow, coordination and architecture checks;
   review the diff, document database access costs and deployment alternatives.

## Validation and review focus

- Provider policy overrides deployment-wide profiles; no client self-authorization.
- Provider metadata errors propagate; disabled/tombstoned identities never fall back.
- No new DB reads on ordinary chat deltas. Negotiation performs bounded identity
  and Provider lookups; mapping keeps the existing per-run surface cache.
- Keep the nameless BaaS result fixture and start/run/Bot/Session identity checks.
- Production credentials and incident transcripts stay outside the repository.

## Progress

- Investigation complete. User explicitly requested this repair and tests.
- Isolated worktree created at baseline 26196ac2c; original workspace untouched.
- Hook installer moved to devops/hook/install_git_hooks.sh in this checkout.

## Implementation and evidence

- Added a Provider-only registry query shared by streaming negotiation and
  effective coordination resolution. It uses authenticated Bot identity.
- Provider policy wins over runtime kind and the deployment allowlist. V3-only
  gating, exact tool mapping, cached start, run/Session/role checks and intent
  claims remain on the existing path. No WS adapter bypass was added.
- Extracted query/discovery/management/runtime application implementations from
  the existing oversized Bot file; unchanged implementations were preserved.
- The initial four service regressions failed on the old behavior, then passed
  after the fix. The final service target has nine passing tests, including
  disabled/corrupt/missing Provider policy, read failures and recovered reconnect,
  legacy binding fallback, metadata precedence, tombstones and token rejection.
- Real WS integration uses production SQLite/cache composition with no globally
  enabled profiles. A bridge-shaped V3 handshake with null client_kind consumes
  a stored v2 intent, the worker receives chat.send with the stored arguments,
  and the receipt is applied with a task_id. Unmapped tools, failed tool results
  and a repeated applied intent do not produce another task.
- Bot, WS, message-flow and coordination-store suites: 1003 passed, 14 existing
  WS tests ignored (their coordination behavior moved to message-flow). The
  permanent nameless BaaS stored-intent case passes in message-flow. New provider
  WS integration plus existing uplink integration: four passed.
- Independent read-only review found no blocking issues. Changed/added Rust
  sources are all below 1000 lines; git diff --check passes.
- Architecture gate is not green: check-deps.sh fails at line 36 with an unbound
  variable, and the import checker reports existing boundary violations. Both
  failures were reproduced in the untouched original checkout. Port-purity and
  forbidden-symbol checks passed. The conformance scanner also reports existing
  harness/entry gaps; the subsequent full-workspace test-discovery compilation
  was stopped after these gate failures. No gates or waivers were weakened.
  Full workspace/Singlebox coverage and live deployment were not run.

## Database access cost

For a Provider-owned connection, negotiation adds one indexed, environment-scoped
Bot metadata query returning at most one row, followed by the existing cached
Provider lookup (at most one row/query on a cache miss). Missing membership uses
the existing delivery-binding projection as a bounded compatibility fallback;
the projection's configured source can also read Bot connection mode. Resolution
does not scan or write Provider data and propagates errors without retry loops.
Effective tool mapping still uses message-flow's per-run surface cache. There
are no added database queries per chat/token delta. No new cache or invalidation
policy, database migration, transaction or external exactly-once claim is added.
Sustained reconnect load and live MySQL were not tested.

## Deployment and release-free alternative

The requested WS repair is server-side: release BCS, then reconnect bridge so
bot.connect returns tool_result_task_intent=true. Existing native_mcp Provider
configuration remains valid. No bridge binary change is required for this fix;
old stored intents are not automatically replayed.

An operational alternative is the existing bridge gateway mode with Provider 2.0
HTTP/SSE delivery. It requires a reachable webhook, authorized gateway Bot
registration/delivery routing, and matching webhook credentials. The repository's
bridge webhook currently verifies a static Bearer token; an AgentPass Provider
cannot be assumed compatible merely by switching mode. A compatible gateway
setup (potentially a separate Provider) must be prepared and validated first.
This path was inspected in source, not deployed or verified against the incident
environment. Changing tool_name_mapping alone cannot repair the old WS gate.
