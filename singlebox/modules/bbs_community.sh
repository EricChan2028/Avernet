#!/usr/bin/env bash
# singlebox/modules/bbs_community.sh — Local BBS community demo initialization
[[ -n "${_BBS_COMMUNITY_SH_LOADED:-}" ]] && return 0
_BBS_COMMUNITY_SH_LOADED=1

BBS_COMMUNITY_PROFILE_DIR="${BBS_COMMUNITY_PROFILE_DIR:-singlebox/agents/2bots_bbs_community_profile}"
BBS_COMMUNITY_OWNER_ID="${BBS_COMMUNITY_OWNER_ID:-mock-user}"
BBS_COMMUNITY_BACKEND_PORT="${BACKEND_PORT:-8888}"
BBS_COMMUNITY_GATEWAY_PORT="${GATEWAY_PORT:-8889}"
BBS_COMMUNITY_LOG="${LOG_DIR}/bbs_community.log"
BBS_COMMUNITY_COOKIE_FILE="${BBS_COMMUNITY_COOKIE_FILE:-${LOG_DIR}/bbs_community.cookies}"
BBS_COMMUNITY_SKILL_SET_NAME="BBS Demo 能力集"
BBS_COMMUNITY_SKILL_SET_DESCRIPTION="BBS 演示内置：社区巡检、主题处理与发帖。"
BBS_COMMUNITY_SKILL_NAMES=(
    "bbs-browse"
    "bbs-topic-handler"
    "bbs-create-topic"
)

bbs_community_enabled() {
    [ "${BOTS_PROFILE_DIR:-}" = "$BBS_COMMUNITY_PROFILE_DIR" ]
}

bbs_community_backend_base_url() {
    printf 'http://127.0.0.1:%s\n' "$BBS_COMMUNITY_BACKEND_PORT"
}

bbs_community_gateway_base_url() {
    printf 'http://127.0.0.1:%s\n' "$BBS_COMMUNITY_GATEWAY_PORT"
}

bbs_community_url_encode() {
    jq -rn --arg value "$1" '$value | @uri'
}

bbs_community_openapi_query() {
    local owner_id
    owner_id="$(bbs_community_url_encode "${BBS_COMMUNITY_OWNER_ID}")"
    printf 'user_id=%s&owner_id=%s\n' "$owner_id" "$owner_id"
}

bbs_community_prereqs() {
    local has_error=false

    echo -e "${CYAN}[bbs_community] Prerequisites${NC}"
    if bbs_community_enabled; then
        if check_command jq; then
            prereq_ok "jq found"
        else
            prereq_error "jq not found. Install jq before starting the BBS demo."
            has_error=true
        fi
        if check_command curl; then
            prereq_ok "curl found"
        else
            prereq_error "curl not found. Install curl before starting the BBS demo."
            has_error=true
        fi
        if check_command python3; then
            prereq_ok "python3 found"
        else
            prereq_error "python3 not found. Install python3 before starting the BBS demo."
            has_error=true
        fi
    else
        prereq_ok "BBS community demo disabled (BOTS_PROFILE_DIR is not ${BBS_COMMUNITY_PROFILE_DIR})"
    fi

    [ "$has_error" = false ]
}

bbs_community_request() {
    local method="$1"
    local path="$2"
    local payload="${3:-}"
    local url response

    url="$(bbs_community_backend_base_url)${path}"
    if [ -n "$payload" ]; then
        response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS \
            -X "$method" \
            -H 'Content-Type: application/json' \
            -d "$payload" \
            "$url" 2>>"$BBS_COMMUNITY_LOG" || true)"
    else
        response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS \
            -X "$method" \
            "$url" 2>>"$BBS_COMMUNITY_LOG" || true)"
    fi
    [ -n "$response" ] || return 1
    printf '%s\n' "$response"
}

bbs_community_expect_success() {
    local response="$1"
    printf '%s\n' "$response" | jq -e '.code == 200000 or .code == 201000' >/dev/null 2>&1
}

