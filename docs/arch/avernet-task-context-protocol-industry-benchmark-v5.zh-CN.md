# Avernet Task Context 协议与业界真实协议对比 V5

> 日期：2026-09-10  
> 状态：协议设计稿  
> 目的：用业界真实的领域对象、API/DSL、核心字段和生命周期，检验 Avernet Context 协议是否可行、是否缺能力、是否存在不必要字段。  
> 重要边界：本文不把业界产品强行改写成 Avernet 协议。每个产品先列原生对象和原生协议，再做对齐；无法确认的能力标记为“未发现”，不写成“没有”。

---

## 0. 先说清楚：这次对比要证明什么

本文不是为了证明“业界也有一个 Context 模块”，也不是先定义一套 Avernet 标准，再把所有产品硬套进来。

要验证的是四个问题：

1. **Avernet 的每个协议是否都对应一个真实消费/生产场景？**
2. **业界同类产品是否有 Avernet 没有的协议？如果有，Avernet 是否真的缺？**
3. **Avernet 比业界多出的协议或字段，是否由动态 Task Loop、多模态来源、知识闭环等额外场景造成？**
4. **相同职责的协议，双方的领域对象和字段为什么不同？是产品边界不同，还是设计不完整？**

所以本文采用固定顺序：

```text
Avernet 原生协议和领域对象
→ 一次具体运行如何经过这些协议
→ 业界产品的原生对象、原生 API/DSL、核心字段
→ 与 Avernet 逐项对齐
→ 解释协议数量和字段数量的差异
→ 删除没有消费场景的设计，补上真实缺口
```

### 0.1 “协议数量”怎么数

不能直接数 HTTP API 数量。Step Functions 的 `StartExecution`、`SendTaskSuccess`、`GetExecutionHistory` 是三个 API，但它们可能属于三个不同协议族，也可能只是同一个执行协议的不同操作。

本文采用两层统计：

| 层级 | 统计对象 | 示例 |
|---|---|---|
| API/操作 | 线上或 SDK 可调用的具体操作 | `StartExecution`、`SendTaskSuccess` |
| 协议族 | 一组围绕同一生命周期职责的操作/字段 | 执行启动协议、节点输入投影协议、结果回调协议 |

本文重点比较**协议族和核心字段**，同时列出关键原生 API，避免“把字段拼成协议”的泛化。

### 0.2 “有/没有”怎么判定

| 标记 | 含义 |
|---|---|
| `原生具备` | 官方对象/API/DSL 明确承担该职责 |
| `部分具备` | 能完成相近动作，但身份、生命周期或保证不同 |
| `外部实现` | 产品允许接入，但产品核心协议不定义 |
| `未发现` | 在核对的官方规范/仓库资料中未发现统一能力；不是断言生态中绝对不存在 |
| `不需要` | 因产品模型已把该责任放在别处，不构成缺失 |

### 0.3 业界协议事实的可核实性标注

本节是给"扣字眼"用的：下列内容在哪些范围是已核实的、哪些只能按公开规范常识引用。

| 内容 | 核实范围 | 标注 |
|---|---|---|
| Avernet 领域对象与协议、当前实现差距 | 已对照本仓库 `Avernet/src/bcsfuse` 源码逐项核对（见 §12.3 的文件行号） | 已核实 |
| LoopX 控制面对象 Goal/Todo/Gate/Evidence/Quota/Claim/Lease/Run、bounded turn、控制面与 harness 分层 | 与本仓库既有调研底稿 `docs/arch/task-loop-industry-context-study.zh-CN.md` 一致 | 已核实（本地底稿） |
| LoopX 具体 CLI 命令名 `start-goal`/`connect`/`review-packet`/`quota should-run`/`capability show`、`Apache-2.0 OR MIT` 许可证、`docs/state-interaction-model.md` 路径 | 本地材料未逐字核对；V5 引用路径与本地底稿引用的 `architecture.md`/`04-state-substrate.md` 不一致 | **待联网复核**，不作已证实字段 |
| AWS `StartExecution`/`SendTaskSuccess`、ASL `InputPath/Parameters/ResultSelector/ResultPath/OutputPath/Retry/Catch`、Task Token、`DescribeExecution/GetExecutionHistory` | 按 AWS 公开规范常识引用；本仓库未联网复核当前版本字段集合 | 公开规范常识，未逐版本复核 |
| AWS State/Payload 256 KiB 约束、`traceHeader` 字段行为 | 未在本地逐字核对 | 待复核 |
| Temporal Workflow ID/Run ID/Activity/Activity Task/Activity Execution Attempt/Event History/Retry Policy/Continue-As-New | 按 Temporal 公开文档常识引用；未联网复核当前 SDK 版本字段 | 公开规范常识，未逐版本复核 |
| A2A `contextId`/`taskId`/`referenceTaskIds`/`historyLength`/`Artifact`/`TaskStatus`、`message/send`/`tasks/get`/`message/stream`/`tasks/cancel` | 按 A2A 公开规范引用；未逐版本核对字段是否仍在当前 spec | 公开规范常识，未逐版本复核 |
| AutoGen Magentic-One Task Ledger/Progress Ledger、Orchestrator 选下一 speaker | 引用为承载面/机制而非稳定外部网络协议；是否称"协议族"有口径争议 | 仅作动态 Loop 参考，不主张为强类型协议 |

> 含义：标 `公开规范常识` 的字段名稳定、变更风险低，可作定性对比依据；不应以它们做逐字段一致性强结论。标 `待复核` 的不进入协议 Core 字段依据。

---

# 1. Avernet 的领域对象与协议

## 1.1 领域对象：对象边界先于 API

Avernet 需要区分以下对象。每个对象都有自己的身份、生命周期和权威来源，不能把它们都叫 `output` 或 `context`。

| 对象 | 定义 | 必须独立存在的原因 | 权威来源 |
|---|---|---|---|
| `Scope` | 一组工作的上层范围，如 Task、Goal、Session、协作空间 | Bot 可能只知道 Session；Context 不能假设所有来源都有 Task | Task/Goal/Session 控制面 |
| `Work` | 当前需要完成的一项工作 | 动态 Loop 不应把协议绑定到 Graph Node | Planner/控制面 |
| `Execution` | 某个 Work 被实际派发的一次执行 | 同一 Work 可被多次执行；需要区分本次执行 | Task Runtime |
| `Attempt` | Execution 的一次尝试，如首次执行、重试 | 失败重试需要保留旧尝试，不能覆盖 | Task Runtime |
| `RuntimeSession` | 实际执行载体，如 Bot Session、协作群线程 | 执行者可能不知道 Task；也可能同一 Session 承载多次执行 | Runtime/来源系统 |
| `SourceLink` | 指向已有 Bot Session、群、BBS 等权威来源的引用 | Context 不复制原始全文，但必须知道去哪里读 | Context Registry + 来源系统 |
| `SourceRecord` | 来源系统中的一条原始记录 | 原始事实和正式结果不是一回事 | Bot/群/BBS 存储 |
| `ContextSpec` | 当前消费请求声明需要什么 | 让动态 Planner 把硬依赖明确写出来 | Planner/调用方 |
| `ContextView` | Context 按 Spec 实际交付的视图 | “请求了什么”和“实际给了什么”必须可区分 | Context |
| `Candidate` | 执行者声明本次执行形成的结果 | 执行者只能声称完成，不能直接完成验收 | Runtime Adapter |
| `Validation` | 对 Candidate 的独立验收 | 防止执行者自己宣布正式完成 | Verifier/Task Runtime |
| `Artifact` | 大结果、文件、数据集、报告的引用 | 防止大内容进入 inline context；保留可回取性 | Artifact Store |
| `Knowledge` | 经过 ECB 抽取、验证、版本化并发布的跨任务知识 | 当前压缩摘要不等于长期知识 | ECB |

### 1.1.1 关键关系

```text
Scope
 └── Work
      └── Execution
           └── Attempt
                ├── ContextSpec
                ├── ContextView
                ├── Candidate
                └── Validation

Scope / Work / Execution / Attempt：Task Runtime 的执行事实。
SourceLink / SourceRecord：已有原始过程。
Artifact：大内容的外置结果。
Knowledge：跨任务复用的治理结果。
```

`Attempt` 不是为了描述某个 Graph 节点是否被“复制”。同一个 `Work` 可以有多个 `Attempt`，每个 Attempt 对应独立的 `Execution` 和 `RuntimeSession`；失败记录继续存在。

## 1.2 Avernet 协议族清单

