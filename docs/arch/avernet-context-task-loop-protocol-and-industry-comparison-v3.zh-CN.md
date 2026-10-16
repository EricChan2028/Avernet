# Avernet Context：Task Loop 协议与业界对标 V3

> 日期：2026-09-10  
> 状态：协议讨论稿  
> 范围：先定义 Avernet Task Loop Profile 的生产、读取和消费协议，再用业界任务系统逐项验证。本文不要求 Bot 理解 Task/DAG，也不复制已经存在的 Bot Session、协作群或 BBS 原始数据。

---

## 0. 结论

Avernet Context 第一版不需要一个包打天下的 `ContextEvent`，也不需要把所有原始会话再次上报一遍。稳定方案是四组接口：

```text
P1 RuntimeOutcomeReport
   Runtime Adapter 报告一次受管执行的正式成功/失败结果。
   报告只携带不透明 execution_ref 和实际 runtime_ref，不要求 Bot 填 task_id。

U1 TaskExecutionPort
   Context 从 Task 的权威存储读取 Task、工作单元、Attempt、依赖和 execution_ref 映射。

U2 SourceAccessPort / ArtifactPort / KnowledgePort
   Context 按引用读取已有 Session/群/BBS 原文、大产物和 ECB 已发布知识。

C1 ContextView
   Task 按明确消费位点请求强类型 View；Context 负责准确取范围、保护必需项、
   选择可选项、控制表示大小，并说明缺失和省略。
```

核心数据流：

```text
已有原始存储 ──SourceAccessPort──────────────┐
Task SSOT ─────TaskExecutionPort─────────────┤
Runtime 回投 ──RuntimeOutcomeReport──▶ Context ├─▶ ContextView ─▶ Planner / Runner / Acceptance
Artifact Store ─ArtifactPort─────────────────┤
ECB ───────────KnowledgePort─────────────────┘

Context ──KnowledgeExtractionBundle────────────▶ ECB
ECB ─────Published Knowledge───────────────────▶ Context
```

三个关键判断：

1. **原始过程不是正式任务结果。** Session 可以完整保存，但 Task 主链路默认消费 `Result Capsule`，重试、审计和知识抽取才回读过程。
2. **Context 不复制 Task 结构。** Task 继续是目标、工作单元、Attempt、依赖和状态的权威来源；Context 通过读取接口解析位置。
3. **Context 的价值不是转发。** 它用少量 View 接口隐藏跨来源解析、必需项完整性、相关内容选择、Token 预算、引用降级和可解释性。删除 Context，这些逻辑会重新散落到 Planner、Runner 和验收代码中。

---

# 第一部分：协议

## 1. 协议适用范围与对象

### 1.1 Core 与 Task Loop Profile 分层

本文不是为所有未来 Context 场景定义唯一业务协议，而是分两层：

```text
Context Core
├── RuntimeRef / SourceRef / ArtifactRef / KnowledgeRef
├── RuntimeOutcomeReport
├── SourceAccessPort / ArtifactPort / KnowledgePort
└── ContextView 公共响应外壳

Avernet Task Loop Profile
├── TaskExecutionPort
├── task.plan.initial
├── task.plan.replan
├── task.execute.prepare
├── task.execute.retry
├── task.accept.read
└── task.inspect.execution
```

未来如果接入“纯会话助手”或“长期协作空间”，可以新增自己的 View Profile，不需要把 `task_id/unit_id` 强塞进 Context Core。

### 1.2 四类对象不要混用

| 对象 | 回答的问题 | 权威来源 |
|---|---|---|
| `Source Record` | Bot、群或 BBS 当时具体发生了什么 | 现有 Bot/群/BBS 存储 |
| `Task Execution` | 哪个 Task 工作单元的哪次尝试，派发到哪次实际执行 | Task SSOT |
| `Runtime Outcome` | 这次实际执行正式成功/失败，交付了什么 | Runtime Adapter 回投，Context 追加保存 |
| `Knowledge` | 哪些内容经过抽取、融合和治理，可跨 Task 复用 | ECB Knowledge System |

因此本文**取消**旧稿中的 `POST /context/source-records`：原始数据已经存在，Context 只保存 `source_ref` 并按需读取。

### 1.3 多模态如何扩展而不修改核心协议

`runtime_ref` 和 `source_ref` 都只使用稳定二元组：

```json
{
  "type": "bot_session",
  "id": "session_9"
}
```

- `type` 选择读取 Adapter，不要求 Context Core 预先理解全部模态；
- `id` 是对应来源系统中的稳定范围 ID；
- 新增邮件、代码评审等来源时，新增 `type + Adapter`，不修改 `RuntimeOutcomeReport` 和既有 Task View；
- 只有新来源出现了 Task Loop 从未有过的**消费意图**，才新增 View/Profile，而不是因为 Payload 形态不同就修改 Core。

Source Adapter 向 Context 返回统一的可读投影，而不是统一原生 Payload：

```json
{
  "record_id": "message_109",
  "occurred_at": "2026-09-10T10:38:00+08:00",
  "actor_ref": "bot_risk_analyzer",
  "parts": [
    {"media_type": "text/plain", "text": "缺少最近三个月数据"},
    {"media_type": "application/json", "artifact_id": "artifact_41"}
  ],
  "relations": [
    {"type": "reply_to", "record_id": "message_101"}
  ]
}
```

Bot 聊天、协作群和 BBS 的原生字段可以完全不同；Adapter 只投影 Context 选择与引用需要的最小语义：稳定记录身份、时间、参与者、内容 Part、Artifact 和回复/引用关系。

---

## 2. P1：RuntimeOutcomeReport 生产协议

### 2.1 为什么仍然需要正式结果上报

Bot Session 中往往同时包含：

```text
用户输入、模型思考、工具调用、中间失败、临时猜测、最终回答、大文件链接。
```

Task 不能靠“读取 Session 最后一条消息”猜测正式结果，因为：

- 最后一条消息未必是结构化交付；
- 群和 BBS 不一定存在唯一“最后回答”；
- Task 需要稳定区分成功结果、失败原因、结构化数据和大产物；
- 下游节点需要一个可验证的结果契约，而不是重新解释整段过程。

所以 Runtime Adapter 需要将 Runtime 原生回调投影成一个小而明确的 `RuntimeOutcomeReport`。这是**正式结果投影**，不是复制原始轨迹。

### 2.2 接口

```http
POST /v1/context/runtime-outcomes
```

成功和失败共用同一个小外壳：

```json
{
  "report_id": "report_201",
  "execution_ref": "exec_88",
  "runtime_ref": {
    "type": "bot_session",
    "id": "session_9"
  },
  "status": "succeeded",
  "finished_at": "2026-09-10T10:40:00+08:00",
  "result": {}
}
```

### 2.3 核心字段：谁填写、为什么必须有

| 字段 | 必填 | 谁填写 | 含义与必要性 |
|---|---:|---|---|
| `report_id` | 是 | Runtime Adapter | **终态结果的稳定幂等身份，不是某次 HTTP 投递 ID。**优先使用 Runtime 的终态事件 ID；若 Callback 与 Poller 没有共享 ID，Adapter 必须先归一结果，再由 `execution_ref + 终态类型 + 归一结果摘要` 稳定生成，使同一终态从不同通道观察时仍能归并。 |
| `execution_ref` | 是 | Task Runtime 在创建本次 Attempt 的待执行记录时生成，Adapter 原样带回 | 一次受管执行的不透明身份。它在准备 Context View **之前**已经存在，Bot 不需要知道其背后的 Task、工作单元或 Attempt。它解决“同一 Session 可能被复用，仅凭 session_id 无法安全归属”的问题。 |
| `runtime_ref.type` | 是 | Runtime Adapter | 实际执行载体类型，如 `bot_session`、`group_thread`、`bbs_work`。Context Core 不解释其业务 Payload，只用它路由 Source Adapter。 |
| `runtime_ref.id` | 是 | Runtime Adapter | 实际 Session/讨论范围/工作实例的稳定 ID，用于按需回读原始过程。 |
| `status` | 是 | Runtime Adapter | 只允许 `succeeded` 或 `failed`，明确这是终态报告，不与过程消息混用。 |
| `finished_at` | 是 | Runtime Adapter | Runtime 终态发生时间，用于乱序判断和审计；不是 Context 接收时间。 |
| `result` | 成功必填 | Runtime Adapter | 成功时的正式 `Result Capsule`。 |
| `error` | 失败必填 | Runtime Adapter | 失败的可操作说明。 |
| `partial_result` | 否 | Runtime Adapter | 失败前已形成且明确允许复用的部分结果；没有明确语义时不要填写。 |