bbs_community_seed_topic() {
    local client_request_id="$1"
    local title="$2"
    local body="$3"
    local response payload

    payload="$(jq -n \
        --arg author_id "$BBS_COMMUNITY_OWNER_ID" \
        --arg client_request_id "$client_request_id" \
        --arg title "$title" \
        --arg body "$body" \
        '{author_type:"HUMAN", author_id:$author_id, client_request_id:$client_request_id, title:$title, body:$body}')"

    response="$(bbs_community_request POST '/api/v1/collaboration/tasks/bbs/topics' "$payload")" || return 1
    bbs_community_expect_success "$response"
}

bbs_community_seed_topics() {
    local result=0

    bbs_community_seed_topic \
        'singlebox-bbs-demo-convention-v1' \
        'BBS 参与公约 v1：回帖必须产生新增价值' \
        '本公约长期生效，适用于本社区所有 Bot。回帖前必须读完整主帖；回帖必须提供新的信息、方案、风险、验收标准或执行承诺。纯“收到”、重复观点和无依据站队不构成价值。通知类主帖的沉淀判断只能使用主帖标题和正文，任何楼层回复不得补充或改写沉淀内容。' \
        || result=1

    bbs_community_seed_topic \
        'singlebox-bbs-demo-topic-direct-mention' \
        'BBS 要不要开放「@ 指定 Bot 参与讨论」？' \
        '当前社区巡检会让所有订阅 Bot 读取同一批开放主题。是否应提供 @ 指定 Bot 的能力？请从产品价值、误用风险、实现边界和最小可用版本四个角度讨论。' \
        || result=1

    bbs_community_seed_topic \
        'singlebox-bbs-demo-topic-onboarding' \
        '社区首页要不要默认提供「新手指引」？' \
        '第一次进入社区的用户需要知道发帖、搜索、我的、结帖和周期巡检分别表示什么。讨论是否把新手指引放在首页，以及若要放置，应保留哪些最小信息。' \
        || result=1

    bbs_community_seed_topic \
        'singlebox-bbs-demo-topic-edit-policy' \
        '主题发布后是否允许作者编辑正文？' \
        '如果允许编辑，后续 Bot 回复可能基于旧版本上下文；如果不允许，作者补充信息需要另发新帖或等待 Human 回帖能力。请给出一致性、成本和迁移方案。' \
        || result=1

    bbs_community_seed_topic \
        'singlebox-bbs-demo-topic-browsing-status' \
        '开启周期巡检后，要不要显示「Bot 正在查看」？' \
        '周期巡检提交后不会立即产生回复，用户可能误以为无效。讨论是否需要显示状态，以及如何避免过度承诺和刷屏。' \
        || result=1

    return "$result"
}

bbs_community_profile_bot_name() {
    case "$1" in
        community-product-officer) printf '%s' '社区产品官' ;;
        community-engineering-officer) printf '%s' '社区工程官' ;;
        *) return 1 ;;
    esac
}

bbs_community_profile_bot_description() {
    case "$1" in
        community-product-officer) printf '%s' '社区产品官，从用户目标、场景、需求边界、验收标准和最小可用范围参与 BBS 讨论，给出产品侧结论与取舍' ;;
        community-engineering-officer) printf '%s' '社区工程官，从实现可行性、系统边界、技术风险和验证方式参与 BBS 讨论，给出风险最小的实现路径' ;;
        *) return 1 ;;
    esac
}

bbs_community_backend_request() {
    local method="$1"
    local path="$2"
    local payload="${3:-}"
    local response
    local args=(
        --noproxy '*'
        --connect-timeout 2
        --max-time 90
        -sS
        -H "x-user-id: ${BBS_COMMUNITY_OWNER_ID}"
        -X "$method"
    )

    if [ -n "$payload" ]; then
        args+=(-H "Content-Type: application/json" --data-binary "@${payload}")
    fi
    response="$(curl "${args[@]}" "$(bbs_community_backend_base_url)${path}" 2>>"$BBS_COMMUNITY_LOG" || true)"
    [ -n "$response" ] || return 1
    printf '%s\n' "$response"
}

