# Legacy harness re-freeze review — 2026-10-29

New frozen base: `8559a7a5258a2cb6750befec187e50aa85060cc6` (worktree `e2e-dev-fresh`, branch codex/e2e-dev-fresh-31bbfdc).
Previous freeze generations: `48b23001e` (umbrella + 15 families) and `71860eae` (8 families), `74dd751b` (caller-execution).

## Drift classes found (frozen pin -> dev tip)

1. **Upstream #3173 (0873001b6) retired service session policies and callbacks.**
   - `stories.sh`: service_spec 200-persistence legs replaced by 410 Gone + `service_policy_retired`;
     the release-invocation fixture patch leg was removed from `_story_complete_and_invoke_sessions`.
   - `cli-stories.sh`: `story_cli_operator_runs_sessions_and_services` lost its service fixture patch leg.
   - `e2e.sh`: now sources `service_policy_story.sh` (new story `story_retired_service_policy`).
   - Disposition: **adopt retirement semantics**; obligations updated; new story registered.
2. **Our enabling harness patch** (`8559a7a52`, committed before this review):
   `common.sh` bcs-cli binary path `src/bcs` -> `apps/bcs` + local loopback admission.
   Disposition: harness deployment-portability lineage (#3043); obligations unchanged.
3. All other pinned source files are byte-identical to their freeze generation
   (authority/draft/fixed-loop/edge/webhook*session* harness files unchanged at dev tip).

## Per-contract pin table

### caller-execution-migration-contract.json
- old source_revision: `74dd751b623b7701608374fc4c7c4a6cf58278da` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `caller_execution_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old 7d7f565b0b9c… -> new 7d7f565b0b9c…, hunks=0
- `caller_e2e_coverage.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 4e4ee24551ec… -> new 4e4ee24551ec…, hunks=0

### channel-cli-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   

### channel-http-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   

### collaboration-team-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \

### custom-collaboration-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \

### delivery-policy-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   

### direct-chat-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `actor.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 9b7679964e0a… -> new 9b7679964e0a…, hunks=0
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then

### draft-group-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `collaboration_draft_group_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old f0df15f704b9… -> new f0df15f704b9…, hunks=0
- `indentation-regression.json` **DIGEST-MISMATCH-AT-FREEZE**: old 23e0a22ea7f1… -> new 23e0a22ea7f1…, hunks=0
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0

### edge-admission-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `edge_permission.sh` **DIGEST-MISMATCH-AT-FREEZE**: old e395ff4f203c… -> new e395ff4f203c…, hunks=0

### edge-bot-search-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `edge_permission.sh` **DIGEST-MISMATCH-AT-FREEZE**: old e395ff4f203c… -> new e395ff4f203c…, hunks=0

### edge-friend-connections-list-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `edge_permission.sh` **DIGEST-MISMATCH-AT-FREEZE**: old e395ff4f203c… -> new e395ff4f203c…, hunks=0

### edge-friend-requests-list-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `edge_permission.sh` **DIGEST-MISMATCH-AT-FREEZE**: old e395ff4f203c… -> new e395ff4f203c…, hunks=0

### fixed-loop-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `fixed_loop_story.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 14939d995cd1… -> new 14939d995cd1…, hunks=0
- `fixed_loop_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old 8fc6daeace71… -> new 8fc6daeace71…, hunks=0
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then

### group-context-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \

### judge-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0
- `mock_services.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 712273ff91e4… -> new 712273ff91e4…, hunks=0

### nested-fixed-loop-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `nested_fixed_loop_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old f50edb9c4fc3… -> new f50edb9c4fc3…, hunks=0
- `fixed_loop_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old 8fc6daeace71… -> new 8fc6daeace71…, hunks=0
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0

### network-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `actor.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 9b7679964e0a… -> new 9b7679964e0a…, hunks=0

### operator-cli-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `cli.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 70b77d816d0f… -> new 70b77d816d0f…, hunks=0
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `group.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 2bbb82c41726… -> new 2bbb82c41726…, hunks=0

### provider-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   

### result-queue-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `result_queue_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old b672cc7bc054… -> new b672cc7bc054…, hunks=0
- `fixed_loop_story.py` **DIGEST-MISMATCH-AT-FREEZE**: old 8fc6daeace71… -> new 8fc6daeace71…, hunks=0
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0

### session-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `group.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 2bbb82c41726… -> new 2bbb82c41726…, hunks=0
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `group_session_ws_probe.py` **DIGEST-MISMATCH-AT-FREEZE**: old 954b4b962f2e… -> new 954b4b962f2e…, hunks=0

### sessions-services-migration-contract.json
- old source_revision: `71860eae99c62d7bf1349a0f46562f6395acbf1e` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `cli-stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 779672ec0d0c… -> new 576031c02600…, hunks=8
  --- frozen:cli-stories.sh
  +++ dev-tip:cli-stories.sh
  @@ -451,5 +451,2 @@
   
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "CLI service fixture enables group invocation" "200" || return
  -
       _cli_story_run "release pipeline invokes the group service" PM service invoke \

### structured-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then

### webhook-migration-contract.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then
- `http_provider_judge_mock.py` **DIGEST-MISMATCH-AT-FREEZE**: old fe56c8c2d79c… -> new fe56c8c2d79c…, hunks=0
- `group_session_ws_probe.py` **DIGEST-MISMATCH-AT-FREEZE**: old 954b4b962f2e… -> new 954b4b962f2e…, hunks=0

### migration-contracts.json
- old source_revision: `48b23001e2edf63fbedc80acdf1474e5c105ad23` -> new: `8559a7a5258a2cb6750befec187e50aa85060cc6`
- `stories.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 68fff1b3a519… -> new 3025c6ee245a…, hunks=17
  --- frozen:stories.sh
  +++ dev-tip:stories.sh
  @@ -461,5 +461,4 @@
       api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "group is exposed as a bounded service" "200" || return
  -    assert_json_eq "service group max concurrency is persisted" "$RESPONSE" "service_spec.max_concurrency" "2"
  -    assert_json_eq "group settings update returns stable status" "$RESPONSE" "status" "ok"
  +    require_status "retired group policy settings return Gone" "410" || return
  +    assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"
   
  @@ -733,6 +732,2 @@
       assert_json_eq "read-back keeps completion output" "$RESPONSE" "output.summary" "release approved"
  -
  -    api_patch "/groups/${group_id}/settings" '{"service_spec":{"max_concurrency":2}}'
  -    require_status "release group is configured for service invocations" "200" || return
  -    assert_json_eq "service invocation concurrency is two" "$RESPONSE" "service_spec.max_concurrency" "2"
   
- `group.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 2bbb82c41726… -> new 2bbb82c41726…, hunks=0
- `friends.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 80c76bb471db… -> new 80c76bb471db…, hunks=0
- `session_files.sh` **DIGEST-MISMATCH-AT-FREEZE**: old 836d05d1798d… -> new 836d05d1798d…, hunks=0
- `common.sh` **DIGEST-MISMATCH-AT-FREEZE**: old ca4d2571f58f… -> new 44f5445031e8…, hunks=12
  --- frozen:common.sh
  +++ dev-tip:common.sh
  @@ -443,3 +443,3 @@
   #   1. $BCS_CLI_BIN (coverage script injects the instrumented build)
  -#   2. src/bcs/target/debug/bcs-cli
  +#   2. apps/bcs/target/debug/bcs-cli
   #   3. fallback: cargo run -p bcs-cli --quiet -- (with warn)
  @@ -453,3 +453,3 @@
       repo_root="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
  -    local debug_bin="$repo_root/src/bcs/target/debug/bcs-cli"
  +    local debug_bin="$repo_root/apps/bcs/target/debug/bcs-cli"
       if [[ -x "$debug_bin" ]]; then

