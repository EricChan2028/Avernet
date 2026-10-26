# Task 3.2 acceptance: isolated CLI, WS probe and credential boundaries

Date: 2026-10-13. Status: **framework integration and safety contract complete**.
This is not remote environment acceptance or full release qualification.

## Requirement and scope

The task requires integration of the existing CLI with an isolated temporary
context, a WS probe, and verification of token/session/share-URL redaction and
cross-origin credential isolation. The lifecycle specification permits an
unsupported CLI route to remain BLOCKED; an HTTP substitute must not count as
CLI coverage. The initial remote pilot excludes full CLI coverage. Later task
6.1 owns complete CLI/file Story migration; tasks 4.2/9.6 and 5.3 own actual
preproduction Session/WS and complete pilot acceptance.

Earlier implementation notes kept 3.2 open while referring to remote CLI or
file migration. Those notes accurately described missing remote capabilities,
but assigned a broader completion condition to 3.2 than its original wording.
This acceptance corrects that task attribution, not the remaining capabilities,
Case inventory, gate policy or remote readiness result.

## Requirement-to-evidence matrix

| Original requirement | Implementation | Executed verification |
| --- | --- | --- |
| Existing product CLI with isolated temporary context | `bcs_e2e/local_cli.py` invokes an explicitly selected binary/digest; private HOME/BOT_DATA_DIR/TMPDIR, 0700 directories, 0600 session.json, allowlisted child environment | `tests/test_local_cli.py` isolation/no inherited token, proxy or URL; `tests/test_local_pilot_live.py` uses actual product CLI + owned BCS |
| Context consumes real identity, not a fake CLI or unauthenticated read mislabeled as auth | Original Session read remains `authentication=NONE`; separate `collaborate permission` uses a newly registered driver token, asserting caller/owner and exact Group/Session | Real CLI rejects empty/invalid token contexts and accepts the generated driver credential; optional `local-run` integration gates on both checks |
| Temporary credential lifecycle and sanitized reporting | Driver-only in-memory handoff; no token in fixture/profile/ledger/report; per-invocation private session file cleared after execution | Success/error/cancellation, scrub failure and combined child-shutdown uncertainty; live context/log scan has no generated token after normal cleanup |
| WS probe integration and session-token redaction | `bcs_e2e/probe.py`, `ws_transport.py`; exact live Run-owned Group/Session, explicit token reference, bounded Upgrade/connect, scope recheck, local close | Actual owned BCS invalid/valid issued-token probes; Run files unchanged; probe failure/close/cancel tests; no redirects or retries on rejection |
| Share/signed-URL redaction and cross-origin auth isolation | `bcs_e2e/file_transport.py` allows only declared product/storage origins, strips product authentication after redirects, token-only shared content, bounded hops | `tests/test_storage_wire.py`: six actual urllib tests with two independent loopback HTTP receivers; no Cookie/Authorization/browser/request/trace identity reaches storage; signed paths and tokens absent from evidence |
| No credential echo through correlation metadata | `bcs_e2e/correlation.py` rejects known actual request secrets before retaining response IDs; File request context includes local Principal and share token | `tests/test_response_correlation.py` covers product success/failure, proxy upload and expected-404 envelopes; separate probe defense-in-depth check remains |

Synthetic HTTP receivers establish wire behavior, not BaaS/cloud deployment
compatibility. The real CLI and BCS tests run only against a fresh owned local
runtime. No permission probe starts a state machine or calls a remote Bot.

## Validation and version provenance

Implementation increments:

- `4e0d5197a`: probe result finalized after local closure, scope/state checks.
- `c00476c19`: standalone probe against actual owned local BCS.
- `524f96547`: authenticated product CLI context and lifecycle integration.
- `c7c399e46`: actual two-origin HTTP credential tests.
- `db7c7b531`: safe actual response IDs and file credential-echo coverage.

The main executor inspected and executed the tests, not just their definitions:

- Complete owned BCS/CLI runner: **1109 tests, OK, no skips, 150.860s** after
  the response-ID increment.
- Complete owned BCS/CLI runner: **1113 tests, OK, no skips, 150.130s** with
  adjacent nonzero-trace validation subsequently committed as `690609a10`.
- The live test helper explicitly supplies approved BCS and CLI binaries plus
  their SHA256 values; the opt-in product tests were not silently skipped.
- Case guard, strict OpenSpec validation, diff checks and committed-range
  credential scans passed for these increments.
- Independent read-only task audit matched the original requirements to code
  and assertion coverage; the main executor separately verified run logs.

The legacy all-module singlebox coverage gate was **not run**. This acceptance
does not claim coverage thresholds or compatibility migration gate completion.

## Remaining scope: unchanged and not accepted here

- Remote CLI Cookie/OAuth identity and route compatibility: **NOT_TESTED**;
  unsupported commands must not fall back to local credentials or HTTP coverage.
- Complete CLI/file and remaining Story migration: task **6.1 remains open**.
- Actual preproduction PRE-02 execution, persistence, downstream completion and
  two same-version runs: tasks **4.2/9.6 remain open**.
- Actual preproduction BaaS/storage and two complete pilot runs: **5.3 open**.
- Socket close means local handle closure, not peer acknowledgement or Bot
  completion. Probe execution state remains **UNCONFIRMED**.
- Normal credential scrubbing is not secure erase or a hard-kill guarantee.
- The original preproduction Run remains retained and its pool quarantined;
  this document neither rewrites historical outcomes nor releases resources.
- The frozen legacy inventory and full-release qualification remain unchanged.
