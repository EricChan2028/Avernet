# Avernet Context 生产与消费协议设计

> 日期：2026-09-10  
> 状态：协议讨论稿  
> 范围：只讨论 Context 的生产协议、消费协议及其业界依据，不讨论模块部署和迁移计划。

---

## 1. 结论

Avernet Context 不应设计一个万能 `ContextEvent`，而应提供：

### 三类生产协议

```text
P1 Source Record
   Bot 会话、协作群、BBS 等原始内容进入 Context

P2 Execution Binding
   建立一次执行与 Task、父执行、实际会话之间的关系

P3 Execution Outcome
   上报一次执行的成功结果、失败原因和产物
```

### 一组消费协议

```text
C1 Context View
   ├── planning.initial
   ├── planning.replan
   ├── execution.initial
   ├── execution.retry
   └── execution.result
```

消费侧可以只有一个 API，但必须是**多个强类型 View 的联合协议**，不能是一个请求加大量 `include_xxx` 开关。

---

## 2. 设计原则

### 2.1 生产者只上报自己知道的事实

不能要求 Bot 上报：

```text
task_id
node_id
attempt
完整 DAG
全局因果关系
```

因为 Bot 通常只知道自己的 `session_id`。

不同主体分别生产自己权威的信息：

| 生产者 | 知道什么 | 应该生产什么 |
|---|---|---|
| Bot/群/BBS Adapter | 原始会话、消息、帖子和产物 | Source Record |
| Task Runtime/Runner | Task、执行单元、第几次尝试 | Execution Binding |
| 分布式调用方 | 谁调用了谁 | 父子 Execution Binding |
| Callback/Poller | 某次执行是否成功、返回什么 | Execution Outcome |
| Context | 已有关系和来源内容 | 不补造业务事实，只连接和投影视图 |

AWS Step Functions 和 Temporal 都采用类似原则：编排系统持有 Workflow 归属，Worker 只通过不透明 Task Token 回报本次执行结果，而不需要理解完整流程。

### 2.2 原始内容、执行关系、执行结果必须分开

三个对象解决不同问题：

```text
Source Record：
Bot/群/BBS 实际说了什么、发生了什么

Execution Binding：
这些内容和哪次执行、哪个 Task 有什么关系

Execution Outcome：
这次执行最终成功还是失败，交付了什么
```

不能因为拿到了 Bot Session，就认为它一定属于某个 Task；也不能因为有一条成功 Outcome，就用 Outcome 的简短摘要代替完整 Session。

A2A 同样将 Message、Task 状态和 Artifact 分开建模，Task 可以保留消息历史，但消息本身不等同于任务结果。

### 2.3 Task 是组织关系，不是所有内容的必填属性

内容可以先于 Task 存在：

```text
协作群先讨论
    → 后来形成 Task

BBS 先出现问题
    → 后来被 Task 接管

Bot 先进行探索会话
    → 后来被 Planner 引用
```

因此：

- Source Record 可以独立存在；
- Task Runtime 可以随后追加 Execution Binding；
- 不允许通过修改原始内容的方式“补 Task ID”；
- 同一群聊片段可以支持多个 Task；
- 同一个 Task 也可以关联多个 Bot、群和 BBS 来源。

### 2.4 执行身份必须稳定，重试不能覆盖历史

同一工作单元的两次尝试必须是两个 Execution：

```text
Task Unit B
    ├── exec_B_1：attempt 1，失败
    └── exec_B_2：attempt 2，成功
```

不能把 `exec_B_1` 改成成功，也不能让新的 Bot Session 覆盖旧 Session。

AWS 在 Callback Task 超时后会生成新的 Task Token；Temporal 也通过服务端执行历史管理 Activity 的执行与重试。这说明“工作单元没变”和“实际执行没变”是两个概念。

### 2.5 新增来源类型不应修改 Context 核心

新增一种来源时，例如未来增加：

```text
邮件线程
代码评审
会议纪要
工单系统
```

应当新增：

