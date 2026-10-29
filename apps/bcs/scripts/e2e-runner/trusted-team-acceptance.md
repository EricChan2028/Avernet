# Trusted-team local business mapping and remaining integration

This document maps the five source-bound obligations of
`story_user_builds_trusted_team`. The separate trusted-team manifest now registers
the owned-local executor; neither this mapping nor registration certifies a remote
deployment or replaces executed regression results. The original parent
and helper sources remain frozen by `migration-contracts.json`.

## Local business mapping

The opt-in combined executor uses Case `LOCAL-TRUST-API-CLI-01`. All assertion
suffixes below are prefixed with that Case ID and a dot. The default API-only
executor uses `LOCAL-TRUST-API-01` and does not satisfy the CLI obligation.

| Obligation | Required business evidence in the executor |
| --- | --- |
| `FRIEND.public-accept` | ENG public visibility API+SQL; `CEO-ENG-created`; `CEO-ENG-accepted-stored`, `-sent`, `-received`; `CEO-ENG-friends` and `ENG-CEO-friends`. The new request is immediately accepted, with no manual accept call. |
| `FRIEND.protected-accept` | PM protected visibility; `QA-PM-created`; pending storage and both directions of request reads; accepted storage and both directions; `QA-PM-pending-cleared`; bilateral friendship present. PM decides the exact freshly returned request. |
| `FRIEND.protected-reject` | CS protected visibility; `ENG-CS-created`; pending storage and both directions; rejected storage and both directions; `ENG-CS-pending-cleared`; bilateral friendship absent. The `*-friends` IDs in this branch assert absence. |
| `FRIEND.list` | `CEO-ENG-friends` asserts the specific ENG relationship, not a nonzero unrelated list. This shares evidence with public acceptance rather than repeating an identical API call. The final actual CEO CLI list separately verifies exactly ENG and PM. |
| `FRIEND.cli-accept` | `cli-request-output`, `CEO-PM-created`, pending API+SQL, `cli-received-exact-request`, `cli-accept-output`, accepted API+SQL, pending cleared, bilateral friendship, `cli-ceo-list-includes-pm`. Four actual CLI subprocesses use CEO/PM/PM/CEO identities and exact request identity. |

Fresh managed identities and empty API/SQLite incident relationship/request
history are mandatory **baseline evidence**, not additional business PASS
counters. Request IDs must be new and nonempty; existing friends, existing pending
requests, a first unrelated pending item, warnings and skipped accepts cannot
qualify. Final complete inventory is four requests and three friendships. The
five roles' original visibility is verified through API and SQLite after guarded
restoration. Unknown writes stop the sequence and retain the local DB.

The wire adapter also requires real CLI exit zero, exact HTTP status, a true
success envelope, type-sensitive JSON agreement, one forwarded request per
command, and cleared owned credentials. These are blocking adapter checks, not
invented additional business requirements or CLI endpoint coverage statistics.

## Semantics versus exact legacy presentation

The old CLI helper parses human-readable output and relies on default request
filters. The new sequence uses JSON and explicit received/pending filters, keeping
PM protected until acceptance so the decision is substantive. Human-readable
formatting/default-filter compatibility is not claimed and is not one of the
five frozen semantic obligations. Likewise, edge friend-connections/cancel,
permission graph synchronization and notification delivery belong to separate
contracts; their lack of qualification is not a sixth trusted-team helper.

## Remaining implementation work

1. **Local registration and requirement binding are implemented.** The separate
   `owned-local-trusted-team-v1` suite registers this Case and all five requirement
   mappings, with 58 required assertions and the unchanged 29-parent denominator.
   Its manifest/executor/source/binary binding is frozen before fixture writes and
   checked through the lifecycle. The offline plan exposes LOCAL_EXECUTOR_ONLY,
   not migrated count or PASS credit. The existing pilot and retained suites do
   not implicitly select this parent; shared pilot preparation rejects the new
   suite until an appropriate Run/Profile lifecycle is integrated.
2. **Canonical local reporting is integrated.** The registered parent freezes
   its schema-2 five-role Profile and context-v2 binding before business writes.
   The owned lifecycle projects its journal and shutdown into the declared
   `report-LOCAL-TRUST-API-CLI-01.json`, uses the shared JSON/JUnit/Markdown
   renderers, and seals source hashes with a last-written completion receipt.
   Ordinary offline report viewing revalidates the binding and complete source
   set; incomplete viewing does not mutate the publisher's outputs. Functional,
   evidence, visibility/restoration, retained data and owned-process outcomes stay
   separate. Cancellation and publication failure cannot silently become PASS.
   Old context-v1 and unregistered smoke artifacts are not auto-adopted. Report
   integration is not remote or full-release qualification.
3. **Provide and validate the remote attachment.** Local credentials, loopback
   capture and owned SQLite cannot prove gateway authentication, borrowed-role
   authority, pool isolation or remote side-effect/retention safety. Keep the same
   semantic obligations, bind actual supported operations, and block unsupported
   environments rather than rewriting their expectations.

Until declared environment acceptance and full release integration are complete,
`full_story_status=NOT_MIGRATED` remains truthful. Local success may be described
as coverage of all five legacy trusted-team business obligations, not full release
migration, full CLI coverage, old Singlebox thresholds or preproduction success.
