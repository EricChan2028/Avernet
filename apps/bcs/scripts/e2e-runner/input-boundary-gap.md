# Exact-scope input closure: existing capability and remaining decision

Status: **source-verified capability gap, not a deployed product change**.
This follow-up to retained-closure-matrix.md answers whether an existing resource
status/stop operation can supply its remaining cutoff/drain evidence. It does not
reopen the already established managed-parent reply argument or request another
ACK/Final scan. No remote writes or experimental calls were made.

## Source baseline

All public paths below are relative to `apps/bcs/crates/` at immutable source
`ee9f4f8af4dbe4b768469df9043215093a74da58`. The pre-Run reported build supports
reviewing this object; this audit is not a fresh deployment or all-replica check.
The main agent independently inspected the queue contracts and the cited resource,
web-input, callback and interaction paths after a read-only subagent audit.

| Existing operation or state | Actual contract | Why it is not a whole-scope cutoff/drain certificate |
| --- | --- | --- |
| Session completion | `services/bcs-session/src/application/sessionmanagementserviceimpl.rs:273-328` persists completion and its event | Does not return proof that every accepted producer/execution has settled. |
| Group status / terminate | `services/bcs-group/src/application/management/operations.rs:19-54,304-338` updates the authorized Group's status | Resource state is not a coordinated admission barrier across all input paths. |
| Ordinary web send | `services/bcs-message-flow/src/group_flow/web_send.rs:9-40` reactivates Inactive and rejects remaining non-Active Group states | Useful entry-specific check; Inactive is expressly resumable. It cannot be generalized to other entries. |
| Session resolution | `services/bcs-message-flow/src/queued_admission.rs:85-114` selects a unique running Session only when no explicit Session is supplied and managed routing needs one | This helper is not itself a universal Session-status validator. It does not prove the behavior of every downstream authorization check or entry. |
| Bot Final relay | `services/bcs-message-flow/src/bot_event/relay.rs:58-83,225-235,275-277` loads Group and optional Session participants, then applies managed/direct routing | No common resource-status cutoff is installed here. The known managed-parent reply exclusion still holds; it does not cover unrelated inputs. |
| Group callback | `services/bcs-message-flow/src/group_flow/callback_abort.rs:3-22,63-81,165-187` is a separate input path with Group scope and no canonical Session in its send context | A Session-only inventory/status cannot silently claim coverage of this input path. Do not invoke it to probe the gap. |
| State-machine one-shot/cancel | `services/bcs-collaboration-runtime/src/runtime.rs:2698-2714,2750-2764,4005-4075,4667-4715` checks Active/Running for one-shot and cancels selected state-machine work | One-shot's check is real, but Run cancellation does not represent all Group/Session producers; chat Sessions are retained. |
| Interaction resolution | `services/bcs-interaction/src/management.rs:240-365` claims an authorized live interaction; `services/bcs-group/src/application/management/workbench.rs:235-272` checks scope, participants and ownership | The ownership check is not a Group/Session terminal-state admission barrier. Relevant already accepted resolution work remains separate. |
| Delivery cancellation/resolution | `service-api/bcs-service-api/src/application/message_delivery.rs:86-130` addresses a selected message/delivery and expected version; `core/message_delivery.rs:7-36` distinguishes cancellation request and trusted terminal evidence | These are not a persistent scope-wide intake closure. Human resolution is an attestation about a particular uncertain attempt, not authority to declare all future inputs absent. |
| Queue drain guard | `services/bcs-message-flow/src/queued_admission.rs:21-72` prevents legacy bypass while target-lane work remains, then permits later requests to resume | A temporary lane-draining check is neither a permanent input cutoff nor evidence of no unrelated accepted work. Global dispatch pause or policy mutation is not a substitute. |

Conclusion: no **uniform exact Group/Session input cutoff plus complete in-flight
acknowledgement** was found in this audited composition. This is a scoped source
conclusion, not a claim that no environment could enforce equivalent isolation.
In particular it does not make a new stop API mandatory for every natural-completion
Story. It does rule out using existing completed/closed/abort labels as a shortcut.

## Two legitimate ways to close the remaining boundary

### A. Existing controlled-input fixture, if independently supportable

Keep real Bot execution and default resource retention. Establish the actual
input model of the dedicated test Bots/runtime: which Human, scheduled jobs,
channel bindings, integrations, callbacks, tool/background tasks and other runtimes
can submit work, and which are inapplicable or constrained for this test scope.
The runner's local pool lock is only one part of that model, not a server ACL.

Reuse actual owner/configuration evidence, rather than generating a statement
that external producers do not exist. Record an explicit cutoff for the test's
submitted root inputs, cover their already accepted descendants under the reviewed
source contract, and obtain a fresh complete inventory at that cutoff. The result
may establish completion under those documented finite-input conditions; it need
not promise that a retained Session can never receive any new input in the future.
Unknown or unaccounted input remains a blocker, even if the known three Runs ended.

The prior owner confirmation establishes dedicated/exclusive Bots, isolated test
runtime and no operational channels. It does **not explicitly enumerate** scheduled
jobs, other integrations or independent background work, nor certify an in-flight
drain. Do not silently broaden that historical confirmation. If actual configuration
is available to establish the remaining facts, inspect it with current read
permission; do not ask the owner to confirm something they cannot know.

### B. Separate product capability, if the fixture cannot bound inputs

Propose and review a separate product change; this E2E change does not quietly
modify BCS API/schema/permissions. The minimal outcome is:

- An authorized, durable cutoff for the **exact owned scope**, consistent across
  replicas and all applicable input paths, with a stable generation/cutoff identity.
- Treatment of already accepted work and its descendants that permits settlement
  without admitting unrelated new root input or losing late terminal evidence.
- Complete, bounded observation of active/queued/retrying/uncertain work and
  applicable callback producers, tied to that same generation/cutoff.
- Failure, rollout and lost-response behavior that stays uncertain; idempotent
  re-observation, not blind re-execution or database repair.
- No global Bot stop, service-wide policy reset, shared Provider shutdown or
  resource deletion as a substitute. Any later reopen/reuse is separately fenced.

A Group-level callback without a canonical Session means a proposed Session-only
capability must explicitly solve that scope mismatch rather than overlook it.
Paths, payloads, schema and deployment are deliberately not invented here.

## Next action and boundaries

The remaining input is **actual fixture producer configuration or a reviewed
product-capability decision**, not another Cookie renewal, a larger log window,
another identical delivery poll or a new Human identity. Until one route provides
its evidence, do not issue a second business Run on the quarantined pool or claim
CONFIRMED_ENDED. Other framework implementation may continue without those writes.

Three source-aligned recheck regressions combine an empty exact state-machine
inventory and observed terminal delivery with (a) completed Group/Session and
succeeded callback, (b) closed Group/completed Session with callback not applicable,
and (c) inactive Group/completed Session. All must preserve UNCONFIRMED and pool
quarantine with original artifacts unchanged. They use synthetic transport and
real temporary ledgers; they do not reproduce or mutate a deployed environment.

Validation: the three new recheck regressions and related producer, scope,
retention-report and terminal-wait suites passed **58 tests in 4.289 seconds**.
Case guard, strict OpenSpec and diff checks passed. Independent read-only review
confirmed the distinction between an audited capability gap and a mandatory
product change for all environments. This increment changes tests and audit docs
only; the previous 1027-test full runner result is historical and was not rerun.
No deployed state was changed or probed through a speculative write.
