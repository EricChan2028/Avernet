# Local entry switch and rollback

Effective after task 6.6, a direct local invocation of
`apps/bcs/scripts/e2e_coverage.sh` uses the `runner.py local-coverage-suite`
execution core. The core does **not** replace Rust unit tests, remote execution,
or the historical coverage gate; those contracts remain separate.

```sh
bash apps/bcs/scripts/e2e_coverage.sh \
  --binary <owned-or-instrumented-bcs> \
  --binary-sha256 <sha256> \
  --state-dir <fresh-suite-state-dir> \
  --run-id <unique-run-id> \
  --confirm-run-id <unique-run-id> \
  --allow-local-processes \
  --allow-fixture-write \
  --allow-write
```

The run ID and state directory are explicit inputs. The shell never invents or
reuses them, and a failed or unavailable core never falls back to the old shell
entry. Optional CLI binary arguments remain paired and validated by the runner.

The core branch is a direct `exec` into `runner.py local-coverage-suite`; it does
not run the legacy port preflight, mock/singlebox startup, EXIT trap, bcs port
termination, or cargo/HTTP/CLI coverage aggregation. The new runner owns all
case runtime lifecycle and local evidence. The legacy orchestration stays
authoritative for the line/endpoint/CLI coverage gate and is selected only by
`--legacy`.

## Why the switch is enabled

The switch decision is recorded only after all 29 frozen release parents have
an owned-local executor with integrated canonical reporting. The current frozen
inventory has 29/29 registered executors, zero partial executors, and no switch
review blockers. The final retained local proven run at
`scripts/.dependencies/e2e-owned-runtime/coverage-suite-20261024-final-003437/run/suite-summary.json`
recorded `LOCAL_COVERAGE_SUITE_PASS`, 29/29 PASS, and no BLOCKED/FAILED cases.

That result is evidence only; it does not grant preprod credit, release
qualification, or remote validation. Core reports continue to retain
`remote_validation=NOT_RUN`, `grants_release_credit=false`, and
`full_release_status=NOT_QUALIFIED`.

## Explicit legacy rollback

The old local shell entry remains available, but only as an explicit rollback
and compatibility admission for the coverage harness until its independent
core-parity review closes:

```sh
BCS_E2E_LOCAL_CORE=0 bash apps/bcs/scripts/e2e_coverage.sh ...
# or
bash apps/bcs/scripts/e2e_coverage.sh --legacy ...
```

Rollback is not a fallback for a failing core run. It is a deliberate transition
decision made before the next run and recorded in evidence. Core-only arguments
are rejected in legacy mode.

`apps/bcs/scripts/pre_push.sh`, `scripts/ci/singlebox_coverage.sh`, and
`.github/workflows/e2e-tests.yml` intentionally retain explicit `--legacy`
rollback while their unified coverage, endpoint-coverage, and CLI-coverage
artifacts still depend on the historical shared-stack entry. That retention is
not a remote or release credit, and it does not shadow the default core decision.

The switch keeps `apps/bcs/scripts/ci_test.sh`, cargo/nextest, and all Rust unit
test targets exactly as they were.

## Ledger recovery

The owned-local ledger and runtime directories under the supplied fresh state
directory are evidence, never disposable input for re-execution.

- A normal successful core run stops only its own owned BCS processes and leaves the
  ledger and reports under its supplied state directory.
- A failed, BLOCKED, cancelled, or killed run never signals a legacy rerun or an
  automatic replay.
- Do not delete, rename, or reuse a failed state directory. Re-run with a new
  state directory and a new run ID after inspecting the retained failure.
- Any local ledger recovery is exact-id: inspect the failed run, compare its intent and
  child-run identities, then use the reviewed case-specific cleanup path. A failure to
  identify the owning runtime, process, lease, or run ID must remain BLOCKED.
- Failed pools and `UNKNOWN` resources are retained for diagnosis unless an owner
  approves explicit cleanup. Resuming a cancelled suite does not automatically re-enter
  any unfinished child run; use a new suite run instead.
- No preprod or production ledger migration is introduced by this local shell switch.
  Preprod recovery remains under the separate 9.6 authorization and policy.

## Script regression and drift

The isolated `--self-test` mode asserts that:
- default invocations without core operands fail closed without calling the runner or legacy entry;
- required core operands dispatch exactly one `runner.py local-coverage-suite` execution;
- core failure is terminal and cannot start the old entry, lsof cleanup, singlebox stop, or coverage aggregation;
- legacy-only startup/stop/rebuild/coverage options are rejected in core mode;
- explicit `--legacy` or `BCS_E2E_LOCAL_CORE=0` preserves the exact old entry and coverage gate;
- no core failure, missing operand, or argument drift falls back to legacy.