```text
Source Adapter
+ 该来源的 Payload Schema
+ 公共字段投影规则
```

不应修改 Execution Binding、Execution Outcome 或 Context View 的核心逻辑。

Context 核心只依赖：

```text
来源身份
记录身份
发生时间
可检索文本
Artifact 引用
记录间引用关系
```

来源特有语义留在 Payload 中。

### 2.6 先通过结构关系保证准确，再对可选内容做相关度排序

Context 不能直接：

```text
所有内容 → 向量检索 → TopK
```

正确顺序应当是：

```text
1. 根据 Execution/Task 确定范围
2. 根据消费 View 确定必须内容
3. 根据 Task 依赖关系找到硬依赖
4. 再召回可选历史和知识
5. 只对可选内容计算相关度
6. 最后进行预算控制
```

硬依赖、当前目标和失败原因不能因为“向量相似度不够高”而被删除。

---

## 3. 业界任务上下文如何处理

没有一个业界产品完整覆盖 Avernet，但可以按相似问题组合参考。

| 业界对象 | 与 Avernet 相似的部分 | 对协议的直接影响 |
|---|---|---|
| AWS Step Functions | 中心化任务编排、异步回调、节点输入选择 | Execution Token；执行结果回报；消费前选择输入 |
| Temporal | Workflow、Activity、Attempt、执行历史和重试 | Execution 与工作单元分离；重试不覆盖历史 |
| A2A | Agent Message、Task、Context、Artifact | 原始内容、任务状态和产物分开 |
| LangGraph | Node 消费 State、Thread Checkpoint、长期 Store | 按消费位置形成有限状态视图 |
| AutoGen Memory | 根据当前请求查询 Memory 并注入模型上下文 | 可选知识按当前意图召回 |
| LoopX | 长任务由持久控制状态驱动多个 bounded turn | 每轮只给继续当前工作的有限状态，而非完整历史 |

### 3.1 生产侧借鉴

AWS Callback Task 会向外部 Worker 传递 Task Token，外部完成后使用同一个 Token 回报成功或失败。Worker 不需要知道 Workflow Execution、状态机结构和下一个节点。

Temporal 的 Activity 完成协议同样以 Task Token 为核心，Workflow 和 Activity 的归属、重试与历史由服务端维护。

因此 Avernet 应采用：

```text
Runner/调用方创建 execution_id
Bot 只执行自己的 Session
Adapter 把 session_id 与 execution_id 建立绑定
Callback/Poller 使用 execution_id 回报结果
```

### 3.2 消费侧借鉴

AWS Step Functions 使用 `InputPath` 和 `Parameters` 选择传给当前 Task 的输入，通过 `ResultSelector` 选择结果中的有效部分；大型内容建议存到外部对象存储而不是在状态间直接传递。

LangGraph 将状态保存为 Thread Checkpoint，Node 消费由 State Schema 定义的输入，而不是天然读取全部历史状态。

AutoGen Memory 将“查询相关记忆”和“把记忆注入当前模型上下文”定义为明确操作，而不是默认把全部 Memory 加给 Agent。

LoopX 将目标、Gate、Todo、Evidence、Quota 和 Handoff 保留在持久控制面，每个 Agent Turn 只获得本轮有限工作所需的状态。它与 Avernet 的相似点是长任务推进，而不是协议字段完全相同。

由此得到消费协议的三个要求：

```text
按当前消费位置选择
区分必须内容与可选内容
对可选内容进行预算和相关度控制
```

---

## 4. 生产协议一：Source Record

### 4.1 目的

记录 Bot、协作群、BBS 的原始内容。

它不要求内容当前已经属于 Task。

### 4.2 接口

```http
POST /v1/context/source-records
```

### 4.3 公共结构

```json
{
  "source": {
    "type": "bot_session",
    "id": "session_9"
  },
  "record": {
    "id": "message_101",
    "occurred_at": "2026-09-10T10:21:00+08:00",
    "payload": {}
  }
}
```

核心字段：