| 编号 | 协议族 | 生产者 | 消费者 | 解决的问题 |
|---|---|---|---|---|
| A1 | `ExecutionBinding` | Task Runtime/调用方 | Context、Runner、Verifier | 本次执行属于哪个 Scope、Work、Attempt、Runtime |
| A2 | `SourceCapture` | Runtime Adapter/来源 Adapter | Context、ECB、审计 | 从哪里读取原始过程；不重复存全文 |
| A3 | `ContextSpec` | Planner/Task Runtime | Context | 当前消费目的、硬依赖、可选来源、预算和输出要求是什么 |
| A4 | `ContextView` | Context | Planner/Runner/Verifier | 实际交付了哪些信息，哪些缺失/省略，是否满足硬依赖 |
| A5 | `ExecutionOutcome` | Runner/Runtime Adapter | Task Runtime、Context、Verifier | 本次执行成功、失败、部分结果、产物和证据是什么 |
| A6 | `Validation` | Verifier | Task Runtime、Context、ECB | Candidate 是否被接受，失败反馈是什么 |
| A7 | `KnowledgeExtraction` | Context/Task Runtime | ECB | 哪个完整 Case 需要加工为经验、规则或失败模式 |

**初步结论：Avernet 是 7 个协议族。** 但这不是最终结论；后文会检查哪些能合并，哪些是因为产品承担了业界没有承担的职责。

---

## 1.3 A1：ExecutionBinding API

### API

```http
POST /v1/executions
Content-Type: application/json
Idempotency-Key: bind_20260910_0001
```

### 请求

```json
{
  "scope_ref": {
    "type": "task",
    "id": "task_1"
  },
  "work_ref": {
    "type": "work",
    "id": "work_20"
  },
  "attempt_ref": {
    "type": "attempt",
    "id": "attempt_2"
  },
  "runtime_ref": {
    "type": "bot_session",
    "id": "session_9"
  },
  "mode": "retry"
}
```

### 响应

```json
{
  "execution_id": "exec_20_2",
  "scope_ref": {"type": "task", "id": "task_1"},
  "work_ref": {"type": "work", "id": "work_20"},
  "attempt_ref": {"type": "attempt", "id": "attempt_2"},
  "runtime_ref": {"type": "bot_session", "id": "session_9"},
  "status": "created",
  "created_at": "2026-09-10T10:30:00+08:00"
}
```

### 核心字段为什么这样设计

| 字段 | 必须性 | 设计理由 |
|---|---:|---|
| `execution_id` | 必须 | Context View、Candidate、Validation 必须指向同一次实际执行；不能只用 Session ID，因为一个 Session 可承载多次执行 |
| `scope_ref` | 必须 | 不假设上层一定是 Task；支持 Goal、Session、协作空间 |
| `work_ref` | 必须 | 当前执行的工作对象；不绑定 Graph Node 名称 |
| `attempt_ref` | 必须 | 重试时区分旧失败和新尝试 |
| `runtime_ref` | 必须 | 指向真实执行载体和原始过程入口 |
| `mode` | 必须 | 首次、重试、恢复等会影响 ContextSpec 配方；不应由 Context 读取历史猜测 |
| `Idempotency-Key` | 必须 | 创建请求重投不能生成两个 Execution |

**谁填写：**知道 Task/Goal 归属的 Task Runtime 或父调用方填写；Bot 不填写 `scope_ref`、`work_ref`、`attempt_ref`。

## 1.4 A2：SourceCapture API

A2 不是“把原文再次上报给 Context”。当前 Bot Session、协作群、BBS 已经有权威存储，A2 的生产动作是**注册可读来源和读取游标**。

### 注册来源

```http
POST /v1/source-links
```

```json
{
  "source_ref": {
    "type": "bot_session",
    "id": "session_9"
  },
  "scope_ref": {
    "type": "task",
    "id": "task_1"
  },
  "access": {
    "mode": "read",
    "owner": "bot-runtime"
  },
  "observed_from": "2026-09-10T10:30:00+08:00"
}
```

### 读取来源

```http
GET /v1/source-links/bot_session%2Fsession_9/records?cursor=message_100&limit=100
```

```json
{
  "records": [
    {
      "record_id": "message_109",
      "occurred_at": "2026-09-10T10:38:00+08:00",
      "actor_ref": {"type": "bot", "id": "risk_bot"},
      "parts": [
        {"media_type": "text/plain", "text": "缺少最近三个月数据"},
        {"media_type": "application/json", "artifact_ref": "artifact_41"}
      ],
      "relations": [
        {"type": "reply_to", "record_id": "message_101"}
      ]
    }
  ],
  "next_cursor": "message_109"
}
```

### 为什么不把 `SourceRecord` 和 `ExecutionOutcome` 合成一个对象

```text
SourceRecord：来源系统发生了什么，可能包含输入、工具调用、中间失败、多个参与者的发言。
ExecutionOutcome：这次受管执行声称交付了什么，必须能被 Task/Verifier 识别。
```

同一个 Session 可能产生多个 Work 的结果；如果把 Session 最后一条消息当 Outcome，就无法确定它属于哪个 Execution，也无法判断它是正式结果还是中间信息。

## 1.5 A3：ContextSpec API

```http
POST /v1/executions/{execution_id}/context-spec
```

```json
{
  "purpose": "execute",
  "variant": "retry",
  "assignment": {
    "goal": "完成支付风险分析",
    "instruction": "补齐最近三个月的数据验证",
    "constraints": ["不得修改已验收的支付入口结论"]
  },
  "required_inputs": [
    {
      "kind": "accepted_result",
      "ref": {"type": "work", "id": "work_7"},
      "representation": "result_capsule"
    },
    {
      "kind": "accepted_result",
      "ref": {"type": "work", "id": "work_12"},
      "representation": "result_capsule"
    },
    {
      "kind": "prior_failure",
      "ref": {"type": "attempt", "id": "attempt_1"},
      "representation": "failure_feedback"
    }
  ],
  "optional_context": {
    "source_refs": [
      {"type": "bot_session", "id": "session_9"},
      {"type": "group_thread", "id": "thread_3"}
    ],
    "query": "最近三个月数据缺失及补齐方法",
    "include_published_knowledge": true
  },
  "budget": {
    "max_inline_tokens": 12000,
    "max_inline_bytes": 200000
  },
  "output_contract": {
    "required_fields": ["risk_count", "highest_level"],
    "evidence_required": true
  }
}
```

### `ContextSpec` 的字段层级

| 层级 | 字段 | 是否可被语义检索替代 | 说明 |
|---|---|---:|---|
| 身份 | `execution_id`（路径参数） | 否 | 绑定当前执行 |
| 消费意图 | `purpose`、`variant` | 否 | 决定读取配方，如 `plan.initial`、`execute.retry` |
| 当前工作 | `assignment` | 否 | 当前 Work 要做什么 |
| 硬依赖 | `required_inputs` | 否 | 缺失必须阻断，不得用相似内容替代 |
| 可选来源 | `optional_context.source_refs` | 是 | 允许按相关度、时效、权限选择 |
| 知识查询 | `optional_context.query` | 是 | 只补充背景，不能代替当前硬依赖 |
| 资源约束 | `budget` | 否 | Context 必须在约束内表达 |
| 输出要求 | `output_contract` | 否 | Runner/Verifier 共同使用 |

**动态 Loop 的关键：** Planner 不是只生成一句 instruction，而是同时生成 `required_inputs`。这是 Avernet 相比静态 Workflow 多出来的运行期契约。

## 1.6 A4：ContextView API

```http
POST /v1/context-views
```

```json
{
  "execution_id": "exec_20_2",
  "spec_version": "spec_20_2",
  "as_of": {
    "task_revision": 18,
    "source_cursor": "message_109",
    "knowledge_revision": "knowledge_rev_42"
  }
}
```

响应：

```json
{
  "view_id": "view_20_2",
  "execution_id": "exec_20_2",
  "purpose": "execute",
  "variant": "retry",
  "assignment": {
    "goal": "完成支付风险分析",
    "instruction": "补齐最近三个月的数据验证"
  },
  "required_inputs": [
    {
      "kind": "accepted_result",
      "ref": {"type": "work", "id": "work_7"},
      "status": "available",
      "digest": "身份核验通过",
      "data": {"identity_status": "verified"},
      "source": {"type": "candidate", "id": "candidate_7"},
      "validation": {"type": "validation", "id": "validation_7"}
    }
  ],
  "optional_context": [
    {
      "source_ref": {"type": "bot_session", "id": "session_9"},
      "record_ids": ["message_101", "message_109"],
      "reason": "直接讨论最近三个月数据缺口",
      "representation": "excerpt"
    }
  ],
  "knowledge": [
    {
      "knowledge_ref": "knowledge_42",
      "digest": "先核验分区完整性，再决定回补",
      "version": "v3"
    }
  ],
  "artifacts": [
    {
      "artifact_ref": "artifact_19",
      "inline": false,
      "reason": "size_limit"
    }
  ],
  "completeness": {
    "required_complete": true,
    "missing": []
  },
  "budget": {
    "max_inline_tokens": 12000,
    "used_inline_tokens": 8400
  },
  "omitted": [
    {
      "ref": {"type": "source_record", "id": "message_100"},
      "reason": "lower_relevance_than_selected_records"
    }
  ]
}
```