协议刻意不要求 Runtime/Bot 填：

```text
task_id、unit_id、attempt、完整 DAG、event_type、spec_version、causation_id。
```

原因不是这些概念无用，而是它们应由知道它们的 Task Runtime 管理。让不知道 Task 的 Bot 填 Task 字段，只会制造伪归属。

### 2.4 成功：Result Capsule

```json
{
  "report_id": "report_201",
  "execution_ref": "exec_88",
  "runtime_ref": {
    "type": "bot_session",
    "id": "session_9"
  },
  "status": "succeeded",
  "finished_at": "2026-09-10T10:40:00+08:00",
  "result": {
    "digest": "完成支付链路分析，识别出三个高风险点。",
    "data": {
      "risk_count": 3,
      "highest_level": "P0"
    },
    "artifact_refs": [
      {
        "artifact_id": "artifact_42",
        "name": "risk-report.md",
        "media_type": "text/markdown"
      }
    ],
    "evidence_refs": [
      {
        "source_ref": {
          "type": "bot_session",
          "id": "session_9"
        },
        "record_ids": ["message_101", "message_109"]
      }
    ]
  }
}
```

`Result Capsule` 由真正理解本次交付语义的一侧产生：优先由执行 Agent/工具按任务输出契约返回；Runtime Adapter 负责校验、字段映射和格式归一，不应凭最后一条消息自由生成业务摘要。若某种 Runtime 只能返回裸文本，第一阶段可将该文本作为 `digest`，但不能伪造 `data`、Evidence 或验收结论。

`Result Capsule` 的字段语义：

| 字段 | 是否必填 | 下游如何消费 |
|---|---:|---|
| `digest` | 是 | Planner/Runner 快速理解本次正式完成了什么；它是执行主体给出的正式结果摘要，不是 Context 临时生成的压缩摘要。 |
| `data` | 否 | 下游必须精确读取的结构化值，例如数量、ID、决策枚举。不能把必须精确传递的数据埋在自然语言摘要里。 |
| `artifact_refs` | 否 | 正式大产物。默认只传引用、元数据和必要片段，不把全文反复塞入 Task 状态或 Prompt。 |
| `evidence_refs` | 否 | 指向支持该结果的原始消息、帖子或记录；验收、重试和审计需要时再回读。 |

`digest` 不能替代 `data`、Artifact 或 Evidence。Task 主链路可以只内联 `digest + data + Artifact 元数据`，但必须保留回到原始证据的路径。

### 2.5 失败

```json
{
  "report_id": "report_202",
  "execution_ref": "exec_88",
  "runtime_ref": {
    "type": "bot_session",
    "id": "session_9"
  },
  "status": "failed",
  "finished_at": "2026-09-10T10:40:00+08:00",
  "error": {
    "code": "INSUFFICIENT_DATA",
    "message": "缺少最近三个月交易数据。",
    "retryable": true,
    "evidence_refs": [
      {
        "source_ref": {
          "type": "bot_session",
          "id": "session_9"
        },
        "record_ids": ["tool_call_17", "message_109"]
      }
    ]
  },
  "partial_result": {
    "digest": "已完成支付入口和幂等模块分析，交易对账部分未完成。",
    "artifact_refs": [
      {
        "artifact_id": "artifact_41",
        "name": "partial-analysis.md",
        "media_type": "text/markdown"
      }
    ]
  }
}
```

失败结果的消费原则：

```text
Task 决定是否重试；Context 不替 Task 决策。
Retry View 默认读取 error、明确可复用的 partial_result 和相关证据片段。
失败 Session 的全部内容不默认进入新 Prompt。
```

### 2.6 幂等、乱序和冲突

第一版只定义可以客观判定的三种情况：

1. 相同 `report_id` 重传：幂等返回原记录；
2. 同一 `execution_ref` 经 Callback/Poller 收到内容相同但 `report_id` 不同的终态：按归一结果摘要判为重复观察，保留通道审计但只形成一个可消费 Outcome；
3. 同一 `execution_ref` 收到互斥终态，或两个内容不同的成功结果：均保留并标记 `terminal_conflict`，`task.accept.read` 返回 `blocked`，由 Task Runtime 根据超时、取消和状态迁移规则处理，Context 不静默选一个覆盖另一个。

`report_id` 解决重复投递，`terminal_conflict` 解决来源真正给出不一致事实；二者不能混为一谈。Context 也不根据自然语言“猜哪个结果更正确”。

---

## 3. U1：TaskExecutionPort——Context 如何理解 Task 位置

`execution_ref` 背后的 Task 归属、依赖和 Attempt 已经由 Task Runtime 保存，Context 不应另建一份可独立修改的 DAG。

建议只读接口：

```python
class TaskExecutionPort(Protocol):
    def resolve_execution(execution_ref: str) -> ExecutionPosition: ...
    def resolve_unit(task_id: str, unit_id: str) -> UnitSnapshot: ...
    def resolve_dependencies(task_id: str, unit_id: str) -> list[DependencyRef]: ...
    def list_attempts(task_id: str, unit_id: str) -> list[AttemptSnapshot]: ...
    def resolve_runtime_refs(execution_ref: str) -> list[RuntimeRef]: ...
    def read_task_revision(task_id: str) -> int: ...
```

最小返回对象：

```json
{
  "task_id": "task_1",
  "unit_id": "node_20",
  "attempt": 2,
  "execution_ref": "exec_88",
  "task_revision": 18
}
```

这里的 `unit_id` 是 **Avernet Task Loop Profile** 的字段，不属于 Context Core。未来其他 Profile 可以用 `conversation_id`、`case_id` 或 `goal_id` 定位自己的消费位置。

### 3.1 中心化和分布式执行都如何归属

**中心化 Task：**

```text
Task Runtime 知道 task_1/node_20/attempt_2
→ 生成 exec_88
→ Adapter 启动 session_9
→ 保存 exec_88 ↔ session_9
→ Bot 只感知 session_9
→ Adapter 回投 exec_88 + session_9
```

**分布式调用：**

```text
调用方只知道父执行 exec_A
→ 创建子执行 exec_B，并记录 parent_execution_ref=exec_A
→ 子 Bot 只感知自己的 session_B
→ 若后来被某个 Task 接管，由 Task Runtime 建立 Task 归属
```

Context 看到的是权威关系，不根据时间顺序推断 DAG。时间只用于排序和审计，不能替代依赖关系。

---

## 4. U2：读取已有来源、产物和知识

### 4.1 SourceAccessPort

```python
class SourceAccessPort(Protocol):
    def read_records(source: SourceRef, record_ids: list[str]) -> list[SourceRecord]: ...
    def read_range(source: SourceRef, cursor: SourceRange) -> list[SourceRecord]: ...
    def search(source: SourceRef, query: str, limit: int) -> list[SourceHit]: ...
```

至少需要三个 Adapter：

```text
BotSourceAdapter
GroupSourceAdapter
BbsSourceAdapter
```

新增邮件、代码评审或工单时，只新增 Adapter 和该来源到统一 `SourceRecord` 投影的实现；`RuntimeOutcomeReport` 和 Task View 不改。

Context Core 对来源只要求：

```text
稳定 SourceRef、稳定 Record ID、发生时间、可展示内容、Artifact 引用、回复/引用关系。
```

它不要求所有来源使用相同原生 Payload。

### 4.2 ArtifactPort

