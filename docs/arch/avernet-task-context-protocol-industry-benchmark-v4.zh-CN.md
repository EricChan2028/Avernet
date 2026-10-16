# Avernet Task Context 协议：业界运行机制与设计依据 V4

> 日期：2026-09-10  
> 状态：协议讨论稿  
> 目标：不是证明业界都有一个名为 Context 的模块，而是比较它们如何完成**任务进入、执行绑定、节点输入交付、结果回流和结果验收**，再据此推导 Avernet 动态 Task Loop 的协议。  
> 范围：Avernet Task Loop Profile。Graph 是当前实现，不进入协议 Core。

---

## 0. 一页结论

### 0.1 业界共同点不是“都有 Context”，而是都有数据契约

| 系统类型 | 用户怎么使用 | 当前节点如何拿到输入 |
|---|---|---|
| AWS Step Functions、Temporal 等 Workflow | 用户提前定义流程、节点和依赖 | 编排定义或 Workflow 代码精确构造节点输入 |
| A2A 等 Agent 互操作协议 | 一个 Agent 把消息或任务交给另一个 Agent | 协议传递 Message、Task、Artifact，但不负责对方内部节点依赖 |
| Magentic-One、LoopX 等动态 Agent Loop | 用户只给开放目标，系统运行时决定下一步 | Orchestrator/控制面维护 Ledger、Todo、Evidence 等状态，再构造本轮工作包 |
| Avernet | Planner 运行时产生下一项工作 | Planner 声明硬依赖；Context 保证硬依赖完整，再按相关度和预算补充背景 |

最重要的结论是：

```text
静态 Workflow：设计期精确传递。
动态 Task Loop：运行期先定义本次 Work，再精确传递。

“节点是动态产生的”不等于“把完整 Task 历史交给模型自己猜”。
```

### 0.2 Avernet 节点上下文的核心公式

```text
Execution Context
  = Assignment
  + Required Inputs
  + Optional Context
  + Output Contract

Required Inputs：
  Planner/控制面显式声明；Context 必须完整交付；不能被 TopK 或 Token 预算淘汰。

Optional Context：
  Context 从 Session、群、BBS、历史执行和知识引擎中检索；允许排序、压缩和省略，
  但必须说明来源与省略原因。
```

这不是凭空抽象：

- `Required Inputs` 来自 AWS/Temporal 的“当前节点显式输入”；
- `Optional Context` 来自 Agent Memory/动态 Orchestrator 的“按当前工作补充相关信息”；
- `ArtifactRef` 来自 Workflow/A2A 对大结果和正式产物的分层；
- `Execution Binding` 来自 Task Token、Activity Task 等不透明执行关联；
- `Candidate + Validation` 来自 LoopX 的执行回写与独立验证分离。

### 0.3 Avernet 第一版协议

```text
S1 SourceAccess
   从已有 Bot Session、协作群、BBS 权威存储读取原始过程；不重复上报全文。

P1 ExecutionBinding
   控制面把一次 Runtime 执行绑定到 Scope、Work 和 Attempt；Bot 不需要知道 Task/DAG。

P2 ExecutionContextSpec
   Planner/控制面声明本次 Assignment、Required Inputs、Optional Context 范围和 Output Contract。

C1 ContextView
   Context 实际交付给 Runner/Planner/Verifier 的有限、可追溯上下文。

P3 ExecutionCandidateReport
   执行者回投自己声称的成功、失败、结果、证据和产物。

P4 ExecutionValidationReport
   独立验收 Candidate，决定是否可成为已接受结果。

K1 KnowledgeExtractionBundle
   Context 向 ECB 提供完整 Case 的引用，由 ECB 提炼跨任务经验和规则。
```

---

## 1. 统一比较口径

产品可以没有 `Context` 这个名词，但无法绕过以下五件事：

| 编号 | 要回答的问题 | Avernet 对应协议 |
|---|---|---|
| I0 Ingress | 用户目标、消息或外部数据怎样进入系统？ | `S1 SourceAccess` 及 Task 自身入口 |
| I1 Execution Binding / Work Definition | 当前执行属于哪个上层范围、哪项工作、哪次尝试？ | `P1 ExecutionBinding` |
| I2 Execution Context Delivery | 当前执行者实际收到什么？谁决定？ | `P2 ExecutionContextSpec` → `C1 ContextView` |
| I3 Outcome Writeback | 成功、失败、结构化结果和大产物怎样回流？ | `P3 ExecutionCandidateReport` |
| I4 Validation | 执行者声称完成后，谁判断是否真的可接受？ | `P4 ExecutionValidationReport` |