bbs_community_find_product_bot() {
    local bot_name="$1"
    local response
    response="$(bbs_community_backend_request GET "/api/bots?user_id=${BBS_COMMUNITY_OWNER_ID}&entity_id=${BBS_COMMUNITY_OWNER_ID}&entity_type=staff&page=1&page_size=100")" || return 1
    printf '%s\n' "$response" | jq -r --arg name "$bot_name" --arg entity_id "$BBS_COMMUNITY_OWNER_ID" '
        (.data.bots // .data.items // .data.list // .data.records // .data // [])
        | if type == "array" then . else [] end
        | map(select((.bot_name == $name) and (.entity_id == $entity_id) and ((.is_delete // 0) == 0)))
        | .[0].bot_id // empty
    ' 2>/dev/null || true
}

bbs_community_create_product_bot() {
    local profile="$1"
    local bot_name bot_summary payload_file response bot_id

    bot_name="$(bbs_community_profile_bot_name "$profile")" || return 1
    bot_summary="$(bbs_community_profile_bot_description "$profile")" || return 1
    payload_file="$(mktemp "${LOG_DIR}/bbs-bot.XXXXXX")" || return 1
    trap 'rm -f "${payload_file:-}"' RETURN
    jq -n \
        --arg bot_name "$bot_name" \
        --arg bot_desc "$bot_summary" \
        --arg entity_id "$BBS_COMMUNITY_OWNER_ID" \
        '{bot_name: $bot_name, bot_desc: $bot_desc, entity_id: $entity_id, entity_type: "staff", engine_type: "openclaw", bot_type: "personal", template_type: "normalCC"}' \
        >"$payload_file"
    response="$(bbs_community_backend_request POST "/api/bots?user_id=${BBS_COMMUNITY_OWNER_ID}" "$payload_file")" || return 1
    bot_id="$(printf '%s\n' "$response" | jq -r '.data.bot_id // .data.bot.bot_id // .bot_id // empty' 2>/dev/null || true)"
    if [ -z "$bot_id" ]; then
        log_error "Backend product bot creation did not return bot_id: ${profile}: $(printf '%s' "$response" | head -c 500)"
        return 1
    fi
    printf '%s' "$bot_id"
}

bbs_community_wait_product_bot_ready() {
    local bot_id="$1"
    local started_at elapsed response status error_message
    started_at=$SECONDS

    while true; do
        response="$(bbs_community_backend_request GET "/api/bots/${bot_id}/status?owner_id=${BBS_COMMUNITY_OWNER_ID}")" || return 1
        if printf '%s\n' "$response" | jq -e '.success == true and .data.is_ready == true' >/dev/null 2>&1; then
            return 0
        fi

        status="$(printf '%s\n' "$response" | jq -r '.data.bot_status // empty' 2>/dev/null || true)"
        error_message="$(printf '%s\n' "$response" | jq -r '.data.error_message // empty' 2>/dev/null || true)"
        if [ "$status" = FAILED ] || [ -n "$error_message" ]; then
            log_error "BBS product bot did not become ready: bot_id=${bot_id}, status=${status:-unknown}, error=${error_message:-none}"
            return 1
        fi

        elapsed=$((SECONDS - started_at))
        if [ "$elapsed" -ge "${BBS_COMMUNITY_PRODUCT_BOT_READY_TIMEOUT_SECONDS:-180}" ]; then
            log_error "Timed out waiting for the BBS product bot: bot_id=${bot_id}"
            return 1
        fi
        sleep 2
    done
}

bbs_community_ensure_product_bot() {
    local profile="$1"
    local bot_name bot_id

    bot_name="$(bbs_community_profile_bot_name "$profile")" || return 1
    backend_ready || {
        log_error "Backend is not ready; cannot create BBS product bot ${bot_name}"
        return 1
    }
    baas_ready || {
        log_error "BAAS is not ready; cannot create BBS product bot ${bot_name}"
        return 1
    }

    bot_id="$(bbs_community_find_product_bot "$bot_name")" || return 1
    if [ -z "$bot_id" ]; then
        bot_id="$(bbs_community_create_product_bot "$profile")" || return 1
    fi
    bbs_community_wait_product_bot_ready "$bot_id" || return 1
    printf '%s' "$bot_id"
}

bbs_community_bot_id() {
    local profile="$1"
    local bot_id

    bot_id="$(bbs_community_ensure_product_bot "$profile")" || return 1
    printf '%s' "$bot_id"
}

bbs_community_gateway_login() {
    local response owner_id

    owner_id="$(bbs_community_url_encode "${BBS_COMMUNITY_OWNER_ID}")"
    mkdir -p "$LOG_DIR"
    rm -f "$BBS_COMMUNITY_COOKIE_FILE"
    response="$(curl --noproxy '*' --connect-timeout 2 --max-time 20 -fsS \
        -c "$BBS_COMMUNITY_COOKIE_FILE" \
        "$(bbs_community_gateway_base_url)/_dev/login?staff_id=${owner_id}&nick_name=${owner_id}&next=${FRONTEND_PORT:-8000}" \
        2>>"$BBS_COMMUNITY_LOG")" || return 1
    [ -f "$BBS_COMMUNITY_COOKIE_FILE" ] || return 1
    printf '%s' "$response"
}

bbs_community_gateway_request() {
    local method="$1"
    local path="$2"
    local payload="${3:-}"
    local content_type="${4:-}"
    local response
    local args=(
        --noproxy '*'
        --connect-timeout 2
        --max-time 60
        -sS
        -b "$BBS_COMMUNITY_COOKIE_FILE"
        -X "$method"
    )

    if [ -n "$content_type" ]; then
        args+=(-H "Content-Type: ${content_type}")
    fi
    if [ -n "$payload" ]; then
        args+=(--data-binary "@${payload}")
    fi
    response="$(curl "${args[@]}" "$(bbs_community_gateway_base_url)${path}" \
        2>>"$BBS_COMMUNITY_LOG" || true)"
    [ -n "$response" ] || return 1
    printf '%s\n' "$response"
}

bbs_community_create_skill_zip() {
    local source_dir="$1"
    local zip_file="$2"

    python3 - "$source_dir" "$zip_file" <<'PY'
from pathlib import Path
import sys
import zipfile

source = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2])

if not source.is_dir() or not (source / "SKILL.md").is_file():
    raise SystemExit(f"Invalid BBS skill source: {source}")

with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if relative == Path("."):
            continue
        if path.is_symlink():
            raise SystemExit(f"BBS skill source contains a symlink: {path}")
        if path.is_file():
            archive.write(path, relative.as_posix())
PY
}

bbs_community_gateway_json_request() {
    local method="$1"
    local path="$2"
    local payload="$3"
    local payload_file response

    payload_file="$(mktemp "${LOG_DIR}/bbs-json.XXXXXX")" || return 1
    trap 'rm -f "${payload_file:-}"' RETURN
    printf '%s\n' "$payload" >"$payload_file"
    response="$(bbs_community_gateway_request "$method" "$path" "$payload_file" 'application/json')" || return 1
    printf '%s' "$response"
}

bbs_community_upload_skill() {
    local profile="$1"
    local source="$2"
    local bot_id="$3"
    local skill_name="$4"
    local source_dir zip_file query upload_response skill_id skill_active

    source_dir="${PROJECT_ROOT}/${BOTS_PROFILE_DIR}/${source}/skills/${skill_name}"
    if [ ! -f "${source_dir}/SKILL.md" ]; then
        log_error "Cannot seed ${profile} skill ${skill_name}: missing ${source_dir}/SKILL.md"
        return 1
    fi

    zip_file="$(mktemp "${LOG_DIR}/bbs-skill.XXXXXX.zip")" || return 1
    trap 'rm -f "${zip_file:-}"' RETURN
    bbs_community_create_skill_zip "$source_dir" "$zip_file" || return 1

    query="$(bbs_community_openapi_query)"
    upload_response="$(bbs_community_gateway_request POST \
        "/openapi/v1/bots/${bot_id}/skills?${query}" \
        "$zip_file" 'application/zip')" || return 1
    if ! printf '%s\n' "$upload_response" | jq -e \
        '.code == 200000 or .code == 201000' >/dev/null 2>&1; then
        log_error "Failed to upload ${profile} skill ${skill_name}: $(printf '%s' "$upload_response" | head -c 500)"
        return 1
    fi
    skill_id="$(printf '%s\n' "$upload_response" | jq -r '.data.skill.skill_id // ""')"
    skill_active="$(printf '%s\n' "$upload_response" | jq -r '.data.skill.active // false')"
    if [ -z "$skill_id" ]; then
        log_error "BBS skill upload did not return skill_id: ${profile}/${skill_name}"
        return 1
    fi
    printf '%s\n%s\n' "$skill_id" "$skill_active"
}

bbs_community_verify_skill_content() {
    local profile="$1"
    local bot_id="$2"
    local skill_id="$3"
    local skill_name="$4"
    local query response

    query="$(bbs_community_openapi_query)"
    response="$(bbs_community_gateway_request GET \
        "/openapi/v1/bots/${bot_id}/skills/${skill_id}/content?${query}")" || return 1
    if ! printf '%s\n' "$response" | jq -e \
        '.code == 200000 and (.data.content | type == "string" and length > 0)' \
        >/dev/null 2>&1; then
        log_error "BBS skill content is not readable after upload: ${profile}/${skill_name}"
        return 1
    fi
}

bbs_community_find_skill_set() {
    local bot_id="$1"
    local query response set_id set_active

    query="$(bbs_community_openapi_query)"
    response="$(bbs_community_gateway_request GET \
        "/openapi/v1/bots/${bot_id}/skill-sets?${query}")" || return 1
    if ! printf '%s\n' "$response" | jq -e \
        '.code == 200000 and (.data | type == "array")' >/dev/null 2>&1; then
        log_error "Cannot list BBS skill set for bot ${bot_id}: $(printf '%s' "$response" | head -c 500)"
        return 1
    fi

    set_id="$(printf '%s\n' "$response" | jq -r --arg name "$BBS_COMMUNITY_SKILL_SET_NAME" \
        '.data[]? | select(.name == $name and .is_default == false) | .id' | head -n 1)"
    set_active="$(printf '%s\n' "$response" | jq -r --arg name "$BBS_COMMUNITY_SKILL_SET_NAME" \
        '.data[]? | select(.name == $name and .is_default == false) | .is_active | if . then "true" else "false" end' | head -n 1)"
    printf '%s\n%s\n' "${set_id:-}" "${set_active:-false}"
}

bbs_community_create_skill_set() {
    local bot_id="$1"
    local query payload response set_id

    query="$(bbs_community_openapi_query)"
    payload="$(jq -n \
        --arg name "$BBS_COMMUNITY_SKILL_SET_NAME" \
        --arg description "$BBS_COMMUNITY_SKILL_SET_DESCRIPTION" \
        '{name:$name, description:$description}')"
    response="$(bbs_community_gateway_json_request POST \
        "/openapi/v1/bots/${bot_id}/skill-sets?${query}" "$payload")" || return 1
    if ! printf '%s\n' "$response" | jq -e \
        '.code == 200000 or .code == 201000' >/dev/null 2>&1; then
        log_error "Cannot create BBS skill set for bot ${bot_id}: $(printf '%s' "$response" | head -c 500)"
        return 1
    fi
    set_id="$(printf '%s\n' "$response" | jq -r '.data.id // ""')"
    if [ -z "$set_id" ]; then
        log_error "BBS skill set creation did not return id for bot ${bot_id}"
        return 1
    fi
    printf '%s' "$set_id"
}

bbs_community_deactivate_direct_skill() {
    local bot_id="$1"
    local skill_id="$2"
    local skill_name="$3"
    local query response

    query="$(bbs_community_openapi_query)"
    response="$(bbs_community_gateway_request POST \
        "/openapi/v1/bots/${bot_id}/skills/${skill_id}/deactivate?${query}")" || return 1
    if ! printf '%s\n' "$response" | jq -e \
        '.code == 200000 and .data.skill.active == false' >/dev/null 2>&1; then
        log_error "Failed to deactivate directly activated skill ${skill_name}: $(printf '%s' "$response" | head -c 500)"
        return 1
    fi
}

bbs_community_add_skill_to_set() {
    local bot_id="$1"
    local set_id="$2"
    local skill_id="$3"
    local skill_name="$4"
    local query response

    query="$(bbs_community_openapi_query)"
    response="$(bbs_community_gateway_request PUT \
        "/openapi/v1/bots/${bot_id}/skill-sets/${set_id}/skills/${skill_id}?${query}")" || return 1
    if ! printf '%s\n' "$response" | jq -e \
        '.code == 200000' >/dev/null 2>&1; then
        log_error "Failed to add skill ${skill_name} to the BBS skill set: $(printf '%s' "$response" | head -c 500)"
        return 1
    fi
}

bbs_community_activate_skill_set() {
    local bot_id="$1"
    local set_id="$2"
    local query response

    query="$(bbs_community_openapi_query)"
    response="$(bbs_community_gateway_request POST \
        "/openapi/v1/bots/${bot_id}/skill-sets/${set_id}/activate?${query}")" || return 1
    if ! printf '%s\n' "$response" | jq -e \
        '.code == 200000 and .data.is_active == true' >/dev/null 2>&1; then
        log_error "Failed to activate the BBS skill set: $(printf '%s' "$response" | head -c 500)"
        return 1
    fi
}

bbs_community_seed_profile_skills() {
    local profile="$1"
    local source="$2"
    local bot_id skill_set_state skill_set_id skill_set_active query
    local skill_name skill_state skill_id skill_active set_skills_response

    bot_id="$(bbs_community_bot_id "$profile")" || return 1
    skill_set_state="$(bbs_community_find_skill_set "$bot_id")" || return 1
    skill_set_id="$(printf '%s\n' "$skill_set_state" | sed -n '1p')"
    skill_set_active="$(printf '%s\n' "$skill_set_state" | sed -n '2p')"
    if [ -z "$skill_set_id" ]; then
        skill_set_id="$(bbs_community_create_skill_set "$bot_id")" || return 1
        skill_set_active=true
    fi

    query="$(bbs_community_openapi_query)"
    set_skills_response="$(bbs_community_gateway_request GET \
        "/openapi/v1/bots/${bot_id}/skill-sets/${skill_set_id}/skills?${query}")" || return 1
    if ! printf '%s\n' "$set_skills_response" | jq -e \
        '.code == 200000 and (.data | type == "array")' >/dev/null 2>&1; then
        log_error "Cannot list members of the BBS skill set for ${profile}: $(printf '%s' "$set_skills_response" | head -c 500)"
        return 1
    fi

    for skill_name in "${BBS_COMMUNITY_SKILL_NAMES[@]}"; do
        skill_state="$(bbs_community_upload_skill "$profile" "$source" "$bot_id" "$skill_name")" || return 1
        skill_id="$(printf '%s\n' "$skill_state" | sed -n '1p')"
        skill_active="$(printf '%s\n' "$skill_state" | sed -n '2p')"
        bbs_community_verify_skill_content "$profile" "$bot_id" "$skill_id" "$skill_name" || return 1
        if ! printf '%s\n' "$set_skills_response" | jq -e --arg name "$skill_name" \
            'any(.data[]?; .name == $name)' >/dev/null 2>&1; then
            if [ "$skill_active" = true ]; then
                bbs_community_deactivate_direct_skill "$bot_id" "$skill_id" "$skill_name" || return 1
            fi
            bbs_community_add_skill_to_set "$bot_id" "$skill_set_id" "$skill_id" "$skill_name" || return 1
        fi
    done

    if [ "$skill_set_active" != true ]; then
        bbs_community_activate_skill_set "$bot_id" "$skill_set_id" || return 1
    fi
}

bbs_community_seed_skills() {
    bbs_community_gateway_login >/dev/null || {
        log_error "Gateway dev login failed; check ${BBS_COMMUNITY_LOG}"
        return 1
    }

    bbs_community_seed_profile_skills community-product-officer community-product-officer || return 1
    bbs_community_seed_profile_skills community-engineering-officer community-engineering-officer
}

bbs_community_seed_subscription() {
    local profile="$1"
    local note="$2"
    local bot_id payload response

    bot_id="$(bbs_community_bot_id "$profile")" || return 1

    payload="$(jq -n --arg note "$note" '{note:$note}')"
    response="$(bbs_community_request POST \
        "/api/v1/collaboration/tasks/bots/${bot_id}/bbs/browse-subscription?owner_user_id=${BBS_COMMUNITY_OWNER_ID}" \
        "$payload")" || return 1
    bbs_community_expect_success "$response"
}

bbs_community_seed_subscriptions() {
    bbs_community_seed_subscription community-product-officer \
        '关注用户目标、使用场景、需求边界和验收标准，优先给出结论、边界和建议。' \
        || return 1
    bbs_community_seed_subscription community-engineering-officer \
        '关注实现可行性、系统边界、技术风险和验证方式，优先给出风险最小的实现路径。' \
        || return 1
}

bbs_community_profile_routine_ready() {
    local profile="$1"
    local bot_id query response

    bot_id="$(bbs_community_bot_id "$profile")" || return 1
    query="$(bbs_community_openapi_query)"
    response="$(bbs_community_gateway_request GET \
        "/openapi/v1/bots/${bot_id}/routines?${query}&page=1&page_size=100")" || return 1
    printf '%s\n' "$response" | jq -e \
        '.code == 200000 and any(.data.items[]?; .name == "bbs-browse-loop" and .trigger.type == "schedule" and .trigger.cron == "*/30 * * * *" and .enabled == true and (.timezone // "Asia/Shanghai") == "Asia/Shanghai")' \
        >/dev/null 2>&1
}

bbs_community_wait_profile_routine_ready() {
    local profile="$1"
    local started_at elapsed

    started_at=$SECONDS
    while true; do
        if bbs_community_profile_routine_ready "$profile"; then
            return 0
        fi

        elapsed=$((SECONDS - started_at))
        if [ "$elapsed" -ge "${BBS_COMMUNITY_ROUTINE_READY_TIMEOUT_SECONDS:-180}" ]; then
            log_error "Timed out waiting for the BBS bbs-browse-loop cron task: profile=${profile}"
            return 1
        fi
        sleep 2
    done
}

bbs_community_wait_routines_ready() {
    bbs_community_wait_profile_routine_ready community-product-officer || return 1
    bbs_community_wait_profile_routine_ready community-engineering-officer || return 1
}

bbs_community_routines_ready() {
    bbs_community_profile_routine_ready community-product-officer || return 1
    bbs_community_profile_routine_ready community-engineering-officer || return 1
}

bbs_community_start() {
    bbs_community_enabled || return 0
    mkdir -p "${LOG_DIR}"
    : > "$BBS_COMMUNITY_LOG"

    backend_ready || {
        log_error "Backend is not ready; cannot initialize the BBS community demo"
        return 1
    }
    gateway_ready || {
        log_error "Gateway is not ready; cannot initialize BBS Skill Center state"
        return 1
    }

    log_info "Seeding BBS demo topics for ${BBS_COMMUNITY_OWNER_ID}..."
    if ! bbs_community_seed_topics; then
        log_error "Failed to seed BBS demo topics. Check ${BBS_COMMUNITY_LOG}"
        return 1
    fi

    log_info "Uploading BBS skills and assembling BBS skill sets for the two community bots..."
    if ! bbs_community_seed_skills; then
        log_error "Failed to initialize BBS skills. Check ${BBS_COMMUNITY_LOG}"
        return 1
    fi

    log_info "Enabling periodic BBS browsing for the two community bots..."
    if ! bbs_community_seed_subscriptions; then
        log_error "Failed to initialize BBS browse subscriptions. Check ${BBS_COMMUNITY_LOG}"
        return 1
    fi

    log_info "Waiting for both BBS bots to expose the bbs-browse-loop routine..."
    if ! bbs_community_wait_routines_ready; then
        log_error "Failed to verify BBS bbs-browse-loop routine tasks. Check ${BBS_COMMUNITY_LOG}"
        return 1
    fi

    log_info 'BBS community demo initialized: 5 topics, 2 active skill sets, 6 active skills, 2 subscriptions, and 2 bbs-browse-loop cron tasks'
}

bbs_community_topics_ready() {
    local response total
    response="$(bbs_community_request GET \
        "/api/v1/collaboration/tasks/bbs/topics?author_id=${BBS_COMMUNITY_OWNER_ID}&page=1&page_size=10")" || return 1
    bbs_community_expect_success "$response" || return 1
    total="$(printf '%s\n' "$response" | jq -r '.data.total // 0' 2>/dev/null)"
    [ "$total" -ge 5 ]
}

bbs_community_subscriptions_ready() {
    local response total
    response="$(bbs_community_request GET \
        "/api/v1/collaboration/tasks/bbs/browse-subscriptions?owner_user_id=${BBS_COMMUNITY_OWNER_ID}&page=1&page_size=10")" || return 1
    bbs_community_expect_success "$response" || return 1
    total="$(printf '%s\n' "$response" | jq -r '.data.total // 0' 2>/dev/null)"
    [ "$total" -ge 2 ]
}

bbs_community_profile_skills_ready() {
    local profile="$1"
    local bot_id query resources set_id skills skill_name

    bot_id="$(bbs_community_bot_id "$profile")" || return 1
    query="$(bbs_community_openapi_query)"
    resources="$(bbs_community_gateway_request GET \
        "/openapi/v1/bots/${bot_id}/skill-sets/resources?${query}")" || return 1
    set_id="$(printf '%s\n' "$resources" | jq -r --arg name "$BBS_COMMUNITY_SKILL_SET_NAME" \
        '.data[]? | select(.name == $name and .is_default == false and .is_active == true) | .id' \
        | head -n 1)"
    if [ -z "$set_id" ]; then
        return 1
    fi

    skills="$(bbs_community_gateway_request GET \
        "/openapi/v1/bots/${bot_id}/skill-sets/${set_id}/skills?${query}")" || return 1
    for skill_name in "${BBS_COMMUNITY_SKILL_NAMES[@]}"; do
        printf '%s\n' "$skills" | jq -e --arg name "$skill_name" \
            '.code == 200000 and any(.data[]?; .name == $name)' \
            >/dev/null 2>&1 || return 1
    done
}

bbs_community_skills_ready() {
    gateway_ready || return 1
    bbs_community_gateway_login >/dev/null || return 1

    bbs_community_profile_skills_ready community-product-officer || return 1
    bbs_community_profile_skills_ready community-engineering-officer
}

bbs_community_ready() {
    bbs_community_enabled || return 0

    bbs_community_topics_ready || return 1
    bbs_community_subscriptions_ready || return 1
    bbs_community_skills_ready || return 1
    bbs_community_routines_ready
}

bbs_community_stop() {
    # Initialization writes durable backend data; stopping the stack does not delete it.
    return 0
}

bbs_community_status() {
    bbs_community_enabled || return 0

    if bbs_community_ready; then
        echo '  BBS Community: Ready (5 topics; 2 active skill sets; 6 active skills; 2 subscriptions; 2 bbs-browse-loop cron tasks)'
    else
        echo '  BBS Community: Not ready'
    fi
}

bbs_community_help() {
    echo 'bbs_community - seed local 2-bot BBS demo topics, skills, skill sets, subscriptions, and fixed-name cron tasks'
}
