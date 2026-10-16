# Avernet Context Center：BBS、Task 与落地方案

> 日期：2026-09-14  
> 状态：阶段性方案基线（已收敛总体架构，未冻结具体实现）  
> 范围：明确 Avernet 选择 Context Center 作为总体架构，并说明 BBS、Task、LoopX 在其中的关系、机制边界和第一阶段落地方式。本文不冻结具体 API、存储或模型算法。

## 1. 结论先行

当前 BBS 偏向以 **Task** 为入口：任务执行遇到缺口、失败或无法继续时，把剩余目标放到 BBS，其他 Bot 竞价或接力完成。

本项目的总体选择是 **Context Center**，而不是再建设一套与 Task Center 并列的模式。Context Center 的核心对象不是“更大的 Task”，而是一个持续维护的**问题状态**：

```text
Context：一个需要持续推进的问题单元。它把问题、证据、判断、争议和后续行动放在同一条可追溯主线上；新信息进入后，主线可能被补充、修正或推翻。
Task：已经明确的一项行动，定义输入、执行动作、交付物和验收标准。
Contribution：Bot 对 Context 或 Task 带来的可追溯增量。
```

核心变化是对象关系反过来：

```text
Task-driven：Task → 执行/失败 → BBS 接力
Context-centric：Context → 暴露缺口 → Bot 自主贡献 → 必要时派生 Task → 结果回写 Context
```

因此，不能只把 `Task` 改名为 `Context`。如果每次都从一个固定命题出发、要求 Bot 交付固定结果、最后按验收标准关闭，那么它仍然是 Task。

**重要收敛：Task Center 不是本方案的另一种产品选择。** Task、BBS 和 LoopX 都保留，但作为 Context Center 内部的不同子机制：Context 负责问题状态，BBS 负责主动获取 Agent 协作，Task/LoopX 负责明确行动的执行。

## 2. 方案选择与概念边界

本文中的“Task-driven”和“Context-centric”是用于分析演进差异的两种工作方式，**不是要建设的两套并列产品**。我们的选择只有一个：

```text
Context Center
├── Context / Problem Workspace：维护问题解决主线及其演进
├── BBS / Opportunity：让 Agent 主动选择参与方式
└── Task / LoopX：执行已经明确的行动
```

需要同时区分两个容易混淆的维度：

1. **工作对象**：是已经定义好的 Task，还是需要持续理解和收敛的 Context；
2. **参与方式**：是中心 Push 分配，还是 Agent Pull 主动领取/参与。

BBS 的核心是 Pull：中心发布机会，Agent 自主决定是否参与；但这不意味着中心完全没有调度。中心仍负责机会发布、能力/权限检查、预算预留、领取冲突处理、验收和结算。一个主动领取的 Task BBS 仍可能是 Task-centric，不能仅凭“Agent 主动领取”判断它已经是 Context-centric。

两种工作方式的关系应理解为：

```text
Context Center 是总体架构选择
Task-driven / Context-centric 是其中的工作模式
Task / BBS / LoopX 是其中的机制组件
```

| 维度 | Task-driven BBS | Context-centric BBS |
|---|---|---|
| 核心对象 | 一次有明确目标和交付物的 Task | 围绕问题持续积累和修订的共享工作档案 Context |
| BBS 的触发 | Task 做不下去、出现 Gap 或需要接力 | Context 出现信息缺口、错误、冲突、低置信度或新事件 |
| Bot 的问题 | “我能为这个 Task 完成什么？” | “我能让这个 Context 变得更准确或更可行动吗？” |
| 贡献范围 | 主要是 Task 的执行结果 | 事实、证据、反例、判断、候选方案、实验结果，也可以是执行结果 |
| Task 是否必要 | 是，贡献最终服务于 Task 交付 | 不一定；只有需要明确执行和验收时才派生 Task |
| 结束条件 | 交付物满足验收标准，Task DONE | 某个问题满足闭合标准；Context 整体可继续存在并产生新议题 |
| 中心主要职责 | 管任务状态、抢占、接力和交付收口 | 管问题状态、信号、贡献收敛、闭合和必要的 Task 派生 |

### 2.1 Context 与 Task 的重叠边界

这里不是要说“放进 Context 的内容就不应该是 Task”，而是要区分两件事：

```text
Context：问题的共享工作档案
Task：档案中已经明确下来的一项行动
```

例如：

```text
Context：Avernet BBS 如何激励 Agent 参与协作

其中可以包含：
- 当前已有的激励假设；
- 已观察到的 Agent 行为；
- 不同方案的争议；
- 尚未验证的问题；
- 已经决定要做的具体动作。
```

当其中一项动作被明确为：

```text
Task：实现“贡献提交接口”，补充单元测试，并满足指定验收条件。
```

它仍然是普通 Task。**Context 不会改变 Task 的执行语义，也不会让 Task 自动变成另一种对象。**

Context-centric 的变化只体现在 Task 的前后关系：

```text
Task-centric：先有 Task，Task 失败后才把问题带到 BBS。
Context-centric：Task 是 Context 中某个已明确行动的派生物，Task 的结果还要回写 Context。
```

如果一个所谓 Context 只有：

```text
一个固定目标 + 一个固定交付物 + 一组固定验收标准
```

并且没有持续积累、冲突、未决事项或后续关联，那么它没有必要单独建 Context，直接建 Task 更准确。

### 2.2 更接近 Context-centric 的例子

```text
Context：Avernet 中 Agent 如何参与协作

当前事实：
- Agent 只有在被调用并获得预算后才运行；
- 当前 BBS 主要在 Task 无法继续时触发；

未解决问题：
- 没有直接完成 Task 的贡献如何评价？
- 多条相互冲突的判断如何收敛？
- 通知 Bot 参与是否会退化成任务指派？

Bot 可以：
- 补充案例或证据；
- 指出一个反例；
- 质疑已有结论；
- 提出实验；
- 判断当前不需要行动；
- 在问题已经明确且需要执行时，派生一个 Task。
```

这里 Bot 不一定要“接下一个任务”，也不一定要提交最终方案；它的贡献首先改变 Context 的状态。

## 3. Context 的定义与边界

### 3.1 用一句话定义 Context

**Context 是一个需要持续推进的问题单元，不是一次要执行的动作。**

它把下面几类内容放在同一条可追溯主线上：

```text
问题：我们要理解或解决什么？
证据：已经拿到了哪些日志、文档、实验结果或案例？
判断：根据这些证据，目前倾向于什么结论？
争议：哪些证据或判断互相冲突？
未决：还缺什么，哪些结论还不能确认？
行动：为了继续推进，已经派生了哪些 Task？结果是什么？
```

新材料进入后，Context 不是简单追加文本，而是要回答：

```text
它支持了什么？推翻了什么？新增了什么缺口？
是否改变当前判断？是否让下一项行动更明确？
```

因此，Context 的核心不是“保存更多上下文”，而是**保存问题如何被逐步理解、验证和收敛的过程**。

### 3.2 Context 与 Task 的最短对照

```text
Task：已经知道要做什么，组织一次执行。
      输入 → 动作 → 交付物 → 验收

Context：还需要持续推进一个问题，维护问题的演进。
         问题 → 证据/判断/争议/未决 → 新材料 → 修正判断或产生 Task
```

Context 不要求“下一步一定未知”。即使当前知道下一步，也可以在 Context 中派生 Task；区别在于，Task 完成后结果仍可能改变这条问题主线。

### 3.3 什么时候不建 Context

用户提出的判断基本正确，但需要把条件说完整：如果一次行动本身就能闭合问题，并且没有以下任一情况，就不应为了体现 Context-centric 而强行创建 Context：

- 后续行动依赖本次积累的证据或判断；
- 多个 Agent 需要共享并持续修订同一份问题理解；
- 存在冲突、反例、低置信度或尚未确认的假设；
- 本次结果可能引出新的验证方向或关联行动。

此时直接创建 Task 更准确。反过来，**只要问题不会因一个 Task 的完成而结束，或者后续 Agent 必须继承前面的判断和争议，就有建立 Context 的理由。**

Context 至少需要持续维护以下内容：

```text
问题：要解决或理解的对象
材料：已经收集到的日志、文档、结果、案例等
判断：基于材料形成的暂时结论或假设
不确定性：冲突、反例、证据不足和未决问题
行动：已经派生的 Task 及其结果
变化：新材料进入后，问题理解发生了什么变化
```

因此，Context 的判断标准不是“字段够不够多”，而是：

```text
Task 完成后，问题是否还需要继续保留这条主线？
后续 Task 或 Agent 是否需要看到前面形成的判断、证据和争议？
新贡献是否可能改变后续行动？
```

如果答案都是“否”，那就不需要 Context，直接使用 Task 更准确。

Context 不是所有历史消息的堆积，也不是无边界的共享记忆。它必须有主题、范围、权限和生命周期规则。

### 3.4 Context 不是什么

Context 不直接替代：

- **Task Engine**：不负责执行 Task 的状态迁移、重试和派发；
- **Planner**：不预先为每个 Bot 指定必须完成的方案；
- **知识库**：未经验证的运行事实不自动升级为跨 Task 的长期知识；
- **聊天记录**：原始贡献需要保留，但不等于规范状态；
- **万能决策模型**：不能只读一遍所有贡献就无依据地选出唯一真相。

## 4. Context 中心需要承担的职责

Context-centric 不等于去中心化。中心必须承担治理职责，否则只会得到一个不断追加回复的论坛。

### 4.1 状态管理

中心维护 Context 的规范状态和版本：

```text
facts              已确认事实
claims             判断或假设
evidence           事实/判断的证据引用
open_questions     未解决问题
conflicts          冲突事实或结论
candidate_options  候选方案
related_tasks      关联 Task
version            当前 Context 版本
```

原始贡献不能被规范状态覆盖。规范状态中的每一项都应能追溯到贡献、证据和导致变更的事件。

### 4.2 信号检测

中心从新事实和当前状态中识别需要关注的信号，例如：

- 信息缺口：当前结论缺少关键证据；
- 事实冲突：新结果与现有事实不一致；
- 结论冲突：不同方案或判断无法同时成立；
- 低置信度：依据不足但被频繁使用；
- 过期：事实或结论超过有效期；
- 重复：新建 Context 可能与已有 Context 讨论同一问题；
- 状态变化：关联 Task 完成、失败或产生新结果。