| 字段 | 谁填写 | 含义 |
|---|---|---|
| `source.type` | Source Adapter | 由哪类来源产生 |
| `source.id` | Source Adapter | 来源中的稳定会话/线程身份 |
| `record.id` | Source Adapter | 来源内部的稳定记录身份，用于去重 |
| `occurred_at` | Source Adapter | 内容在来源系统实际发生的时间 |
| `payload` | Source Adapter | 由 `source.type` 决定具体 Schema |

不要求业务方填写：

```text
event_type
spec_version
schema_version
task_id
attempt_id
causation_id
```

API 版本由 `/v1` 表达；Payload Schema 由 `source.type` 决定。

### 4.4 单 Bot 会话

```json
{
  "source": {
    "type": "bot_session",
    "id": "session_9"
  },
  "record": {
    "id": "message_101",
    "occurred_at": "2026-09-10T10:21:00+08:00",
    "payload": {
      "role": "agent",
      "parts": [
        {
          "type": "text",
          "text": "支付链路存在重复扣款风险。"
        }
      ]
    }
  }
}
```

Bot Adapter 只需要理解 Bot Session，不需要知道它是否属于 Task。

### 4.5 协作群

```json
{
  "source": {
    "type": "group_thread",
    "id": "group_3:topic_12"
  },
  "record": {
    "id": "message_208",
    "occurred_at": "2026-09-10T10:25:00+08:00",
    "payload": {
      "sender": {
        "type": "bot",
        "id": "bot_B"
      },
      "reply_to": "message_205",
      "parts": [
        {
          "type": "text",
          "text": "方案一没有处理并发回调。"
        }
      ]
    }
  }
}
```

这里必须使用 `group_thread` 或其他有边界的讨论单元，不能简单把整个长期协作群绑定给一个 Task，否则群里不相关的历史都会成为候选上下文。

如果协作平台没有 Thread，Adapter 应创建一个内部讨论范围，例如：

```text
group_id + discussion_round_id
```

### 4.6 BBS

```json
{
  "source": {
    "type": "bbs_thread",
    "id": "topic_18"
  },
  "record": {
    "id": "post_33",
    "occurred_at": "2026-09-10T10:30:00+08:00",
    "payload": {
      "operation": "submit",
      "actor": {
        "type": "bot",
        "id": "bot_C"
      },
      "object": {
        "type": "proposal",
        "id": "proposal_7"
      },
      "parts": [
        {
          "type": "artifact_ref",
          "artifact_id": "artifact_42"
        }
      ]
    }
  }
}
```

BBS 的 `claim`、`handoff`、`submit` 是来源自身明确知道的操作，因此可以放在 BBS Payload 中；但 Context 不把这些操作强行推广成所有来源都必须理解的通用 `event_type`。

### 4.7 新来源如何接入

假设未来接入代码评审：

```json
{
  "source": {
    "type": "code_review",
    "id": "pr_3118"
  },
  "record": {
    "id": "comment_27",
    "occurred_at": "2026-09-10T11:00:00+08:00",
    "payload": {
      "reviewer": "user_8",
      "file": "service.py",
      "line": 81,
      "parts": [
        {
          "type": "text",
          "text": "此处缺少幂等保护。"
        }
      ]
    }
  }
}
```

新增的是：

```text
code_review Payload Schema
code_review Adapter
公共文本与 Artifact 投影
```

Execution 和消费协议不需要修改。

---

## 5. 生产协议二：Execution Binding

### 5.1 目的

将一次实际执行与 Task 或父执行建立关系，并关联实际来源。

### 5.2 接口

```http
PUT /v1/context/execution-bindings/{execution_id}
```

### 5.3 Task 场景

```json
{
  "execution_id": "exec_88",
  "owner": {
    "type": "task_unit",
    "task_id": "task_1",
    "unit_id": "node_20",
    "attempt": 2
  },
  "source": {
    "type": "bot_session",
    "id": "session_9"
  }
}
```

字段含义：