对标时必须区分两类描述：

- **产品原生协议**：官方 DSL、API、函数参数、JSON、CLI 或持久化对象；
- **Avernet 等价映射**：为了横向比较，把产品机制映射成 I0—I4。等价映射不是声称产品原本就有 Avernet 字段。

---

## 2. 产品总览

| 产品 | 它是什么 | 谁决定下一项工作 | 节点输入怎样形成 | 开源情况 | 与 Avernet 相似度 |
|---|---|---|---|---|---|
| AWS Step Functions | 托管状态机/Workflow 服务 | 用户预定义状态机；运行时只在预定义分支中选择 | ASL 的 InputPath、Parameters 等显式投影 | 服务端不开源；ASL 和 API 公开 | 中：执行契约强，但流程主要静态 |
| Temporal | 开源耐久执行平台 | 用户编写的 Workflow 代码 | Workflow 用函数参数显式调用 Activity | 开源，MIT | 中：Attempt/历史强，但不是动态语义规划器 |
| A2A | Agent 与 Agent 的互操作协议 | 对端 Agent 内部决定 | Message/Task/history/Artifact 交付到 Agent 边界 | 开源，Apache-2.0 | 边界相似：适合对象分层，不解决内部节点上下文 |
| AutoGen Magentic-One | 多 Agent 动态 Orchestrator | Orchestrator 运行时选择下一 Agent | Task Ledger、Progress Ledger、群聊和本轮 instruction | AutoGen 开源，MIT | 高：动态决定下一步，但硬依赖类型较弱 |
| LoopX | 长周期 Agent Goal 控制面 | Goal/Todo/Gate 控制面按周期决定是否继续 | 紧凑的本轮包 + Evidence/Handoff/Quota 等持久状态 | 开源，Apache-2.0 OR MIT | 高：bounded turn、持久回写、独立验证最接近 |

选择这五个产品的理由：

```text
AWS、Temporal：证明确定性任务系统如何精确交付节点依赖。
A2A：证明 Session/Context、Task、Message、Artifact 不是同一个对象。
Magentic-One：证明开放目标确实需要运行期生成下一项工作和指令。
LoopX：证明长任务不能依赖无限 Session，需要有限工作包、持久回写和独立验证。
```

---

## 3. AWS Step Functions：用户预先定义 Workflow 和节点输入

### 3.1 用户视角

用户先写一份状态机：有哪些状态、从哪里分支、每个 Task 调哪个服务、输入从 Workflow State 的哪些字段取得、结果合并回哪里。

`Choice` 可以根据运行数据动态选择分支，但候选状态和每条分支规则仍由用户提前定义。它不是运行时让模型创造一个未知节点。

### 3.2 实际怎么 Run

```text
启动 Workflow，形成一份 Execution State
→ InputPath 从 State 中取一部分
→ Parameters 构造当前 Task 输入
→ Worker/Service 执行
→ ResultSelector 选择正式结果字段
→ ResultPath 把结果合并回 State
→ OutputPath 决定下一状态能看到什么
```

简化的原生 ASL 示例：

```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::lambda:invoke",
  "InputPath": "$.order",
  "Parameters": {
    "FunctionName": "risk-check",
    "Payload": {
      "order.$": "$",
      "ruleVersion.$": "$$.Execution.Input.ruleVersion"
    }
  },
  "ResultSelector": {
    "riskLevel.$": "$.Payload.riskLevel",
    "evidenceKey.$": "$.Payload.evidenceKey"
  },
  "ResultPath": "$.riskResult"
}
```

它表达的是：当前节点需要 `order + ruleVersion`，不是“只拿上一个节点输出”，也不是“把完整历史交给节点猜”。

### 3.3 I0—I4 等价映射

