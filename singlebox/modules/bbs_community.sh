#!/usr/bin/env bash
# singlebox/modules/bbs_community.sh — Local BBS community demo initialization
[[ -n "${_BBS_COMMUNITY_SH_LOADED:-}" ]] && return 0
_BBS_COMMUNITY_SH_LOADED=1

BBS_COMMUNITY_PROFILE_DIR="${BBS_COMMUNITY_PROFILE_DIR:-singlebox/agents/2bots_bbs_community_profile}"
BBS_COMMUNITY_OWNER_ID="${BBS_COMMUNITY_OWNER_ID:-mock-user}"
BBS_COMMUNITY_BACKEND_PORT="${BACKEND_PORT:-8888}"
BBS_COMMUNITY_LOG="${LOG_DIR}/bbs_community.log"

bbs_community_enabled() {
    [ "${BOTS_PROFILE_DIR:-}" = "$BBS_COMMUNITY_PROFILE_DIR" ]
}

bbs_community_backend_base_url() {
    printf 'http://127.0.0.1:%s\n' "$BBS_COMMUNITY_BACKEND_PORT"
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

bbs_community_seed_subscription() {
    local profile="$1"
    local note="$2"
    local session_file bot_id payload response

    session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"
    bot_id="$(bots_session_bot_uuid "$session_file")"
    if [ -z "$bot_id" ]; then
        log_error "Cannot seed BBS subscription for ${profile}: bot_uuid missing in ${session_file}"
        return 1
    fi

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

bbs_community_start() {
    bbs_community_enabled || return 0
    mkdir -p "${LOG_DIR}"
    : > "$BBS_COMMUNITY_LOG"

    backend_ready || {
        log_error "Backend is not ready; cannot initialize the BBS community demo"
        return 1
    }

    log_info "Seeding BBS demo topics for ${BBS_COMMUNITY_OWNER_ID}..."
    if ! bbs_community_seed_topics; then
        log_error "Failed to seed BBS demo topics. Check ${BBS_COMMUNITY_LOG}"
        return 1
    fi

    log_info "Enabling periodic BBS browsing for the two community bots..."
    if ! bbs_community_seed_subscriptions; then
        log_error "Failed to initialize BBS browse subscriptions. Check ${BBS_COMMUNITY_LOG}"
        return 1
    fi

    log_info 'BBS community demo initialized: 5 topics, 2 subscriptions, and 2 bbs-browse-loop cron tasks'
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

bbs_community_ready() {
    bbs_community_enabled || return 0

    bbs_community_topics_ready || return 1
    bbs_community_subscriptions_ready
}

bbs_community_stop() {
    # Initialization writes durable backend data; stopping the stack does not delete it.
    return 0
}

bbs_community_status() {
    bbs_community_enabled || return 0

    if bbs_community_ready; then
        echo '  BBS Community: Ready (5 topics; 2 subscriptions; 2 bbs-browse-loop cron tasks)'
    else
        echo '  BBS Community: Not ready'
    fi
}

bbs_community_help() {
    echo 'bbs_community - seed local 2-bot BBS demo topics, subscriptions, and fixed-name cron tasks'
}