中心识别的是“哪里需要关注、缺什么、冲突是什么”，不是在没有贡献和证据时凭空制造业务方案。

### 4.3 机会发布

中心把信号转成可参与的议题或通知，例如：

```text
问题：方案 A 的可行性与最新压测结果冲突
需要补充：复现实验、适用条件或反例
闭合条件：得到可复现证据，或明确方案 A 的适用边界
```

机会发布只负责让问题可见，不应默认指定唯一 Bot 或唯一解法：

```text
通知：你可能对这个问题有价值，是否参与由 Bot 决定
指派：你必须负责验证并提交某个方案
```

前者仍是 BBS；后者属于 Planner/调度器行为。

### 4.4 贡献收敛

多个 Bot 的贡献不能全部直接写入 Context 的当前规范状态，也不能简单按数量投票。中心需要完成：

1. 接收并保留原始贡献；
2. 判断贡献属于事实、证据、假设、候选方案、反例还是问题；
3. 对重复内容去重并合并来源；
4. 对同一方向的贡献提炼共同判断，同时保留不同适用条件；
5. 对真正冲突的内容建立显式冲突项，不抹平差异；
6. 根据证据、来源、复现情况和有效范围更新置信度；
7. 生成新的 Context 版本和变更说明。

典型规则：

| 贡献关系 | 规范化处理 |
|---|---|
| 相同事实、不同来源 | 合并为一条事实，保留全部来源 |
| 同一判断、不同证据 | 保留一个判断，挂接多个证据 |
| 同一目标、不同方案 | 提炼共同目标，保留多个候选方案 |
| 结论在不同条件下成立 | 合并为带适用条件的结论 |
| 事实或结论互相矛盾 | 建立 `conflict`，等待验证或裁决 |
| 只有观点、没有证据 | 保留为 `claim/hypothesis`，不升级为事实 |

因此，“有规则地合并”是 Context 中心的必要职责；“强行合并”则是没有证据地抹平冲突、删除少数意见或把多个问题揉成一个。

### 4.5 闭合判断

Context 也需要验收/闭合标准，但验收对象不同：

```text
Task 验收：交付物是否符合预先定义的要求？
Context 闭合：关于这个问题的证据和决策是否达到预先定义的充分程度？
```

Context 可以给每个议题定义闭合标准，例如：

```text
问题：方案 A 是否适用于高并发？
闭合标准：
- 完成指定测试；
- 结果可复现；
- 明确适用边界；
- 剩余风险已记录；
- Owner 对是否采用作出决策。
```

闭合不一定意味着得到绝对真理，也可以是：

- 已验证并形成结论；
- 已作出决策并记录适用范围；
- 证据不足，明确标记为已知风险并暂不行动；
- 当前无法解决，但原因、影响和下一步已明确。

整个 Context 可以长期存在，但其中的议题必须能够独立进入 `open → investigating → proposed → resolved/accepted` 等状态。

### 4.6 Task 派生与结果回写

Context 中心在满足“需要具体行动”的条件时派生 Task，而不是一发现缺口就自动拆任务：

```text
Context 缺口
  ↓ 问题已经足够明确，且需要外部执行/验证
派生 Task（含目标、交付物、验收标准）
  ↓ Task Engine 执行和验收
Task 结果作为事实回写 Context
  ↓ 更新结论、关闭议题或暴露新缺口
```

Task 的验收标准仍然保留，并且是 Context 获得可靠证据的重要来源；Context 不应绕过 Task Engine 直接修改 Task 的运行状态。

## 5. 中心决策与 Bot 自主性的边界

中心会决策，但决策不是单次模型摘要后拍板，而是一个可追溯的收敛过程：

```text
贡献进入
  ↓
规则校验、分类、去重
  ↓
证据关联、冲突检测
  ↓
形成候选状态
  ↓
验证、评审或 Owner 裁决（按风险分级）
  ↓
检查闭合标准
  ↓
写入 Context 新版本
```

中心可以决定：

- 当前有哪些事实、缺口、冲突和议题；
- 一条贡献以什么类型和置信度进入规范视图；
- 哪些重复内容可归并；
- 一个议题是否满足闭合条件；
- 是否需要派生 Task 或升级人工/评审。

中心不应默认决定：

- 在贡献产生前由哪个 Bot 参与；
- 每个 Bot 必须采用什么思路；
- 证据不足时哪个方案“天然正确”；
- 为了看起来有进展而删除冲突、少数意见或原始来源。

这不是取消中心决策，而是把中心决策变成**有规则、有证据、有来源、可反驳、可回滚**的决策。

## 6. 一个完整的 Context-centric BBS 流程

```text
1. Context 持续保存当前问题状态
2. 新事实、Task 结果或外部事件进入 Context
3. Context 中心检测缺口、错误、冲突、过期或重复
4. 中心发布议题/通知，说明“缺什么”和闭合条件
5. 相关 Bot 自主判断是否参与、从什么角度贡献
6. 中心保留原始贡献，并按规则归类、去重、关联证据
7. 需要时进行复现、评审或 Owner 裁决
8. 更新 Context 版本和议题状态
9. 若需要具体执行，派生 Task；Task 结果再次回写 Context
```

与当前 Task-driven BBS 的直接区别是：

```text
当前：Task 失败/遇到 Gap 才进入 BBS，目标是接力完成 Task
目标：Context 持续存在，Task 结果和非 Task 贡献都能更新它
```

## 7. Context 合并：必要，但不是默认的大合并

长期存在不意味着所有 Context 必须合并。建议区分三种关系：

```text
同一 Context：版本持续演进
相关 Context：建立引用、依赖或关联
重复 Context：经审查后受控合并
```

判断合并时至少要检查：主题范围、权限边界、事实来源、有效期、冲突结论和历史可追溯性。第一版可以先支持“关联 + 合并候选”，把真正合并交给 Owner 或专门的 Consolidator，避免形成一个无边界的超级 Context。

## 8. 方案收敛：我们建设的是 Context Center

### 8.1 不是两套并列方案

本阶段不在以下两者之间二选一：

```text
Task Center 或 Context Center
```

而是建设：

```text
Context Center
  ├── Context：问题状态的规范来源
  ├── BBS：Context 暴露 Gap 后的主动协作入口
  ├── Task：Context 中已明确的一项行动承诺
  └── LoopX：Task/Goal 的执行控制器
```

Task Center 的能力不会被删除，而是成为 Context Center 的执行子平面。对于目标、方案、执行者和验收标准都已经明确的问题，Context Center 可以直接派生 Task 并交给 LoopX；只有在认知、执行供给或资源上存在缺口时，才需要通过 BBS 获取外部 Agent 的贡献。

### 8.2 “中心”与“主动参与”的边界

Context Center 的中心不是传统意义上逐个指定 Agent 的调度器，而是问题状态和协作规则的治理者。中心负责：

- 维护 Context 的事实、证据、判断、冲突、Gap 和版本；
- 从 Context 中识别缺口，并将缺口转换为边界明确的 Opportunity；
- 规定允许的贡献类型、输出格式、权限范围、验收条件和预算；
- 对贡献进行分类、去重、关联证据、冲突检测和风险分级；
- 决定贡献是否进入规范 Context、是否产生 Decision 或派生 Task；
- 处理领取冲突、预算预留、验收、奖励和信誉更新。

Agent 自主决定：

- 是否订阅或领取 Opportunity；
- 投入多少成本；
- 以证据、反例、诊断、方案、验证还是执行结果的方式参与；
- 是否继续投入或主动退出。

因此，设计原则是：

```text
中心约束问题边界和验收条件，
Agent 自主选择参与方式和实现路径。
```

### 8.3 BBS 中的 Opportunity 不是第二套对象体系

BBS 对外发布的统一对象是 `Opportunity`，其来源始终是 Context 中的某个 `Gap`：

```text
Context → Gap → Opportunity → Contribution/Execution → Verification → Context Update
```

根据 Gap 类型，Opportunity 可以要求不同输出，但不形成两套系统：

| Gap 类型 | Opportunity 要求 | 结果 |
|---|---|---|
| 认知缺口 | 证据、反例、诊断、候选方案 | 更新 Context 判断或产生验证 Task |
| 验证缺口 | 复现、实验、评审 | 更新证据和置信度 |
| 执行供给缺口 | 领取明确行动并交付结果 | 绑定/派生 Task，由 LoopX 执行 |
| 资源/权限缺口 | 提供数据、工具、权限或审批 | 解除阻塞并更新 Context |

“认知型 BBS”和“执行型 BBS”只是 Opportunity 类型，不是 Context Center 与 Task Center 的二选一。执行型 Opportunity 仍然属于 Context Center，因为“缺少合适执行者”本身就是 Context 的一个执行缺口。

## 9. 第一阶段如何落地：受约束的 Context 协作闭环

第一阶段不做“所有 Agent 看到所有 Context 后自由发挥”的开放市场，而做一个边界明确、可验收的垂直闭环：

```text
1. 创建或持续维护一个 Context
2. 从 Context 识别一个明确 Gap
3. 为 Gap 发布一个 Opportunity
4. 相关 Agent 自主领取或提交 Contribution
5. 中心按结构化协议接收并验证
6. 更新 Context 版本，关闭 Gap 或产生新 Gap
7. 必要时派生 Task，交给现有 Task/LoopX 执行
8. Task 结果回写 Context，并完成奖励/信誉结算
```

### 9.1 最小对象

MVP 至少需要以下对象：

```text
Context
  问题状态和规范视图

Gap
  Context 中尚未解决的认知、执行或资源缺口

Opportunity
  面向 Agent 的有边界协作机会，包含参与要求和验收条件

Contribution
  Agent 提交的证据、判断、方案、验证结果或执行结果

Verification
  对 Contribution 的相关性、可信度、复现性和影响进行验证

Task
  从 Context 中派生的明确行动，继续使用现有 Task/LoopX 机制
```

### 9.2 Opportunity 必须有边界

每个 Opportunity 至少要明确：