| 口径 | Step Functions 实际承载 |
|---|---|
| I0 | `StartExecution` 的 JSON Input |
| I1 | State Machine、Execution、当前 State；Callback 场景用不透明 Task Token 关联一次等待执行 |
| I2 | `InputPath + Parameters` 构造出的 Task Input |
| I3 | Task Result，经 `ResultSelector + ResultPath` 形成正式状态 |
| I4 | Retry/Catch、后续校验状态或业务服务；产品不强制统一 Verifier |

### 3.4 为什么它的协议和 Avernet 不完全一样

Step Functions 不需要在每次执行时上报“节点顺序”或让 Context 推断依赖，因为 Workflow 作者已经提前把节点和输入投影写进 ASL。运行时只需执行既定契约。

Avernet 的 Work 是 Planner 在运行期产生的，因此不能复制固定 ASL；但必须保留同一个原则：

> Planner 产生 Work 时，同时产出当前 Work 的 Required Inputs，而不是事后从全部历史猜。

### 3.5 对 Avernet 的直接依据

- `Task Token` 支持由控制面建立不透明 `execution_ref`，Worker/Bot 不必知道整个 Workflow；
- `Parameters` 支持 `required_inputs` 必须显式构造；
- `ResultSelector` 支持正式结果投影不能等同于原始 Session；
- Workflow Payload 有大小限制，支持大产物通过外部存储引用传递。

**开源情况：**AWS Step Functions 是非开源托管服务；Amazon States Language 和服务 API 公开。

---

## 4. Temporal：完整历史用于耐久恢复，Activity 输入仍由代码显式传递

### 4.1 用户视角

用户用 SDK 编写 Workflow 代码。Workflow 像一段可恢复的业务程序，通过函数参数调用 Activity；Activity Worker 只完成某个明确动作。

### 4.2 实际怎么 Run

```text
Workflow Execution 启动
→ Workflow 代码构造 Activity 参数
→ Temporal 调度 Activity Task
→ Activity Worker 执行并返回 Result 或 Failure
→ 事件写入 Event History
→ Workflow 重放历史恢复状态并继续调度
```

简化后的用户代码形态：

```python
identity = await execute_activity(check_identity, order_id)
risk = await execute_activity(
    check_risk,
    RiskInput(order_id=order_id, identity=identity, rule_version=rule_version),
)
```

第二个 Activity 能拿到 `identity`，不是因为 Temporal 自动从历史中检索相关信息，而是 Workflow 作者把它作为函数参数明确传入。

### 4.3 I0—I4 等价映射

| 口径 | Temporal 实际承载 |
|---|---|
| I0 | 启动 Workflow 的参数、Signal、Update 等 |
| I1 | Namespace/Workflow ID/Run ID、Activity Task、Activity Attempt |
| I2 | Workflow/Activity 函数参数 |
| I3 | Activity Result/Failure 写入 Event History |
| I4 | Retry Policy 和业务 Workflow 逻辑；独立业务验收需应用自己实现 |

### 4.4 为什么它不把 Event History 全部给 Activity

Event History 的主要消费者是 Temporal Runtime 和 Workflow Worker，用于 Replay、恢复和审计。Activity 的消费者需求是“完成当前动作”，所以只拿显式业务参数。

这证明：

```text
完整保存 ≠ 当前节点全量消费
Execution History ≠ Execution Context
```

### 4.5 对 Avernet 的直接依据

- Work、Attempt、Runtime Execution 应分层，重试不能覆盖旧失败；
- 新 Attempt 可以保持相同 Work，但拥有新的执行身份；
- 失败历史应保留，却不应自动全塞给重试 Runner；
- `Continue-As-New` 说明长历史和下一段 continuation state 应分离。

**开源地址：**https://github.com/temporalio/temporal （MIT）

---

## 5. A2A：Agent 边界协议，不负责 Agent 内部节点编排

### 5.1 用户视角

一个 Agent 客户端向另一个 Agent 发送 Message。简单请求可直接得到 Message；较长工作可形成 Task，客户端继续读取状态、历史和 Artifact。

### 5.2 实际怎么 Run

```text
客户端发送 Message
→ 服务端直接回复 Message，或创建 Task
→ Task 产生 status update / history
→ 正式结果以 Artifact 返回
```

A2A 将几个对象明确分开：

```text
contextId：一组连续交互的上下文范围；不等于 taskId。
taskId：一项长运行任务的身份。
Message：交互过程。
Artifact：任务正式产物。
history：Task 相关消息历史，可按需限制返回长度。
```