```python
class ArtifactPort(Protocol):
    def metadata(artifact_id: str) -> ArtifactMetadata: ...
    def read(artifact_id: str, range: ArtifactRange | None = None) -> ArtifactContent: ...
```

大文件默认通过引用进入 View。Context 可以根据请求读取标题、目录、指定段落或受控片段，但不复制 Artifact 的权威存储。

### 4.3 KnowledgePort

```python
class KnowledgePort(Protocol):
    def search(query: KnowledgeQuery) -> list[PublishedKnowledge]: ...
```

Context 只消费 ECB 已发布且未撤回的知识，并保留：

```text
knowledge_id、version、applicability、source_refs、validation_status。
```

知识召回永远是 Task View 的可选增强，不能取代当前 Task 的硬依赖和正式结果。

---

## 5. C1：ContextView 消费协议

### 5.1 一个入口、多个强类型 View

```http
POST /v1/context/views:resolve
```

第一版 Task Loop Profile：

```text
task.plan.initial
    初次规划

task.plan.replan
    因 Gap/失败重新规划

task.execute.prepare
    为某工作单元首次执行准备输入

task.execute.retry
    为同一工作单元的新 Attempt 准备输入

task.accept.read
    精确读取某次回投，用于 Task 验收和状态推进

task.inspect.execution
    调试/审计指定执行
```

不使用 `consumer + purpose + variant + include_xxx` 的自由组合。调用者只需说清“现在处于哪个消费位点”和“定位谁”；每个 View 的内容配方由 Context 维护。

### 5.2 请求公共外壳

模型输入类 View。以执行准备为例，Task Runtime 先创建 Attempt/Execution，再用 `execution_ref` 精确请求：

```json
{
  "view": "task.execute.retry",
  "target": {
    "execution_ref": "exec_89"
  },
  "budget": {
    "max_tokens": 12000
  }
}
```

字段解释：

| 字段 | 谁填写 | 含义 |
|---|---|---|
| `view` | Task 调用点固定填写 | 表达“为什么读”，直接选择强类型内容配方。 |
| `target` | Task | 表达“给谁读”。规划 View 使用 `task_id`；执行 View 使用已创建的 `execution_ref`；验收 View 使用 `report_id`。调用方不传第几个节点、Graph、依赖列表或历史 Attempt，这些由权威接口解析。 |
| `budget.max_tokens` | Runner/Planner 的模型配置 | 只限制内联给模型的内容；不会导致硬依赖事实被悄悄删除。 |

精确结果读取：

```json
{
  "view": "task.accept.read",
  "target": {
    "report_id": "report_201"
  }
}
```

`task.accept.read` 不做语义 TopK，也不需要 Token 预算。它必须精确返回指定报告、关联执行、Artifact 和明确证据。

### 5.3 各消费位点的内容契约

#### 5.3.1 `task.plan.initial`

请求：

```json
{
  "view": "task.plan.initial",
  "target": {"task_id": "task_1"},
  "budget": {"max_tokens": 12000}
}
```

必需内容：

```text
Task 原始目标；
验收要求；
用户/系统硬约束；
当前可用执行能力与限制。
```

可选内容：

```text
ECB 中适用的静态规范；
相似任务的已发布经验；
Task 明确关联的 Session/群/BBS 背景片段。
```

明确排除：

```text
尚不存在的失败历史；
完整历史会话；
与目标无关联的其他 Task 结果。
```

#### 5.3.2 `task.plan.replan`

请求：

```json
{
  "view": "task.plan.replan",
  "target": {
    "task_id": "task_1",
    "gap_id": "gap_7"
  },
  "budget": {"max_tokens": 12000}
}
```

必需内容：

```text
原始目标和仍有效的约束；
当前 Gap；
已经完成且仍有效的 Result Capsule；
直接导致 Gap 的失败报告和证据；
重规划不能破坏的既有结论。
```

可选内容：

```text
同类失败的已发布经验；
与 Gap 相关的会话、群或 BBS 片段；
可替代能力或方案。
```

与当前“把完整 Graph 交给 Planner”的区别是：Graph 可以继续作为 Task SSOT，但 Planner 拿到的是围绕当前 Gap 的消费视图，而不是自己从整图恢复焦点。

#### 5.3.3 `task.execute.prepare`

请求：

```json
{
  "view": "task.execute.prepare",
  "target": {
    "execution_ref": "exec_88"
  },
  "budget": {"max_tokens": 12000}
}
```

Task Runtime 此时已经保存：`exec_88 → task_1/node_20/attempt_1`，但尚未派发给 Bot。这样 Context 返回的一定是本次 Attempt 的输入，不依赖“当前最新 Attempt”这种有并发歧义的推断。

必需内容：

```text
当前工作单元的目标、instruction、约束和验收条件；
TaskExecutionPort 返回的所有硬依赖当前有效 Result Capsule；
明确必须使用的 ArtifactRef；
必要的权限、工具和执行限制。
```

可选内容：

```text
语义相关但非硬依赖的兄弟结果；
与当前目标直接相关的 Source 片段；
ECB 已发布知识。
```

Context 不需要知道它是“第 20 个节点”。它用 `execution_ref=exec_88` 经 TaskExecutionPort 解析出 `unit_id=node_20`；顺序和依赖仍由 Task 权威数据返回。

#### 5.3.4 `task.execute.retry`

请求结构与首次执行相同，但 View 不同：

```json
{
  "view": "task.execute.retry",
  "target": {
    "execution_ref": "exec_89"
  },
  "budget": {"max_tokens": 12000}
}
```

`exec_89` 已归属 `task_1/node_20/attempt_2`。Context 读取它的前序 Attempt，而不是笼统读取“这个节点最近一次失败”。

必需内容：

```text
首次执行的全部必需内容；
最近一次失败的 error；
上次正式采用的方案摘要；
明确允许复用的 partial_result；
外部验收反馈或 Gap；
本次必须改变/不得重复的条件。
```

可选内容：

```text
失败 Session 中与 error/Gap 直接相关的消息和工具证据；
更早 Attempt 的重复失败模式；
ECB 中适用的失败经验。
```

Retry 是同一工作单元的新 Attempt，不复制 Task 节点。Context 通过 TaskExecutionPort 的 `list_attempts` 找到历史，而不是让调用方拼 Attempt 列表。

#### 5.3.5 `task.accept.read`

请求：

```json
{
  "view": "task.accept.read",
  "target": {"report_id": "report_201"}
}
```

必须精确返回：

```text
RuntimeOutcomeReport；
execution_ref 对应的 Task/工作单元/Attempt；
Result Capsule 或 Error；
Artifact 元数据；
明确引用的 Evidence；
是否存在重复终态或归属缺失。
```

Task 基于该 View 判断验收、状态推进、retry 或 replan。Context 不生成验收结论。

#### 5.3.6 `task.inspect.execution`

面向人工调试和审计，可按 `execution_ref` 或 `report_id` 查询：

```text
Task 位置；
该 Attempt 的完整 RuntimeOutcomeReport 时间线；
关联 Source 范围；
Task 状态变化；
Artifact；
重复、乱序和冲突；
某次模型 View 实际包含和省略了什么。
```

它允许展开完整过程，但不会把该行为变成 Runner 的默认输入。

### 5.4 响应公共外壳

```json
{
  "view_id": "view_301",
  "view": "task.execute.retry",
  "based_on": {
    "task_revision": 18,
    "resolved_at": "2026-09-10T10:42:00+08:00"
  },
  "content": {},
  "provenance": [],
  "omitted": [],
  "completeness": {
    "status": "complete",
    "missing_required": []
  },
  "usage": {
    "max_tokens": 12000,
    "estimated_tokens": 8640
  }
}
```