### Context 的硬约束

```text
1. required_inputs 先解析、先校验，不能参与 TopK 淘汰。
2. required_inputs 有一项不可读取、版本不满足或未验收，返回 required_complete=false。
3. max_inline_tokens 只能裁剪 optional_context 或把大内容降级为 ArtifactRef。
4. ContextView 必须保存 as_of、来源和 omitted，保证“当时实际给了什么”可解释。
```

## 1.7 A5：ExecutionOutcome API
> 术语统一：本文 `ExecutionOutcome`（A5 API 承载对象）与领域对象表中的 `Candidate`（§1.1）指**同一对象**——"执行者声称、但尚未被验收的本次执行结果"。下文 A6 的 `outcome_id` 即 `candidate_id`。两个名字是为分清"API 产出的对象名"与"领域对象的角色名"；落地应只保留一个正式名。



```http
POST /v1/executions/{execution_id}/outcomes
Idempotency-Key: outcome_exec_20_2_terminal
```

成功：

```json
{
  "status": "succeeded",
  "finished_at": "2026-09-10T10:40:00+08:00",
  "result": {
    "summary": "发现 3 项高风险差异",
    "data": {
      "risk_count": 3,
      "highest_level": "high"
    },
    "artifact_refs": [
      {"artifact_id": "artifact_42", "media_type": "text/markdown"}
    ],
    "evidence_refs": [
      {
        "source_ref": {"type": "bot_session", "id": "session_9"},
        "record_ids": ["message_131", "message_135"]
      }
    ]
  }
}
```

失败：

```json
{
  "status": "failed",
  "finished_at": "2026-09-10T10:40:00+08:00",
  "error": {
    "code": "DATA_PARTITION_MISSING",
    "message": "最近三个月分区不完整",
    "retryable": true
  },
  "partial_result": {
    "summary": "已完成前两个月核验"
  }
}
```

### Outcome 的核心字段

| 字段 | 作用 | 为什么不能省略 |
|---|---|---|
| 路径中的 `execution_id` | 绑定本次执行 | 不能通过 Session 或 Work 猜归属 |
| `status` | 区分成功/失败 | 不能把失败信息混进成功结果 |
| `finished_at` | 终态时间 | 处理重复、乱序和审计 |
| `result` / `error` | 正式结果或失败原因 | 明确下游该消费什么 |
| `partial_result` | 失败但可复用的部分结果 | 不把所有失败 Session 都当可用结果 |
| `artifact_refs` | 大产物引用 | 不把大内容内联进 Context |
| `evidence_refs` | 结果依据 | 支持验收和知识加工 |
| 幂等键 | 重复回投去重 | Callback、Poller、重试可能重复上报 |

## 1.8 A6：Validation API

```http
POST /v1/outcomes/{outcome_id}/validations
```

```json
{
  "decision": "accepted",
  "checks": [
    {"name": "required_fields_complete", "passed": true},
    {"name": "evidence_available", "passed": true},
    {"name": "acceptance_criteria_met", "passed": true}
  ],
  "feedback": []
}
```

拒绝时：

```json
{
  "decision": "rejected",
  "checks": [
    {"name": "required_fields_complete", "passed": false}
  ],
  "feedback": [
    {
      "code": "MISSING_EVIDENCE",
      "message": "风险数量没有对应的交易明细证据",
      "retryable": true
    }
  ]
}
```

`Validation` 不修改历史 Candidate；它产生新的验收事实。下一次 retry/replan 是否创建，由 Task Runtime 决定。

## 1.9 A7：KnowledgeExtraction API

```http
POST /v1/knowledge-extraction/cases
```

```json
{
  "scope_ref": {"type": "task", "id": "task_1"},
  "source_refs": [
    {"type": "bot_session", "id": "session_9"},
    {"type": "group_thread", "id": "thread_3"}
  ],
  "execution_refs": ["exec_19_1", "exec_20_2"],
  "outcome_refs": ["outcome_19_1", "outcome_20_2"],
  "validation_refs": ["validation_20_2"],
  "intents": ["experience", "rule", "failure_pattern"]
}
```

这里 Context/Task Runtime 只提交 Case 边界和引用，ECB 负责：

```text
抽取 → 去重 → 冲突处理 → 证据关联 → 版本化 → 发布/撤回
```

为当前 Context 做临时摘要不等于知识加工：

| 动作 | 归属 |
|---|---|
| 把 200 KB 报告压缩为 500 token 供本次 Runner 使用 | Context |
| 从多次 Task 失败中提炼“缺数时先检查分区”的规则并发布 | ECB |

---

# 2. Avernet 一次完整运行：协议实际怎样串联

```text
1. Task Runtime 创建 Work。
2. Task Runtime 创建 Attempt，并 POST /v1/executions 得到 execution_id。
3. Planner POST /context-spec，声明 assignment、required_inputs、optional_context、budget。
4. Context POST /context-views，返回 ContextView。
5. 若 required_complete=false，Task Runtime 不派发 Runner，进入补数据/replan。
6. 若完整，Runner 在指定 RuntimeSession 中执行。
7. Runtime Adapter POST /outcomes，产生 Candidate/Outcome。
8. Verifier POST /validations。
9. accepted：Task Runtime 将结果标记为可用。
10. rejected + retryable：Task Runtime 创建新的 Attempt/Execution，旧 Outcome 保留。
11. 需要跨 Task 复用：提交 KnowledgeExtraction Case 给 ECB。
```

其中 Context 不负责：

```text
不创建 Task/Work；不决定是否 retry；不改变 Task 状态；不自行发布 Knowledge；不把失败结果改写为成功结果。
```

---

# 3. 业界对比的共同场景

为了避免各说各话，后文都用同一个场景：

```text
用户目标：完成支付风险分析。
第 20 个工作需要：
  - 身份核验结果；
  - 支付摘要；
  - 风险结果；
  - 规则版本；
  - 最近三个月数据缺失的处理背景。

其中风险报告是 200 KB；第 20 个工作首次失败后需要重试。
```

对每个产品都只回答同一组问题：

1. 用户创建/启动什么对象？
2. 哪个对象表示本次实际执行？
3. 当前工作输入由什么 API/DSL/函数参数生成？
4. 结果由什么 API/对象回流？
5. 原始过程在哪里？如何读取？
6. 重试如何区分？
7. 是否有知识加工？
8. 与 Avernet 的协议/字段差异是什么？为什么？

---

# 4. AWS Step Functions：协议多，但由状态机合并了动态选择责任

## 4.1 AWS 原生领域对象

| AWS 对象 | 官方含义/使用方式 | 对本次场景的作用 |
|---|---|---|
| State Machine | Amazon States Language 定义的状态机 | 设计期定义节点、分支和输入投影 |
| Execution | 一次状态机运行，具有 `executionArn` | 对应一次 Workflow Run |
| State | Execution 当前所在的状态 | 对应一个预定义步骤，不等于动态 Work |
| Task State | 调用 Lambda、SDK、Activity 等外部动作的状态 | 触发执行 |
| Task Token | Callback 模式下代表等待中 Task 的不透明令牌 | Worker 回传时绑定执行 |
| State Input/Output | 状态之间传递的 JSON | 节点输入和结果承载 |
| Execution History | 状态转换和执行事件 | 查询过程和审计 |

## 4.2 AWS 原生协议族和核心 API/字段

| 协议族 | 原生 API/DSL | 核心字段/机制 | 负责什么 |
|---|---|---|---|
| 状态机定义协议 | `CreateStateMachine`、ASL `States` | `StartAt`、`States`、`Type`、`Next`、`Choice` | 定义允许的状态和转移 |
| Execution 启动协议 | `StartExecution` | `stateMachineArn`、`name`、`input`、`traceHeader` | 创建一次 Execution |
| 节点输入投影协议 | ASL Task 字段 | `InputPath`、`Parameters`、Context Object | 从 State 构造当前 Task 输入 |
| 结果投影/状态合并协议 | ASL Task 字段 | `ResultSelector`、`ResultPath`、`OutputPath` | 选择结果并决定下游看到什么 |
| Callback 结果协议 | `SendTaskSuccess`、`SendTaskFailure` | `taskToken`、`output` 或 `error`/`cause` | Worker 把结果绑定回等待中的 Task |
| 执行查询协议 | `DescribeExecution`、`GetExecutionHistory` | `executionArn`、`status`、`input`、`output`、events | 读取运行状态和历史 |
| 重试/错误路由协议 | ASL `Retry`、`Catch` | `ErrorEquals`、`IntervalSeconds`、`MaxAttempts`、`BackoffRate`、`Next` | 失败后的重试和转移 |

**AWS 至少有 7 个协议族。** 这比 Avernet 的 7 个协议族并不说明两者相同：AWS 的 `Retry/Catch` 和 `State Machine Definition` 承担了 Avernet 中部分运行时职责；AWS 没有 Avernet 的多源采集和 ECB 知识协议。