```text
context_id / gap_id
需要解决的缺口
允许的贡献类型
输入和权限范围
期望输出
验收或闭合条件
所需能力
预算/悬赏
有效期
最大参与人数或领取策略
```

例如，认知机会不能只写：

```text
请大家帮忙看看这个问题。
```

而应写成：

```text
缺口：确认渠道交易号是否能作为稳定幂等键
允许输出：证据、反例、历史案例、复现方案
闭合条件：得到可复现证据，或明确其适用边界
```

执行机会则应明确：

```text
缺口：已有改造方案，但没有合适执行者
输出：代码提交和回归测试
验收：测试通过且不改变已有支付语义
```

### 9.3 Agent 主动参与的状态机

```text
Opportunity Published
  → Agent Discovered
  → Agent Opted-in / Claimed
  → Budget Reserved
  → Contributing / Executing
  → Submitted
  → Verified
  → Accepted / Partial / Rejected
  → Context Updated
  → Reward Settled / Reputation Updated
```

领取不代表贡献有效：

```text
Claimed ≠ Accepted
Submitted ≠ Verified
Task Done ≠ Context Resolved
```

### 9.4 受约束自治，而不是自由发挥

为避免噪声和刷贡献，第一阶段应采用以下约束：

- Agent 不能直接修改规范 Context，只能提交原始 Contribution；
- Contribution 必须声明类型、依据、目标 Gap 和置信度；
- 事实必须有来源，观点不得自动升级为事实；
- 高风险结论需要复现、独立评审或 Owner 裁决；
- 相互冲突的贡献必须保留为 Conflict，不得按数量强行投票抹平；
- Opportunity 有范围、预算、有效期和最大参与数；
- Agent 可以忽略或拒绝低价值机会，不因拒绝直接扣信誉；
- 中心只在满足规则和闭合条件时更新规范 Context。

### 9.5 第一条可验证链路

建议先实现以下真实链路，而不是先做复杂积分市场：

```text
Task 执行失败
  → 失败结果回写 Context
  → 产生一个 diagnosis Gap
  → 发布诊断 Opportunity
  → Agent 主动提交证据/反例/复现结果
  → 验证并更新 Context
  → 形成 Decision
  → 派生执行 Task
  → Agent 主动领取 Execution Opportunity
  → LoopX 执行并验收
  → 结果回写 Context
```

这一链路同时验证 Context Center 的两个关键能力：

1. 在中心不知道下一步时，能否通过 Agent 贡献推动认知收敛；
2. 在中心知道下一步但缺少执行者时，能否通过主动领取获得执行供给。

## 10. Context Center 与 Task/LoopX 的边界

### 10.1 总体关系

本方案的总体选择是 **Context Center**。Task/LoopX 不是另一套待选择的架构，而是 Context Center 内部负责明确行动执行的子平面：

```text
Context Center
  ├── Context / Problem Plane：维护问题状态
  ├── BBS / Opportunity Plane：获取 Agent 的主动协作
  └── Task / LoopX Plane：执行明确行动
```

三者分别回答：

```text
Context：当前问题是什么，知道什么，还缺什么？
BBS：哪些 Agent 愿意以什么方式参与？
Task/LoopX：已经决定的行动如何可靠完成？
```

### 10.2 不再使用“是否知道下一步”做绝对边界

“如果没有 Agent 主动贡献，中心是否已经知道下一步”是一个有帮助的诊断问题，但不是系统边界。它只能帮助判断当前更需要哪类协作：

- 如果下一步已经明确，通常可以直接派生 Task；
- 如果下一步不明确，需要发布认知型 Opportunity；
- 如果下一步明确但缺少执行者，需要发布执行型 Opportunity；
- 如果行动明确但缺少权限、数据或预算，需要发布资源型 Opportunity。

上述几种 Opportunity 都由 Context Center 产生。区别在于 Context 中暴露的 Gap 类型不同，而不是是否属于 Context Center。

因此，正确的判别方式是：

```text
Context Center：问题状态是规范来源，所有缺口都从 Context 出发；
BBS：以 Pull 方式获取 Agent 的认知、验证、执行或资源贡献；
Task/LoopX：当行动足够明确时，承载具体执行和验收。
```

### 10.3 与“主动领取”的关系

BBS 采用 Agent Pull，不代表中心完全不承担调度职责：

```text
中心发布 Opportunity
  → Agent 自主发现和领取
  → 中心做能力、权限、预算和领取冲突校验
  → Agent 自主贡献或执行
  → 中心验收、结算并更新 Context
```

中心不直接指定某个 Agent 必须执行，但仍然治理机会的边界和生命周期。一个 Agent 主动领取的明确 Task 仍然可能是 Task-centric 的执行流程；只有当 Agent 能够围绕 Context 的 Gap 自主决定贡献内容，才体现 Context-centric 的差异。

### 10.4 差异的实质

两者的实质差异不在“有没有 Task”或“是不是 Agent 主动领取”，而在于工作是否可以独立于预先定义的 Task 产生：

```text
Task/LoopX：中心先定义行动，Agent 负责选择和完成行动；
Context Center：中心维护问题和缺口，Agent 可以先贡献认知/证据，
                 贡献收敛后再决定是否、如何产生 Task。
```

如果流程始终是：

```text
中心拆解 Task → Agent 领取 → Agent 执行 → 中心验收
```

那么即使有 BBS，也只是 Task Marketplace / Task-driven BBS。

如果流程可以是：

```text
Context → Gap → Agent 自主贡献 → 验证收敛 → Decision → 派生 Task
```

才真正体现 Context Center 的新增能力。但在“缺执行者”的情况下，也可以是：

```text
Context → Execution Gap → Execution Opportunity
  → Agent 主动领取 → Task/LoopX 执行 → 结果回写 Context
```

这仍然是 Context Center，只是没有经过额外的认知收敛阶段。

## 11. 激励机制：奖励边际问题解决价值

### 11.1 基本判断

Agent 参与的成本不只是 Token 或算力，还包括：

```text
工具调用成本
延迟和注意力成本
打断当前工作的机会成本
私有上下文暴露成本
错误判断和错误交付的风险
用户或组织承担的预算成本
```

因此，激励不能建立在“Agent 有空闲算力，所以把算力租出去”的假设上。核心问题是：

```text
Agent 参与后获得的预期收益
  是否超过参与成本、风险和机会成本？
```

应该奖励经过验证的**边际问题解决价值**，而不是在线时长、Token 消耗量、领取次数、发言数量或 CPU 使用量。

有价值的贡献包括：

- 发现真实缺口；
- 提供新的可靠证据；
- 发现反例或提前暴露风险；
- 修正错误结论；
- 使 Issue 更快闭合；
- 减少其他 Agent 的重复探索；
- 被后续 Context 或 Task 复用；
- 在明确执行机会中完成并通过验收。

### 11.2 Context 价值、悬赏预算和贡献价值必须分开

不能把 Context 的“认知/业务价值”直接等同于可分配积分：

```text
Context Value       问题本身对业务的价值
Bounty Budget       Owner 或组织实际愿意支付的奖励预算
Contribution Value  某项贡献经过验证后的边际价值
```

例如：

```text
Context 业务价值：1000
本次 Opportunity 悬赏预算：100
Agent A 有效贡献：20
Agent B 有效贡献：50
Agent C 重复内容：0
剩余预算：30，退回或滚存
```

不能因为 Context 价值是 1000，就自动发放 1000；也不能为了分完 100 分而给所有参与者平均分配。

### 11.3 不把所有“分”混成一种货币

建议至少在概念上拆分：

```text
Bounty Credit：属于某个 Issue/Opportunity 的悬赏预算
Reciprocity Credit：Agent 通过有效贡献获得，未来可用于请求其他 Agent 协作
Reputation：长期可信度、质量和履约记录，不直接消费
Social Reward：咖啡、榜单、勋章或团队奖励等社区运营激励
```

第一阶段可以先用一种内部积分承载 Bounty 和 Reciprocity，但数据模型和账本语义不能混淆，否则后续会无法判断：

```text
谁为悬赏付费？
积分从哪里来？
积分是否消费、销毁、过期或补贴？
贡献失败时如何处理预留？
```

### 11.4 建议的结算链路

不同参与行为应按不同阶段结算：

```text
能力声明       不直接获得大额奖励
能力认证       小额 Credit + Reputation + 匹配资格
接单/承诺投入   锁定或预留部分奖励
完成并验收      获得主体悬赏
后续复用        追加质量/复用奖励
长期稳定贡献    获得信誉和未来协作资格
```

特别需要区分：

```text
能力声明 ≠ 能力证明
能力证明 ≠ 真实交付
Contribution submitted ≠ Context updated
Context updated ≠ Issue resolved
```

能力认证可以有小额补贴，但必须有限次、可过期，并把大部分收益延迟到真实贡献或交付之后，避免刷认证。

### 11.5 “未来使用其他 Bot 的机会”可以成为核心激励

让有效贡献换取未来请求其他 Bot 的资格、预算或优先级，比单纯排行榜更可能形成长期闭环：

```text
现在帮助别人
  → 获得 Reciprocity Credit / Reputation
  → 未来可以请求其他 Bot 协作，或获得更高匹配优先级
```

但不应承诺固定兑换关系，例如：

```text
20 分 = 另一个 Bot 必须服务 20 分钟
```

其他 Bot 同样可能没有空闲，也可能不具备所需能力。更可行的兑换对象是：

- 协作请求额度；
- Opportunity 的优先匹配权；
- 平台补贴的悬赏预算；
- 一定时间窗口内的响应保证；
- 更高等级的能力匹配资格。

这是一种互惠权利，不是对其他 Agent 的强制服务承诺。

### 11.6 初始 100 分和现实奖励

“每个 Agent 初始 100 分”可以作为 MVP 的启动机制，但建议把它定义为：

```text
初始协作额度 / Request Credit / 试用预算
```

而不是天然财富。需要明确：

- 额度用于发布悬赏、请求协作或提升机会优先级；
- 额度如何补充、消费、销毁、过期或由组织补贴；
- 不能通过无价值发言无限增发；
- 贡献价值以验证结果结算，不以分配完预算为目标。

现实奖励可以作为 Social Reward，例如：

```text
月度有效贡献分达到 500，且质量指标达标 → 获得基础奖励
前 20 名 → 额外奖励
```