### 5.3 I0—I4 等价映射

| 口径 | A2A 实际承载 |
|---|---|
| I0 | JSON-RPC Message，Part 可承载文本、文件或结构化数据 |
| I1 | `taskId`、`contextId`、`referenceTaskIds` |
| I2 | 当前 Message、可选 Task history、Artifact；不定义对端内部 Work 依赖 |
| I3 | Task status update、Message、Artifact |
| I4 | 不规定对端 Agent 内部如何独立验收 |

### 5.4 为什么它不能直接替代 Avernet 协议

A2A 的边界是“Agent A 与 Agent B 怎么互操作”，而 Avernet 还要解决“Agent B 内部当前 Work 需要哪些历史结果”。因此 A2A 可以证明对象边界，却不能证明第 20 个 Work 的硬依赖选择算法。

### 5.5 对 Avernet 的直接依据

- Session/Context 与 Task 必须分开；同一 Session 可承载 Task 内执行，也可只是普通会话；
- Bot 聊天、协作群、BBS 原始过程不应强制填写 `task_id`；
- Message/history、Task 状态、正式 Artifact 不应混为一个 output 字符串；
- 大内容和正式产物应通过 ArtifactRef 消费。

**开源地址：**https://github.com/a2aproject/A2A （Apache-2.0）

---

## 6. AutoGen Magentic-One：运行期决定下一位 Agent 和下一条指令

### 6.1 用户视角

用户只提交一个开放任务，不需要提前定义完整 Workflow。Orchestrator 观察任务进展，在运行时选择下一位 Agent，并为它生成当前轮指令。

### 6.2 实际怎么 Run

```text
用户目标
→ Orchestrator 建立/更新 Task Ledger
→ 根据群聊和 Ledger 生成 Progress Ledger
→ 判断是否完成、是否卡住、是否有进展
→ 选择下一位 speaker
→ 为 speaker 生成本轮 instruction
→ Agent 输出进入 group chat
→ 再次判断
```

Progress Ledger 的核心不是某个固定字段名，而是结构化回答：

```text
用户目标是否已经满足？
是否陷入循环或停滞？
当前是否有进展？
下一位由谁执行？
下一位具体应做什么？
```

### 6.3 I0—I4 等价映射

| 口径 | Magentic-One 实际承载 |
|---|---|
| I0 | 用户任务 Message |
| I1 | Group Chat、Team Run 和 Orchestrator 当前选择；不是耐久 Workflow Node/Attempt 协议 |
| I2 | Task Ledger + Progress Ledger + 群聊历史 + 当前 instruction |
| I3 | Agent Message 写回 group chat |
| I4 | Orchestrator 判断是否完成；不是独立的强类型验收报告 |

### 6.4 它解决了什么，又缺什么

它证明动态任务确实存在：下一位 Agent 和下一条工作指令可以运行时生成，而非用户预定义 Workflow。

但其主要机制仍是让 LLM 阅读 Ledger/聊天并解释依赖。它没有天然保证：

- 某三个硬依赖结果必须全部出现；
- 失败属于哪个稳定 Attempt；
- 大 Artifact 是否以内联还是引用交付；
- 执行者声明完成后是否由独立 Verifier 验收。

### 6.5 对 Avernet 的直接依据

Magentic-One 支持 Avernet 的 `assignment` 由运行期 Planner 产生，但也暴露出仅靠 Ledger/聊天的边界：

> 动态 instruction 仍需配套强类型 Required Inputs 和 Output Contract，否则“动态”容易退化成“让模型从历史中猜”。

**开源地址：**https://github.com/microsoft/autogen （MIT）  
**实现目录：**`python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_magentic_one`

---

## 7. LoopX：长期 Goal、bounded turn、持久回写和独立验证

### 7.1 用户视角

用户给出一个可能跨很多轮、跨进程甚至跨不同 Agent Harness 的长期 Goal。用户不提前定义完整 Workflow；LoopX 控制每次 Agent 只完成一个有边界的工作切片，并把证据和下一步状态持久化。

### 7.2 `bounded turn` 到底是什么

它不是“选几个相关聊天 turn”，而是：

```text
每个周期只允许执行一个范围有限、可验证的工作切片；
本轮结束后必须形成证据、阻塞信息、handoff 或下一 Todo；
下一周期重新根据持久 Goal 状态决定是否继续。
```

