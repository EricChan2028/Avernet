# Avernet Task Context 协议修订版（Fix）

> 日期：2026-09-10
> 状态：基于本讨论达成；session 主键版
> 适用范围：动态 Task Loop、单 Bot/协作群/BBS/Poller 多模态、ECB 知识闭环

---

# 0. 修订根原则

1. **身份主键不进入协议层**：`execution_id` 由 Server 内部生成、可追溯，不在协议字段中。本协议以 **`(scope, self.session)` 二元组**作为实例对外身份。
2. **session 在 scope 内唯一**：Bot/Group/BBS 各自 runtime 产出的 session id 在同一 scope 内不重复。这是本协议成立的硬前提。
3. **协议族仍为 7 个**（A1–A7）。所有下游协议（A2–A7）只用 `(scope, session)` 寻址；`type` / `participants` 等元信息由 Server 从 A1 绑定记录反查得到，不在下游协议重复携带。
4. **关系即绑定**：A1 只建立"任务下谁派生谁"的运行网络。`parent` 是数组、AND 语义：parent 里所有 session 都规位后 self 才成立；空数组 = root。
5. **节点身份内嵌**：没有独立 `node` 字段。"哪个 bot/参与方"由 `self.participants` 表达，按 type 不同而走不同协议。
6. **领域对象目录与协议同源**：第 8 节的 Self Type 目录、Result Schema、Error Catalog 与协议字段同版本演进。

---

# 1. A1：ExecutionBinding（终版）

由知道任务归属的 Task Runtime/调用方调用，在任务下登记一个实例并表达其多入边派生关系。

## 1.1 API

```http
POST /v1/executions
Content-Type: application/json
```

## 1.2 请求

```json
{
  "scope": "task_1",
  "self": {
    "type": "bot",
    "session": "s2",
    "participants": ["bot_B"]
  },
  "parent": [{"session": "s1"}]
}
```

- `scope`：必填；任务范围 id
- `self`：必填；本次新建实例
- `parent`：必填；空数组 `[]` = root；非空时全部 session 必须已在该 scope 内注册（AND 语义）
- `session`：scope 内唯一；下游 A2–A7 只用它做寻址

## 1.3 各 type 的字段说明

### type = `bot`

```json
{
  "type": "bot",
  "session": "s2",
  "participants": ["bot_B"]
}
```

`participants` 是单元素数组，唯一那个就是 bot 自身。保持数组形式以便 schema 统一。

### type = `group`

```json
{
  "type": "group",
  "session": "thread_1",
  "participants": [
    "bot_A",
    "bot_B",
    "bot_C"
  ],
  "leader": "bot_A"
}
```

- `participants`：全部参与者的 bot_id
- `leader`：可选；绑定时就能确定，避免下游靠对话历史反查

### type = `bbs`

```json
{
  "type": "bbs",
  "session": "post_88"
}
```

- BBS 不必是 bot 集合，`participants` 省略
- session 用 BBS 的 thread / 原 post id
- BBS 多作为 parent 出现；type=bbs 的 self 兜底（极少用）

## 1.4 典型场景

### 场景 1：Bot_A root

```json
{
  "scope": "task_1",
  "self": {"type": "bot", "session": "s1", "participants": ["bot_A"]},
  "parent": []
}
```

### 场景 2：Bot_A 拉群

```json
{
  "scope": "task_1",
  "self": {
    "type": "group",
    "session": "thread_1",
    "participants": ["bot_A", "bot_B"],
    "leader": "bot_A"
  },
  "parent": [{"session": "s1"}]
}
```

### 场景 3：群派生子 Bot（DAG 跨模态）

```json
{
  "scope": "task_1",
  "self": {"type": "bot", "session": "s3", "participants": ["bot_C"]},
  "parent": [{"session": "thread_1"}]
}
```

Context 反查 `thread_1` 得知 parent 是 group，得到参与者和 leader。

### 场景 4：BBS 触发 Bot

```json
{
  "scope": "task_1",
  "self": {"type": "bot", "session": "s2", "participants": ["bot_D"]},
  "parent": [{"session": "post_88"}]
}
```

Context 反查 `post_88` 得知 parent 是 bbs（之前已建立绑定）。