| 字段 | 作用 |
|---|---|
| `view_id` | 固定本次实际交付的内容，可用于回放和问题定位。 |
| `based_on` | 说明 View 基于哪个 Task 修订版、何时解析。第一版不要求维护一个全局 `context_revision`；Outcome、Knowledge 和 Source 的具体版本放在 `provenance` 中。 |
| `content` | 由具体 View 定义，不是万能字段袋。 |
| `provenance` | 说明每项来自哪个 Task 字段、报告、Source、Artifact 或 Knowledge。 |
| `omitted` | 说明哪些候选未进入，以及原因：无关、超预算、被更新版本替代、无权限。 |
| `completeness` | 证明该 View 的必需集合是否完整。 |
| `usage` | 仅模型输入类 View 返回预算使用。 |

`task.execute.retry` 的 `content` 示例：

```json
{
  "assignment": {
    "goal": "完成交易对账风险分析",
    "instruction": "补全最近三个月的数据验证",
    "acceptance": ["覆盖三个月交易", "结论附证据"]
  },
  "dependencies": [
    {
      "unit_id": "node_19",
      "result": {
        "digest": "已完成支付入口分析。",
        "data": {"risk_count": 2},
        "artifact_refs": [{"artifact_id": "artifact_40"}]
      }
    }
  ],
  "prior_failure": {
    "attempt": 1,
    "error": {
      "code": "INSUFFICIENT_DATA",
      "message": "缺少最近三个月交易数据。"
    },
    "reusable_partial_result": {
      "digest": "支付入口和幂等模块分析仍有效。"
    },
    "evidence_snippets": []
  },
  "knowledge": [],
  "artifact_refs": []
}
```

### 5.5 Context View 的确定性处理顺序

Context 不是“所有数据做向量 TopK”。处理顺序必须固定：

```text
1. 识别 View 配方
   view → required / optional / excluded

2. 解析权威范围
   TaskExecutionPort → Task、工作单元、Attempt、依赖、当前有效结果

3. 保护必需项
   目标、约束、硬依赖、指定失败和指定报告不参与语义淘汰

4. 召回可选项
   SourceAccessPort + KnowledgePort → 候选背景、经验和证据

5. 选择可选项
   适用范围 → 相关度 → 时效 → 可信度 → 去重

6. 控制表示
   inline → snippet → context_summary → ref → omitted

7. 返回解释
   provenance + omitted + completeness + based_on
```

如果必需项本身超过预算，返回：

```json
{
  "completeness": {
    "status": "blocked",
    "missing_required": [],
    "reason": "required_content_exceeds_budget"
  }
}
```

不能把硬依赖静默截掉后仍声称上下文完整。Runner 可以扩大预算、按 Artifact 分段读取或要求 Task 拆分工作单元。

### 5.6 三种“摘要”必须分开

| 名称 | 谁生产 | 生命周期 | 含义 |
|---|---|---|---|
| `Result Digest` | 执行主体/Runtime Adapter | 随 Runtime Outcome 持久保存 | 本次正式完成了什么，是结果契约的一部分。 |
| `Context Summary` | Context | 针对某个 View 和预算临时生成 | 只是长内容在本次消费中的压缩表示，必须可回到来源。 |
| `Knowledge Extraction` | ECB | 经抽取、融合、治理后发布 | 可跨 Task 复用的经验、事实或规则，带适用范围和版本。 |

是否属于知识加工，不取决于“有没有 summary”，而取决于是否形成了可跨 Task 复用、可治理的新语义。

---

## 6. K1：Context 与 ECB 的知识加工协议

### 6.1 KnowledgeExtractionBundle

Context 不把 Session 摘要冒充知识，而是向 ECB 提供一个带执行语境的 Case Bundle：

```json
{
  "case_ref": {
    "task_id": "task_1",
    "unit_id": "node_20"
  },
  "goal": "完成交易对账风险分析",
  "constraints": ["结论必须附原始证据"],
  "attempts": [
    {
      "attempt": 1,
      "execution_ref": "exec_88",
      "runtime_refs": [
        {"type": "bot_session", "id": "session_9"}
      ],
      "outcome_ref": "report_202",
      "acceptance": {
        "verdict": "retry",
        "gaps": ["缺少三个月数据"]
      }
    },
    {
      "attempt": 2,
      "execution_ref": "exec_89",
      "runtime_refs": [
        {"type": "bot_session", "id": "session_10"}
      ],
      "outcome_ref": "report_203",
      "acceptance": {
        "verdict": "accepted",
        "gaps": []
      }
    }
  ]
}
```

ECB 通过 `runtime_refs` 读取完整 Session，并结合：

```text
Task 目标；
失败和成功 Outcome；
验收结论与 Gap；
Artifact 和证据；
权限与知识库范围；
```

抽取 `ProblemSolution`、`ToolExperience`、领域事实或规则候选。单次 Session 只能形成候选，是否融合、发布或撤回由 ECB 负责。

### 6.2 知识回流

Context 通过 `KnowledgePort.search` 查询 ECB Published Knowledge，并根据当前 View 决定是否进入可选候选集合。

必须满足：

```text
只使用 published/active 版本；
按 tenant/user/bot/domain 权限过滤；
按 applicability 过滤；
保留 knowledge_id、version 和 source_refs；
撤回后新 View 不再引用旧版本。
```

---

## 7. 端到端示例：30 个工作单元，第 20 个首次执行、失败和重试

假设：

```text
Task 有 30 个工作单元；
当前执行 node_20；
硬依赖是 node_7、node_12、node_19；
node_19 产出 200 KB 报告；
node_1～6 与本节点没有依赖，只有少量语义相关；
ECB 有一条适用的失败经验。
```

### 7.1 首次执行

Task Runtime 先创建 Attempt 1 和待派发执行 `exec_88`，再请求：

```json
{
  "view": "task.execute.prepare",
  "target": {"execution_ref": "exec_88"},
  "budget": {"max_tokens": 12000}
}
```

Context：

```text
必放：node_20 目标/约束/验收；node_7/12/19 的当前有效 Result Capsule；
引用：node_19 的 200 KB Artifact，只内联 digest、结构化值和必要片段；
可选：ECB 相关知识、node_1～6 中真正相关的片段；
省略：无依赖且无语义关联的节点原文，并在 omitted 中说明。
```

Context 返回完整 View 后，Task Runtime 启动 `session_9`，保存 `exec_88 ↔ session_9`，再把 View 派发给执行主体。

### 7.2 失败回投与验收

Adapter 上报 `report_202`：

```text
execution_ref=exec_88
runtime_ref=bot_session/session_9
status=failed
error=INSUFFICIENT_DATA
```

Task 收到 `report_id` 通知，请求 `task.accept.read(report_202)`，精确读取结果后决定重试。Context 不自行把 Node 复位。

### 7.3 重试

Task 在同一 `node_20` 下创建 Attempt 2 和待派发执行 `exec_89`，请求：

```json
{
  "view": "task.execute.retry",
  "target": {"execution_ref": "exec_89"},
  "budget": {"max_tokens": 12000}
}
```

Context 返回：

```text
首次执行必需输入；
Attempt 1 的明确失败原因；
仍有效的 partial_result；
失败 Session 中与缺数直接相关的工具错误片段；
ECB 中适用的补救经验；
不返回 Attempt 1 全量 Session，也不把失败结果当作成功依赖。
```

### 7.4 知识抽取

Task 完成后，Context 将两个 Attempt 的引用、Outcome 和验收组合成 `KnowledgeExtractionBundle` 给 ECB。ECB 读取完整 Session，才可能形成：

```text
“交易风险分析必须先校验数据时间范围”——规则候选；
“接口 X 缺数时改用数据源 Y”——工具经验候选。
```

下一个相似 Task 的 Planner/Runner 通过 `KnowledgePort` 按需消费已发布版本，而不是直接复用旧 Session 的原文。

---

# 第二部分：业界任务上下文如何生产、保存和消费

## 8. 对标方法

不能只比较是否有 `task_id`、`context` 或 `memory` 字段。本文按同一条消费链比较：

```text
生产了什么
→ 保存在哪里
→ 哪个下游在什么时机消费
→ 默认给正式结果还是完整过程
→ 重试如何使用历史
→ 长上下文/大产物如何控制
→ 能否直接支撑 Avernet 的协议决策
```