它避免长期任务依赖一个无限增长、不可恢复的 Chat Session。

### 7.3 实际怎么 Run

```text
创建 Goal，并维护 Todo / Gate / Evidence / Quota
→ should-run 判断是否允许下一周期
→ 给 Agent 一个紧凑的本轮工作包
→ Agent 完成一个 bounded turn
→ 回写证据、结果、阻塞和 handoff
→ 独立 validator 检查
→ 刷新持久 Goal State
→ 继续下一周期或停止
```

需要注意：LoopX 官方提供的是 Goal 控制面、CLI/JSON 和状态对象，并不是一份可直接复制为 Avernet 的通用 Context 网络 Schema。下面是等价映射，不是声称 LoopX 原生字段与 Avernet 一致。

### 7.4 I0—I4 等价映射

| 口径 | LoopX 实际承载 |
|---|---|
| I0 | 创建 Goal、Todo、Gate 等控制面对象 |
| I1 | Goal、当前 Run/Cycle、Todo、Quota |
| I2 | 当前 Goal 状态、Todo/Gate、最新 Evidence/Handoff 组成的有限工作包 |
| I3 | Evidence、Run payload、blocker、handoff、durable writeback |
| I4 | independent validation，再刷新 active goal state |

### 7.5 与 Avernet 的相同点和差异

相同点：

- 下一步不是完整预定义 Workflow；
- 长期连续性属于持久 Goal/Task，不属于某个 Chat Session；
- 每次执行只拿有限工作包；
- 丰富证据按引用保存；
- 执行回写和独立验证分开。

差异：

- LoopX 强绑定 Goal/Todo/Gate/Quota 领域模型；Avernet 还需支持 Task、Session、协作空间等不同 Scope；
- LoopX 的工作包由自身控制面构造；Avernet 需要单独定义 Task 与 Context 的 `ExecutionContextSpec/ContextView` 边界；
- Avernet 需要明确 `required_inputs` 与 `optional_context`，保证 Task 硬依赖不被相关度算法淘汰；
- Avernet 已有 ECB，跨任务经验和规则应交给知识引擎治理，而不是塞进 Task 控制面。

**开源地址：**https://github.com/huangruiteng/loopx （Apache-2.0 OR MIT）

---

## 8. 横向对比：第 20 个 Work 到底拿什么

假设一个任务运行中动态产生了第 20 个 Work。它需要：

```text
硬依赖：Work 7 的身份结果、Work 12 的支付摘要、Work 19 的风险结果；
大产物：Work 19 的 200 KB 报告；
可选背景：当前 Session 中有关“最近三个月”的讨论；
可选知识：历史任务中相似缺数问题的处理经验。
```

| 产品 | 第 20 个执行者怎样拿到输入 | 是否自动决定硬依赖 | 为什么这样设计 |
|---|---|---:|---|
| AWS Step Functions | Workflow 作者用 JSONPath/Parameters 明确传 7、12、19 的结果 | 否 | 节点和依赖在设计期已知，不需要运行时猜 |
| Temporal | Workflow 代码把 7、12、19 的返回值作为 Activity 参数 | 否 | Workflow 代码就是依赖关系的权威定义 |
| A2A | 接收对方 Message/Task/Artifact；不理解对方内部的 7、12、19 | 不负责 | 它解决 Agent 边界互操作，不解决内部编排 |
| Magentic-One | Orchestrator 从 Ledger/聊天判断下一位 Agent及 instruction | 主要由 LLM 判断 | 开放任务无法预定义完整流程，但硬依赖保证较弱 |
| LoopX | 控制面从当前 Goal/Todo/Gate/Evidence 构造 bounded turn | 由控制面状态决定 | 长任务靠持久 continuation state，而不是重放完整会话 |
| **Avernet** | Planner 在 P2 声明 7、12、19 为 required；Context 必放摘要/结构化结果，200 KB 报告放 ArtifactRef，再按预算补 Session 片段和 ECB 知识 | **required 不猜；optional 可检索** | 同时保留 Workflow 的确定性和 Agent Loop 的运行期动态性 |

因此，Avernet 不是发明一种“万能相关度算法”，而是组合两种经过验证的机制：