### 场景 5：BBS 触发协作群

```json
{
  "scope": "task_1",
  "self": {
    "type": "group",
    "session": "thread_2",
    "participants": ["bot_D", "bot_E"],
    "leader": "bot_D"
  },
  "parent": [{"session": "post_88"}]
}
```

### 场景 6：DAG 多入边（fan-in / AND）

```json
{
  "scope": "task_1",
  "self": {"type": "bot", "session": "s4", "participants": ["bot_F"]},
  "parent": [{"session": "s2"}, {"session": "s3"}]
}
```

self 在 s2 和 s3 都到位后才触发。

### 场景 7：完整 DAG 链 bot → group → bot

```text
bot_A(s1) ─spawn─> group_X(thread_1) ─spawn─> bot_C(s3)
```

三次 A1 调用串联。每个 spawn 用 parent 指上一个 session。

### 场景 8：Retry

self.session 换新，parent 含上一次失败的 session：

```json
{
  "scope": "task_1",
  "self": {"type": "bot", "session": "s4", "participants": ["bot_B"]},
  "parent": [{"session": "s2"}]
}
```

retry 在协议上和普通 spawn 同构，不需要 `mode` / `attempt` 字段。

## 1.5 响应

```json
{
  "scope": "task_1",
  "self": {"type": "bot", "session": "s2", "participants": ["bot_B"]},
  "parent": [{"session": "s1"}],
  "status": "registered|exists",
  "registered_at": "2026-09-10T10:30:00+08:00"
}
```

`status=exists`：同 `(scope, self.session)` 已记录，按幂等返回。

## 1.6 字段对照

| 字段 | 必填 | 作用 |
|---|---|---|
| `scope` | 是 | 任务范围根锚点 |
| `self.type` | 是 | `bot` / `group` / `bbs`；后续可扩 |
| `self.session` | 是 | scope 内唯一运行实例 id |
| `self.participants` | bot/group 必填 | 参与者 bot_id 列表；bbs 省略 |
| `self.leader` | group 可选 | 拉群者的 bot_id |
| `parent[]` | 是 | 父绑定引用列表；`[]` = root |
| `parent[].session` | 是 | 父实体的 session（scope 内唯一） |
| `Idempotency-Key` | 不要求 | `(scope, self.session)` 天然唯一 |

## 1.7 v5 中删除/合入的字段

| v5 字段 | 处理 |
|---|---|
| `execution_id` | 删除；内部主键 |
| `work_ref` | 删除；Work 由 `(scope, parent)` 派生链表达 |
| `attempt_ref` / `mode` | 删除；retry 同 spawn，由 parent 链表达 |
| `runtime_ref` | 含义并入 `self.type + self.session + self.participants` |
| `node` | 删除；身份内嵌到 `self.session` + `participants` |
| `Idempotency-Key` | 不进 A1 |

---

# 2. A2：SourceCapture

A1 已记录 session 的来源（含 type/participants），A2 简化为按 session 读取原始过程游标。

## 2.1 读取原始过程

```http
GET /v1/scopes/{scope}/sessions/{session}/events
```

Query 参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `after_event_id` | 否 | 上次读取游标 |
| `limit` | 否 | 单次返回条数 |

响应：

```json
{
  "items": [
    {
      "event_id": "evt_135",
      "kind": "message|tool_call|observation|status",
      "occurred_at": "...",
      "source_session": "s2",
      "content": {},
      "content_ref": null
    }
  ],
  "next_event_id": "evt_200",
  "has_more": false
}
```

## 2.2 字段说明

| 字段 | 说明 |
|---|---|
| `event_id` | 该 session 内事件 id；用作游标 |
| `kind` | 受控枚举：`message` / `tool_call` / `observation` / `status` |
| `source_session` | 原始来源 session 再次自报，audit 时不依赖读取者拼装 |
| `content` / `content_ref` | 短内容内联；长内容走 `content_ref` |
| `next_event_id` | 下次读取游标 |

## 2.3 谁会调用

- Context 生成 ContextView 时回查原始过程
- A6 验收时拉证据
- A7 知识抽取时回查完整 Case

---

# 3. A3：ContextSpec