没有一个产品同时解决“耐久 Task、LLM 语义选择、多模态来源、跨 Task 知识治理”。Avernet 需要组合机制，而不是复制任意一家字段。

这些对标对象的作用不同：

| 对标层次 | 产品 | 与 Avernet 相似在哪里 | 主要用于验证什么 |
|---|---|---|---|
| 耐久任务编排 | AWS Step Functions、Temporal | 有节点/Activity、异步执行、失败与重试、持久历史 | 执行身份、结果投影、Attempt 和历史边界 |
| Agent 间任务协议 | A2A | 同时存在 Message、Task、Artifact 和会话上下文 | 多模态过程、任务状态和正式产物为何要分层 |
| Agent 工作流/记忆 | LangGraph、AutoGen、CrewAI | 模型调用会消费状态、上游结果或检索记忆 | 持久状态不等于 Prompt；硬依赖与可选知识如何进入 |
| 长周期 Agent Goal | LoopX | 任务跨多个 Turn/进程推进 | 为什么下一轮应消费有限 continuation state，而不是重放全部会话 |

因此，AWS/Temporal 对字段级执行语义的证据更强；A2A 对多模态对象边界更强；LangGraph/AutoGen/CrewAI 对模型输入组织更强；LoopX 对长期任务连续性更强。后文不会把它们当成同一类产品硬比较。

---

## 9. AWS Step Functions：最清晰的“节点输入投影—结果投影—状态合并”

### 9.1 它实际保存和生产什么

Step Functions 维护 Workflow Execution 的状态 JSON。一个 Task State 执行前后可经过多次明确投影：

```text
原始 State Input
→ InputPath：从输入中筛选一部分
→ Parameters：构造当前 Task 真正收到的参数
→ Task/Service 返回原始结果
→ ResultSelector：从原始结果中选出正式结果结构
→ ResultPath：决定结果覆盖、合并还是丢弃到 Workflow State
→ OutputPath：决定下游状态实际看到什么
```

这说明它没有把“Workflow 中所有已知信息”天然全部交给每个节点，而是让状态定义显式决定本节点输入和下游输出。

### 9.2 下游如何消费生产数据

假设上一步 Lambda 返回大量 SDK 元数据和业务结果：

```text
ResultSelector 先抽出 Payload 中真正需要的字段；
ResultPath 将精简结果放到 $.analysis；
下一状态通过 InputPath/Parameters 只拿 $.analysis 和必要订单字段。
```

下游消费的是**经过投影的状态结果**，不是 Lambda 日志或整个调用轨迹。

### 9.3 异步执行与 Runtime 身份

Callback 模式向 Worker 传 Task Token，Step Functions 等待 Token 被带着成功/失败结果返回。Worker 不需要理解完整状态机；Token 是一次等待执行的不透明关联身份。

这直接支持 Avernet 的选择：

```text
AWS Task Token      → Avernet execution_ref
Worker 回调结果      → RuntimeOutcomeReport
ResultSelector       → Result Capsule
Input/Output Path    → 强类型 Context View
```

### 9.4 重试如何处理

Retry/Catch 是状态机定义的一部分。Worker 只报告本次成功或失败，编排系统决定是否再次调度及失败如何进入后续状态。失败历史不会因为 Worker 重试就自动成为下一个 Task 的全部输入；是否保留错误由状态机的 Catch/ResultPath 决定。

### 9.5 大小如何控制

Step Functions 对 Task、State 和 Execution 输入输出有 256 KiB 限制。大型数据通常放外部存储，状态间传引用。这一限制本身迫使系统区分“工作流状态”和“大产物”。

### 9.6 能借鉴什么，不能借鉴什么

**可直接借鉴：**

```text
一次执行使用不透明关联身份；
结果进入下游前先做正式结果投影；
当前节点输入是显式消费投影；
大产物使用引用。
```

**不能照搬：**

```text
InputPath 是确定性 JSONPath，不解决 LLM 的语义相关度；
它没有跨 Task 知识抽取和召回；
依赖完整性由状态机作者保证，不自动解释“为什么省略”。
```

因此 Avernet 在 AWS 的确定性投影外，还要增加 required/optional、KnowledgePort、Token 预算和 `omitted/completeness`。

---

## 10. Temporal：完整历史用于恢复，Activity 输入仍是明确参数

### 10.1 它实际保存和生产什么

Temporal 服务端为 Workflow Execution 保存 append-only Event History，例如：

```text
WorkflowExecutionStarted
ActivityTaskScheduled
ActivityTaskCompleted / ActivityTaskFailed
TimerStarted / TimerFired
SignalReceived
```

Workflow Worker 通过重放 Event History 恢复确定性状态。Activity Worker 从 Task Queue 领取一次 Activity Task，执行后报告结果或 Failure。

关键点是：

> “服务端完整保存 Event History”不等于“每个 Activity Worker 获得完整 Event History”。Activity 的业务输入仍由 Workflow 代码明确传参。

### 10.2 下游如何消费生产数据

Activity 返回值成为 Workflow 代码中的结果；Workflow 根据结果继续安排下一个 Activity。Event History 主要被 Temporal Runtime 用于恢复、重放和审计，而不是直接拼进下一个 Activity 的业务输入。

映射到 Avernet：

```text
Temporal Event History → Task/Context 的可追溯执行事实
Activity Result         → Result Capsule
Activity Arguments      → task.execute.prepare View
```

### 10.3 重试如何处理

Activity 抛出 retryable Failure 或超时后，Temporal Service 按 Retry Policy 再调度 Activity Task。逻辑 Activity 没变，但实际执行尝试会被区分；最终成功或失败继续写入历史。

这支持 Avernet 的身份分层：

```text
Task Work Unit 不变；
Attempt 增加；
每次 Runtime Session/Execution 独立；
旧失败不被新成功覆盖，而是保留为历史。
```

Avernet 不能把失败历史全部自动给新 Attempt；Temporal 也没有这么做。应由 `task.execute.retry` 明确选择 error、可复用 partial result 和必要证据。

### 10.4 长历史如何处理

Temporal 明确指出长或大的 Event History 会影响性能，并提供 Continue-As-New：把最新相关状态作为新 Workflow Execution 的参数，开启一份新历史并继续执行。

这体现两层分离：

```text
完整历史用于恢复和审计；
Continuation State 用于下一段继续工作。
```

对应 Avernet：完整 Session/执行事实保留，但 Runner 只消费当前 View。

### 10.5 能借鉴什么，不能借鉴什么

**可直接借鉴：**

```text
工作单元、Attempt、实际 Runtime 分层；
执行历史追加保存，不用新结果覆盖旧失败；
完整历史与下一步业务输入分离；
长历史通过 continuation state 收敛。
```

**不能照搬：**

```text
Temporal 解决耐久执行和确定性重放，不做语义相关度；
Activity Result 仍由业务代码自行设计；
它不负责将 Session 加工成长期知识。
```

所以 Temporal 是身份、重试和历史保留的依据，不是 Context View 排序算法的依据。

---

## 11. A2A：Message、Task、Artifact 和 Context ID 明确分层

### 11.1 它实际保存和生产什么

A2A 的核心对象包括：

```text
Message：一次 Agent 间通信，可包含 text、file 或 structured data Part；
Task：长运行工作的身份、状态、history 和 artifacts；
Artifact：Task 产生的正式输出；
contextId：逻辑分组多个相关 Task 和 Message 的会话上下文；
referenceTaskIds：Message 显式引用的相关 Task。
```

简单请求可以直接返回 Message；复杂异步工作则返回 Task，后续通过轮询、流式或推送获取状态和 Artifact。

### 11.2 下游如何消费生产数据

`Get Task` 返回当前 Task 状态和 Artifact，并允许通过 `historyLength` 控制最多返回多少条最近历史：

```text
historyLength=0：不返回消息历史；
正数：最多返回指定数量的最近消息；
未设置：客户端不设上限，但服务端仍可施加更低限制。
```

