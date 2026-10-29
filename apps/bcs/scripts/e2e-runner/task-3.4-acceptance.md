# Task 3.4 acceptance: scoped database observer

Date: 2026-10-13. Status: **observer implementation contract complete**.
This document concerns observer implementation, not full preproduction rollout.

## Original requirement and implementation evidence

| Requirement | Implementation and exercised boundary |
| --- | --- |
| Discover database/table/schema | `metadata_probe.py`, `alisddbquery.py` and `alisd_wire.py` implement explicit discovery, normalized SHOW TABLES/SHOW CREATE TABLE parsing, fixed required-column checks and fail-closed malformed/error/truncated metadata handling. Tests: `test_metadata_probe.py`, `test_alisd_wire.py`, `test_alisddbquery_process.py`, `test_alisddbquery.py` |
| Confirm physical target with honest provenance | `remote_observer.py` pins complete Profile/Run, database/environment, receipt digest and validity. Bare profile booleans or table existence cannot establish mapping. The adopted owner-confirmed shared-database variant explicitly labels attestation and unverified deployment, rather than inventing independent inspection. `mapping_confirmation.py`/`mapping_recheck.py` separate persistent facts from one-shot query authority. Tests: `test_remote_observer_cli.py`, `test_shared_database_review.py`, `test_mapping_recheck.py` |
| Allowlisted, read-only queries | `evidence.py` defines finite semantic assertions/projections; `alisddbquery.py` builds bounded tool arguments from validated identifiers, not Story SQL. Metadata commands cannot query business rows. Extended delivery/producer/downstream surfaces each require separate scoped selection; no DML or secret/config/blob projection is admitted |
| Exact resource and parent scope | `case_scope.py` and `observation_gate.py` validate current Case/attempt, created/live identity, full owned parent chain and unchanged before/after scope. Remote queries require env plus exact IDs; delivery reads further bind Group/Session/two Bots to the ledger and held lease. Tests: `test_observation_scope.py`, `test_case_scope.py`, `test_observation_gate.py` |
| Tool row cap and completeness | Existing exact-one reads use limit 2 to reject ambiguity. Collection contracts retain finite caps; full pages, tool errors and invalid truncation flags never imply completeness. Both normalized clients and wire responses reject incomplete metadata/results. Tests: `test_evidence.py`, `test_delivery_observation.py`, `test_producer_inventory.py` |
| Bounded keyset pagination where needed | `delivery_pagination.py` is integrated into explicitly selected retained rechecks, not a disconnected utility. Integer-primary-key traversal keeps exact filters, page/row bounds, original deadline and per-page shared-call accounting; old reviews remain unchanged. Only a complete, finally authorized scan supplies downstream alias evidence. Seventeen new tests exercise real SQLite queries and actual CLI/ledger/lease/MCP-client composition with synthetic tool replies |

The metadata compatibility claim is **required-column presence plus supported
DDL parsing and per-row semantic validation**, not full database schema/version
or deployed-release certification. A schema mismatch blocks affected observation.

## Concrete corrections completed during this audit

- `cd481e914`: reject malformed truncation flags and incomplete normalized
  catalogues/schema before row queries, not only in the mcporter wire parser.
- `cca1321e4`: opt-in bounded keyset traversal through the existing command path;
  distinguish page attempts from actual query attempts, reject post-scan
  authority loss, and preserve legacy permissions/cardinality limits.
- `5be3738b7`: post-return binding validation: a reproduced CLI case previously returned
  PASS when a database query completed after its review expired. Catalogue,
  table, schema and resource reads now reject in-flight invalidation before
  exposing successful responses. Primary transport errors are not overwritten;
  API functional/cleanup outcomes remain independent.

## Executed evidence and qualification limits

- Complete owned BCS/CLI regression after keyset implementation: **1145 tests,
  OK, no skips, 179.602s**. The actual product opt-in tests were enabled.
- Keyset-specific tests: **17 passed**; original wait-v2 tests: **20 passed**.
- Post-return binding focused tests: **25 remote-observer tests passed**;
  keyset **17 tests passed again**.
- Final complete owned BCS/CLI regression including post-return checks:
  **1148 tests, OK, no skips, 181.407s**. Main executor inspected the completed
  process and log. Case guard, strict OpenSpec validation, diff checks and
  committed-range credential scans passed; no push was performed.
- A saved real retained-Group report was inspected locally: semantic evidence
  `source=alisddbquery`, `OBSERVED`, required evidence PASS and functional PASS.
  Its overall result remains BLOCKED, cleanup RETAINED and pool QUARANTINED.
  Mapping provenance is OWNER_CONFIRMED_SHARED_DATABASE, with independent/live
  deployment verification explicitly NOT_PERFORMED. This is historical evidence,
  not a new remote call or current authorization/topology verification.
- Independent read-only review found and rechecked the keyset accounting and
  final-validation defects. The final in-flight binding patch was also reviewed.
  The original six requirement axes were independently audited with no remaining
  implementation blocker after the final full regression. Tests and source
  establish implementation behavior, not universal backend parity.

## Remaining work outside this acceptance

- **Live MySQL/OB keyset execution and optimizer/latency qualification are not
  performed.** SQLite establishes query/traversal behavior; synthetic MCP
  replies establish wire/lifecycle integration, not deployed optimizer behavior.
- Returned rows are bounded; database rows examined/sort work may be larger.
  Operational adoption needs scoped deployed schema/index/latency checks.
- Traversal is explicitly non-atomic. It cannot prove no concurrent inserts,
  commits below the cursor, subsequent input, or whole-scope termination.
- Full PRE-01/02/03, remote BaaS/file behavior, two complete pilot runs and
  current deployment qualification remain in tasks 4.2/9.6, 5.3 and 1.3.
- Original remote resources remain retained; input-boundary/producer closure,
  cleanup and pool reuse remain separately gated. No permission is granted here.
- Legacy all-module singlebox coverage and full migration remain open (6.5/6.6).
  No thresholds, performance benchmark, new remote Run or release gate are
  claimed by completion of the observer implementation task.