```text
结构依赖负责完整性；
语义检索负责有限补充。
```

---

## 9. 从对标推导 Avernet 协议

### 9.1 协议总图

```text
                        ┌────────── Task / Goal Control Plane ──────────┐
用户目标 ──────────────▶│ Planner 产生 Work                              │
                        │ P1 ExecutionBinding                            │
                        │ P2 ExecutionContextSpec                        │
                        └─────────────┬──────────────────────▲───────────┘
                                      │                      │读取 P4 后决定
                                      ▼                      │accept/retry/replan
┌──────────────────────────── Context ────────────────────────────────────────────┐
│ Binding Resolver  →  Required Resolver  →  Optional Retriever  →  Budget/Pack   │
│                                                                                │
│ P3 Candidate Ledger  ◀──────── Runner/Agent                                    │
│ P4 Validation Ledger ◀──────── Verifier                                        │
│                                                                                │
│ 对 Runner/Planner/Verifier 输出 C1 ContextView                                  │
└──────────▲──────────────────┬───────────────────┬────────────────────▲──────────┘
           │                  │ C1                │ Candidate          │ KnowledgePort
 S1 SourceAccess              ▼                   ▼                    │
 Bot Session / 群 / BBS   Runner / Agent       Verifier                │
                                                     │                 │
                                                     └── P4 ───────────┘

Context ──K1 Case References──▶ ECB ──Published Knowledge──▶ Context
```

职责边界：

- Task/Goal 控制面决定**做什么、硬依赖什么、是否重试/接收**；
- Context 决定**如何从多个来源完整取硬依赖，并有限表达可选信息**；
- Runtime 执行并报告 Candidate，不自行宣布 Task 已验收；
- ECB 将完整 Case 提炼成跨任务可复用知识；
- 为当前 Token 预算做临时压缩属于 Context，跨 Case 提炼稳定经验/规则属于 ECB。

### 9.2 S1 SourceAccess：读取已有原始过程

Bot Session、协作群、BBS 已有权威存储，因此不要求业务再次上报全文。Context 通过 Adapter 增量读取统一投影：

```json
{
  "source_ref": {"type": "bot_session", "id": "session_9"},
  "cursor": "message_100",
  "limit": 100
}
```

```json
{
  "records": [
    {
      "record_id": "message_109",
      "occurred_at": "2026-09-10T10:38:00+08:00",
      "actor_ref": {"type": "bot", "id": "risk_bot"},
      "parts": [
        {"media_type": "text/plain", "text": "缺少最近三个月数据"}
      ],
      "relations": [
        {"type": "reply_to", "record_id": "message_101"}
      ]
    }
  ],
  "next_cursor": "message_109"
}
```

新来源只需增加 `source_ref.type + Adapter`。只有出现新的消费意图，才新增 Context View；不是每增加一种模态就修改协议 Core。

### 9.3 P1 ExecutionBinding：由知道归属的控制面建立

```json
{
  "binding_id": "binding_88",
  "execution_ref": "exec_88",
  "scope_ref": {
    "type": "avernet_task",
    "id": "task_1"
  },
  "work_ref": {
    "type": "task_unit",
    "id": "work_20"
  },
  "attempt_ref": {
    "type": "task_attempt",
    "id": "attempt_2"
  },
  "runtime_ref": {
    "type": "bot_session",
    "id": "session_9"
  }
}
```

核心约束：

- Bot 只需知道 Runtime Session 和不透明 `execution_ref`，不要求知道 Task/DAG；
- 中心化模式由 Task Runtime 建立 Binding；
- 分布式模式由父执行或调用方建立 Binding；
- `scope_ref.type` 可扩展为 Task、Goal、Session 或协作空间；
- `work_ref` 表示当前可执行工作，不等于必须存在 Graph Node。

### 9.4 P2 ExecutionContextSpec：动态 Work 的输入契约