声明"谁、要什么上下文、为什么、最多多大"。

## 3.1 请求

```http
POST /v1/contexts/specs
```

```json
{
  "scope": "task_1",
  "consumer": {"session": "s2"},
  "purpose": "task.execute",
  "variant": "retry",
  "requirements": {
    "include": ["hard_dependencies", "failure_feedback", "attempted_plan"],
    "exclude": ["unrelated_history"]
  },
  "budget": {
    "max_input_tokens": 12000
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `scope` | 是 | 上下文范围 |
| `consumer.session` | 否 | 消费实例 session；缺省由 Server 凭鉴权上下文确定；type 由 A1 反查 |
| `purpose` | 是 | 受控域 → 配方组（见 §8.5） |
| `variant` | 是 | 子配方（initial / replan / retry / result 等） |
| `requirements` | 是 | 显示声明硬依赖与排除项（见 §8.4） |
| `budget` | 是 | 输入预算 |

## 3.2 响应

```json
{
  "spec_id": "spec_123",
  "scope": "task_1",
  "registered_at": "..."
}
```

---

# 4. A4：ContextView

## 4.1 请求

```http
POST /v1/contexts/views
```

```json
{
  "spec_id": "spec_123"
}
```

## 4.2 响应

```json
{
  "view_id": "view_456",
  "spec_id": "spec_123",

  "items": [
    {
      "item_id": "fact_77",
      "kind": "fact|knowledge|task_state",
      "role": "hard_dependency|failure_feedback|reference",
      "representation": "inline|summary",
      "content": {},
      "source_sessions": ["s1"]
    }
  ],

  "omitted": [
    {
      "candidate_id": "evt_200",
      "reason": "out_of_scope|budget_exceeded|permission_denied",
      "priority": "optional"
    }
  ],

  "conflicts": [],

  "completeness": {
    "status": "complete|partial|blocked",
    "missing_required": []
  },

  "based_on": {
    "scope": "task_1",
    "source_sessions": ["s1", "s2"]
  },

  "budget": {
    "max_input_tokens": 12000,
    "used_input_tokens": 8900
  }
}
```

## 4.3 与 v5 差异

| 项 | 处理 |
|---|---|
| `representation=ref|snippet` | 删除；第一版只支持 `inline|summary` |
| `source_refs` | 改名 `source_sessions`；session 数组 |
| `derivation.method_version` / `context_version` | 删除；第一版未实装 |

---

# 5. A5：ExecutionOutcome

由执行实例上报"我（实例）跑完了"。

## 5.1 API

```http
POST /v1/outcomes
```

身份由 `(scope, session)` 寻址；type 由 Server 从 A1 反查，body 内不重复带。

## 5.2 请求（Bot 成功路径）

```json
{
  "scope": "task_1",
  "self": {"session": "s2"},

  "status": "succeeded",
  "finished_at": "2026-09-10T10:40:00+08:00",
  "input_view_ref": "view_456",

  "result": {
    "data_type": "bot.execution_output",
    "summary": "发现 3 项高风险差异",
    "data": {
      "assistant_message_ref": "msg_135",
      "structured_claims": {
        "risk_count": 3,
        "highest_level": "high"
      },
      "tool_artifacts": [],
      "follow_up_suggested": []
    },
    "artifact_refs": [
      {"artifact_id": "artifact_42", "media_type": "text/markdown", "uri": "..."}
    ],
    "evidence_refs": [
      {
        "source_session": "s2",
        "record_ids": ["msg_131", "msg_135"]
      }
    ]
  },

  "error": null,
  "partial_result": null
}
```

## 5.3 请求（Group 成功路径）

```json
{
  "scope": "task_1",
  "self": {"session": "thread_1"},

  "status": "succeeded",
  "finished_at": "...",
  "input_view_ref": "view_456",

  "result": {
    "data_type": "group.decision_result",
    "summary": "已就连接池上限调整达成一致",
    "data": {
      "decisions": [
        {
          "text": "连接池上限调到 100",
          "agreed_by": ["bot_A", "bot_B"],
          "dissenting_by": [],
          "decision_event_refs": ["msg_120"]
        }
      ],
      "open_questions": []
    },
    "artifact_refs": [],
    "evidence_refs": []
  },

  "error": null,
  "partial_result": null
}
```

## 5.4 请求（失败路径）

```json
{
  "scope": "task_1",
  "self": {"session": "s2"},

  "status": "failed",
  "finished_at": "2026-09-10T10:40:00+08:00",
  "input_view_ref": "view_456",

  "result": null,
  "error": {
    "code": "TOOL_CALL_FAILED",
    "stage": "query_database",
    "message": "数据库无权限",
    "retryable": false,
    "evidence_refs": [
      {"source_session": "s2", "record_ids": ["msg_133"]}
    ],
    "extras": {
      "failed_tool_name": "query_database",
      "tool_input_ref": "artifact://tool_in_2"
    }
  },
  "partial_result": {
    "data_type": "bot.execution_output",
    "summary": "已完成前两个月核验",
    "data": {},
    "artifact_refs": [],
    "evidence_refs": []
  }
}
```

协作群失败示例：

```json
"error": {
  "code": "NO_CONSENSUS",
  "stage": "group_decision",
  "message": "3 轮讨论未达成一致",
  "retryable": true,
  "evidence_refs": [],
  "extras": {"rounds": 3, "disagreements": []}
}
```

## 5.5 字段对照

| 字段 | 必填 | 说明 |
|---|---|---|
| `scope` + `self.session` | 是 | 上报实例身份；type 由 A1 反查 |
| `status` | 是 | `succeeded` / `failed` / `partial`（含 blocked 入口） |
| `finished_at` | 是 | 终态时间 |
| `input_view_ref` | 否 | 指向 A4；供 A6 配对验收 |
| `result.data_type` | 成功必填 | 对应 §8.2；必须与 `self.type` 兼容 |
| `result.summary` | 是 | 给消费方快速判断的短描述 |
| `result.data` | 否 | 结构化结果，按 data_type 解析 |
| `result.artifact_refs[].uri` | 否 | 引用直接带 uri |
| `result.evidence_refs` | 否 | 结果证据精确点 |
| `error.code` | 失败必填 | 受控枚举（见 §8.3） |
| `error.stage` | 失败必填 | 哪一步出问题；retry 配方需要 |
| `error.retryable` | 是 | 决策建议（不是 Server 决策） |
| `error.evidence_refs` | 否 | 错误细节指向 |
| `error.extras` | 否 | 模态特有补充 |
| `partial_result` | 否 | **与 result 同构** |

## 5.6 与 v5 差异

| 项 | 处理 |
|---|---|
| `execution_id` path 参数 | 删除；改用 `(scope, self.session)` 寻址 |
| `Idempotency-Key` | 删除；Server 用 `(scope, session)` 判重 |
| `started_at` 等 | 删除；不进协议 |
| `usage` | 不在 A5；走独立 metrics 通道 |
| `tool_calls` | 不内联；走 `evidence_refs` |
| `input` 全文 | 不在 A5；用 `input_view_ref` 指向 A4 |
| `type` | 不在 A5 重复；由 A1 反查 |

---

# 6. A6：Validation

## 6.1 请求

```http
POST /v1/validations
```

```json
{
  "scope": "task_1",
  "self": {"session": "s2"},

  "input_view_ref": "view_456",
  "outcome_ref": "evt_outcome_1",

  "criteria": [
    {
      "criterion_id": "ac_1",
      "status": "passed|failed|unknown",
      "evidence_refs": [],
      "reason": "结论已包含基线与对比"
    }
  ],

  "summary": "通过验收",
  "verifier_id": "verifier_1"
}
```

## 6.2 响应

```json
{
  "scope": "task_1",
  "self": {"session": "s2"},
  "validation_id": "validation_1",
  "final_status": "passed|failed",
  "validated_at": "..."
}
```

## 6.3 要点

- 验收针对一条 A5 上报，对应 `(scope, session)` 身份
- `input_view_ref` 让 Verifier 对照输入；`outcome_ref` 指向被验收的 A5 outcome
- **Verifier 不替换 A5 的 status**：A5 是声称，A6 是结论
- 失败时 Task 决策 retry：retry 通过 A1 新建绑定，`parent` 含上次失败 session

---

# 7. A7：KnowledgeExtraction

向 ECB 推荐一个完整 Case 进行知识加工。

## 7.1 请求

```http
POST /v1/knowledge/candidates
```

```json
{
  "scope": "task_1",
  "case_sessions": ["s1", "thread_1", "s3"],
  "candidate_type": "failure_pattern|solution|constraint|background",
  "content_ref": "artifact://case_pack_1",
  "source_refs": [],
  "scope_summary": "..."
}
```

## 7.2 响应

```json
{
  "candidate_id": "kc_123",
  "status": "submitted",
  "processing_status_ref": "..."
}
```

## 7.3 要点

- **Case 不是单点 outcome**，而是任务内一条绑定链/分支（DAG）
- `case_sessions` 指明由哪些 session 拼接出这个 Case；type 由 A1 反查
- ECB 自取 `content_ref` 完整大文本；本协议不传原文

---

# 8. 领域对象目录

## 8.1 执行身份相关

| 对象 | 字段 | 说明 |
|---|---|---|
| `Scope` | `scope`(string) | 任务范围 id |
| `Session` | `session`(string) | scope 内唯一的运行实例 id |
| `Binding` | `scope + self + parent[]` | A1 记录的关系对象 |

### Self Type 目录

| `type` | 必备字段 | 可选字段 | 含义 |
|---|---|---|---|
| `bot` | `session`, `participants` | — | 单 Bot 运行实例 |
| `group` | `session`, `participants` | `leader` | 协作群讨论线程 |
| `bbs` | `session` | — | BBS thread / post 兜底 |
| 后续可扩 | | | `poller` / `external` 等 |

注：

- `participants` 类型为 `string[]`（bot_id 列表）；bot 是单元素数组，group 是多元素数组，bbs 省略
- `leader` 是 bot_id 字符串；绑定时就能确定的话尽量填，避免下游靠对话历史反查
- 新增 type 只登记本表，下游协议形状不变

## 8.2 Result Schema Directory

`result.data_type` 取值受控。data_type 必须与 `self.type` 兼容（由 A1 反查得到 self.type）。

| `data_type` | 适用 self.type | 字段 |
|---|---|---|
| `bot.execution_output` | `bot` | `assistant_message_ref`, `structured_claims`, `tool_artifacts`, `follow_up_suggested` |
| `group.decision_result` | `group` | `decisions[]`, `proposals[]`, `open_questions[]`, `final_summary_ref` |
| `poller.passthrough_result` | 后续 type=`poller` | `external_run_id`, `external_status`, `mapped_internal_result`, `external_metrics`, `raw_response_ref` |
| `bbs.submission` | `bbs` 或 `bot`（BBS 派发的 bot） | `work_item_id`, `submission`, `deliverable_artifact_refs`, `handed_off_to` |

### 详细结构（单 Bot）

```json
"data": {
  "assistant_message_ref": "msg_135",
  "structured_claims": {},
  "tool_artifacts": [
    {"tool": "search_log", "artifact_ref": "..."}
  ],
  "follow_up_suggested": [
    {"type": "plan", "text": "..."}
  ]
}
```

### 详细结构（协作群）

```json
"data": {
  "decisions": [
    {
      "text": "连接池上限调到 100",
      "agreed_by": ["bot_A", "bot_B"],
      "dissenting_by": [],
      "decision_event_refs": ["msg_120"]
    }
  ],
  "proposals": [],
  "open_questions": [],
  "final_summary_ref": "msg_135"
}
```

### 详细结构（BBS Submission）

```json
"data": {
  "work_item_id": "item_7",
  "submission": {
    "submitted_by": "agent_a",
    "submitted_at": "...",
    "claim_id": "claim_5",
    "lease_id": "lease_7",
    "title": "已完成连接池调整",
    "deliverable_type": "fix"
  },
  "deliverable_artifact_refs": [],
  "handed_off_to": null
}
```

## 8.3 Error Catalog

`error.code` 一等枚举；模态特有补充放 `error.extras`。

| `code` | 适用 self.type | 关键 `extras` |
|---|---|---|
| `TOOL_CALL_FAILED` | `bot` | `failed_tool_name`, `tool_input_ref` |
| `AGENT_RUNTIME_ERROR` | `bot` | `crash_stack_ref`, `restartable` |
| `NO_CONSENSUS` | `group` | `rounds`, `disagreements[]` |
| `EXTERNAL_RUN_FAILED` | 后续 `poller` | `external_run_id`, `external_error_code` |
| `LEASE_EXPIRED` | `bbs` | `lease_id`, `expired_at` |
| `INPUT_REQUIRED` | 跨模态 | `missing_evidence_refs[]` |
| `DATA_PARTITION_MISSING` | 业务类（任意） | `missing_partitions[]` |

## 8.4 Requirement Vocabulary

A3 `requirements.include/exclude` 受控词表（首版，可扩展）：

```text
hard_dependencies
current_spec
failure_feedback
attempted_plan
acceptance_criteria
cross_task_knowledge
conversation_history
partial_results
decisions
proposals
evidence_refs
unrelated_history
```

## 8.5 Purpose / Variant 受控表

| purpose | variant | 默认 required | 默认 excluded |
|---|---|---|---|
| `task.plan` | initial | `current_spec`, `acceptance_criteria`, `cross_task_knowledge` | `conversation_history` |
| `task.plan` | replan | `current_spec`, `failure_feedback`, `attempted_plan` | `unrelated_history` |
| `task.execute` | initial | `current_spec`, `hard_dependencies`, `acceptance_criteria` | `partial_results` |
| `task.execute` | retry | `current_spec`, `hard_dependencies`, `failure_feedback`, `attempted_plan` | `unrelated_history` |
| `task.accept` | result | `current_spec`, `acceptance_criteria`, `evidence_refs` | — |
| `agent.respond` | default | `conversation_history`, `cross_task_knowledge` | — |
| `collaboration.capture` | default | `decisions`, `proposals`, `evidence_refs` | — |
| `audit.inspect` | default | `*`（全部） | — |

---

# 9. 相对 v5 的总结

## 9.1 删除

- `execution_id` 全协议外露字段
- A1 中 `work_ref`, `attempt_ref`, `mode`, `runtime_ref`, `node`
- A2 中"注册来源"部分（由 A1 覆盖）
- A1 / A5 中 `Idempotency-Key`（`(scope, session)` 自然幂等）
- A5 中 `usage` / `tool_calls` / `input` 全文 / `session_ref` 内联
- A4 中 `representation=ref|snippet`（第一版未实装）
- A4 中 `derivation.method_version` / `context_version` 等高级审计字段

## 9.2 保留

- A1–A7 共 7 个协议族
- 三层消费请求声明：`purpose/variant → requirements → budget`
- A4 View 的 `completeness` / `omitted` / `conflicts` / `based_on`
- Result 的"外壳稳定 + `data` 模态特有"双层
- Error 的"受控 `code` + 模态 `extras`"双层
- ECB 知识脱出/回填的边界（A7）

## 9.3 新增本版（session 主键化）

- 身份主键统一为 **`(scope, session)`**；session 在 scope 内唯一
- A1 self 删 `node`，改用 `type` + `participants` 表达节点身份
- A1 parent 改为数组，AND 语义；`[]` 表达 root
- `parent` 反查：Server 在同 scope 内用 session 反查 type/participants；下游不重复携带
- A2–A7 self/consumer/case 等字段全部瘦到 `{session}`
- A1 第 8 节加 Self Type 目录；新增 type 仅登记本表，不改协议形状

---

# 10. 落地次序（建议）

1. A1 + A5：能建绑定、能报结果（bot 模态先打通）
2. A1 加 group 模态 + leader：协作群参与者入绑定，群能产 decision 类 outcome
3. A3 + A4 通的最小可用：至少 `task.execute.initial` 一种配方
4. A6 验收接入：能区分声称与结论
5. A2 上线：Context/Verifier 能拉原始事件流
6. A1 加 bbs 模态：BBS 作为 parent 触发 bot/group 工作
7. A1 加 poller 模态：外部 Run 观察纳入绑定（如真需要）
8. A7 接 ECB：选定一种 `candidate_type` 跑通

任何时候新增模态、新 Result Schema、新 Error Code，通过第 8 节目录登记即可，不必修改协议形状。