## 4.3 AWS 关键原生字段实例

### 启动

```json
{
  "stateMachineArn": "arn:aws:states:cn-hangzhou:123:stateMachine:payment-risk",
  "name": "payment-risk-20260910-001",
  "input": "{\"order_id\":\"order_1\",\"rule_version\":\"r7\"}",
  "traceHeader": "Root=1-..."
}
```

### Task 输入和结果投影

```json
{
  "Type": "Task",
  "Resource": "arn:aws:lambda:cn-hangzhou:123:function:risk-check",
  "InputPath": "$.payment",
  "Parameters": {
    "payment.$": "$",
    "identity.$": "$.identity_result",
    "ruleVersion.$": "$$.Execution.Input.rule_version"
  },
  "ResultSelector": {
    "riskLevel.$": "$.Payload.risk_level",
    "reportKey.$": "$.Payload.report_key"
  },
  "ResultPath": "$.risk_result",
  "Retry": [
    {
      "ErrorEquals": ["Lambda.ServiceException"],
      "IntervalSeconds": 2,
      "MaxAttempts": 3,
      "BackoffRate": 2
    }
  ]
}
```

### Callback 结果

```json
{
  "taskToken": "opaque-token-issued-by-step-functions",
  "output": "{\"risk_level\":\"high\",\"report_key\":\"s3://...\"}"
}
```

AWS 官方 `SendTaskSuccess` 的核心输入就是 `taskToken + output`；它不是一个通用的 `task_id + node_id + attempt_id` 业务上报协议。Task Token 代表 AWS 已经创建的等待中 Task。

## 4.4 与 Avernet 逐项对齐

| Avernet 协议族 | AWS 原生最接近的协议 | 结论 | 差异原因 |
|---|---|---|---|
| A1 `ExecutionBinding` | Execution + State + Task Token | **部分具备** | AWS 能绑定 Execution/State/Callback Task，但不提供通用 Scope/Work/Attempt 对象；其 Work 在状态机中提前定义 |
| A2 `SourceCapture` | State Input + Execution History | **部分具备** | AWS 记录 Workflow 自己的数据，不负责读取 Bot Session/群/BBS；数据边界集中 |
| A3 `ContextSpec` | InputPath + Parameters | **部分具备** | AWS 有节点输入契约，但在 ASL 设计期确定，不是运行期 Planner 生成 |
| A4 `ContextView` | 传给 Task 的 JSON Input | **部分具备** | 有输入投影，但不是独立 View，不返回 `required_complete/omitted/provenance` |
| A5 `ExecutionOutcome` | SendTaskSuccess/Failure + ResultSelector/ResultPath | **原生具备** | Task Token 绑定结果，ResultSelector 投影正式结果 |
| A6 `Validation` | Choice/后续 Task/Catch | **外部/编排实现** | AWS 提供控制流原语，不定义统一 Candidate/Validation 对象 |
| A7 `KnowledgeExtraction` | 无统一原生协议 | **未发现** | Step Functions 目标是执行状态机，不是跨 Workflow 知识治理 |

## 4.5 AWS 比 Avernet 多/少什么，为什么

### AWS 多出的协议职责

AWS 比 Avernet 当前 Context 协议多出一个核心责任：**State Machine Definition + State Transition**。Avernet 没有等价协议，因为 Planner 在运行期产生 Work，Task Runtime 自己维护工作推进；如果未来要把 Planner/Task Runtime 独立成可复用编排服务，才需要单独补充 `WorkPlan` 协议。

AWS 还把 `Retry/Catch` 做成状态机协议。Avernet 当前把“是否重试、如何重试”放在 Task Runtime，而不放在 Context。这个差异是边界选择，不是 Context 缺失。

### Avernet 多出的协议职责

Avernet 比 AWS 多出：

- `SourceCapture`：数据来自多种外部权威来源；
- `ContextSpec/View` 的运行时语义选择和完整性返回；
- `KnowledgeExtraction`：跨 Task 提炼知识；
- 独立 `Validation` 对象：AWS 可编排验收，但不提供统一对象。

原因是 AWS 的节点输入来自同一份 Workflow State，节点/依赖在设计期已经确定；Avernet 既有多模态来源，又允许 Planner 运行时生成 Work。

## 4.6 AWS 对 Avernet 的可靠性依据

可以直接借鉴：

```text
1. 执行绑定使用不透明的一次性 Token/Execution 身份，而不是让 Worker 自己拼 Task 归属。
2. 当前节点输入必须经过显式投影，不默认传整个状态。
3. 结果经过投影后再进入后续状态。
4. 大 Payload 外置，状态里传引用。
5. Retry 规则与业务结果分开，不把一次失败改写成成功。
```

不能直接照搬：

```text
1. JSONPath 只适合结构已知的 Workflow，不能替代动态 Work 的语义选择。
2. AWS 不负责跨 Session/群/BBS 读取。
3. AWS 的 State Definition 不能替代 Avernet 运行时 ContextSpec。
```

---

# 5. Temporal：把绑定、历史、重试做深，但把输入选择留给 Workflow 代码

## 5.1 Temporal 原生领域对象

| Temporal 对象 | 作用 |
|---|---|
| Workflow Definition | 用户写的确定性 Workflow 代码 |
| Workflow Execution | 一次 Workflow 运行 |
| Workflow ID | 业务可识别的 Workflow 身份 |
| Run ID | 一次具体执行 Run 身份 |
| Activity | Workflow 调用的外部动作定义 |
| Activity Task | 派发到 Worker 的任务 |
| Activity Execution Attempt | Activity 的具体尝试；Retry Policy 可创建多次尝试 |
| Event History | Workflow 的追加事件历史 |
| Retry Policy | Activity 失败后的重试参数 |
| Continue-As-New | 用有限状态开启新的 Workflow Run，避免历史无限增长 |

## 5.2 Temporal 原生协议/承载

Temporal 不是以一组面向业务用户的 REST API 作为主要编程方式，而是：

```text
SDK Workflow/Activity 函数参数
+ Worker Task Queue
+ Service/SDK RPC
+ Event History
```

| 协议族 | 原生承载 | 核心字段/机制 | 负责什么 |
|---|---|---|---|
| Workflow 启动协议 | `StartWorkflowExecution` RPC / SDK `start` | Namespace、Workflow ID、Workflow Type、Task Queue、Input、Workflow ID Reuse Policy | 创建 Workflow Execution |
| Workflow 调度协议 | Workflow Task/Worker Poll | Task Queue、Workflow Execution、History Event | 让 Workflow Worker 根据历史恢复并决定下一动作 |
| Activity 输入协议 | `execute_activity(fn, args)` / Activity Task | Activity Type、Arguments、Task Queue、timeouts | 把当前 Activity 输入显式传给 Worker |
| Activity 结果协议 | Activity Completed/Failed/Timed Out | Result、Failure、Heartbeat、Attempt | 回流 Activity 结果和失败 |
| 历史读取/恢复协议 | Event History、Replay | Event ID、Event Type、Attributes | 恢复确定性状态、审计 |
| 重试协议 | Retry Policy | Initial Interval、Backoff Coefficient、Maximum Interval、Maximum Attempts、Non-Retryable Errors | 失败后创建新的 Activity 尝试 |
| 历史收敛协议 | Continue-As-New | New Run Input、Workflow ID 继续关联 | 把有限 continuation state 交给新 Run |

## 5.3 Temporal 的真实输入形态

```python
identity = await workflow.execute_activity(
    check_identity,
    args=[order_id],
    start_to_close_timeout=timedelta(minutes=5),
)

risk = await workflow.execute_activity(
    check_risk,
    args=[order_id, identity, rule_version],
    retry_policy=retry_policy,
    start_to_close_timeout=timedelta(minutes=5),
)
```

Temporal 不从整个 Event History 自动为 `check_risk` 选择输入。`identity`、`rule_version` 是 Workflow 代码明确传入的 Activity 参数。

## 5.4 与 Avernet 逐项对齐

| Avernet 协议族 | Temporal 原生最接近的协议 | 结论 | 差异原因 |
|---|---|---|---|
| A1 `ExecutionBinding` | Workflow ID/Run ID + Activity Task/Attempt | **原生具备但对象不同** | Temporal 的 Workflow/Activity 层次固定；Avernet Scope/Work 可不绑定 Workflow/Graph |
| A2 `SourceCapture` | Event History、Signals、Updates | **部分具备** | 记录 Temporal 自己的执行事件；不提供多模态来源 Adapter |
| A3 `ContextSpec` | Activity 参数 | **原生具备但不独立** | 由 Workflow 代码形成；没有运行时 ContextSpec 文档对象 |
| A4 `ContextView` | Activity Task 输入 | **部分具备** | 有实际输入，但不返回 optional/omitted/provenance，也不做跨来源召回 |
| A5 `ExecutionOutcome` | Activity Completed/Failed/Heartbeat | **原生具备** | 结果/失败有明确事件和身份 |
| A6 `Validation` | Workflow 代码中的判断/后续 Activity | **应用实现** | Temporal 不规定统一独立验收对象 |
| A7 `KnowledgeExtraction` | 无统一原生协议 | **未发现** | Temporal 目标是耐久执行，不是跨 Workflow 知识治理 |