| 字段 | 谁填写 | 含义 |
|---|---|---|
| `execution_id` | Runner | 一次实际执行的稳定身份 |
| `owner.type` | Runner | 该执行由 Task 还是父执行组织 |
| `task_id` | Task Runtime | 所属 Task |
| `unit_id` | Task Runtime | Task 内的工作单元 |
| `attempt` | Task Runtime | 该工作单元第几次尝试 |
| `source` | Runtime Adapter | 实际承载此次执行的 Session/Thread |

实际时序：

```text
1. Runner 创建 exec_88
2. Runner 通过 Adapter 启动 Bot
3. Bot 平台返回 session_9
4. Adapter 建立 exec_88 ↔ session_9
5. Bot 后续仍然只使用 session_9
```

如果 Bot 启动前就失败，仍可直接给 `exec_88` 上报失败 Outcome，不需要虚构 `source`。

### 5.4 无中心 Task 的分布式调用

```json
{
  "execution_id": "exec_B_7",
  "owner": {
    "type": "parent_execution",
    "execution_id": "exec_A_3"
  },
  "source": {
    "type": "bot_session",
    "id": "session_B"
  }
}
```

此时 Context 只知道：

```text
exec_A_3 调用了 exec_B_7
```

不知道 Task 就不写 Task。

以后如果中心 Task 接管，可以再建立 Task 与这些 Execution 的关联，而不修改原始 Source Record。

### 5.5 关系基数

Binding 必须支持：

```text
一个 Execution → 多个 Source
```

例如：

```text
exec_88
  ├── bot_session/session_9
  ├── group_thread/group_3:topic_12
  └── artifact/artifact_42
```

也支持：

```text
一个 Source → 多个 Execution
```

例如同一个协作群讨论为多个 Task 提供证据。

因此 Binding 是关系记录，不是把 `task_id` 写回 Source 对象。

---

## 6. 生产协议三：Execution Outcome

### 6.1 目的

明确一次 Execution 的最终状态和交付内容。

Source Record 表达过程，Outcome 表达本次执行的结论。

### 6.2 接口

```http
POST /v1/context/execution-outcomes
```

### 6.3 成功

```json
{
  "outcome_id": "outcome_201",
  "execution_id": "exec_88",
  "status": "succeeded",
  "observed_at": "2026-09-10T10:40:00+08:00",
  "result": {
    "summary": "完成支付链路风险分析，识别出三个高风险点。",
    "artifact_refs": [
      {
        "artifact_id": "artifact_42",
        "name": "risk-report.md"
      }
    ],
    "evidence_refs": [
      {
        "source": {
          "type": "bot_session",
          "id": "session_9"
        },
        "record_ids": [
          "message_101",
          "message_109"
        ]
      }
    ]
  }
}
```

### 6.4 失败

```json
{
  "outcome_id": "outcome_202",
  "execution_id": "exec_88",
  "status": "failed",
  "observed_at": "2026-09-10T10:40:00+08:00",
  "error": {
    "code": "INSUFFICIENT_DATA",
    "message": "缺少最近三个月的交易数据。",
    "retryable": true
  }
}
```

约束：

```text
status=succeeded → result 必填
status=failed    → error 必填
```

`outcome_id` 用于处理 Callback/Poller 重复回投。

大型报告只能通过 `artifact_refs` 引用，不能作为无限制字符串进入 Outcome。

---

## 7. 消费协议：Context View

### 7.1 为什么是一个 API、多个 View

统一 API：

```http
POST /v1/context/views:resolve
```

但请求是强类型联合：

```text
planning.initial
planning.replan
execution.initial
execution.retry
execution.result
```

不能设计成：

```json
{
  "include_dependencies": true,
  "include_failures": true,
  "include_knowledge": true,
  "include_siblings": false
}
```

否则 Planner 和 Runner 必须理解 Context 内部的数据组织，Context 退化成通用查询接口。

### 7.2 首次规划