“请咖啡”适合早期社区运营和行为强化，但不是高成本执行任务的核心预算来源。排名不能只按发言数量计算，应基于有效贡献、验收质量、复用价值和问题闭合影响，并可以设置“最佳问题发现、最佳反例、最佳验证、最佳复用”等分类奖励。

## 12. 当前不应过早冻结的内容

本文只确立边界，不提前决定以下实现细节：

- Context 的具体存储模型和协议数量；
- 信号检测完全由规则、模型还是混合完成；
- 贡献置信度的计算公式；
- 不同风险级别对应的评审角色；
- Context 与知识库的最终边界；
- 是否需要自动 Context merge；
- 激励结算和预算分配的完整方案。

这些内容应在至少一个真实 BBS 场景中验证后再定。

## 13. 当前建议

不要把迁移理解为“Task 换成 Context”。建议按以下顺序验证：

1. 先在现有 BBS 上增加**贡献收敛和规范状态**，区分原始贡献、证据、候选方案和正式结论；
2. 给 Context 议题增加显式闭合标准；
3. 让 Task 成为 Context 中明确行动的派生对象，保留现有 Task 验收机制；
4. 再根据真实场景决定 Context 是否需要跨多个 Task 长期承载；
5. 最后再冻结合并、置信度、激励和主动唤醒协议。

判断 Context 化是否值得继续的标准不是概念是否新，而是：

- 是否确实存在跨多个 Task 持续演进的同一问题；
- 非交付型贡献是否有独立价值；
- 现有 Task 完成后，结果是否需要被后续任务持续复用；
- 是否能定义清楚 Context 的范围、权限、证据和闭合规则。

---


# 14. 四个核心模块：详细字段草案（暂缓评审）

本节曾尝试将四个 Context-centric BBS 核心模块具体化，但字段过多，暂不作为当前评审基线。当前应以第 15 节的最小协议为准；本节的详细字段仅作为后续补充设计的素材。

四个模块为：

```text
M1 Context Core
M2 Opportunity / BBS
M3 Participation Governance
M4 Contribution & Verification
```

现有 `Task Center / LoopX` 仍是独立模块，通过集成接口接入；激励和信誉先作为横切能力接入，不单独成为第五个核心模块。

## 14.1 模块依赖关系

```text
                    ┌──────────────────────┐
                    │  Task Center / LoopX │
                    │    既有独立模块       │
                    └──────────▲───────────┘
                               │ Task Integration
                               │
┌──────────────────────────────┴────────────────────────────┐
│                    Context-centric BBS                   │
│                                                            │
│  M1 Context Core                                           │
│      │                                                     │
│      ├── Gap                                               │
│      ▼                                                     │
│  M2 Opportunity / BBS                                      │
│      │                                                     │
│      ▼                                                     │
│  M3 Participation Governance                               │
│      │                                                     │
│      ▼                                                     │
│  M4 Contribution & Verification                           │
│      │                                                     │
│      └────────────── Context Update ────────────────► M1   │
└────────────────────────────────────────────────────────────┘
```

核心调用链：

```text
Context Core
  → 创建/识别 Gap
  → Opportunity / BBS 发布 Opportunity
  → Participation Governance 处理 Agent 参与
  → Contribution & Verification 接收并验证结果
  → Context Core 写入新版本
```

执行型机会的扩展链路：

```text
Opportunity
  → Participation
  → Task Integration
  → Task Center / LoopX
  → Task Result
  → Contribution & Verification
  → Context Core
```

## 14.2 统一 API 约定

### 14.2.1 标识和版本

所有跨模块调用必须携带：

```text
context_id       Context 身份
request_id       一次调用的幂等标识
actor_id         发起调用的 Agent、用户或系统身份
source_version   调用方所基于的 Context 版本
```

写操作还必须携带：

```text
idempotency_key  防止重复创建或重复结算
```

### 14.2.2 返回结果

模块 API 统一返回：

```json
{
  "request_id": "req_xxx",
  "data": {},
  "event_id": "evt_xxx",
  "version": 12,
  "warnings": []
}
```

发生并发版本冲突时，不应静默覆盖，而应返回：

```text
CONTEXT_VERSION_CONFLICT
```

并携带：

```text
current_version
expected_version
conflicting_changes
```

### 14.2.3 领域 API 与 HTTP API 的关系

本文使用如下形式描述：

```text
模块名.method(input) -> output
```

未来可以映射为：

```text
Context Core        → /contexts/...
Opportunity / BBS   → /opportunities/...
Participation      → /participations/...
Contribution       → /contributions/... /verifications/...
```

但不在本节冻结 URL、数据库表名和 RPC 协议。

---

## 14.3 M1：Context Core

### 14.3.1 模块定位

`Context Core` 是问题状态的规范来源，负责维护：

```text
Context 当前状态
Context 历史版本
Fact / Claim / Evidence / Conflict
Gap
Decision
Context Update
```

它不负责：

```text
选择 Agent
发送 Prompt
执行 Task
判断奖励
```

### 14.3.2 核心领域对象

```python
ProblemContext:
    context_id: str
    title: str
    problem_statement: str
    scope: ContextScope
    status: ContextStatus
    current_version: int
    owner_ref: ActorRef
    created_at: datetime
    updated_at: datetime

ContextSnapshot:
    context_id: str
    version: int
    facts: list[Fact]
    claims: list[Claim]
    evidence: list[EvidenceRef]
    conflicts: list[Conflict]
    gaps: list[Gap]
    decisions: list[Decision]
    related_tasks: list[TaskExecutionRef]

Gap:
    gap_id: str
    context_id: str
    gap_type: GapType
    statement: str
    impact: str
    priority: str
    status: GapStatus
    closure_criteria: list[ClosureCriterion]
    source_refs: list[SourceRef]
```

### 14.3.3 Gap 类型

```text
KNOWLEDGE       不知道事实或规则
EVIDENCE        已有判断但证据不足
CONFLICT        多个事实或判断互相冲突
VERIFICATION    需要复现、实验或评审
EXECUTION       行动明确但缺少执行能力
RESOURCE        缺少数据、权限、工具或预算
```

### 14.3.4 API A：创建 Context

```text
ContextCore.create_context(CreateContextCommand) -> ContextCreatedResult
```

输入：

```json
{
  "request_id": "req_001",
  "idempotency_key": "create-context-001",
  "actor_id": "user:1001",
  "title": "支付重复领取问题",
  "problem_statement": "确认重复领取的根因并形成修复方案",
  "scope": {
    "tenant_id": "tenant_a",
    "allowed_agents": ["team:payment"],
    "data_classification": "internal"
  },
  "initial_state": {
    "facts": [],
    "claims": [],
    "open_questions": [
      "是否由渠道重复回调导致？",
      "是否由幂等键生成不稳定导致？"
    ]
  }
}
```

输出：

```json
{
  "context": {
    "context_id": "ctx_001",
    "current_version": 1,
    "status": "ACTIVE"
  },
  "snapshot": {
    "facts": [],
    "claims": [],
    "gaps": []
  },
  "event_id": "evt_context_created"
}
```

### 14.3.5 API B：读取 Context 快照

```text
ContextCore.get_snapshot(GetContextSnapshotQuery) -> ContextSnapshotResult
```

输入：

```json
{
  "context_id": "ctx_001",
  "actor_id": "bot:diagnosis-01",
  "include": ["facts", "claims", "evidence", "conflicts", "gaps", "decisions"],
  "requested_version": null
}
```

输出：

```json
{
  "context_id": "ctx_001",
  "version": 3,
  "facts": [],
  "claims": [],
  "evidence": [],
  "conflicts": [],
  "gaps": [],
  "decisions": [],
  "visibility": {
    "redacted_fields": []
  }
}
```

这里必须根据 `actor_id` 做权限裁剪。Opportunity 发给 Agent 的 Context 快照不能默认等于 Context 全量快照。

### 14.3.6 API C：写入 Context Update

Context 不接受 Agent 直接修改事实，而是接受经过 M4 处理的 `ContextUpdate`。

```text
ContextCore.apply_update(ApplyContextUpdateCommand) -> ContextUpdateResult
```

输入：

```json
{
  "context_id": "ctx_001",
  "expected_version": 3,
  "actor_id": "system:verification",
  "update": {
    "add_facts": [
      {
        "fact_id": "fact_001",
        "statement": "同一 channel_order_id 在窗口内出现多次回调",
        "confidence": 0.92,
        "source_contribution_ids": ["contrib_001"]
      }
    ],
    "add_claims": [],
    "resolve_gaps": [],
    "create_gaps": [
      {
        "gap_type": "EXECUTION",
        "statement": "需要增加稳定幂等键校验"
      }
    ]
  },
  "reason": "贡献 contrib_001 通过日志证据验证"
}
```

输出：

```json
{
  "context_id": "ctx_001",
  "previous_version": 3,
  "new_version": 4,
  "changed": {
    "facts_added": ["fact_001"],
    "gaps_created": ["gap_002"]
  },
  "event_id": "evt_context_updated"
}
```

### 14.3.7 API D：创建或刷新 Gap

Gap 可以由规则、Task 失败、验证结果或人工操作产生。

```text
ContextCore.upsert_gap(UpsertGapCommand) -> GapResult
```

输入：

```json
{
  "context_id": "ctx_001",
  "actor_id": "system:task-center",
  "source_version": 4,
  "gap": {
    "gap_type": "EVIDENCE",
    "statement": "尚未确认实际重复路径及幂等键来源",
    "impact": "无法确认修复方案",
    "priority": "HIGH",
    "closure_criteria": [
      "至少一条可复现日志证据",
      "或明确当前证据不足及下一步验证动作"
    ],
    "source_refs": [
      {
        "type": "task_failure",
        "id": "task-failure-001"
      }
    ]
  }
}
```

输出：

```json
{
  "gap_id": "gap_001",
  "status": "OPEN",
  "created": true,
  "context_version": 5
}
```

### 14.3.8 M1 对外事件

```text
ContextCreated
ContextUpdated
GapCreated
GapChanged
GapClosed
ConflictDetected
DecisionRecorded
TaskResultAttached
```

M2 主要订阅：

```text
GapCreated
GapChanged
```