## 5.5 Temporal 比 Avernet 多/少什么，为什么

Temporal 比 Avernet Context 多一个强协议：**Workflow Task/Replaying 协议**。它保证 Workflow 在故障后从历史恢复，并不是给业务 Agent 选择上下文。Avernet 不应复制这一套 Runtime 内核；但应借鉴它的追加历史和 Run 边界。

Temporal 的 Retry Policy 字段比 Avernet `mode=retry` 更细：它有间隔、退避、最大次数和不可重试错误。原因是 Temporal 负责耐久调度；Avernet Context 只负责为新 Attempt 准备输入，不应该决定调度策略。

Avernet 比 Temporal 多：

```text
SourceCapture：来源系统不止 Task Runtime；
ContextSpec/View：需要把动态 Work 的输入要求和实际投影显式化；
KnowledgeExtraction：跨任务知识闭环；
Validation：统一的 Candidate/Validation 对象，而不是由每个 Workflow 自己定义。
```

## 5.6 Temporal 对 Avernet 的可靠性依据

```text
1. Event History 用于恢复，不等于 Activity Prompt；
2. Workflow/Run/Activity Attempt 分层；
3. 失败尝试保留，Retry Policy 调度新尝试；
4. Continue-As-New 用有限状态衔接下一 Run；
5. 输入通过 Activity 参数明确传递，不让 Activity 从全历史猜。
```

---

# 6. A2A：真正的跨 Agent 协议，字段可直接核对

## 6.1 A2A 原生领域对象

| A2A 对象 | 规范含义 |
|---|---|
| `AgentCard` | Agent 能力、接口和认证信息 |
| `Message` | 一次通信，含角色和一个或多个 Part |
| `Part` | TextPart、FilePart、DataPart 等内容单元 |
| `Task` | 长运行工作的核心对象，含状态、历史和产物 |
| `TaskStatus` | 当前状态和可选状态消息 |
| `Artifact` | Task 的正式输出，可含多个 Part |
| `contextId` | 一组相关 Tasks/Messages 的上下文集合 |
| `referenceTaskIds` | Message 引用其他相关 Task 的提示 |
| `Task History` | 与 Task 相关的 Message 历史 |

A2A 规范明确区分 Message 和 Artifact：Message 用于通信、启动、澄清和状态消息；Task 输出应使用 Artifact，而不是把结果当普通 Message。

## 6.2 A2A 原生 API/方法

| 协议族 | A2A 方法/对象 | 核心字段 |
|---|---|---|
| Agent 发现协议 | `AgentCard` | name、description、url、capabilities、skills、securitySchemes |
| 消息/任务创建协议 | `message/send` | `messageId`、`role`、`parts`、`contextId`、`taskId`、`referenceTaskIds` |
| 任务状态读取协议 | `tasks/get` | `id`、`historyLength` |
| 流式状态/产物协议 | `message/stream` | TaskStatusUpdateEvent、TaskArtifactUpdateEvent |
| 任务取消协议 | `tasks/cancel` | `id` |
| 推送订阅协议 | Task Push Notification Config | `taskId`、url、authentication、token |
| 任务数据模型 | `Task` | `id`、`contextId`、`status`、`artifacts`、`history` |
| 内容模型 | `Part` | text/file/data 及 metadata |

## 6.3 A2A 核心对象实例

### Message

```json
{
  "messageId": "msg_1",
  "role": "user",
  "contextId": "ctx_1",
  "taskId": "task_1",
  "parts": [
    {"kind": "text", "text": "请分析这份风险报告"},
    {"kind": "file", "file": {"name": "risk.csv", "uri": "https://..."}}
  ],
  "referenceTaskIds": ["task_0"]
}
```

### Task

```json
{
  "id": "task_1",
  "contextId": "ctx_1",
  "status": {
    "state": "working",
    "message": null,
    "timestamp": "2026-09-10T10:30:00Z"
  },
  "artifacts": [],
  "history": []
}
```

### Artifact

```json
{
  "artifactId": "artifact_1",
  "name": "risk-report",
  "description": "风险分析报告",
  "parts": [
    {"kind": "text", "text": "发现 3 项高风险差异"}
  ],
  "metadata": {}
}
```

## 6.4 与 Avernet 逐项对齐

| Avernet 协议族 | A2A 原生最接近的协议 | 结论 | 差异原因 |
|---|---|---|---|
| A1 `ExecutionBinding` | `contextId`、`taskId`、Message 关联 | **部分具备** | A2A Task 是 Agent 边界的任务，不表达内部 Work/Attempt/Runtime 执行；`contextId` 可跨多个 Task |
| A2 `SourceCapture` | Message/Task History | **部分具备** | A2A 保存协议交互历史，不负责读取已有 BBS/群/Bot Session；且规范提示并非所有 Message 都保证进入 History |
| A3 `ContextSpec` | Message parts、`referenceTaskIds`、`historyLength` | **部分具备** | 能表达请求和相关 Task，但没有 `required_inputs` 完整性契约 |
| A4 `ContextView` | `tasks/get` 返回 Task、history、artifacts | **部分具备** | 支持按需读 history/artifact，但不做 Avernet 的必需项解析、预算选择和 omitted 解释 |
| A5 `ExecutionOutcome` | Artifact + TaskStatus | **原生具备** | 正式结果与过程 Message 分离 |
| A6 `Validation` | Task terminal status，如 completed/rejected | **部分具备** | Agent 可以报告状态，但 A2A 不定义独立 Verifier 的验证协议 |
| A7 `KnowledgeExtraction` | 未定义 | **未发现** | A2A 关注 Agent 互操作，不负责跨任务知识治理 |

## 6.5 A2A 比 Avernet 多/少什么，为什么

A2A 比 Avernet 多：

- `AgentCard` 能力发现和认证协商；
- `Part`/Extension 等跨 Agent 互操作字段；
- 流式更新、推送订阅、任务取消。

这些是 Agent 边界协议需要的，不应直接加入 Avernet Context Core。Avernet 可以在 Runner Adapter 层接入 A2A，但 Context 不需要成为 Agent Registry。

Avernet 比 A2A 多：

- `ExecutionBinding` 的 Work/Attempt 细分；
- `ContextSpec` 的 required/optional 分层；
- `ContextView` 的完整性、预算、来源和省略解释；
- `Validation` 独立对象；
- `KnowledgeExtraction`。

原因是 A2A 不知道对端 Agent 内部如何规划第 20 个 Work；它把 Agent 当黑盒，重点是跨边界传输。

## 6.6 A2A 对 Avernet 的可靠性依据

```text
1. contextId 与 taskId 分开，证明会话范围和任务身份不能合并。
2. Message 与 Artifact 分开，证明交互过程和正式结果不能混为一个 output。
3. historyLength 支持按需读过程，证明历史不应默认全量注入。
4. referenceTaskIds 提供显式相关任务引用，但仍不替代内部硬依赖协议。
5. Part 让多模态内容有统一承载，而不是要求 Context Core 预先理解每种原生格式。
```

---

# 7. AutoGen Magentic-One：动态工作有了，但协议强度不足

## 7.1 原生领域对象/承载

Magentic-One 是 AutoGen 中的多 Agent Orchestrator，而不是一个独立的通用网络协议。其核心承载是：

```text
User Task
Team / Group Chat
Orchestrator
Task Ledger
Progress Ledger
Agent Message
当前 speaker 和 instruction
```

## 7.2 原生协议维度

| 协议族 | 原生承载 | 核心信息 |
|---|---|---|
| 任务入口 | `MagenticOneGroupChat` 初始化/用户消息 | task message、参与 Agent |
| 协作消息 | Group Chat Message | sender、recipient/speaker、content、history |
| 任务状态 | Task Ledger | 目标、事实、约束、计划状态 |
| 进度判断 | Progress Ledger | 是否完成、是否有进展、是否循环、下一位 speaker、instruction |
| 执行输出 | Agent Message | 文本、工具结果、状态信息 |
| 继续执行 | Orchestrator 选择下一 speaker | speaker、instruction |

## 7.3 与 Avernet 逐项对齐