`List Tasks` 默认可以不带 Artifact，以减少 Payload；需要时显式 `includeArtifacts=true`。

这说明 A2A 没有把 Task history、状态和正式产物混成一个字符串：消费者可以先看状态和 Artifact，只有需要过程时才取 history。

### 11.3 对 Avernet 的直接意义

```text
A2A Message/history   → 已有 Source Record，通过 SourceAccessPort 读取
A2A Task              → Avernet TaskExecutionPort 提供的位置和状态
A2A Artifact          → ArtifactRef
A2A contextId         → 会话范围，不等于 Task ID
A2A historyLength     → 过程历史是按需消费，不是默认全量输入
```

它也证明 `session/context` 与 `task` 可以是不同维度：同一 contextId 下可以有多个 Task；Avernet 不应把 `task_id` 设计成所有来源内容的必填字段。

### 11.4 能借鉴什么，不能借鉴什么

**可直接借鉴：**

```text
过程消息、Task 状态和正式 Artifact 分离；
上下文会话 ID 不等于任务 ID；
历史和 Artifact 可按需返回；
多模态内容使用 Part/Artifact 表达。
```

**不能照搬：**

```text
A2A 解决跨 Agent 互操作，不知道 Avernet 内部硬依赖；
historyLength 只按最近条数控制，不能保证语义相关或硬依赖完整；
没有跨 Task 知识治理。
```

因此 Avernet 借鉴其对象分层，不直接使用“最近 N 条”作为执行 Context 算法。

---

## 12. LangGraph：Checkpoint 保存完整 State，模型输入仍需裁剪和总结

### 12.1 它实际保存和生产什么

LangGraph 有两套不同持久化机制：

```text
Checkpointer：按 thread_id 保存 Graph State checkpoint，作用域是单个 Thread；
Store：保存跨 Thread 的应用级长期数据。
```

Checkpoint 用于会话连续、人工介入、time travel 和故障恢复。Store 用于用户偏好、共享事实和跨 Thread 知识。

### 12.2 下游如何消费生产数据

Node 读取的是应用定义的 State Schema。保存了完整 State，不代表模型调用必须拿到全部消息。LangChain/LangGraph 官方文档对长会话列出常见处理：

```text
Trim messages：调用模型前裁剪；
Delete messages：从 State 中永久删除；
Summarize messages：用摘要替换较早历史；
Custom strategies：按应用规则过滤。
```

所以存在两个不同动作：

```text
持久化完整/可恢复状态；
为当前模型调用准备有限输入。
```

### 12.3 重试和恢复如何消费

Checkpoint 允许 Thread 从中断位置继续，pending writes 等机制避免成功写入被无意义重复执行。恢复依赖持久 State，而不是要求用户把完整聊天重新提交一次。

映射到 Avernet：

```text
Thread checkpoint     → Task/执行事实的可恢复状态
Node State input      → Context View
Store                 → ECB/长期知识的消费入口
trim/summarize        → Context 的表示与预算策略
```

### 12.4 能借鉴什么，不能借鉴什么

**可直接借鉴：**

```text
持久状态和模型输入分离；
Thread 短期状态与跨 Thread 长期 Store 分离；
长历史需要显式 trim/summarize/custom strategy。
```

**不能照搬：**

```text
State Schema 和裁剪规则主要由应用自己定义；
没有现成的多来源 SourceAccessPort；
默认不会证明 Task 硬依赖是否完整；
摘要不自动成为有治理的长期知识。
```

这正是 Avernet 需要强类型 View 和 `completeness`，而不是只引入一个通用 Message Trimmer 的原因。

---

## 13. AutoGen Memory：长期记忆先 query，再显式注入当前模型上下文

### 13.1 它实际保存和生产什么

AutoGen AgentChat 的 Memory Protocol 定义了：

```text
add：添加 Memory；
query：根据当前查询检索相关内容；
update_context：把检索到的内容加入 Agent model context；
clear / close：管理生命周期。
```

Memory 的典型用途是 RAG：在某个具体步骤前查询相关事实，再加入当前 Agent 上下文，而不是默认把 Memory Store 全量拼入 Prompt。

### 13.2 下游如何消费生产数据

模型调用前，Agent/Memory 实现根据当前消息或任务生成 query，拿到相关 MemoryContent，再由 `update_context` 注入模型上下文。简单的 `ListMemory` 可以按时间附加，但更复杂的实现可以接向量库或其他检索系统。

对应 Avernet：

```text
Memory.query          → KnowledgePort.search
Memory.update_context → Context 将已选知识放入具体 View
```

### 13.3 它没有解决什么

AutoGen Memory 不知道：

```text
哪个 Task 工作单元是当前硬依赖；
某次失败属于哪个 Attempt；
哪个 Runtime Outcome 是正式任务结果；
是否已经通过 Task 验收。
```

因此知识检索必须发生在 Task 范围和必需项已经解析之后。不能用“相似 Memory TopK”替代依赖结果。

### 13.4 对 Avernet 的结论

AutoGen 支持 P4“知识按当前意图进入上下文”，但不支持 P1/P2 的任务执行身份和结果契约。Avernet 应把 ECB 知识放在 View 的 optional 阶段，而不是让 Memory 模块接管整个 Context。

---

## 14. CrewAI：显式选择上游 TaskOutput，但更适合短流程

### 14.1 它实际保存和生产什么

CrewAI Task 可以声明：

```text
context: 哪些其他 Task 的输出作为本 Task 上下文；
output_json / output_pydantic: 结构化输出模型；
output_file: 结果文件；
guardrail: 对 Task 输出进行校验；
guardrail_max_retries: 校验失败后的重试次数。
```

Task 完成后产生 `TaskOutput`，可包含 raw、json_dict 或 pydantic 结果；是否有结构化结果取决于 Task 是否配置对应输出模型。

### 14.2 下游如何消费生产数据

下游 Task 不需要消费整个 Crew 轨迹，而是通过显式 `context=[task_a, task_b]` 使用指定上游 Task 的输出。若配置结构化模型，下游可以读取稳定字段而不是解析纯文本。

映射到 Avernet：

```text
Task.context          → TaskExecutionPort 的显式依赖
TaskOutput            → Result Capsule
output_json/pydantic  → Result Capsule.data
output_file           → ArtifactRef
guardrail             → Task 的验收流程
```

### 14.3 重试和大小的局限

CrewAI guardrail retry 会把校验反馈提供给 Agent 重新执行，适合说明“重试输入与首次输入不同”。但它不是耐久工作流执行账本，公开 Task 接口也没有完整定义跨进程 Attempt 历史、乱序回投和大规模执行恢复。

### 14.4 对 Avernet 的结论

CrewAI 最有价值的证据不是“它也有 context 字段”，而是：

```text
下游依赖应显式指向上游 TaskOutput；
正式输出可以是结构化模型；
校验失败反馈应成为重试输入。
```

Avernet 需要在此基础上补足耐久身份、Source 引用、预算和知识治理。

---

## 15. LoopX：最像长期任务控制面，但不是字段级协议模板

### 15.1 它解决的任务形态

LoopX 面向跨多个 Agent Turn、进程重启甚至不同 Harness 的长期 Goal。它持久保存：

```text
objective、scope、gates、todos、evidence、quota、run history、handoff。
```

Goal 不是 Chat Thread；Thread 是可替换的执行者，Goal 才拥有长期连续性。

### 15.2 什么是 bounded turn

`bounded turn` 不是“检索相关的几个 turn”，而是：

```text
每次只允许 Agent 完成一个有范围、可验证的工作切片；
完成后写回 evidence、handoff 和 next todo；
下一轮重新读取当前 Goal 状态和 quota，再决定是否继续。
```

它解决的是长任务不应依赖某个无限增长的 Chat Session。下一轮消费的是持久控制状态和本轮工作包，而不是把所有历史 Turn 再播放一遍。

### 15.3 下游如何消费生产数据

