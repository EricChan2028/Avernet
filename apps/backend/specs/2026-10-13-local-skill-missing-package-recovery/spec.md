# Restore a missing Bot-owned Local Skill package during replacement

## Problem and scope

A Bot-owned Local Skill can retain its metadata and desired-state references
after its package disappears. Same-name upload currently reaches the legacy
replacement adapter and fails instead of restoring the validated new package.

Implement recovery of this unique existing identity. Shared-package deletion
protection, historical deduplication, standalone renaming and production data
repair are outside this change.

## Contract

- Authorize and acquire the existing edit guard before resolving the unique
  existing Skill and its layout-owned locator.
- Retain the Skill ID, owner, recorded `git_path`, Installation and Membership.
- An explicitly missing package may be restored from the validated complete
  upload. Unreadable storage must fail before staging or metadata writes.
- In the Legacy Adapter, stage and verify the upload before publishing it to
  the canonical package. Back up the old package only when one exists.
- If missing-package recovery in the Legacy Adapter fails during publication,
  metadata, audit or final cleanup, restore the previous metadata and original
  package absence. Existing-package replacement behavior is unchanged.
- Preserve existing runtime projection semantics: a PENDING projection retains
  the committed new package and reports its unresolved runtime status.
- Modern Engine package apply already accepts a missing package. Its Backend
  metadata replacement must continue to retain the original identity; runtime
  success followed by DB failure retains the runtime bytes for retry.

HTTP request/response schemas, status mappings, DI and database schema stay
compatible. Core recovery and the OCB ARCA listing implementation must agree
that absence is distinct from permission, transport, proxy and response errors.

## OCB propagation

Reviewed against OCB `dev@d8331bb42b`, whose Avernet gitlink is
`0f5c58c3909379cd12a873d18fee3b2b8e742486` at `submodules/avernet`.
ARCA `DeviceFileSystem.list_dir` delegates through `SandboxRuntimeClient` to
`arca_io.list_arca_directory`; the filesystem selects strict absence semantics
through an optional `preserve_read_errors` argument. Other client and direct
listing callers retain their existing default behavior.
The enterprise release must include both changes and the updated gitlink.

## Acceptance

- Active and inactive missing packages recover without creating a new Skill.
- Missing-package recovery faults in publication, metadata, audit and cleanup
  preserve original absence and metadata.
- Denied or unavailable directory reads cause no writes.
- Existing-package replacement, locator validation and runtime PENDING behavior
  remain covered by the existing suite.
- OCB tests cover explicit directory absence, generic 404, authorization and
  server failures, timeout, invalid successful responses and empty directories.

Simulator validation is waived for this task by the user because it is occupied.
Record unit/contract/architecture/DI, review and CI evidence independently from
merge, deployment and production runtime verification.
