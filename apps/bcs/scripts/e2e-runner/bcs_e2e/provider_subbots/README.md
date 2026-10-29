# Provider subbots owned-local slice

Independent migration slice for the legacy parent
`story_provider_operator_manages_subbots`.

## Status

- Local executor: `LOCAL_EXECUTOR_ONLY`.
- Preprod: `REMOTE_UNSUPPORTED`.
- Remote validation: `NOT_RUN`.
- Release credit: **none** (`grants_release_credit=false`).
- Reviewed release denominator: not included
  (`release_parent_denominator=false`).
- Evidence granularity: `OWNED_CHILD_RECEIPT`.

The parent remains in `KNOWN_UNMAPPED_LEGACY_IDS` and in the 41-row legacy
inventory outside the reviewed 31-Story release denominator. Promotion to the
release denominator requires a separate review; this slice does not perform it.

## Upstream authority

The source binding is frozen in
`provider-subbots-migration-contract.json` against these files:

| Source | Purpose | Frozen git tip |
| --- | --- | --- |
| `apps/bcs/scripts/e2e-test/agentpass_subbots_story.py` | Executable 131-assertion story | `49ce1afc9f3d2b1f1dc9ec485f8cd368ade2b873` |
| `apps/bcs/scripts/e2e-test/e2e.sh` | Legacy shell registration/entry | `0873001b66c47fe32c43b5b9d0074dd6cc927c2c` |
| `apps/bcs/docs/agentpass-subbots.md` | Product acceptance narrative | `a30d8c2e20acfe2dc8e0eaa0b9f8edf5d9451898` |

Pinned SHA-256 values are in the contract. Any upstream byte change fails the
binding before a runtime starts.

The current OpenSpec evidence note mentions
`story_provider_operator_publishes_agent`, and the current
`next-story-migrations.md` has no subbots section. That is a planning-document
gap, not an acceptance ambiguity: the executable story and the product
acceptance document both identify the parent above. This slice records the gap
without editing OpenSpec, manifests, shared inventories or freeze documents.

## Frozen obligations

The story's `Story.checks` count is exactly **131**:

- 47 direct request status checks outside `self_view`.
- 75 checks from 15 `self_view` executions
  (9 successful projections × 7 checks and 6 rejected projections × 2 checks).
- 9 explicit checks outside `self_view`.

The fifteen independently checked obligations are:

1. `PROVIDER-SUBBOTS.provider-registration-discovery`
2. `PROVIDER-SUBBOTS.admin-complete-ref-validation`
3. `PROVIDER-SUBBOTS.admin-sibling-registration`
4. `PROVIDER-SUBBOTS.scoped-complete-ref-validation`
5. `PROVIDER-SUBBOTS.scoped-sibling-distinct-identity`
6. `PROVIDER-SUBBOTS.duplicate-reference-conflicts`
7. `PROVIDER-SUBBOTS.self-lookup-actor-binding`
8. `PROVIDER-SUBBOTS.legacy-api-actor-binding`
9. `PROVIDER-SUBBOTS.metadata-update-parent-immutability`
10. `PROVIDER-SUBBOTS.onboard-parent-immutability`
11. `PROVIDER-SUBBOTS.parent-discovery-fail-closed`
12. `PROVIDER-SUBBOTS.callback-actor-binding`
13. `PROVIDER-SUBBOTS.signed-principal-live-state`
14. `PROVIDER-SUBBOTS.disable-enable-state`
15. `PROVIDER-SUBBOTS.deletion-tombstone-retirement`

`checks.py` validates these obligations independently from non-secret
observations. The child never persists a Bot runtime token, Provider admin
token, signed Gateway principal, signed-principal header value or browser state. The
runner also scans the child streams, console and generated reports for the
frozen redaction canaries before writing them.

## Landing recipe

The slice is intentionally self-contained and is not registered by the shared
command or migration routers. Integration is a separate, reviewed change.

Prerequisites:

- A fresh state directory that does not exist yet.
- An explicitly supplied BCS binary and its SHA-256 digest.
- Explicit process, fixture/business-write and state-write permissions.
- Exact Run ID confirmation.

Lifecycle:

1. `provider_subbots_source_binding()` validates the frozen sources,
   obligations, anchors, config fragment, backlog status and no-release-credit
   boundary.
2. `provider_subbots_config_overlay()` calls the shared
   `prepare_local_bcs_inputs`, then merges the slice-owned top-level pin from
   `provider-subbots-required.json` into the private 0600 config. Shared
   `local_config.py` is never edited.
3. `OwnedBcsProcess` starts and verifies the exact owned listener.
4. The black-box `story.py` child runs against the loopback origin using only a
   minimal environment containing the owned per-run
   `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE`.
5. The runner requires child exit 0, exactly one marker with 131 assertions,
   all fifteen semantic checks PASS, redaction PASS, an unchanged binding,
   unchanged executor/binary digests and owned runtime `STOPPED` with return
   code 0.
6. Canonical summary, JUnit and Markdown reports are published with
   `remote_validation=NOT_RUN` and `grants_release_credit=false`.

## Preprod gap

`PREPROD_PROVIDER_CONTROL_PLANE_ABSENT`: the preprod public-v1 profile exposes
only `group.*` and `session.*` route bindings. It has no Provider
registration/admin routes (`/providers`, `/providers/{id}/bots`), no
parent-discovery or Provider callback binding, and no `provider_admin_token`
credential channel. Remote validation therefore remains `NOT_RUN`; the local
PASS is not remote evidence and grants no release credit.

## Focused verification

```bash
cd apps/bcs/scripts/e2e-runner
PYTHONPATH=. /usr/local/bin/python3 -m unittest \
  tests.test_local_provider_subbots_contract \
  tests.test_local_provider_subbots_checks \
  tests.test_local_provider_subbots_runtime \
  tests.test_local_provider_subbots_run -v
```

The tests cover contract tampering, source drift, config overlay conflicts,
marker/count drift, redaction failure, binding drift, permission/fresh-state
guards, all fifteen positive obligation checks and one reverse-failure case per
obligation.