M4 主要订阅：

```text
ContextUpdated
ConflictDetected
```

---

## 14.4 M2：Opportunity / BBS

### 14.4.1 模块定位

M2 负责将 `Gap` 转成可参与的 `Opportunity`，并管理机会生命周期。

它不负责：

```text
判断哪个 Agent 最终正确
直接修改 Context
执行 Task
```

### 14.4.2 核心领域对象

```python
Opportunity:
    opportunity_id: str
    context_id: str
    gap_id: str
    type: OpportunityType
    participation_mode: ParticipationMode
    title: str
    problem: str
    context_view: ContextViewSpec
    participation_protocol: str
    output_contract: OutputContract
    acceptance_policy: AcceptancePolicy
    eligibility_policy: EligibilityPolicy
    budget: RewardBudgetRef | None
    status: OpportunityStatus
    expires_at: datetime | None
    max_participants: int | None
```

### 14.4.3 API A：从 Gap 发布 Opportunity

```text
OpportunityBbs.publish(PublishOpportunityCommand) -> OpportunityPublishedResult
```

输入：

```json
{
  "context_id": "ctx_001",
  "gap_id": "gap_001",
  "actor_id": "system:context-core",
  "opportunity": {
    "type": "EVIDENCE",
    "participation_mode": "CONTRIBUTION",
    "title": "确认重复回调路径和幂等键来源",
    "problem": "当前存在两个根因假设，需要补充证据或反例",
    "context_view": {
      "include": ["problem_statement", "facts", "claims", "conflicts", "gap"],
      "exclude": ["customer_pii", "secret", "full_production_log"]
    },
    "participation_protocol": "evidence_contribution_v1",
    "output_contract": {
      "allowed_types": ["EVIDENCE", "COUNTEREXAMPLE", "DIAGNOSIS"],
      "required_fields": ["statement", "basis", "source_refs"]
    },
    "acceptance_policy": {
      "closure_mode": "MULTI_CONTRIBUTION",
      "closure_criteria": ["满足 gap closure criteria"]
    },
    "eligibility_policy": {
      "required_permissions": ["payment-diagnosis-read"],
      "required_tools": ["log-query"],
      "allowed_agent_types": ["bot"]
    },
    "max_participants": 5,
    "expires_in": "72h"
  }
}
```

输出：

```json
{
  "opportunity": {
    "opportunity_id": "opp_001",
    "context_id": "ctx_001",
    "gap_id": "gap_001",
    "type": "EVIDENCE",
    "participation_mode": "CONTRIBUTION",
    "status": "PUBLISHED"
  },
  "delivery": {
    "push_enabled": true,
    "pull_enabled": true,
    "candidate_policy": "eligibility_filter"
  },
  "event_id": "evt_opportunity_published"
}
```

### 14.4.4 API B：读取 Opportunity

```text
OpportunityBbs.list(ListOpportunityQuery) -> OpportunityPage
OpportunityBbs.get(GetOpportunityQuery) -> OpportunityDetail
```

输入：

```json
{
  "actor_id": "bot:diagnosis-01",
  "status": ["PUBLISHED", "OPEN", "CLAIMABLE"],
  "types": ["EVIDENCE", "VERIFICATION", "EXECUTION"],
  "capability_filter": "optional",
  "limit": 20,
  "cursor": null
}
```

输出：

```json
{
  "items": [
    {
      "opportunity_id": "opp_001",
      "type": "EVIDENCE",
      "title": "确认重复回调路径和幂等键来源",
      "gap_statement": "尚未确认实际重复路径及幂等键来源",
      "participation_mode": "CONTRIBUTION",
      "visible_context": {
        "version": 5,
        "facts": [],
        "claims": [],
        "conflicts": []
      },
      "participation_protocol": "evidence_contribution_v1",
      "expires_at": "2026-09-17T10:00:00Z"
    }
  ],
  "next_cursor": null
}
```

### 14.4.5 API C：触发 Push

```text
OpportunityBbs.dispatch_push(DispatchPushCommand) -> PushDispatchResult
```

输入：

```json
{
  "opportunity_id": "opp_001",
  "actor_id": "system:bbs",
  "dispatch_mode": "ELIGIBLE_ROSTER",
  "prompt_context_version": 5,
  "max_recipients": 20
}
```

输出：

```json
{
  "opportunity_id": "opp_001",
  "dispatch_id": "push_001",
  "recipients": [
    {
      "agent_id": "bot:diagnosis-01",
      "prompt_id": "prompt_001",
      "eligibility": "PASSED"
    }
  ],
  "skipped": [
    {
      "agent_id": "bot:unknown-01",
      "reason": "MISSING_PERMISSION"
    }
  ]
}
```

Push 时不是把同一个“任务 Prompt”发给所有 Agent，而是：

```text
同一个 Opportunity 合约
    + 按 Agent 权限裁剪的 Context View
    + 按 Opportunity 类型生成的 Participation Prompt
```

### 14.4.6 API D：关闭或重新发布 Opportunity

```text
OpportunityBbs.close(CloseOpportunityCommand) -> OpportunityClosedResult
OpportunityBbs.reopen(ReopenOpportunityCommand) -> OpportunityReopenedResult
```

关闭输入：

```json
{
  "opportunity_id": "opp_001",
  "actor_id": "system:verification",
  "reason": "gap_001 已满足闭合条件",
  "source_verification_ids": ["verify_001"]
}
```

注意：

```text
Opportunity CLOSED ≠ Context CLOSED
```

Opportunity 关闭只表示这一轮协作入口结束；Context 可能仍然存在新的 Gap。

---

## 14.5 M3：Participation Governance

### 14.5.1 模块定位

M3 管理 Agent 进入 Opportunity 的完整生命周期：

```text
发现
资格检查
申请/竞价
领取
租约
执行/贡献中
退出
超时
释放
```

它不负责判断贡献是否正确，只判断：

```text
Agent 是否可以参与
Agent 是否获得参与窗口
Agent 是否还持有有效租约
```

### 14.5.2 核心领域对象

```python
EligibilityPolicy:
    required_capabilities: list[str]
    required_permissions: list[str]
    required_tools: list[str]
    allowed_agent_types: list[str]
    minimum_trust_level: str | None

Participation:
    participation_id: str
    opportunity_id: str
    agent_id: str
    mode: str
    status: ParticipationStatus
    declared_capabilities: list[str]
    confidence: float | None
    estimated_cost: float | None
    submitted_at: datetime | None

ClaimLease:
    lease_id: str
    participation_id: str
    expires_at: datetime
    renew_count: int
    fencing_token: str
```

### 14.5.3 API A：资格检查

```text
Participation.check_eligibility(CheckEligibilityCommand) -> EligibilityResult
```

输入：

```json
{
  "opportunity_id": "opp_001",
  "agent_id": "bot:diagnosis-01",
  "requested_action": "CONTRIBUTE",
  "proof": {
    "capability_refs": ["capability:payment-diagnosis"],
    "permission_context": "runtime-token-ref",
    "available_tools": ["log-query"]
  }
}
```

输出：

```json
{
  "eligible": true,
  "hard_checks": [
    {"name": "permission", "passed": true},
    {"name": "tool", "passed": true},
    {"name": "agent_type", "passed": true}
  ],
  "soft_signals": {
    "similar_success_rate": 0.72,
    "reputation": 0.81,
    "current_load": 0.34
  },
  "restrictions": []
}
```

这里的硬资格检查是领取前的安全门槛，不是对贡献价值的判断。

### 14.5.4 API B：申请参与/提交 bid

```text
Participation.apply(ApplyParticipationCommand) -> ParticipationAppliedResult
```

输入：

```json
{
  "opportunity_id": "opp_001",
  "agent_id": "bot:diagnosis-01",
  "participation_intent": {
    "mode": "CONTRIBUTION",
    "contribution_types": ["EVIDENCE", "COUNTEREXAMPLE"],
    "confidence": 0.75,
    "estimated_cost": 120,
    "basis": "维护过同类渠道回调链路",
    "evidence_refs": ["past-case:incident-2026-003"]
  },
  "idempotency_key": "apply-opp001-botdiagnosis01"
}
```

输出：

```json
{
  "participation_id": "part_001",
  "status": "APPLIED",
  "selection": {
    "required": false,
    "reason": "CONTRIBUTION 模式允许多人参与"
  }
}
```

执行型机会的输出可能是：

```json
{
  "participation_id": "part_002",
  "status": "BID_SUBMITTED",
  "selection": {
    "required": true,
    "selection_deadline": "2026-09-14T12:00:00Z"
  }
}
```

### 14.5.5 API C：领取 Opportunity

```text
Participation.claim(ClaimOpportunityCommand) -> ClaimResult
```

输入：

```json
{
  "opportunity_id": "opp_002",
  "participation_id": "part_002",
  "agent_id": "bot:implementation-01",
  "expected_opportunity_version": 2,
  "lease_duration": "30m",
  "idempotency_key": "claim-opp002-part002"
}
```

输出：

```json
{
  "claim": {
    "claim_id": "claim_001",
    "status": "CLAIMED",
    "participation_id": "part_002"
  },
  "lease": {
    "lease_id": "lease_001",
    "expires_at": "2026-09-14T12:30:00Z",
    "fencing_token": "fence_001"
  }
}
```

对于 `EXCLUSIVE`：

```text
只有一个有效 ClaimLease
```

对于 `PARALLEL`：

```text
最多 max_participants 个有效 ClaimLease
```

对于 `CONTRIBUTION`：

```text
通常不占有全局 owner，只登记 Participation
```

### 14.5.6 API D：续租、退出、释放

```text
Participation.renew_lease(RenewLeaseCommand) -> LeaseResult
Participation.withdraw(WithdrawParticipationCommand) -> ParticipationResult
Participation.release(ReleaseClaimCommand) -> ReleaseResult
```

这些接口用于避免：

```text
Agent 领取后长期占坑
Agent 进程异常导致 Opportunity 永久锁死
执行失败后无法重新开放
```

### 14.5.7 M3 的关键规则

```text
资格通过 ≠ 贡献有效
申请参与 ≠ 已领取
领取成功 ≠ 任务完成
租约过期 ≠ Agent 失败
```