```json
{
  "execution_ref": "exec_20",
  "mode": "retry",
  "assignment": {
    "goal": "完成交易对账风险分析",
    "instruction": "补齐最近三个月的数据验证",
    "constraints": [
      "不得修改已验收的支付入口结论"
    ],
    "acceptance": [
      "覆盖最近三个月交易",
      "结论必须附证据"
    ]
  },
  "required_inputs": [
    {"type": "accepted_result", "work_ref": {"type": "task_unit", "id": "work_7"}},
    {"type": "accepted_result", "work_ref": {"type": "task_unit", "id": "work_12"}},
    {"type": "accepted_result", "work_ref": {"type": "task_unit", "id": "work_19"}},
    {"type": "prior_failure", "attempt_ref": {"type": "task_attempt", "id": "attempt_1"}}
  ],
  "optional_context": {
    "source_scopes": [
      {"type": "bot_session", "id": "session_9"}
    ],
    "query": "最近三个月数据缺失及补齐方法",
    "include_published_knowledge": true
  },
  "output_contract": {
    "required_data": ["risk_count", "highest_level"],
    "evidence_required": true
  }
}
```

`mode` 不是让调用方自由拼大量 include 开关，而是选择稳定消费配方，例如：

```text
plan.initial     初次规划
plan.replan      缺口或失败后的重新规划
execute.prepare  首次执行
execute.retry    同一 Work 的新 Attempt
accept.read      精确验收 Candidate
inspect          审计和完整过程回看
```

不同消费位点需要不同内容，所以协议必须表达“本次准备什么上下文”，而不要求调用者知道自己是第几个节点。

### 9.5 C1 ContextView：Context 实际交付结果

```json
{
  "view_id": "view_20_2",
  "execution_ref": "exec_20",
  "assignment": {
    "goal": "完成交易对账风险分析",
    "instruction": "补齐最近三个月的数据验证"
  },
  "required_inputs": [
    {
      "ref": {"type": "accepted_result", "work_id": "work_7"},
      "digest": "身份核验通过",
      "data": {"identity_status": "verified"}
    }
  ],
  "optional_context": [
    {
      "source_ref": {"type": "bot_session", "id": "session_9"},
      "record_ids": ["message_101", "message_109"],
      "reason": "直接讨论最近三个月数据缺口"
    }
  ],
  "knowledge": [
    {
      "knowledge_ref": "knowledge_42",
      "digest": "历史缺数场景应先核验分区完整性，再决定回补"
    }
  ],
  "budget": {
    "used_tokens": 8400,
    "max_tokens": 12000
  },
  "completeness": {
    "required_complete": true,
    "missing": []
  },
  "omitted": [
    {
      "ref": "source_message_100",
      "reason": "low_relevance_after_required_inputs"
    }
  ]
}
```

Context 的核心处理顺序必须固定：

```text
1. 解析 Binding，确认 Scope/Work/Attempt；
2. 精确解析 Required Inputs，并做版本、验收状态和权限检查；
3. 缺硬依赖则返回 blocked，不能用相似内容替代；
4. 从允许的 Source、历史执行和 ECB 中生成 Optional 候选；
5. 按相关度、时效、来源可信度和预算选择；
6. 大内容降级为 digest + ArtifactRef；
7. 返回 completeness、provenance 和 omitted。
```

### 9.6 P3 ExecutionCandidateReport：执行者的结果声明

