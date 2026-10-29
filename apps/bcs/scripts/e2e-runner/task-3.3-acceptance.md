# Task 3.3 acceptance: correlation and evidence gates

Date: 2026-10-13. Status: **framework contract complete**.
This accepts neither full preproduction regression nor execution/pool closure.

## Original scope

Integrate x-request-id/traceparent and actual response correlation; constrain log
observation by exact identity/environment and bounded windows; define required
versus diagnostic evidence. The observation specification makes tracing
route-capability-aware: request-log-only evidence must not invent spans, and
an unsupported mandatory trace source cannot qualify.

## Requirement-to-evidence matrix

| Requirement | Implementation | Executed verification |
| --- | --- | --- |
| Submitted and actual request correlation | `correlation.py`, read/write/file/WS transports and ledger retain Run/Case/attempt/operation/resource associations, actual header/envelope IDs and explicit conflicts | `test_response_correlation.py` exercises rewritten IDs on successful and denied/error responses, propagation to ledger and exact log selectors; actual owned BCS denial and three-Story log tests exercise the product middleware |
| Capability-gated traceparent | Route binding explicitly selects disabled or w3c; finite valid nonzero trace/span generation and response/ledger validation | `test_correlation.py`, `test_log_observer.py`, `test_ledger.py`: disabled mode, submitted/effective contexts, malformed/zero identifiers and mismatched trace rejection. These are contract tests, not proof of deployed tracing |
| Exact environment/ID/time and finite budgets | `log_observer.py` validates selectors and returned identities/timestamps/limits; `diagnostics.py` selects actual response IDs, limits calls and total elapsed budget, and stores summaries only | `test_log_observer.py`, `test_diagnostics.py`, `test_case_diagnostics.py`: wrong scope, empty/truncated/error results, deadline/call exhaustion and lifecycle checkpoint behavior |
| Real local log integration | `local_logs.py` binds the owned process, JSON access sink inode/configuration and current Case ledger, with finite scans | `test_local_pilot_live.py::test_real_three_story_request_logs_are_observed_from_owned_json_sink` executes PRE-01/02/03 against actual BCS; all selected request logs are observed, exact cleanup and child shutdown checked |
| Scoped remote log acquisition | `observ_transport.py` dynamically discovers instances per invocation, checks environment/target/query echoes and matches exact recorded delivery attempts | Saved real acquisition inspected: 22 calls, three discovered instances, three ACKs matching three known Sends, TRANSPORT_OBSERVED. `test_observ_transport.py` covers fresh discovery and no cached-instance fallback. Current topology does not prove retired-instance coverage |
| Required versus diagnostic declaration admission | `evidence_policy.py` and `inventory.py` reject unsupported mandatory sources, removal/demotion, malformed or unknown diagnostics before manifest publication, pool lease or ledger creation | Seven new admission tests; both current suites unchanged; historical snapshot parser remains independent of current admission policy |
| Required versus diagnostic reporting | `reporting.py` compares actual `evidence_required` against the frozen Run inventory, with explicit owned-local OB-to-SQLite substitution; malformed/mismatched declarations block PASS in JSON/Markdown/JUnit | Four added report tests plus existing lifecycle diagnostics tests: failure preservation, offline historical comparison, no artifact rewrite, optional outages cannot replace required/functional results |

The finite required contracts remain API+persistence for Group, additionally WS
for Session, and additionally bytes for File. Logs/request correlation remain
diagnostic. Adding mandatory logs or traces requires a real executor/preflight
gate; changing JSON alone is explicitly rejected, not silently ignored.

## Verification and provenance

- Admission change: `efde32041`; complete owned BCS/CLI runner **1120 tests,
  OK, no skips, 181.349s**.
- Reporting change: `051a482c3`; complete owned BCS/CLI runner **1124 tests,
  OK, no skips, 181.736s**.
- The full-suite helper supplies approved actual BCS and product CLI binaries
  and their digests; opt-in integration tests were enabled, not skipped.
- Main executor inspected both full logs, the historical local pilot reports
  (13/19/23 diagnostic queries, all PASS without exhausted budgets), and the
  saved remote acquisition receipt. These saved receipts are historical
  evidence, not a new remote run or a claim of current credentials/topology.
- Independent read-only audit found no blocking issue in the admission/report
  patches or the original task-3.3 scope. Case guard, strict OpenSpec validation,
  diff checks and committed-range credential scans passed.

## Explicitly not claimed

- Live end-to-end trace propagation or exported spans: **NOT_VERIFIED**.
  Owned-local logs intentionally support request IDs, not trace selectors.
- Universal gateway/backend identity equality, complete historic log coverage,
  or successful queries as proof that no execution can still occur.
- Whole remote PRE-01/02/03 acceptance, BaaS/OB parity, or two full preproduction
  runs: remain separate tasks 4.2/9.6 and 5.3.
- Resource deletion, input cutoff, full producer drain or Bot-pool release:
  original retained Run remains BLOCKED, scope UNCONFIRMED and pool QUARANTINED.
- Legacy all-module singlebox coverage thresholds, complete migration, or a
  performance benchmark: that gate was not run and task 6.5 remains open.

Earlier task notes kept 3.3 open while individual fixes or actual log collection
were outstanding. This acceptance closes the original framework requirement
using the now-inspected implementations and execution evidence; it does not
remove any of the remaining environment, safety or migration requirements.