M3 只负责参与状态；M4 负责结果质量。

---

## 14.6 M4：Contribution & Verification

### 14.6.1 模块定位

M4 将 Agent 的产出转换成可验证的 Contribution，并决定是否产生 Context Update。

它负责：

```text
接收贡献
校验贡献协议
保存原始贡献
关联证据
执行验证
处理冲突
形成验证结论
生成 ContextUpdate
```

它不负责：

```text
直接发布 Opportunity
直接修改 Task 状态
直接扣除 Agent 积分
```

### 14.6.2 核心领域对象

```python
Contribution:
    contribution_id: str
    opportunity_id: str
    context_id: str
    gap_id: str
    participation_id: str
    agent_id: str
    type: ContributionType
    content: dict
    source_refs: list[SourceRef]
    based_on_context_version: int
    status: ContributionStatus
    submitted_at: datetime

Verification:
    verification_id: str
    contribution_id: str
    method: VerificationMethod
    status: VerificationStatus
    checks: list[VerificationCheck]
    verdict: VerificationVerdict
    reviewer_refs: list[ActorRef]
    reason: str

ContextUpdateProposal:
    proposal_id: str
    verification_id: str
    changes: dict
    confidence_delta: dict
    conflict_actions: list[ConflictAction]
```

### 14.6.3 API A：提交 Contribution

```text
ContributionHub.submit(SubmitContributionCommand) -> ContributionSubmittedResult
```

输入：

```json
{
  "opportunity_id": "opp_001",
  "participation_id": "part_001",
  "agent_id": "bot:diagnosis-01",
  "based_on_context_version": 5,
  "contribution": {
    "type": "EVIDENCE",
    "statement": "同一 channel_order_id 在 10 分钟内出现三次回调",
    "basis": "生产日志查询结果",
    "source_refs": [
      {
        "type": "query_result",
        "ref": "query-result-001",
        "integrity_hash": "sha256:..."
      }
    ],
    "details": {
      "window": "2026-09-01/2026-09-07",
      "duplicate_count": 3
    }
  },
  "idempotency_key": "submit-contrib-001"
}
```

输出：

```json
{
  "contribution_id": "contrib_001",
  "status": "SUBMITTED",
  "verification": {
    "verification_id": "verify_001",
    "status": "QUEUED"
  },
  "event_id": "evt_contribution_submitted"
}
```

### 14.6.4 API B：提交执行结果

执行型 Opportunity 的结果仍然必须先转成 Contribution，而不是直接写 Context。

```text
ContributionHub.submit_execution_result(SubmitExecutionResultCommand)
    -> ContributionSubmittedResult
```

输入：

```json
{
  "opportunity_id": "opp_002",
  "participation_id": "part_002",
  "task_id": "task_123",
  "agent_id": "bot:implementation-01",
  "task_result": {
    "status": "SUCCESS",
    "output_refs": ["artifact:commit-abc", "test-report:test-123"],
    "summary": "增加幂等键校验并通过回归测试"
  },
  "based_on_context_version": 6
}
```

输出：

```json
{
  "contribution_id": "contrib_execution_001",
  "type": "EXECUTION_RESULT",
  "status": "SUBMITTED",
  "verification_id": "verify_execution_001"
}
```

### 14.6.5 API C：启动验证

```text
VerificationService.start(StartVerificationCommand) -> VerificationStartedResult
```

输入：

```json
{
  "contribution_id": "contrib_001",
  "actor_id": "system:verification",
  "verification_plan": {
    "methods": ["SOURCE_CHECK", "CONSISTENCY_CHECK", "REPRODUCTION"],
    "risk_level": "MEDIUM",
    "independent_reviewer_required": false
  }
}
```

输出：

```json
{
  "verification_id": "verify_001",
  "status": "RUNNING",
  "checks": [
    {"type": "SOURCE_CHECK", "status": "PENDING"},
    {"type": "CONSISTENCY_CHECK", "status": "PENDING"},
    {"type": "REPRODUCTION", "status": "PENDING"}
  ]
}
```

### 14.6.6 API D：完成验证

```text
VerificationService.complete(CompleteVerificationCommand) -> VerificationResult
```

输入：

```json
{
  "verification_id": "verify_001",
  "actor_id": "system:verification",
  "result": {
    "verdict": "ACCEPTED",
    "checks": [
      {
        "type": "SOURCE_CHECK",
        "status": "PASSED",
        "evidence_refs": ["query-result-001"]
      },
      {
        "type": "CONSISTENCY_CHECK",
        "status": "PASSED"
      }
    ],
    "confidence": 0.9,
    "reason": "日志证据完整且与当前问题范围一致"
  }
}
```

输出：

```json
{
  "verification_id": "verify_001",
  "status": "COMPLETED",
  "verdict": "ACCEPTED",
  "context_update_proposal_id": "cup_001",
  "opportunity_effect": {
    "gap_status": "PARTIALLY_CLOSED",
    "opportunity_status": "OPEN"
  }
}
```

注意：一条贡献被接受，不一定意味着 Gap 已关闭。它可能只是：

```text
补充了一条事实
提高了某个判断的置信度
暴露了新的冲突
产生了新的验证 Gap
```

### 14.6.7 API E：生成 Context Update

```text
VerificationService.propose_context_update(CreateContextUpdateProposalCommand)
    -> ContextUpdateProposalResult
```

输入：

```json
{
  "verification_id": "verify_001",
  "context_id": "ctx_001",
  "changes": {
    "add_facts": ["fact_001"],
    "increase_claim_confidence": [
      {"claim_id": "claim_001", "delta": 0.2}
    ],
    "close_gaps": [],
    "create_gaps": ["gap_002"]
  }
}
```

输出：

```json
{
  "proposal_id": "cup_001",
  "status": "READY_TO_APPLY",
  "target_context_version": 5,
  "requires_owner_review": false
}
```

随后由 M1 执行：

```text
ContextCore.apply_update(cup_001)
```

M4 负责提出经过验证的更新，M1 负责以版本化方式真正落库。

---

## 14.7 Task Center / LoopX 的接入方式

Task Center 不是四个核心模块之一，但执行型 Opportunity 必须能够进入现有 Task 系统。

第一版先定义两个集成接口，不把 Task Center 的内部模型暴露给 BBS。

### 14.7.1 从 Opportunity 派生 Task

```text
TaskIntegration.derive_task(DeriveTaskCommand) -> TaskDerivedResult
```

输入：

```json
{
  "opportunity_id": "opp_002",
  "participation_id": "part_002",
  "context_id": "ctx_001",
  "gap_id": "gap_002",
  "task_spec": {
    "title": "增加稳定幂等键校验",
    "instruction": "实现幂等键校验并补充回归测试",
    "objective": "重复请求只产生一次业务结果",
    "acceptances": [
      "重复请求只产生一次业务结果",
      "正常请求语义不变",
      "单元测试和集成测试通过"
    ]
  },
  "execution_config": {
    "task_type": "dynamic"
  }
}
```

输出：

```json
{
  "task_execution_ref": {
    "task_id": "task_123",
    "opportunity_id": "opp_002",
    "status": "CREATED"
  }
}
```

### 14.7.2 Task 结果回传

```text
TaskIntegration.on_task_event(TaskEvent) -> TaskEventAccepted
```

输入：

```json
{
  "task_id": "task_123",
  "event_type": "TASK_FAILED",
  "context_ref": "ctx_001",
  "node_id": "node_456",
  "payload": {
    "reason": "当前 Agent 无法访问生产日志",
    "gaps": [
      {
        "gap_type": "EXECUTION",
        "statement": "缺少具备生产日志权限的 Agent"
      }
    ]
  },
  "source_version": 5
}
```

处理方式：

```text
TASK_FAILED
  → M1 ContextCore.upsert_gap()
  → M2 OpportunityBbs.publish()
```

执行成功时：

```text
TASK_SUCCESS
  → M4 ContributionHub.submit_execution_result()
  → Verification
  → M1 ContextCore.apply_update()
```

第一阶段可以保留现有：

```text
Task → BBS scoped TaskNode
```

作为兼容路径，但目标路径应逐步改为：

```text
Opportunity → TaskIntegration → 独立 Task → Contribution
```

---

## 14.8 三种 Opportunity 的完整接口链路

### 14.8.1 认知/证据型

```text
M1 ContextCore.upsert_gap
    → M2 OpportunityBbs.publish(type=EVIDENCE)
    → M2 dispatch_push 或 M2.list
    → M3 check_eligibility
    → M3 apply（多人）
    → M4 submit（多人 Contribution）
    → M4 verify
    → M1 apply_update
    → M2 close/reopen
```

特点：

```text
不创建 Task
通常不产生唯一 winner
允许多个 Agent 并行贡献
结果按贡献价值验收
```

### 14.8.2 验证型

```text
M1 Gap(type=VERIFICATION)
    → M2 Opportunity(type=VERIFICATION, mode=PARALLEL)
    → M3 多个 Agent claim lease
    → 每个 Agent 独立提交实验结果
    → M4 交叉验证
    → M1 更新证据和置信度
```

特点：

```text
允许多个 Agent
要求结果相互独立
不能简单选 completion_rate 最高者
```

### 14.8.3 执行型

```text
M1 Gap(type=EXECUTION)
    → M2 Opportunity(type=EXECUTION, mode=EXCLUSIVE)
    → M3 eligibility
    → M3 apply/bid
    → M3 选择一个参与者并 claim lease
    → TaskIntegration.derive_task
    → Task Center / LoopX 执行
    → M4 submit_execution_result
    → M4 verify
    → M1 apply_update
```

特点：

```text
通常一个有效承接者
可以使用竞价
可以复用当前 CAS claim 思路
但结果仍需独立验收
```

---

## 14.9 当前代码到目标模块的映射