| Avernet 协议族 | Magentic-One 原生最接近的承载 | 结论 | 差异原因 |
|---|---|---|---|
| A1 `ExecutionBinding` | Team/Group Chat/Agent Message | **部分具备** | 有协作范围，但没有统一 Work/Execution/Attempt 的耐久身份协议 |
| A2 `SourceCapture` | Group Chat history、Ledger | **部分具备** | 保存协作过程，但不是跨 Bot/群/BBS Source Adapter |
| A3 `ContextSpec` | Orchestrator 生成 instruction | **部分具备** | 动态生成当前工作，但通常是消息/结构化 Ledger，不是 required_inputs Schema |
| A4 `ContextView` | Ledger + history + instruction | **部分具备** | 有上下文组装，但未形成硬依赖完整性、预算和省略协议 |
| A5 `ExecutionOutcome` | Agent Message | **部分具备** | 输出回群聊；没有统一 Candidate/Artifact 终态对象 |
| A6 `Validation` | Orchestrator completion/progress 判断 | **部分具备** | 有完成判断，但非独立 Verifier 协议 |
| A7 `KnowledgeExtraction` | 未发现 Magentic-One 核心协议 | **未发现** | 长期知识不属于其核心 Loop 契约 |

## 7.4 它比 Avernet 多/少什么，为什么

它没有比 Avernet 多出一套更强的执行协议；它在“下一步工作决定”上更依赖 Orchestrator + LLM。它比 Avernet 少的是：

```text
稳定 Execution/Attempt 身份；
硬依赖集合；
正式 Outcome/Artifact；
独立 Validation；
来源读取和知识抽取协议。
```

原因不是 Magentic-One 设计错误，而是它主要展示一个动态协作编排模式，可靠执行账本和知识治理不在该模块的协议边界内。

它支持 Avernet 的关键判断：动态 Loop 不能只依赖预定义 Workflow；但它不能单独证明“把 Ledger + 群聊给模型”足以保障硬依赖完整。

---

# 8. LoopX：控制面协议比 Context 更完整，但领域模型更窄

## 8.1 LoopX 原生领域对象

LoopX README 和状态交互模型明确把自己定位为长周期 Agent 的外置、持久控制面：保留 objective、scope、gates、todos、evidence、quota、handoffs 和 run history；Agent Harness 执行 bounded work。

| LoopX 对象 | 作用 |
|---|---|
| Goal | 长期目标和范围 |
| Todo | 有序工作项、所有者、claims/leases |
| Gate | 需要人或系统判断的门 |
| Evidence | 结果依据、验证和阻塞信息 |
| Run | 一次 bounded turn 或周期执行 |
| Handoff | 给下一轮/下一 Harness 的交接 |
| Quota | 继续执行的预算和限制 |
| Validator | 独立验证路径 |
| Kernel State | 持久控制面事实 |

## 8.2 LoopX 原生协议承载

LoopX 的协议主要通过 CLI、状态文件/Kernel API 和能力（Capability）入口承载，不是像 A2A 一样的跨厂商 JSON-RPC 标准。根据公开 README/文档，可核对的核心操作包括：

| 协议族 | 公开承载/命令 | 核心信息 |
|---|---|---|
| Goal 创建/连接 | `start-goal`、`connect` | project、goal text、scope |
| 状态读取 | `status`、`diagnose`、`review-packet` | active goal、todos、gates、evidence、run history、attention |
| 是否继续 | `quota should-run` | quota、scheduler hint、stop condition |
| 能力执行 | `capability show ... --format json` | provider、entry commands、write boundary、protocols、validation |
| 运行回写 | Kernel durable writeback | evidence、blocker、handoff、accepted state |
| 验证 | independent validation/canary/receipts | accepted writeback、validation evidence |

这里不能把 LoopX 的 CLI 输出凭空写成一个未公开的 HTTP Schema；对比只使用其公开的控制面对象和操作语义。

## 8.3 与 Avernet 逐项对齐

| Avernet 协议族 | LoopX 原生最接近的承载 | 结论 | 差异原因 |
|---|---|---|---|
| A1 `ExecutionBinding` | Goal + Run + Todo + claims/leases | **部分具备** | 长期 Run 有归属，但不是 Scope/Work/Execution/Attempt 的通用绑定协议 |
| A2 `SourceCapture` | Run history、Evidence、workspace state | **部分具备** | 记录控制面证据，不是读取 Bot/群/BBS 原始记录的来源协议 |
| A3 `ContextSpec` | review-packet、bounded work slice、Goal State | **部分具备** | 有有限工作包，但公开模型不等同于 required_inputs/optional_context 字段 |
| A4 `ContextView` | project(active_goal_state、event_ledger、run_history、todos、gates、quota...) 得到 observation packet | **原生具备理念/部分具备接口** | 有观察包和 compact state，但非 Avernet 的多来源、预算、omitted Schema |
| A5 `ExecutionOutcome` | evidence、handoff、run payload、accepted writeback | **原生具备** | 结果以持久控制面事实回写 |
| A6 `Validation` | independent validation、receipts、quality gates | **原生具备** | 执行和验收明确分离 |
| A7 `KnowledgeExtraction` | evidence 可复用、探索/研究能力 | **部分具备** | 有证据沉淀，但不是 ECB 式跨 Task 知识抽取/发布协议 |

## 8.4 LoopX 比 Avernet 多/少什么，为什么

LoopX 比 Avernet 多的主要不是一个“缺失的 Context 字段”，而是更完整的控制面治理对象：

```text
claims/leases、quota、authority、human gates、scheduler hint、capability/provider 边界。
```

这些字段用于控制“能不能继续、谁拥有当前工作、是否需要人判断”，Avernet 如果只讨论 Context，不应吞入；但 Task Runtime 如果要承担完整控制面，需要另行评估。

Avernet 比 LoopX 多：

```text
多模态 SourceAccess；
Task/Session/协作空间通用 Scope；
required_inputs 与 optional_context 的硬边界；
ArtifactRef、Candidate、Validation 的机器可校验接口；
ECB 知识提炼和发布协议。
```

LoopX 对 Avernet 最强的证明是：长任务下一轮消费的应是有限、可审计的 continuation/observation packet，而不是无限历史。

---

# 9. 协议数量、领域对象和字段数量的归因

## 9.1 协议族数量对比

| 产品 | 本文核对出的核心协议族 | 比 Avernet 多/少的原因 |
|---|---:|---|
| Avernet | 7 | 同时承担动态 Work 上下文、多来源读取、结果/验收、ECB 入口 |
| AWS Step Functions | 7 | 多了 State Machine/State Transition；缺少多模态 SourceAccess、ECB；Validation 是编排实现 |
| Temporal | 7 | 多了 Workflow Task/Replay 和 Retry Policy；缺少多模态 SourceAccess、Context View、ECB |
| A2A | 7 | 多了 AgentCard/发现、流式/推送/取消；内部硬依赖和知识治理不在边界内 |
| Magentic-One | 6 个主要承载面 | 动态 instruction/ledger 强，但执行绑定、结果、验收较弱 |
| LoopX | 6 个主要控制面承载 | Governance/Quota/Goal 强，但没有多模态 SourceAccess 和通用 ContextSpec |

**注意：**这是协议族统计，不是产品 API 数量，也不是声称这些产品官方就把协议分成这些名字。每个数量都能回到前文原生对象/API 表。

## 9.2 Avernet 为什么和 AWS 都是 7 个，却不是同一套协议

| Avernet | AWS | 是否一一对应 | 关键差异 |
|---|---|---:|---|
| `ExecutionBinding` | Execution + State + Task Token | 否 | AWS 绑定固定 State；Avernet 绑定运行时 Work/Attempt |
| `SourceCapture` | State Input + History | 否 | AWS 数据集中在 Workflow；Avernet 需要外部多源读取 |
| `ContextSpec` | InputPath + Parameters | 部分 | AWS 设计期；Avernet 运行期 |
| `ContextView` | Task JSON Input | 部分 | Avernet 返回完整性、来源、省略和预算结果 |
| `ExecutionOutcome` | Task Result/Callback | 部分 | AWS 输出通常直接进入 State；Avernet 先形成 Candidate |
| `Validation` | Choice/后续 State | 否 | AWS 提供控制流，Avernet提供统一验收对象 |
| `KnowledgeExtraction` | 无核心等价物 | 否 | Avernet承担跨 Task 知识闭环 |

因此“协议数量相同”不代表设计相同；需要继续比较字段和责任边界。

## 9.3 同一职责的核心字段对比

### 9.3.1 执行绑定/启动

| 语义 | AWS `StartExecution` | Temporal Workflow Start | A2A Task/Message | LoopX Goal/Run | Avernet |
|---|---|---|---|---|---|
| 上层定义 | `stateMachineArn` | Workflow Type/Namespace | Agent/Card/Server | project/Goal | `scope_ref` |
| 本次运行身份 | `executionArn`（响应返回） | Workflow ID + Run ID | Task `id` | Run/Kernel state | `execution_id` |
| 业务输入 | `input` | Workflow arguments | Message `parts` | goal text/todo | `assignment`/SourceRefs |
| 具体工作 | 固定 State | Activity/Workflow code | Task 内部黑盒 | Todo/Capability | `work_ref` |
| 尝试身份 | Retry/Catch 语义，非单独启动字段 | Activity Attempt/Retry Policy | 未定义内部 Attempt | Run/cycle | `attempt_ref` |
| 实际载体 | Task Token/Resource | Activity Task/Task Queue | Agent endpoint | Harness/session | `runtime_ref` |
| 幂等 | `name + input`（Standard Execution） | Workflow ID reuse/conflict policy | message/task IDs | Kernel state/claims | Idempotency-Key |