```json
{
  "view": "planning.initial",
  "target": {
    "task_id": "task_1"
  },
  "budget": {
    "max_tokens": 12000
  }
}
```

Context 配方：

```text
必须：
- 原始目标
- 验收要求
- 用户约束
- 当前可用能力

可选：
- 相关静态知识
- 相似任务经验
- 与目标直接相关的 Bot/群/BBS 背景
```

首次规划不需要失败历史，也不应因为未来可能重规划而提前读取完整 Graph。

### 7.3 重规划

```json
{
  "view": "planning.replan",
  "target": {
    "task_id": "task_1",
    "focus": {
      "type": "gap",
      "id": "gap_7"
    }
  },
  "budget": {
    "max_tokens": 12000
  }
}
```

Context 配方：

```text
必须：
- 原始目标和约束
- 当前 Gap
- 已经完成且仍有效的结果
- 导致 Gap 的失败证据
- 不允许破坏的已有结论

可选：
- 相似失败的历史经验
- 与 Gap 相关的群/BBS 讨论
- 可替代执行方案
```

### 7.4 首次执行

```json
{
  "view": "execution.initial",
  "target": {
    "execution_id": "exec_88"
  },
  "budget": {
    "max_tokens": 12000
  }
}
```

Context 通过 Binding 找到：

```text
exec_88
  → task_1
  → node_20
  → attempt 1
```

然后组装：

```text
必须：
- 当前工作单元目标
- 当前工作单元约束和验收条件
- 所有硬依赖的有效 Outcome
- 必须使用的 Artifact

可选：
- 相关兄弟结果
- 相关 Bot/群/BBS 讨论
- ECB 静态知识和跨 Task 经验
```

这里不需要请求方传“第几个节点”或完整 DAG。

### 7.5 重试执行

```json
{
  "view": "execution.retry",
  "target": {
    "execution_id": "exec_89"
  },
  "budget": {
    "max_tokens": 12000
  }
}
```

Context 通过 Binding 得知 `exec_89` 是同一工作单元的新 Attempt。

返回内容：

```text
首次执行的全部必须内容
+ 上次实际采用的方案
+ 上次明确失败原因
+ 外部反馈
+ 已产生且仍然有效的部分 Artifact
+ 本次不能重复的条件
+ 相关失败经验
```

不能返回整个旧 Session 代替失败反馈。完整 Session 保留为 Source/Artifact，View 中只放与本次重试直接有关的片段和引用。

### 7.6 读取执行结果

```json
{
  "view": "execution.result",
  "target": {
    "execution_id": "exec_88"
  }
}
```

用于 Task 的 Report & Acceptance。

该 View 不做语义 TopK，只返回：

```text
指定 Execution 的 Outcome
结果 Artifact
明确引用的证据
来源和版本
```

这是精确读取，不是模型 Prompt 组装，所以不要求 `max_tokens`；大型内容继续通过 ArtifactRef 返回。

---

## 8. 消费响应

不同 View 的内容结构不同，但响应外壳统一：

```json
{
  "view_id": "view_301",
  "view": "execution.retry",
  "context_version": "ctx_25",
  "content": {
    "goal": [],
    "constraints": [],
    "dependencies": [],
    "prior_attempt": [],
    "knowledge": [],
    "artifacts": []
  },
  "provenance": [],
  "omitted": [],
  "completeness": {
    "required_complete": true
  }
}
```

核心字段：

| 字段 | 作用 |
|---|---|
| `view_id` | 标识本次实际交付的 Context |
| `context_version` | 说明此次 View 基于哪个事实版本 |
| `content` | 按具体 View Schema 组织的内容 |
| `provenance` | 每项内容来自哪个 Task Fact、Source Record、Outcome 或 Knowledge |
| `omitted` | 哪些候选没有进入、为什么 |
| `required_complete` | 所有必须内容是否完整 |

---

## 9. Context View 的确定性处理流程