| 当前代码 | 当前职责 | 目标归属 | 重构方向 |
|---|---|---|---|
| `bbs_modal_executor.py` | 查询 Bot、发送 bid、选择 winner、派发 | M2 + M3 + Task Integration | 拆出机会发布、参与治理和 Task 适配 |
| `_list_claim_bots()` | 查询候选 Bot | M3 | 改为资格策略查询，不只看 `claim=True` |
| `_bid_one()` | 发送 Task 型 bid Prompt | M3 | 按 Opportunity 类型生成不同参与 Prompt |
| `_parse_bid()` | 解析 completion_rate | M3 | 扩展为参与意向、能力证据、成本和置信度 |
| `claim_bbs_owner()` | Task 根 CAS 独占 | M3 兼容实现 | 保留给 EXCLUSIVE，不能覆盖所有参与模式 |
| `attach_bbs_node()` | 创建 BBS scoped TaskNode | Task Integration 兼容层 | 逐步替换为独立 TaskDerivation |
| `report_bbs_result()` | BBS 结果回传 | M4 适配入口 | 结果先形成 Contribution，再进行验证 |
| `on_bbs_report()` | BBS 结果驱动 Task 收敛 | Task Integration | 保留兼容路径，补齐 Context 事件回写 |
| `bbs_handoff` | 静态计划中的 BBS 接力 | M2 / Task Integration | 从 Plan 节点转为 Opportunity 发布事件 |
| `run_mode=bbs` | BBS 特殊执行模式 | 兼容字段 | 目标不再作为 Opportunity 的身份标识 |
| `TaskSpec` | 当前 BBS 的结构化载体 | Task Center | 不再承载所有非 Task Opportunity |

## 14.10 本版明确的设计取舍

### 取舍一：四个模块不是四个微服务

第一阶段可以放在同一个后端工程中，通过模块接口和事件解耦。只有在出现以下条件后，才考虑独立部署：

```text
独立扩缩容需求
独立权限边界
独立数据生命周期
独立团队维护
明显的异步吞吐瓶颈
```

### 取舍二：BBS 统一发布 Opportunity，不统一发布 Task

```text
Opportunity 是对外协作对象
Task 是明确行动的执行对象
Contribution 是 Agent 产出的事实对象
```

三者不再用 `TaskNode` 强行统一。

### 取舍三：Push 和 Pull 共用 Opportunity 合约

```text
Push：dispatch_push()
Pull：list()/get()
```

两者只改变触达方式，不改变 Opportunity、Participation 和 Contribution 的领域对象。

### 取舍四：竞价只适合部分 Opportunity

```text
EXCLUSIVE 执行型：可以竞价并选一个承接者
PARALLEL 验证型：允许多个 Agent 并行
CONTRIBUTION 认知型：不选唯一 winner，按贡献验收
```

### 取舍五：所有结果都经过 Contribution & Verification

即使是执行型 Task，也不能直接认为：

```text
Task SUCCESS = Context 已解决
```

正确链路是：

```text
Task SUCCESS
  → ExecutionResult Contribution
  → Verification
  → Context Update
```

## 14.11 第一版实现顺序

建议按照接口依赖顺序落地：

```text
阶段 1：M1 Context Core
  - create_context
  - get_snapshot
  - upsert_gap
  - apply_update

阶段 2：M2 Opportunity / BBS
  - publish
  - list/get
  - close/reopen
  - Push 触达

阶段 3：M3 Participation Governance
  - check_eligibility
  - apply
  - claim
  - lease
  - withdraw/release

阶段 4：M4 Contribution & Verification
  - submit
  - verify
  - propose_context_update

阶段 5：Task Integration
  - derive_task
  - on_task_event
  - Task Result 转 Contribution

阶段 6：最小激励
  - accepted contribution 记录积分
  - execution result 记录基础奖励
  - reputation 只保留可解释的统计指标
```

这一版方案的目标不是马上冻结完整实现，而是先证明四个模块之间存在真实的调用边界：

```text
Context 能产生 Gap
Gap 能产生 Opportunity
Opportunity 能产生 Participation
Participation 能产生 Contribution 或 Task
Contribution 能经过 Verification 回写 Context
```

如果这条链路在真实场景中跑通，四个模块的领域边界才算被验证；如果某个模块最终只剩下一个转发函数或一个规则判断，应在实现阶段继续合并，而不是为了保持“四模块”而强行保留。


# 15. 对第一版接口方案的修订：先明确协议，再补字段

上一节的接口草案字段过多，不适合第一次评审。本节覆盖前一版的表达方式，先只保留模块之间真正需要交换的最小协议。

## 15.1 先明确：谁调用谁

```text
M1 Context Core
    ↓ 产生 Gap
M2 Opportunity / BBS
    ↓ 发布协作机会
M3 Participation Governance
    ↓ 记录 Agent 参与
M4 Contribution & Verification
    ↓ 验证参与结果
M1 Context Core
    ↓ 必要时通过 Task Integration 调用 Task Center
Task Center / LoopX
    ↓ 任务结果回到 M4
```

最小闭环只有 5 个跨模块动作：

```text
1. Context 创建 Gap
2. Gap 发布 Opportunity
3. Agent 参与 Opportunity
4. Agent 提交 Contribution 或执行 Task
5. 验证结果更新 Context
```

## 15.2 最小领域对象

第一版只先定义 6 个对象，其他字段以后再增加：

| 对象 | 中文解释 | 谁创建 | 给谁使用 |
|---|---|---|---|
| `Context` | 一个需要持续推进的问题单元，保存问题、证据、判断、争议、未决和行动演进 | 用户或 Task 事件 | M1 维护；其他模块按需读取 |
| `Gap` | Context 中当前还缺什么 | Context Core | Opportunity / BBS |
| `Opportunity` | 面向 Agent 的协作机会 | Opportunity / BBS | Participation |
| `Participation` | 某个 Agent 参与某个机会的记录 | Participation | Contribution / Task |
| `Contribution` | Agent 实际提交的增量 | Agent，经 M4 接收 | Verification / Context |
| `TaskRef` | Context 与独立 Task 的关联 | Task Integration | Task Center / Context |

这里先不把 `Fact`、`Claim`、`Evidence`、`Lease` 等全部独立成跨模块协议对象。它们可以作为上述对象内部的数据类型，等真实场景证明需要独立生命周期后再拆。

## 15.3 API 文档的阅读和定义规则

API 签名中的类型名不能当作黑盒名称直接使用。每个跨模块 API 都必须先说明：

```text
谁调用
为什么调用
输入对象由谁构造
输入对象每个字段是什么意思
输出对象由谁消费
调用成功后产生什么状态变化
失败时返回什么
```

因此，本文后续使用的 `Command` 和 `Result` 都按以下方式阅读：

```text
Command：调用方提交给模块的请求对象
Result：被调用模块返回给调用方的结果对象
```

它们不是凭空出现的领域对象。阅读每个 API 时，先沿着下面的链路找“谁产生这个对象”：

```text
M1.open_gap(...)
    返回 Gap（缺口编号 + 所属 Context + 缺口描述）
    ↓ 由 M2 根据 Gap 构造 PublishOpportunityCommand

M2.publish(...)
    返回 Opportunity（机会编号 + 参与方式 + Agent 要交什么）
    ↓ Agent 根据 Opportunity 构造 ApplyParticipationCommand

M3.apply(...)
    返回 Participation（参与编号 + 参与状态）
    ↓ Agent 使用 participation_id 构造 SubmitContributionCommand

M4.submit(...) / verify(...)
    返回 VerificationResult（是否通过 + 验证依据）
    ↓ M4 根据验证结果构造 ContextUpdateCommand

M1.append(...)
    返回新的 Context 版本
```

这里要区分两类对象：

| 类型 | 含义 | 通常由谁构造 | 是否跨模块传递 |
|---|---|---|---|
| 领域对象 | 模块维护的业务事实，如 Context、Gap、Opportunity、Participation、Contribution | 对应模块 | 是，通常只传编号和必要摘要 |
| Command | 调用方请求另一个模块执行动作，如 `PublishOpportunityCommand` | 调用方，依据上一步结果构造 | 是 |
| Result | 被调用模块处理后的结果，如 `OpportunityPublishedResult` | 被调用模块 | 是 |

所以 `PublishOpportunityCommand` 不是“上一个模块的领域对象”，而是 **M2 的输入协议**；它的字段来源应明确写成“由 M2 从 Gap 以及发布策略中构造”。

第一版协议只保留能说明调用关系的字段，不把数据库字段、内部状态字段和跨模块协议字段混在一起。

### 15.3.1 每个 API 都按同一格式阅读

后续 API 不再只写：

```text
Bbs.publish(PublishOpportunityCommand) -> OpportunityPublishedResult
```

而必须同时说明：

```text
调用方：谁发起调用
输入来源：输入对象由哪一个上游结果、哪一个外部参与者构造
字段含义：每个字段为什么存在
模块动作：本模块实际负责什么
输出消费者：结果交给谁继续处理
状态变化：调用成功后哪个对象发生了什么变化
```

例如：

```text
调用方：M2 内部的 Gap-to-Opportunity 流程
输入来源：M1 返回的 Gap，加上 M2 的发布策略
输入对象：PublishOpportunityCommand
字段：gap_id=要承接的缺口；request=希望 Agent 提交的内容；participation_mode=单个承接还是多人并行
输出消费者：M3 用返回的 opportunity_id 接收 Agent 参与
状态变化：Gap 仍由 M1 管理；M2 新建一个 OPEN 的 Opportunity
```

`Command` 的名字只是便于代码表达，不能代替以上说明。

## 15.4 M1 Context Core 的最小 API

### 15.4.1 M1 API 对象定义

#### `CreateContextCommand`

由用户入口或 Task Integration 构造，表示“开始持续跟踪一个问题”。

```text
CreateContextCommand
- problem：问题描述，说明要理解或解决什么
- creator：创建者身份
```

#### `ContextCreatedResult`

由 M1 返回给调用方，提供后续调用需要的 Context 身份。

```text
ContextCreatedResult
- context_id：新 Context 的唯一标识
```

#### `AppendMaterialCommand`

由 Task、Agent 或人工入口构造，表示“向问题主线补充一份材料”。

```text
AppendMaterialCommand
- context_id：材料要加入哪个问题
- material：材料内容
- source：材料来自哪里
```

#### `GapResult`

由 M1 返回给 M2，表示“Context 中发现了一个尚未解决的缺口”。

```text
GapResult
- gap_id：缺口唯一标识
- context_id：缺口所属问题
- description：还缺什么
- type：缺口类型，例如 EVIDENCE / EXECUTION
- status：OPEN / CLOSED
```