**关键结论：**Avernet 的 `execution_id + attempt_ref + runtime_ref` 不是重复字段。AWS/Temporal 可以把其中一部分由 Runtime 内部管理；Avernet 的 Context 服务需要跨 Task Runtime、Bot Session、群、BBS 取数，必须能接到这些身份。

### 9.3.2 当前执行输入

| 语义 | AWS | Temporal | A2A | LoopX | Avernet |
|---|---|---|---|---|---|
| 当前目的 | State Type/State name | Activity invocation | Message intent | Goal/Todo/Capability | `purpose`/`variant` |
| 硬依赖 | `Parameters` 路径 | Activity arguments | `referenceTaskIds`（提示，不是硬依赖） | Todo/Evidence 状态 | `required_inputs` |
| 可选历史 | State 数据/Context Object | Workflow code 自行选择 | `historyLength` | observation packet | `optional_context` |
| 知识查询 | 无内置 | 无内置 | 无内置 | capability/研究路径 | `knowledge_query` |
| 预算 | 256 KiB State/Payload 约束 | History/应用约束 | history/artifact 请求控制 | quota | `budget` |
| 版本时间点 | State machine version/Execution | Run/History | Task status/history | Kernel state | `as_of` |
| 来源证明 | State path/History | Event History | Message/Artifact | Evidence/receipts | `provenance` |
| 缺失反馈 | Task failure/Choice | Activity failure | input-required state | gate/blocker | `completeness.missing` |
| 省略原因 | 不统一返回 | 不统一返回 | history 可限制但不要求解释 | observation 投影 | `omitted[].reason` |

这张表显示 Avernet 的字段更多不是因为“想得更完整”，而是因为它同时要解决：

```text
动态硬依赖 + 多来源读取 + 可选语义检索 + Token 限制 + 可解释省略。
```

如果 Avernet 不实现其中某个行为，对应字段应删除，而不是保留在协议里装饰。

### 9.3.3 结果和验收

| 语义 | AWS | Temporal | A2A | LoopX | Avernet |
|---|---|---|---|---|---|
| 结果身份 | Task Token/Execution event | Activity completion event | Artifact ID/Task ID | Run/Evidence | `outcome_id`/`candidate_id` |
| 成功结果 | `output`/ResultPath | Activity Result | Artifact | accepted writeback | `result` |
| 失败 | SendTaskFailure/Error/Cause | Failure/Timeout | failed/rejected status | blocker/validation | `error` |
| 部分结果 | 应用自定义 State | 应用自定义 result | Artifact/status 自定义 | Evidence | `partial_result` |
| 大结果 | S3 ARN/外部引用 | 应用实现 | Artifact Part/File | evidence/run payload | `artifact_refs` |
| 证据 | 日志/状态数据 | History/应用实现 | Message/Artifact metadata | Evidence | `evidence_refs` |
| 独立验收 | 后续 State/应用实现 | Workflow/Activity 应用实现 | 未规定 | independent validator | `Validation` |

Avernet 将 Candidate 和 Validation 分开，不是把两个业界字段简单拆开，而是因为动态 Agent 的执行者和验收者可能是不同 Agent/不同服务，且验收结果必须成为下一次 Context 的事实来源。

---

# 10. 哪些 Avernet 设计被业界支持，哪些仍是新增假设

## 10.1 已被多个产品共同支持

| Avernet 设计 | 业界依据 | 结论强度 |
|---|---|---|
| 正式结果与原始过程分离 | AWS ResultSelector/State、Temporal Result/History、A2A Artifact/History、LoopX Evidence/Run | 强 |
| 当前输入不是完整历史 | AWS InputPath/Parameters、Temporal Activity args、LoopX observation packet | 强 |
| 大结果用引用 | AWS S3 recommendation、A2A Artifact/File、LoopX evidence/payload | 强 |
| 执行身份不应依赖自然语言 | AWS Task Token、Temporal Run/Activity Task、A2A Task ID | 强 |
| 长任务需要有限 continuation state | Temporal Continue-As-New、LoopX bounded/compact observation | 强 |
| 会话范围与任务身份可以分开 | A2A `contextId` vs `taskId` | 强 |

## 10.2 Avernet 为动态多模态 Task Loop 增加的设计

| Avernet 设计 | 业界直接对应 | 判断 |
|---|---|---|
| `required_inputs` 与 `optional_context` 强制分层 | Workflow 的显式参数 + Agent Loop 的动态观察包分别支持 | 合理组合，但需要用验收用例证明 |
| `required_complete` | AWS/Temporal 多由定义/代码保证，不作为返回对象；A2A 未定义 | Avernet 新增的机器可验证反馈 |
| `SourceCapture` 多模态 Adapter | 各产品通常只管理自己的 State/Message/History | 由 Avernet 数据分散场景直接要求 |
| `omitted[].reason` | 业界常有截断/限制，但不统一解释原因 | 可解释性增强，需控制字段稳定性 |
| ECB `KnowledgeExtraction` | 各产品可接外部 Memory/Store，但核心协议未统一定义 | Avernet 现有知识引擎决定其必要性 |
| Candidate/Validation | LoopX 有强依据；其他产品多由业务编排 | 对动态 Agent Loop 合理，但不是所有场景都必须强制独立验证 |

## 10.3 可能应该删掉或下沉的字段

协议可靠不等于字段越多越好。以下字段不应放进 Context Core：

```text
1. RetryPolicy 的退避参数：归 Task Runtime；Context 只读取失败事实。
2. Workflow Graph 的 node_id：Context 只接受 WorkRef，不绑定 Graph。
3. AgentCard/能力发现：归 Agent 互操作层。
4. Session 原始所有字段：归 Source Adapter，不污染 Context Core。
5. Knowledge 的抽取算法参数：归 ECB，Context 只提交 Case refs/intents。
6. Task 状态变更命令：归 Task Runtime，Context 只返回 View/事实。
```

---

# 11. 对协议可靠性的可验证要求

## 11.1 领域对象验收

```text
1. 同一 RuntimeSession 可绑定多个 Execution，但每个 Execution 唯一。
2. 同一 Work 可创建多个 Attempt；旧 Attempt 的 Outcome 和 Validation 不被覆盖。
3. Candidate 未被 Validation accepted 前，不得作为 accepted_result 提供给下游。
4. SourceRecord 可以被多个 Execution 引用，但不因此复制成多个事实副本。
5. ArtifactRef 永远可通过权限检查和版本定位到外部内容。
6. Knowledge 只有 ECB 发布版本才能进入 optional_context.knowledge。
```

## 11.2 ContextView 验收

```text
1. required_inputs 缺一项，HTTP 200 也必须返回 required_complete=false；不能伪装成完整成功。
2. required_inputs 不受 semantic TopK 和 token budget 淘汰。
3. optional_context 可以因预算被省略，但必须返回 ref + reason。
4. View 必须带 as_of/provenance，不能用最新状态重新拼出一个不同 View 冒充历史 View。
5. 大 Artifact 在 inline=false 时仍必须保留 artifact_ref。
```

## 11.3 回投和幂等验收

```text
1. 同一 Idempotency-Key 重投不产生两个 Outcome。
2. 旧 Attempt 失败后新 Attempt 成功，两个事实都可查询。
3. 同一 Execution 收到两个互相冲突的终态，不静默覆盖；返回 conflict。
4. Validation 必须指向明确 outcome/candidate，不读取 Session 最后一条消息猜结果。
5. source_cursor 乱序或重复读取不能造成 SourceRecord 重复事实。
```

---

# 12. 最终结论

## 12.1 Avernet 的协议不是“比业界多所以更完整”

更准确的结论是：

```text
Workflow 产品把很多职责放在 State Machine、Workflow 代码和 Runtime 内部；
Agent 协议把很多职责放在 Message、Task、Artifact 的边界层；
长周期控制面把很多职责放在 Goal、Todo、Evidence、Quota 和 Kernel。

Avernet 的场景同时具备：
- 动态生成 Work；
- 多来源、多模态原始过程；
- 受控执行和重试；
- 结构化结果与独立验收；
- ECB 跨任务知识闭环。
```

因此 Avernet 必须显式化业界产品经常隐藏在别处的能力：

```text
ExecutionBinding：因为 Bot 可能不知道 Task；
SourceCapture：因为事实不只存在于 Task State；
ContextSpec/View：因为 Work 运行时生成且输入不能靠全历史猜；
ExecutionOutcome/Validation：因为 Candidate 不能自动成为已验收结果；
KnowledgeExtraction：因为当前系统有跨 Task ECB 闭环。
```

## 12.2 需要保留的最小协议族

经过对比，第一版可以保留 7 个协议族，但职责边界如下：