```json
{
  "candidate_id": "candidate_20_2",
  "execution_ref": "exec_20",
  "status": "succeeded",
  "finished_at": "2026-09-10T10:40:00+08:00",
  "result": {
    "digest": "发现 3 项高风险差异",
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

失败时使用同一外壳，但填写：

```json
{
  "status": "failed",
  "error": {
    "code": "DATA_PARTITION_MISSING",
    "message": "最近三个月分区不完整",
    "retryable": true
  },
  "partial_result": {
    "digest": "已完成前两个月核验"
  }
}
```

Runtime 上报的是正式结果投影，不是再次复制完整 Session。完整过程由 `runtime_ref/source_ref` 回读。

### 9.7 P4 ExecutionValidationReport：独立验收

```json
{
  "validation_id": "validation_20_2",
  "candidate_id": "candidate_20_2",
  "decision": "accepted",
  "checks": [
    {"name": "required_data_complete", "passed": true},
    {"name": "evidence_available", "passed": true},
    {"name": "acceptance_criteria_met", "passed": true}
  ],
  "feedback": []
}
```

Candidate 与 Validation 分开的原因很直接：执行者只能声明“我完成了”，不能同时作为 Task 的最终裁判。若拒绝，Validation feedback 进入下一次 `execute.retry` 或 `plan.replan` 的 Required Inputs。

### 9.8 K1 KnowledgeExtractionBundle：把完整 Case 交给 ECB

```json
{
  "case_id": "case_task_1",
  "scope_ref": {"type": "avernet_task", "id": "task_1"},
  "source_refs": [
    {"type": "bot_session", "id": "session_9"}
  ],
  "execution_refs": ["exec_7", "exec_12", "exec_19", "exec_20"],
  "candidate_refs": ["candidate_20_2"],
  "validation_refs": ["validation_20_2"],
  "extraction_intents": ["experience", "rule", "failure_pattern"]
}
```

Context 只提供 Case 边界和可追溯引用；ECB 负责抽取、去重、验证、版本、冲突和发布。ECB 发布后的知识再通过 KnowledgePort 进入 `optional_context`，不能替代当前 Task 的 Required Inputs。

---

## 10. 最终取舍

### 10.1 哪些设计可以直接定下来

| 决策 | 依据 |
|---|---|
| Bot 不填 `task_id`，由控制面建立 ExecutionBinding | AWS Task Token、Temporal Activity Task 都由知道编排关系的控制面关联 |
| Planner 产生 Work 时必须同步声明 Required Inputs | AWS Parameters、Temporal Activity 参数证明确定性依赖应由编排层显式交付 |
| Required 与 Optional 分开 | Workflow 的精确依赖与 Agent Memory/动态 Loop 的相关背景解决的是两类问题 |
| 完整历史保留但不默认进入 Runner | Temporal Event History、A2A history、LoopX rich evidence 都与当前执行包分离 |
| 正式 Result 与原始 Session 分开 | AWS ResultSelector、A2A Artifact、Temporal Activity Result |
| 大内容使用 ArtifactRef | AWS Payload 限制、A2A Artifact、LoopX rich evidence by reference |
| Candidate 与 Validation 分开 | LoopX independent validation；也符合执行者与验收者职责分离 |
| 跨任务知识由 ECB 处理 | Context 的临时压缩与长期知识治理目标不同 |

### 10.2 第一版不做什么

```text
不要求 Context 预先理解所有模态原生 Schema；新模态通过 Source Adapter 接入。
不把 Graph Node 设计成 Context Core 的固定对象。
不让语义 TopK 决定硬依赖。
不复制 Bot Session、协作群和 BBS 的全文存储。
不把当前 Token 压缩摘要直接当作已治理知识。
不声称任一业界产品可以原样复制；协议来自同类机制的组合验证。
```

### 10.3 可验证的不变量

```text
1. 每次 Runtime Outcome 都能通过 execution_ref 唯一找到 Binding。
2. Required Inputs 少一项，execute View 必须 blocked，不能静默补相似内容。
3. 重试创建新 Attempt/Execution，旧失败保留，不被新成功覆盖。
4. Verifier 只验收指定 candidate_id，不从“最新一条 Session 消息”猜结果。
5. 超预算只能裁剪 Optional；Required 必须保留或显式返回无法交付。
6. 任何被省略内容都有 ref 和 reason。
7. 大产物保留 ArtifactRef 和证据来源。
8. 新增来源模态只增加 Adapter，不修改 P1—P4 Core。
9. 知识可以补充当前执行，但不能替代当前任务已接受结果。
```

---

## 参考资料

### AWS Step Functions

- InputPath、Parameters、ResultSelector：https://docs.aws.amazon.com/step-functions/latest/dg/input-output-inputpath-params.html
- ResultPath：https://docs.aws.amazon.com/step-functions/latest/dg/input-output-resultpath.html
- Callback 与 Task Token：https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html
- Service quotas：https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html

### Temporal

- 开源仓库：https://github.com/temporalio/temporal
- Event History：https://docs.temporal.io/workflow-execution/event
- Continue-As-New：https://docs.temporal.io/workflow-execution/continue-as-new

### A2A

- 开源仓库：https://github.com/a2aproject/A2A
- Specification：https://a2a-protocol.org/latest/specification/

### Microsoft AutoGen / Magentic-One

- 开源仓库：https://github.com/microsoft/autogen
- Magentic-One 用户文档：https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html
- Orchestrator 实现：https://github.com/microsoft/autogen/tree/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_magentic_one

### LoopX

- 开源仓库与 README：https://github.com/huangruiteng/loopx