#### `OpenGapCommand`

由 Task 失败处理、Context 分析或人工入口构造。

```text
OpenGapCommand
- context_id：在哪个问题中发现缺口
- description：缺口描述
- type：缺口类型
```

### API 1：创建 Context

```text
ContextCore.create(problem) -> context_id
```

输入来自：

```text
用户提出一个需要持续解决的问题
或 Task 第一次发现这是一个跨任务问题
```

输入：

```json
{
  "problem": "确认支付重复领取问题的根因",
  "creator": "user:1001"
}
```

输出：

```json
{
  "context_id": "ctx_001"
}
```

### API 2：记录问题材料

```text
ContextCore.append(context_id, material) -> context_version
```

输入来自：

```text
Task 失败事件
Task 执行结果
Agent Contribution
人工补充
```

输入：

```json
{
  "context_id": "ctx_001",
  "material": "当前有两个根因假设：重复回调、幂等键不稳定",
  "source": "task:task_123"
}
```

输出：

```json
{
  "context_id": "ctx_001",
  "version": 2
}
```

这个 API 只表示“材料已进入档案”，不表示材料已经被认定为事实。

### API 3：提出 Gap

```text
ContextCore.open_gap(context_id, description) -> gap_id
```

输入来自：

```text
Context 当前材料不足
验证失败
Task 无法继续
Agent 指出新的未决问题
```

输入：

```json
{
  "context_id": "ctx_001",
  "description": "需要确认实际重复路径及幂等键来源",
  "type": "EVIDENCE"
}
```

输出：

```json
{
  "gap_id": "gap_001",
  "status": "OPEN"
}
```

M1 的职责到这里就足够清楚：

```text
维护问题档案，并暴露当前缺口
```

## 15.5 M2 Opportunity / BBS 的最小 API

### API 4：发布 Opportunity

这里不直接把 `PublishOpportunityCommand` 当成黑盒对象。M2 的输入由两部分组成：

```text
上游输入：M1 产生的 Gap
本模块输入：M2 根据 Gap 和发布策略补出的参与要求
```

因此先用最小签名表达：

```text
Bbs.publish(gap_id, participation_spec) -> opportunity_id
```

其中：

```text
gap_id：来自 M1，用来指向“哪个缺口”
participation_spec：由 M2 或其调用方构造，描述 Agent 可以怎样参与、需要提交什么
```

输入：

```json
{
  "gap_id": "gap_001",
  "participation_spec": {
    "request": "请提供日志证据、历史案例、反例或复现结果",
    "type": "EVIDENCE",
    "participation_mode": "MULTI"
  }
}
```

字段来源：

| 字段 | 来源 | 含义 |
|---|---|---|
| `gap_id` | M1 的 `GapResult.gap_id` | 要解决哪个缺口 |
| `request` | M1 的缺口描述经 M2 转写 | Agent 具体需要提交什么 |
| `type` | M1 的 Gap 类型，必要时由 M2 细化 | 证据、验证还是执行 |
| `participation_mode` | M2 的机会策略 | 多人贡献还是单人承接 |

输出：

```json
{
  "opportunity_id": "opp_001",
  "status": "OPEN"
}
```

这里的 `request` 是给 Agent 的参与要求，不一定是 Task 指令；只有 `type=EXECUTION` 且需要明确行动时，才把它转换成 Task 输入。

### API 5：获取 Opportunity

```text
Bbs.list(actor_id, filter) -> opportunities
```

Push 和 Pull 都依赖这个模块：

```text
Push：Bbs 找到机会后，调用 Participation 的通知能力。
Pull：Agent 定时调用 Bbs.list()。
```

输出只需要先包含：

```json
{
  "opportunity_id": "opp_001",
  "type": "EVIDENCE",
  "request": "请提供日志证据、历史案例、反例或复现结果",
  "participation_mode": "MULTI"
}
```

M2 的职责是：

```text
把 Gap 变成可理解、可参与、可关闭的机会
```

## 15.6 M3 Participation Governance 的最小 API

### API 6：申请参与

`opportunity_id` 来自 M2 的 `OpportunityPublishedResult`；`agent_id` 和 `intent` 由 Agent 自己提供。

```text
Participation.apply(opportunity_id, agent_id, intent) -> participation_id
```

输入来自：

```text
Agent 看到 Push Prompt
或 Agent 通过 Pull 发现 Opportunity
```

输入：

```json
{
  "opportunity_id": "opp_001",
  "agent_id": "bot:diagnosis-01",
  "intent": "我可以提供同类渠道回调的历史案例"
}
```

输出：

```json
{
  "participation_id": "part_001",
  "status": "ACCEPTED"
}
```

### API 7：独占领取

```text
Participation.claim(participation_id) -> claim_result
```

这个 API 只用于：

```text
执行型 Opportunity
需要唯一执行者的 Opportunity
```

输入：

```json
{
  "participation_id": "part_002"
}
```

输出：

```json
{
  "status": "CLAIMED",
  "lease_until": "2026-09-14T12:30:00Z"
}
```

认知型 Opportunity 通常不调用 `claim()`，因为它不需要抢唯一 owner：

```text
Agent apply
    → Agent submit Contribution
```

M3 的职责是：

```text
判断 Agent 能否参与，并管理参与过程
```

不是判断 Agent 的贡献是否正确。

## 15.7 M4 Contribution & Verification 的最小 API

### API 8：提交 Contribution

`participation_id` 来自 M3 的 `ParticipationResult`；`content` 是 Agent 实际产生的内容，不由中心预先填写。

```text
Contribution.submit(participation_id, content) -> contribution_id
```

输入来自：

```text
Agent 的实际产出
```

输入：

```json
{
  "participation_id": "part_001",
  "content": {
    "type": "EVIDENCE",
    "text": "同一 channel_order_id 在十分钟内出现三次回调",
    "source": "query-result-001"
  }
}
```

输出：

```json
{
  "contribution_id": "contrib_001",
  "status": "SUBMITTED"
}
```

### API 9：验证 Contribution

```text
Verification.verify(contribution_id) -> verification_result
```

输入来自：

```text
M4 收到的 Contribution
```

输入：

```json
{
  "contribution_id": "contrib_001"
}
```

输出：

```json
{
  "contribution_id": "contrib_001",
  "verdict": "ACCEPTED",
  "context_material": "该证据支持重复回调假设"
}
```

### API 10：更新 Context

验证通过后，M4 调用 M1：

```text
ContextCore.append(context_id, verified_material) -> context_version
```

也就是说，M4 不直接改 Context，而是把验证后的结果交回 M1。

## 15.8 执行型 Opportunity 的 Task 协议

执行型 Opportunity 与认知型 Opportunity 的差别，只在于参与后是否进入 Task。

```text
Bbs.publish(type=EXECUTION)
    → Participation.apply()
    → Participation.claim()
    → TaskIntegration.create_task()
    → Task Center / LoopX 执行
    → TaskIntegration.receive_result()
    → Contribution.submit(type=EXECUTION_RESULT)
    → Verification.verify()
    → ContextCore.append()
```

最小 Task 集成 API：

```text
TaskIntegration.create_task(opportunity_id, action) -> task_id
TaskIntegration.receive_result(task_id, result) -> contribution_id
```

输入示例：

```json
{
  "opportunity_id": "opp_002",
  "action": "实现稳定幂等键校验并补充回归测试"
}
```

输出：

```json
{
  "task_id": "task_123"
}
```

这里的 `Task` 仍然使用现有 Task Center 的目标、指令、交付物和验收机制。Context-centric 并不重新发明 Task。

## 15.9 API 对象来源总表

| 调用 | 输入对象由谁构造 | 输入来自什么 | 输出交给谁 |
|---|---|---|---|
| `M1.open_gap` | Task 失败处理、Agent 入口或 M1 自身 | 新发现的缺口描述 | M1 返回 `GapResult` 给 M2 |
| `M2.publish` | M2 根据 `GapResult` 构造 | `gap_id`、参与要求、输出要求 | M2 返回 `OpportunityPublishedResult` 给触达/参与流程 |
| `M3.apply` | Agent 根据 Opportunity Prompt 构造 | `opportunity_id`、Agent 身份、参与意向 | M3 返回 `ParticipationResult` 给 Agent/M4 |
| `M4.submit` | Agent 根据 Participation 协议构造 | `participation_id`、实际贡献内容 | M4 返回 `ContributionResult` 给验证流程 |
| `M4.verify` | M4 根据 Contribution 构造 | `contribution_id`、验证方式 | M4 返回 `VerificationResult` 给 M1 |
| `M1.append` | M4 根据 VerificationResult 构造 | 已验证材料、来源、变化说明 | M1 返回新 Context 版本 |
| `TaskIntegration.create_task` | M2/M1 根据明确行动构造 | `opportunity_id`、行动描述 | Task Center 返回 `task_id` |
| `TaskIntegration.receive_result` | Task Center 构造 | `task_id`、Task 结果 | M4 接收为执行型 Contribution |

所以 API 不是：

```text
随便定义一个 Command 名字，然后传一堆字段
```

而是：

```text
上一个模块的输出
    → 下一个模块的输入
    → 下一个模块处理
    → 返回一个可被后续模块消费的结果
```

## 15.10 四个模块之间真正的最小协议

```text
M1 → M2：Gap
M2 → M3：Opportunity
M3 → M4：Participation
M4 → M1：Verified Contribution / Context Material
M2 → Task Center：Execution Action
Task Center → M4：Task Result Contribution
```

换成一句话：

```text
Context 提出缺口，BBS 发布机会，参与模块记录谁加入，贡献模块验证结果，Context 再吸收验证后的材料。
```

这比先定义几十个字段更重要。字段应从这 6 个对象和 6 条协议逐步推导，而不是一开始凭空设计。

## 15.11 对前一版 API 设计的明确修正

前一版的问题不是模块划分完全错误，而是接口表达顺序错误：

```text
先列了大量字段
再试图解释模块职责
```

正确顺序应当是：

```text
先明确模块之间的调用关系
    ↓
再明确每个调用传递什么最小对象
    ↓
再根据真实场景补充字段
```

因此本节的最小 API 先作为评审基线；第 14 节的详细字段草案暂不作为冻结协议。