LoopX 的 Kernel 保存 Durable Goal truth；`quota should-run`、`refresh-state`、status 等接口将 Goal 当前状态投影给下一次 Agent Turn。Run payload 可保存丰富私有证据，但第一屏状态和 compact run index 保持精简。

具体消费关系：

```text
上一 Turn 生产 validated work / blocker / evidence / handoff
→ Kernel 追加事件并刷新 Active Goal State
→ 下一 Turn 先读取 goal、gate、todo、quota、fresh evidence
→ 执行一个 bounded slice
```

这与 Avernet 的相似处是“长期 Task 多轮推进”，不是 Graph 结构或字段相同。

### 15.4 对 Avernet 的直接意义

```text
Durable Goal           → Task SSOT，而不是 Session
bounded turn packet    → task.execute.prepare/retry View
Evidence + handoff     → Result Capsule + EvidenceRef
compact run index      → 默认 View 中的有限历史
rich run payload       → Source/Artifact，按需读取
```

### 15.5 能借鉴什么，不能借鉴什么

**可直接借鉴：**

```text
长期目标状态不寄托在 Chat Thread；
每轮只交付可继续工作的有限状态；
证据和 handoff 必须写回持久控制面；
运行细节和紧凑状态分层保存。
```

**不能照搬：**

```text
LoopX 是 Goal 控制面，不是跨 Bot/群/BBS 的通用协议；
其 quota/gate/todo 是自身领域对象，不应直接复制进 Avernet Context Core；
它不替代 ECB 的知识抽取与发布。
```

所以 LoopX 用来验证“为什么需要 Context View”，不用于决定 `RuntimeOutcomeReport` 的字段。

---

## 16. 横向比较：业界生产数据到底如何被消费

| 产品 | 生产/保存的主要数据 | 默认下游消费 | 过程历史何时读取 | 重试输入 | 大小控制 | 对 Avernet 的主要证据 |
|---|---|---|---|---|---|---|
| AWS Step Functions | Workflow State、Task Result、执行事件 | 经 Input/Result/Output 投影后的状态 | 调试执行历史时 | 状态机定义的错误与输入，不自动给全部日志 | 256 KiB 限制、外部对象引用 | 明确输入投影、结果投影、不透明执行 Token |
| Temporal | Event History、Activity Result/Failure | Workflow 代码显式接收 Activity Result | 恢复、重放、审计 | Retry Policy 调度新尝试；业务输入仍显式构造 | Continue-As-New，避免历史无限增长 | Work Unit/Attempt/Runtime 分层；完整历史不等于业务输入 |
| A2A | Message、Task status/history、Artifact、contextId | 先看 Task 状态和 Artifact；history 可限量 | 需要对话过程时通过 historyLength | 协议允许继续发 Message，但不定义内部重试算法 | historyLength、可不返回 Artifact | Session Context 与 Task 分开；过程、状态、产物分层 |
| LangGraph | Thread Checkpoint、Graph State、跨 Thread Store | Node State；模型前可再次裁剪 | 恢复、time travel、人工介入 | 从 checkpoint 继续，应用决定输入 | trim/delete/summarize/custom | 持久状态与模型上下文不是一回事 |
| AutoGen | Memory Store | query 后选中 Memory，再 update_context | 只在检索命中时 | 不负责 Task Attempt | query/实现自定义 | 知识是可选召回，不是默认全量注入 |
| CrewAI | TaskOutput、显式 Task context | 指定上游 TaskOutput | 通常不提供完整耐久轨迹 | guardrail 反馈 + retry | 主要靠任务拆分和结构化输出 | 显式依赖、结构化 Result、校验反馈进入重试 |
| LoopX | Goal、Todo、Gate、Evidence、Run index、Handoff | 当前 Goal 投影和一个 bounded work slice | 审计或需要 rich run payload 时 | 基于当前 state/evidence 进入下一 bounded turn | compact state + bounded turn | 长任务连续性属于持久目标，不属于某个无限 Session |

### 16.1 放进同一个“30 个工作单元、执行第 20 个”场景

假设第 20 个工作单元硬依赖 7、12、19，19 有 200 KB 产物，并且知识库中有相似经验。不同产品实际会这样处理：

| 产品 | 第 20 个执行者默认拿到什么 | 它不会自动做什么 | 单独照搬的缺口 |
|---|---|---|---|
| AWS Step Functions | 状态机用 `InputPath/Parameters` 明确投影出的 JSON | 不会按语义自动找相关兄弟或知识 | 精确但依赖人工写投影，不理解 LLM 相关度 |
| Temporal | Workflow 代码显式传给 Activity 的参数 | 不会把 Event History 自动作为 Activity Prompt | 历史可靠，但输入内容仍由业务代码组织 |
| A2A | Task/Message 请求、当前状态、Artifact；history 可按需取 | 不会解析 Avernet DAG 的 7/12/19 是硬依赖 | 对象边界清楚，但没有内部依赖算法 |
| LangGraph | 当前 Thread State；模型调用前由应用裁剪消息 | 不会天然证明 7/12/19 是否完整 | 有持久化和裁剪机制，但 required 集合由应用定义 |
| AutoGen | 对当前 query 命中的 Memory | 不会区分命中内容是硬依赖还是相似经验 | 适合补充知识，不能构造任务主输入 |
| CrewAI | `context` 显式指定的上游 `TaskOutput` | 不会自动治理长期历史和跨任务知识 | 硬依赖直观，但耐久执行和大规模历史能力弱 |
| LoopX | 当前 Goal、Todo、Gate、Evidence、Handoff 组成的有限工作包 | 不会根据 Avernet DAG 自动选择 7/12/19 | 验证长任务要有 continuation state，但不是通用任务协议 |
| **Avernet 方案** | **7/12/19 的 Result Capsule 必放；200 KB 产物用 ArtifactRef；相关知识和非依赖内容在预算内选入** | **不把完整 Graph、全部 Session 或 Memory TopK 当成默认输入** | 组合了任务确定性与 LLM 可选相关度，但实现上必须真正维护 View 配方和完整性检查 |

由此可判断“最优”不是某个统一 TopK 算法，而是两段式：

```text
Task 结构确定 required 集合，保证不漏；
语义检索只处理 optional 集合，解决信息过多。
```

前者由 AWS/Temporal/CrewAI 一类显式依赖机制支持，后者由 LangGraph/AutoGen 一类模型上下文机制支持；Artifact 与 Session/Task 分层由 AWS/A2A 支持。Avernet 的方案是对这些不同问题分别采用最强约束，而不是把任一家产品包装成完整答案。

可以看到共同规律不是“大家都有一个 Context 模块”，而是四条更稳定的工程事实：

```text
1. 正式结果与原始过程分开；
2. 完整状态/历史与当前消费输入分开；
3. 重试保留执行历史，但只把可操作失败反馈带给新尝试；
4. 大内容通过引用或受限投影消费。
```

Avernet 的额外问题是 LLM 语义相关度和 ECB 知识闭环，因此需要在这些规律上增加 KnowledgePort、可选候选排序和可解释响应。

---

## 17. 从业界证据到 Avernet 协议决策

| Avernet 决策 | 业界证据 | 为什么不是机械照搬 |
|---|---|---|
| 用 `execution_ref`，不让 Bot 填 Task/DAG | AWS Task Token；Temporal Activity Task 由服务端归属 | Avernet 仍保留自己的 TaskExecutionPort，不复制 AWS/Temporal 状态机模型。 |
| 定义 Result Capsule | AWS ResultSelector；CrewAI TaskOutput；A2A Artifact | Capsule 同时适配 LLM digest、精确 data、Artifact 和 Evidence，比单纯 JSONPath 或 raw output 更适合多模态 Agent。 |
| 原始 Source 不重复上报 | A2A Message/Task/Artifact 分层；Temporal 历史与 Activity Result 分层 | Avernet 已有 Source 权威存储，因此通过 Adapter 读取比再建一份 Message Store 更可靠。 |
| 一个入口、多个强类型 View | AWS 每个 State 的输入投影；LoopX bounded work packet | View 不是让调用者自由组合 include 开关，而是由消费位点决定配方。 |
| 必需项先于语义 TopK | CrewAI 显式 Task context；Workflow/Activity 的确定性参数 | 向量召回只用于可选内容，不能淘汰 Task 硬依赖。 |
| 完整历史保留但不默认进入 Prompt | Temporal Event History；LangGraph checkpoint；LoopX rich run payload | Context 还需提供 Source 片段选择和 Token 表示降级。 |
| ECB 负责知识加工 | LangGraph Store；AutoGen Memory query/update_context | Avernet 已有抽取、建模、检索和治理引擎，Context 不应再做第二套 Knowledge System。 |