```text
A1 ExecutionBinding       必须：Context 无法从 Session 猜 Task/Work/Attempt。
A2 SourceCapture          必须：已有多模态权威来源需要统一可读入口。
A3 ContextSpec            必须：动态 Work 必须声明硬依赖和可选背景。
A4 ContextView            必须：实际交付、完整性、预算和省略需要可验证。
A5 ExecutionOutcome       必须：正式成功/失败不能从原始 Session 猜。
A6 Validation             新增：需说明与现有 worker 能力审计的区别（见 §12.3.4）。
A7 KnowledgeExtraction    必须：已有 ECB 要接收完整 Case 并形成跨 Task 知识。
```

需要澄清：当前仓库里的 `verify_judge`/`CapabilityVerifyService` 是 **worker 能力审计**（判定机器人是否真正具备其声明的能力、映射 `TrustLevel`），由 `WorkerProfileCreatedEvent` 触发，与 A6 要验收"某个 task 执行的 Candidate 是否可采纳"是两条生命周期。因此 A6 不是复用现有 verify，而是新增一类"对执行结果"的验收协议；若第一阶段不做独立验收方，A6 可由 Task Runtime 内部承担，但不得把 Candidate 直接命名为 accepted result。

## 12.3 已对照 Avernet 当前代码核实的实现结论

以下不再是"待确认"的假设，而是对照本仓库 `Avernet/src/bcsfuse` 源码逐项核实的结论，给出文件行号，可作为落库依据。

### 12.3.1 A1 Execution/Attempt 身份：当前不存在 → A1 需整体新建

- 在 `domain/models/` 与 `application/services/execution_packet_service.py` 中对 `attempt`/`retry_policy`/`execution_id`/`work_ref` 的命中数为 **0**。
- 当前没有"某次 Work 的某次执行、某次尝试"这一层稳定身份。`ExecutionPacket`（`execution_packet.py:76`）是给整个 Team 的编译产物，不含 per-execution/per-attempt 身份。
- **结论**：A1 不是在已有对象上加字段，而是需要新落库 Execution/Attempt 身份表；否则 Context 无法从 Session 反推归属、也无法区分重试。

### 12.3.2 A3/A4 required/optional 与预算：当前只有"全量/裁剪开关 + 条数上限" → 协议是增量

- 当前上下文选择由两处控制：`compiler_input.py:28 include_full_context`（布尔，True=全量、False=裁剪）、`compiler_input.py:40 max_context_items`（条数上限，None=不限）。
- 实际拼装在 `baseline_execution_packet_compiler.py:116 _compile_context_pack`：取 `CandidateBundle.knowledge_items` → 按 `id` 去重 → `_generate_context_summary`（:415）生成摘要 → 收集 `citations`。没有 token 预算、没有 required/optional 分层、没有相关度排序、没有 `omitted[].reason`、没有 `provenance/as_of` 返回。
- 已有 `OutputContract`（`execution_packet.py:64`，含 `required_artifacts`/`required_sections`/`must_include_validation`/`format_hints`）可对应 A3 的 `output_contract`，这部分**已具备**，是可复用基础。
- **结论**：A3 的 `required_inputs` 与 `budget`、A4 的 `completeness/omitted/as_of` 均为净新增。这正是对话中反复指出的"`_build_context` 只有结构邻接、没有预算和语义相关度"的代码级证据。

### 12.3.3 A5 结果回流：当前管线单向、无"结果声明"对象 → A5 需新建

- 当前管线单向：Task Understanding → Planning → Team Composition → Retrieval(CandidateBundle) → Compile(ExecutionPacket) → `launch_prompt`。域模型里**没有**"执行回来的结果是否被采纳"的对象（无结果语义的 Outcome/Candidate、无 accepted_result；§12.4 列出的现存 `*Result` 对象全是各算子中间产物）。
- **结论**：A5（把成功/失败/部分结果结构化回投、区分 accepted result vs candidate vs partial result）是净新增。今天没有适配层可做这件事，需新建结果回流协议与存储。

### 12.3.4 A6 Validation：现有 verify 是 worker 能力审计，不是 task 结果验收 → A6 为新增、不可误用现有 verify

- `capability_verify_service.py:48 CapabilityVerifyService` 由 `on_worker_profile_created`（:131）触发，目的是判定 Bot 是否真正具备其声明能力、映射 `TrustLevel`（SANDBOX_ONLY/GUARDED/TRUSTED，:611 `_map_trust_level`）。
- `verify_judge.py:27 _JudgeOutput` 输出 `dimension/confidence/reasoning`，是"能力维度"评判，不是"本次执行结果是否可采纳"的验收。
- **结论**：A6 验收的是"某次 Execution 的 Candidate 是否被接受为可用结果"，与 worker 信任等级审计是两条生命周期。不能把 `CapabilityVerifyService` 当作 A6 的现成实现；A6 是新协议，承担者与触发时机都不同。

### 12.3.5 仍需确认但已有线索的两点

- **A2 来源存储**：既有调研指出 Session/群/BBS 已有权威存储并走过采集；稳定 `cursor`/`record_id` 增量与幂等是否满足，需在对应来源 Adapter 处确认（本地核对范围外）。
- **A7 ECB 接口**：知识系统位于 `ocb/src/ecb`（独立模块），其抽取/发布接口能否直接接受 `source/execution/validation refs` 需读 ECB 接口确认；若不能，A7 先做引用协议、抽取仍由 ECB 内部完成。Context/Task Runtime 只提交 Case 边界与 `intents`、不承担抽取算法——这一职责划分已在 §1.9 固定。

> 修正记录：相比上一稿把 1/3 表述为"没有则需落库""不能则需适配层"的疑问句，现依据源码改为已核实的实现结论。最终实现风险收敛到 A2 的来源 Adapter 增量保证与 A7 的 ECB 接口对齐。

---

## 12.4 佐证：当前仓库实际存在的"结果类"对象清单

为支撑 §12.3.3（当前无"执行结果采纳"对象），列出 `domain/models/` 中真实存在的 `*Result`/`*Report` 类对象。可见它们都属于理解/规划/组队/检索/编译/融合/画像/能力审计各算子，不属于"某次 Task 执行的结果回流与采纳"：

```text
TaskUnderstandingResult（task_understanding_result.py:58）
PlanningResult（planning_result.py:123）
CompilerResult（compiler_result.py:72）
CompositionResult（composition_result.py:72）
RetrievalResult / HybridRetrievalResult（retrieval_result.py / hybrid_retrieval_result.py）
FusionResult（fusion_result.py:135）
EvaluationResult（evaluation_result.py:72）
WorkspaceAssemblyResult（workspace_assembly_result.py:94）
VerifyData/DimensionResult/PeerReviewResult（verify_dto.py）
WorkerProfileExtractionResult / ProfileMatchResult（worker_profile.py）
AttributionReport（attribution_report.py）
```

这进一步说明：今天的"结果"是各算子中间产物，缺一个贯穿"执行→声称→验收→采纳"的终态对象，正是 A5/A6 要补的层。

---

# 参考资料

## AWS Step Functions

- `StartExecution` API：https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartExecution.html
- `SendTaskSuccess` API：https://docs.aws.amazon.com/step-functions/latest/apireference/API_SendTaskSuccess.html
- InputPath/Parameters/ResultSelector：https://docs.aws.amazon.com/step-functions/latest/dg/input-output-inputpath-params.html
- 输入输出处理顺序：https://docs.aws.amazon.com/step-functions/latest/dg/concepts-input-output-filtering.html
- ResultPath：https://docs.aws.amazon.com/step-functions/latest/dg/input-output-resultpath.html
- `DescribeExecution` API：https://docs.aws.amazon.com/step-functions/latest/apireference/API_DescribeExecution.html

## Temporal

- Temporal 开源仓库：https://github.com/temporalio/temporal
- Temporal 官方文档：https://docs.temporal.io/
- Event History 事件参考：https://github.com/temporalio/documentation/blob/main/docs/references/events.mdx
- Continue-As-New：https://docs.temporal.io/workflow-execution/continue-as-new

## A2A

- A2A 规范：https://a2a-protocol.org/latest/specification/
- A2A 开源仓库：https://github.com/a2aproject/A2A
- Task 生命周期：https://a2aproject.github.io/A2A/latest/topics/life-of-a-task/

## AutoGen Magentic-One

- AutoGen 开源仓库：https://github.com/microsoft/autogen
- Magentic-One 用户文档：https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html
- Magentic-One 实现目录：https://github.com/microsoft/autogen/tree/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_magentic_one

## LoopX

- LoopX 开源仓库：https://github.com/huangruiteng/loopx
- State Interaction Model：https://github.com/huangruiteng/loopx/blob/main/docs/state-interaction-model.md
- Goal Control Plane Architecture：https://github.com/huangruiteng/loopx/blob/main/docs/development/control-plane-course/02-goal-control-plane-architecture.md