```text
第一步：解析执行范围
Execution Binding / Task Port
→ 找到 Task、工作单元、Attempt 和依赖结构

第二步：执行 View 配方
view
→ 确定 required / optional / excluded

第三步：保护必须内容
目标、约束、硬依赖、指定失败原因
→ 不参与语义淘汰

第四步：召回可选内容
Source Record + ECB Knowledge
→ 形成候选集合

第五步：选择可选内容
适用范围 + 相关度 + 时效 + 可信度
→ 排序和去重

第六步：决定交付形式
inline → snippet → summary → artifact_ref
→ 在预算内降级

第七步：生成解释
provenance + omitted + completeness
```

如果所有必须内容本身已经超过预算，Context 不应静默删除硬依赖，而应返回：

```json
{
  "completeness": {
    "required_complete": false,
    "reason": "required_content_exceeds_budget"
  }
}
```

由 Runner 选择扩大预算、读取 Artifact 或拆分工作，不能伪装成完整上下文。

---

## 10. 多来源场景验证

| 场景 | Source Record | Binding | Outcome | 消费方式 |
|---|---|---|---|---|
| Bot Session 独立聊天 | 直接保存 | 暂无 | 可无 | 后续规划时按相关度引用 |
| Task 派发 Bot | 保存 Session | `execution ↔ task unit ↔ session` | 成功/失败 | `execution.initial/retry/result` |
| 协作群共同完成节点 | 保存有边界的 Group Thread | 一个 Execution 关联 Group Thread | 群决议或交付结果 | 提取相关消息和 Artifact |
| 一个群讨论支持多个 Task | 保存一份 | 多条 Binding | 各 Execution 独立 Outcome | 每个 Task 得到自己的 View |
| BBS 先有提案、后建 Task | 先保存 BBS Thread | Task 建立后追加 Binding | 执行后产生 | 初始规划读取相关提案 |
| Bot A 调用 Bot B，无中心 Task | 两个 Session | `exec_A → exec_B` | 分别产生 | 先按调用链消费，之后可再挂 Task |
| 同一节点失败后重试 | 两个 Session | 两个 Execution、不同 Attempt | 一失败一成功 | Retry View 读取失败，Result View 精确读取新结果 |
| 完整 Session 交给 ECB | Session/Artifact 保留 | 保留 Task/Execution 来源 | 不要求变成 Outcome | ECB 抽取跨 Task 知识，Context 后续召回 |

---

## 11. 协议不变量

协议是否成立，不以 JSON 是否好看判断，而以这些不变量判断：

1. **Bot 不知道 Task，也能完成上报。**
2. **Source 内容可以在 Task 之前存在。**
3. **Task 可以随后建立关系，而不修改原始内容。**
4. **一次重试一定产生新的 Execution，不覆盖旧 Attempt。**
5. **任意 Outcome 都能唯一定位到一次 Execution。**
6. **任意 Task 内 Execution 都能由 Task Runtime 定位到工作单元和 Attempt。**
7. **大型内容只通过 ArtifactRef 进入消费链路。**
8. **硬依赖不参与相关度淘汰。**
9. **摘要必须保留原始 Source/Artifact 引用。**
10. **消费结果必须说明来源、省略和必需项完整性。**
11. **新增来源只增加 Adapter 和 Payload Schema，不修改核心消费协议。**
12. **Context 不复制并维护第二份 Task DAG，任务结构以 Task 系统为权威。**

最终形成的协议闭环是：

```text
Source Record
    记录真实发生的内容
        ↓
Execution Binding
    建立任务/调用关系
        ↓
Execution Outcome
    表达一次执行的结论
        ↓
Context View
    面向当前消费位点生成有限、准确、可解释的上下文
```

---

## 参考资料

- AWS Step Functions：Callback with Task Token；Input/Output Processing
- Temporal API：Activity Task、Task Token、Activity Completion
- A2A Protocol：Message、Task、Context、Artifact、Task History
- LangGraph：State、Thread、Checkpoint、Store
- AutoGen：Memory Query 与 Context Update
- LoopX：Control Plane、Bounded Turn、Evidence 与 Handoff
