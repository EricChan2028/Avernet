# Create Group Human Notification Implementation Plan

> **For agentic workers:** Use executing-plans to implement the steps in order with test checkpoints.

**Goal:** Allow ordinary collaboration-group creation to select the source of @Human notifications.

**Architecture:** Keep the selection in the creation form and map the domain field to the existing HTTP creation body. Reuse `HumanMentionNotifyMode` and creation-form dropdown primitives. The user confirmed that task-execute creation is excluded: hide the field and omit it from that branch's input.

**Tech Stack:** React, TypeScript, Umi Max, Jest, Testing Library.

## Approved behavior and contract

- Label: `@Human 通知来源`.
- Options: `所有成员` → `all` (default), `仅群主 Bot` → `driver_bot_only`, `不通知` → `none`.
- Free chat: right of automatic reply. Manager/worker: right of Manager Bot. Custom: after YAML validation, outside and to the right of the script summary box, in two equal-width columns (stacked on narrow screens). The edit button stays inside the summary box.
- Reopening resets to `all`; changing strategy within an open form preserves the choice.
- Ordinary creation sends top-level `human_mention_notify_mode` in `POST /openapi/v1/collaboration/groups` for all three strategies.
- Other callers may omit the optional domain field, preserving server-default behavior. No task-execute payload changes.

## Steps

- [x] Extend `test/services/workspace/groupCreateRequest.test.ts` with three strategies × three modes, asserting `human_mention_notify_mode` at the top level and omission for legacy callers.
- [x] Extend `test/pages/Workspace/components/Modals/CreateGroupModal.test.tsx` to cover default selection, changed choices in the three layouts, reset on reopening, YAML validation visibility, and task-execute exclusion. Run both suites and observe failures for missing field/UI.
- [x] Add optional `humanMentionNotifyMode: HumanMentionNotifyMode` to creation input types and optional `human_mention_notify_mode: HumanMentionNotifyMode` to the three HTTP creation bodies. In `groupCreateRequest.ts` map the optional field with a conditional spread in the common body.
- [x] Add `CreateGroupHumanNotifyMode.tsx` with the dropdown and open/reset state hook. Wire it through `CreateGroupModal.tsx`, `GroupConfigFields.tsx`, and a summary action slot in `BindingSlot.tsx`. Keep the modal within 300 lines by reusing its existing `setField` helper for driver/manager changes.
- [x] Run focused modal/request/service/hook tests, typecheck, lint, guardrails, and open-core checks. Check changed source line counts and review the diff against the approved behavior.

All paths in Steps are relative to `apps/frontend-nextgen/`. Verification commands run there:

```sh
npm test -- --runInBand test/services/workspace/groupCreateRequest.test.ts test/pages/Workspace/components/Modals/CreateGroupModal.test.tsx
npm run typecheck
npm run lint
npm run ci:guardrails
npm run check:open-core
```

## Verification results

- Before implementation: 16 expected failures for the missing dropdown/request field.
- After implementation: 7 suites / 102 tests passed, including creation, visibility scopes, binding, management, and default-session navigation.
- Typecheck, full frontend lint, guardrails, open-core checks, and `git diff --check` passed.
- Production build passed; generated CSS includes the responsive three-column layout. Build emitted the existing Node localStorage experimental warning.
- Modified/new source files meet file-size gates; the creation modal is 300 lines.
- Browser visual inspection and live API verification were not run; UI placement is covered by DOM assertions and payload mapping by request tests.