---

## 18. 为什么该协议能够验证“覆盖全”和“读取准”

### 18.1 覆盖全：按真实消费位点而不是按假设功能枚举

按当前 Avernet Task Runner、Planner、回投验收和 ECB 入口可见的消费路径，核心意图全部映射到 View：

| 消费意图 | View |
|---|---|
| 初次规划 | `task.plan.initial` |
| Gap/失败后的重新规划 | `task.plan.replan` |
| 下游工作单元首次执行 | `task.execute.prepare` |
| 同一工作单元重试 | `task.execute.retry` |
| 结果验收和状态推进 | `task.accept.read` |
| 调试、审计、完整过程回看 | `task.inspect.execution` |
| 跨 Task 经验复用 | 上述模型类 View 的 `knowledge` 可选区 |
| Task Session 经验沉淀 | `KnowledgeExtractionBundle` |

未来出现真正不同的消费意图时新增 View；如果只是来源新增，不改 View。

### 18.2 读取准：结构确定性优先，语义相关度只处理可选项

准确性来源不是“检索模型足够聪明”，而是：

```text
TaskExecutionPort 确定 Task/Unit/Attempt/依赖；
report_id/execution_ref 确定指定结果和本次 Attempt；
Result Capsule 确定正式结果；
ArtifactRef/EvidenceRef 确定可回溯来源；
required 集合不参与语义淘汰；
只有 optional 候选才做相关度和预算选择。
```

### 18.3 完整性可机器验证

每个 View 都有固定 required schema。Context 可以逐项计算：

```text
需要 3 个硬依赖，是否得到 3 个当前有效 Result Capsule；
需要指定失败报告，report_id 是否存在且归属正确；
需要 Artifact，引用是否存在且有权限；
需要验收证据，evidence_ref 是否可读取。
```

缺失时返回 `partial/blocked + missing_required`，而不是生成一段看似完整的 Prompt。

---

## 19. 第一版不变量与验收用例

### 19.1 不变量

```text
1. Bot/Runtime 不需要知道 task_id、unit_id 或 DAG；
2. execution_ref 必须在 Context View 解析前创建，并能由 Task SSOT 解析到唯一 Task/工作单元/Attempt；
3. Source 原始数据不在 Context 重复存储；
4. RuntimeOutcomeReport 追加保存，重试不能覆盖旧 Attempt；
5. Result Digest、Context Summary、ECB Knowledge 不混用；
6. 硬依赖不能被语义相关度或 Token 预算淘汰；
7. 大产物始终保留 ArtifactRef；
8. `task.accept.read` 是精确读取，不做 TopK；
9. Context 不修改 Task 状态，不决定 retry/replan/accept；
10. 所有模型输入 View 返回来源、版本、遗漏和完整性。
```

### 19.2 最小验收用例

```text
1. Callback 和 Poller 重复报告同一 report_id，只形成一条 Outcome；
2. 同一 session 被多个动作复用时，仍可通过 execution_ref 唯一归属；
3. Node 20 重试不复制 Node，但能读取 Attempt 1 的失败和 Attempt 2 的新执行；
4. Retry View 包含 error，不默认包含失败 Session 全文；
5. 200 KB 报告只内联 digest/必要片段并返回 ArtifactRef；
6. 三个硬依赖缺一个时，execute.prepare 返回 blocked；
7. 两个不同成功结果指向同一 execution_ref 时，accept.read 不静默覆盖；
8. 超预算时 optional 内容降级或省略，并返回 omitted；
9. Planner View 可以召回 ECB Published Knowledge，但知识不能替代当前 Task 结果；
10. ECB 可通过 KnowledgeExtractionBundle 回读完整 Session、Outcome 和 Acceptance；
11. 新增邮件来源时只新增 Source Adapter，不修改生产报告和 Task View；
12. 任一历史 View 可根据 view_id、已保存的选择清单/内容摘要、based_on 和 provenance 解释当时给了什么；不能只靠重新查询最新 Source 来冒充历史输入。
```

---

## 20. 与当前 Avernet 实现的关系

当前 `TaskRunner._build_context(task_id, node_id)`：

```text
执行模式：父节点规格 + 成功兄弟 output + 当前节点规格；
聚合/验收模式：成功子节点 output + 当前节点 goal/acceptances/instruction。
```

它可以作为第一版 Adapter 的实现来源，但不能继续作为 Context 的最终接口：

```text
当前 output → 逐步投影为 Result Capsule；
当前 Graph 查询 → 落到 TaskExecutionPort；
当前固定拼装 → 分化为 execute.prepare / execute.retry / accept.read；
现有 callback audit event_id → 仅当它稳定标识同一终态结果时可直接作为 report_id；否则需在 Adapter 归一后生成；
现有 Session/群/BBS 存储 → 实现 SourceAccessPort；
ECB Bot Session Pipeline → 接收 KnowledgeExtractionBundle。
```

第一阶段可以不立即引入语义检索：先完成身份、正式结果、强类型 View、硬依赖完整性和 ArtifactRef，已经能解决“糙”和“缺”的核心问题。相关度、Context Summary 和更复杂的候选排序可以后续替换实现，不改变协议。

---

## 参考资料

### Avernet / ECB

- `Avernet/src/backend/src/agentclaw/community/core/task/task_runner/task_runner.py`
- `Avernet/src/backend/src/agentclaw/community/core/task/task_runner/callback_adapter.py`
- `Avernet/src/backend/src/agentclaw/community/core/task/domain/models.py`
- `ocb/src/ecb/docs/knowledge-engine-analysis.md`
- `ocb/src/ecb/docs/specs/knowledge_system/_shared/overview.md`
- `ocb/src/ecb/docs/specs/knowledge_system/_shared/integration-with-knowledge-system.md`
- `ocb/src/ecb/docs/specs/knowledge_system/knowledge_bot_session/2026-07-13-bot-session-knowledge-pipeline/spec.md`
- `ocb/src/ecb/docs/specs/knowledge_system/knowledge_retrieval/2026-07-02-full-retrieval/spec.md`

### 业界一手资料

- AWS Step Functions：Manipulate parameters in Step Functions workflows  
  <https://docs.aws.amazon.com/step-functions/latest/dg/input-output-inputpath-params.html>
- AWS Step Functions：Discover service integration patterns  
  <https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html>
- AWS Step Functions：Service quotas  
  <https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html>
- Temporal：Events and Event History  
  <https://docs.temporal.io/workflow-execution/event>
- Temporal：Continue-As-New  
  <https://docs.temporal.io/workflow-execution/continue-as-new>
- A2A Protocol Specification  
  <https://a2a-protocol.org/latest/specification/>
- LangGraph：Persistence  
  <https://docs.langchain.com/oss/python/langgraph/persistence>
- LangChain：Short-term memory  
  <https://docs.langchain.com/oss/python/langchain/short-term-memory>
- AutoGen AgentChat：Memory and RAG  
  <https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/memory.html>
- CrewAI：Tasks  
  <https://docs.crewai.com/en/concepts/tasks>
- LoopX README  
  <https://github.com/huangruiteng/loopx>
- LoopX State Interaction Model  
  <https://github.com/huangruiteng/loopx/blob/main/docs/state-interaction-model.md>
- LoopX Project Agent Todo Contract  
  <https://github.com/huangruiteng/loopx/blob/main/docs/project-agent-todo-contract.md>
