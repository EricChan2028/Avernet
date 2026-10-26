# Owned-local coverage suite

`local-coverage-suite` sequentially runs the 29 `local_registered_executors` in
migration-inventory order. After task 6.6 this is the default execution core of
the local `e2e_coverage.sh` entry; the old shell entry is explicit rollback
only. It is still **not** a Rust unit-test entry, shared singlebox startup, or
remote/release qualification gate.

```sh
python3 runner.py local-coverage-suite --list-cases

python3 runner.py local-coverage-suite \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --cli-binary "$CLI_BINARY" --cli-binary-sha256 "$CLI_SHA256" \
  --template-dir "$TEMPLATE_DIR" --template-manifest "$TEMPLATE_MANIFEST" \
  --template-manifest-sha256 "$TEMPLATE_MANIFEST_SHA256" \
  --panel-asset "$PANEL_ASSET" --panel-sha256 "$PANEL_SHA256" \
  --state-dir "$FRESH_SUITE_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
```

Run these commands from this runner directory, or give the absolute path to
`runner.py`. Use Python 3.10+. Run IDs have at most 120 characters, leaving room
for the per-case `-01` through `-29` suffix. No credentials are command arguments.

## Inputs and isolation

- Every case gets its own `cases/<case-id>/` state directory and fresh owned BCS
  runtime. Nothing attaches to a shared service. Existing suite or case paths
  (including symlinks) are never adopted, overwritten or automatically removed.
- Existing single-parent commands and the two existing full-parent lifecycle
  functions are reused. The four read-only and seven mutation Edge parents use
  the existing per-case functions and owned adapters, not the batch wrappers:
  each Edge parent runs once, in its own runtime.
- `--list-cases` is read-only and lists every command/function and required
  argument. Inventory/mapping drift blocks execution rather than silently
  dropping or auto-dispatching a new case.
- BCS and CLI binary hashes are recorded and verified. A missing/mismatched CLI
  blocks only CLI-dependent cases; an invalid BCS binary blocks all cases.
- Panel defaults to the actual `apps/bcs/assets/panel/dist/index.umd.js` in this
  worktree. An explicit asset is also accepted. If no panel SHA is supplied,
  the actual file is hashed; the existing `LocalPanelAsset` validator and owned
  copy validate it. Missing/invalid assets block the Network case, without a
  download, build, placeholder, or fake response.
- Template directory defaults to `apps/bcs/seeds/collaboration-templates`. Supply
  the existing approved manifest and its SHA (the `LocalTemplateAsset` schema
  documented in README). No template catalog/approval is fabricated. Missing
  approval blocks Structured only; a supplied catalog is validated and copied
  into that case's owned state by the existing executor.
- Custom collaboration uses the existing lifecycle's real YAML definition in
  its own state directory; the aggregator does not simulate its result.

## Outcome and retained evidence

`suite-summary.json` and `suite-report.md` are checkpointed at suite level.
They retain suite/case run IDs, binary paths and hashes, asset references, every
case's status, sanitized failure category/error type, duration and state path.
Individual executors retain their existing evidence; standalone Edge execution
also writes `coverage-case-execution.json` inside its case directory.

Normal exceptions, BLOCKED and nonzero case outcomes **continue to the next
case**. A successful process exit cannot override a non-success report. Exit 0
requires 29 PASS results; any failure/blocker returns 2. SIGINT/SIGTERM or a
returned cancellation stops dispatch and returns 130: unfinished cases are
explicitly CANCELLED, with `attempted=false` for those never started. Owned
runtime context managers remain responsible for stopping their own children.

No failed directory is cleaned, resumed or retried automatically. Rerun with a
new suite directory; inspect retained state before any explicit recovery. Every
report states `remote_validation=NOT_RUN` and `grants_release_credit=false`.
Its orchestration field remains `entry_switch=NOT_PERFORMED`: the runner cannot
vouch for which shell entry called it. The 6.6 shell-level switch is reviewed in
`local-entry-switch.md` and asserted by the golden shell entry, not by a
per-suite self-report.

## Implementation placement

The aggregator is co-located with command registration in
`bcs_e2e/local_story_commands.py`: adding a top-level Python module exceeded the
current artifact source-file budget. This keeps the new implementation covered
by the existing executor digest without modifying/bypassing `artifact.py` or
changing any existing case logic. The file remains below the 1,000-line gate.
