# Avernet Task 生命周期与 Context 交互走读

## 0. 先给结论

当前代码的主要问题不是 Context 位点太少，而是 **Task 的结构/控制状态和 Context 事实被放进了同一批对象**：

```text
TaskNode
RuntimeInfo
TaskExecutionGraph
TaskNodePatch
```

按 Task 生命周期看，Context 主要参与四类事情：

```text
1. 记录输入事实
2. 记录 Execution Binding
3. 记录执行过程和结果
4. 为 Planner / Runner / Verifier / Recovery 提供 View
```

但这不代表每一步都要单独调用一次 Context API。合理的实现可以是：

```text
一次 Task 创建事务
  ├─ 创建 Task
  └─ 同步记录初始 Source Record

Task Runtime 后续按需读取 Context View
```

即：**输入记录可以和 Task 创建合并在一个应用事务中，不必人为拆成“Task 调 Context，再由 Context 返回 Task”。**

---

# 1. 任务创建

## Task 在做什么

接收标题、指令、目标、验收标准和来源，创建 Task。

## 是否涉及 Context

涉及，但只是记录“初始输入事实”。

合理的交互应是：

```text
外部提交
  → Task 创建命令
  → Task 创建 + 初始 Source Record 一起落库
```

不合理的是：

```text
先调用 Context API 写输入
  → 再调用 Task API 创建
  → Task 再回头读取刚才的输入
```

这样会把一次提交人为拆成多次往返，增加一致性和失败处理成本。

## 当前代码

HTTP DTO：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/adapters/http/task/schemas.py
```

主要对象：

```text
TaskInfoRequestDTO
RequestTaskSpecDTO
ExecutionConfigDTO
```

领域请求：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/domain/requests.py
```

主要对象：

```text
TaskInfoRequest
RequestTaskSpec
```

领域模型：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/domain/models.py
```

主要对象：

```text
TaskSpec
Context
Goal
AcceptanceCriteria
TaskInfo
```

创建入口：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_center/task_service.py
```

方法：

```text
TaskService.execute()
```

## 当前交互

```text
TaskInfoRequest
  → TaskInfo
  → TaskGraphService.initialize_graph()
```

当前 `TaskSpec.context` 只有：

```text
background
extend_props
```

它更像“提交时附带的背景信息”，还不是独立的 Context。

## 问题

### 1. `TaskSpec.context` 命名容易误导

它让人以为 Task 已经拥有完整 Context，实际上只是 Task 输入的一部分。

### 2. `TaskInfo` 职责不清（Task 规格 / Runtime policy / 执行绑定 / Context 输入 混在一个对象）

涉及文件：

```text
.../community/core/task/domain/models.py            # TaskInfo / TaskSpec / Context 定义
.../community/core/task/domain/requests.py          # TaskInfoRequest
.../community/adapters/http/task/schemas.py         # ExecutionConfigDTO / TaskInfoRequestDTO
.../community/core/task/task_center/task_service.py # execute() 按 task_type 分流 / materialize 补字段
.../community/core/task/task_context/task_graph_service.py # initialize 把 execution_config 等塞 graph.extend_props
```

#### 当前代码包含什么

`TaskInfo`（models.py）：

```python
@dataclass
class TaskInfo:
    task_spec: TaskSpec
    source_type: str            # "bot" | "coop_group" | "api"
    owner_bot_id: str
    owner_user_id: str = ""
    execution_config: dict[str, Any] = field(default_factory=dict)
```

`TaskSpec` 含 `Metadata(task_id/title/instruction)` + `Context(background/extend_props)` + `Goal(objective/acceptances)`。
`ExecutionConfigDTO`（schemas.py，`extra="allow"`）显式字段只有 `task_type/yaml/workflow_id/panel_component_name`，实际还透传 `static_plan_id/static_plan_yaml/template_input/main_session_id/MAX_DEPTH/MAX_LOOP`。
整段 `execution_config` 进 `graph.extend_props`，`execute()` 用 `cfg.get("task_type")` 分流 `_run_workflow/_run_yaml/_run_bbs`，`_materialize_static_plan_if_needed` 再往里补 `static_plan_*`。

#### 是否涉及 Context

部分涉及：

- `task_spec.context.background/extend_props` 是"初始输入事实"，属 Context 的 Source Record 范畴，当前挂在 TaskSpec 名义下。
- `execution_config.main_session_id` 是外部会话绑定，属 External Execution Binding / Context 性质。

其余 `execution_config` 多为 Runtime policy，不属 Context，但同样是"非 Task 核心字段混进 Task 快照"。

#### 字段归属（当前问题）

| 字段（位置） | 实际语义 | 应归属 | 当前问题 |
|---|---|---|---|
| `task_spec.metadata`(title/instruction) `goal`(objective/acceptances) | 任务目标/指令/验收 | Task 核心规格 | 合理，保留 |
| `source_type` `owner_bot_id/owner_user_id` | 触发渠道 / 归属身份 | Task provenance（创建事实） | 基本合理；owner 语义混了"创建者/任务 owner/执行观察者/Bot 归属"，需澄清 |
| `task_spec.context.background` | 提交时附带背景 | 初始输入事实/Source Record | 挂在 TaskSpec.context 名下，被当成"Task 拥有 Context"，实际应归初始输入事实 |
| `task_spec.context.extend_props` | 无 schema 杂项 | 部分 Task provenance、部分 Context 输入 | 混合容器，无法判断归属 |
| `execution_config.task_type`(yaml/workflow/dynamic/static_plan/bbs) | 执行路径选择 | ExecMode / Runtime policy | 不应是任意 free dict 键；外部可控制内部分轨 |
| `execution_config.yaml/workflow_id` | 计划来源 | PlanSource（应结构化） | 散在 dict，无契约 |
| `execution_config.static_plan_id/static_plan_yaml/template_input` | 预置计划物化（运行时补写） | 内部运行态 | 不应长期驻留 Task 快照 / 不应经 HTTP 透传 |
| `execution_config.main_session_id` | 外部会话绑定 | ExternalExecutionBinding（Context/runtime） | Task 快照里混了外部绑定 |
| `execution_config.panel_component_name` | UI/业务面板组件 | UI/业务侧，非 Task 领域 | 不该进 Task 领域对象 |
| `execution_config.MAX_DEPTH/MAX_LOOP/重试` | Runtime/Recovery policy | RuntimePolicy，非 Context 非 Task | 经 HTTP 透传进内部运行参数 |
| 其余 `extra="allow"` 透传键 | 任意内部 flag | 不应存在 | 无 schema 口子 |

#### 实际影响

1. Task "是什么（目标）" 与 "怎么执行（task_type/计划/重试）" 绑在一个对象，两者生命周期不同（目标稳定、执行策略随重试/重规划变），导致目标与执行方式无法独立演进/审计。
2. `extra="allow"` 让 HTTP 请求能把 `static_plan_id/main_session_id/MAX_DEPTH/MAX_LOOP` 等内部字段透传到 engine，外部输入直接控制内部运行参数和分轨，Task 作为稳定契约的内聚性被破坏。
3. 持久化进 `graph.extend_props["execution_config"]` 后，查询/恢复 Task 都要连带加载/反解执行实现细节；新增一种执行模态会持续污染这条 dict。
4. Context 初始事实被吞进 `TaskSpec.context`：没有独立 API 记录初始输入事实，只能塞进 Task，造成 Context 事实分散、无法独立增删/溯源，也跟后续 Context 协议对不上。
5. 字段来源不可判：外部 Context 提供（背景/会话）/ Task 自身（目标/验收）/ Runtime 决定（task_type/重试）全混在一个 free dict + expandable DTO，无明确边界。

#### 判断 / 处理

- 保留：`task_spec.metadata + goal + acceptances`；`source_type`、`owner_*`（语义澄清后）。
- `TaskInfo` 改名 `TaskSnapshot`（任务稳定快照）。
- 拆分 `execution_config`：`task_type`/计划来源 → 结构化 `ExecMode`+`PlanSource`；`MAX_DEPTH/MAX_LOOP/重试` → `RuntimePolicy`；`main_session_id` → `ExternalExecutionBinding`（移出 Task 快照，进 Context/runtime）；`panel_component_name` → 移出 Task 领域（UI/业务侧）。
- `background/extend_props` 保留为 Task 初始规格内的描述，同时创建时落 Source Record，之后不再以 `TaskSpec.context` 名义当成完整 Context。
- 收紧 `extra="allow"` 为显式字段；`static_plan_*` 仅作内部信号，不入 Task 快照/不经 HTTP 透传。

### 3. 外部配置可以透传内部参数

```python
ExecutionConfigDTO.model_config = ConfigDict(extra="allow")
```

外部请求可以带入静态计划、重试、Session、内部开关等字段。

## 判断

```text
DTO → Domain Request：合理，保留
TaskSpec / Goal / AcceptanceCriteria：合理，保留
TaskSpec.context：可保留为初始输入，但不要当成完整 Context
TaskInfo：建议改名为 TaskSnapshot
extra="allow"：不合理，应收紧
```

这里需要补的是“初始输入事实的记录能力”，不是单独增加一次 Context RPC。

---

# 2. Task 图初始化

## Task 在做什么

创建 Task Graph、Root TaskNode 和初始状态。

## 是否涉及 Context

只涉及初始输入快照的关联，不需要读取复杂 Context。

## 当前代码

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_context/task_graph_service.py
```

主要方法：

```text
initialize_graph()
add_task_nodes()
add_relations()
```

领域对象：

```text
TaskExecutionGraph
TaskNode
Relation
```

## 当前问题

### 1. `TaskNode` 反向持有整张 Graph

涉及文件：

```text
.../community/core/task/domain/models.py                       # TaskNode.node_run_graph 定义
.../community/core/task/task_context/task_graph_service.py     # :293/:303/:349/:695 回填循环引用
.../community/core/task/repository/serializers.py             # :178 反序列化回填
.../community/core/task/task_center/task_service_execution.py # :79  构造传 None+ignore
.../community/core/task/task_plan/strategies.py                # :134/:277  构造传 None+ignore
.../community/core/task/task_plan/static_plan_runtime.py      # :46   构造传 None+ignore
.../community/core/task/task_runner/modal_executor/bbs_modal_executor.py # :139
```

#### 当前代码包含什么

```python
@dataclass            # models.py
class TaskNode:
    node_id: str
    task_id: str
    status: Status
    task_spec: TaskSpec
    run_info: RuntimeInfo
    node_run_graph: "TaskExecutionGraph"   # 节点所属执行图实例引用
```

`TaskExecutionGraph.tasks: list[TaskNode]`，于是形成 `Graph.tasks → Node → node_run_graph → Graph` 双向引用。
写入只在：根节点先传 `None` 后 `root.node_run_graph = graph` 回填；每个新子节点 `t.node_run_graph = graph`；反序列化同。多处构造点被迫写 `node_run_graph=None, # type: ignore[arg-type]`。

**关键事实：全仓库 grep 后 `node_run_graph` 只被"写"，从未被读取 reach-in（无任何 `node.node_run_graph.xxx` 消费点）。它是一个被迫存在的死字段。**

#### 是否涉及 Context

不直接是 Context 问题，是 Task Graph 内部模型的双向耦合。但 Node 经此引用可隐式 reach 整 Graph，未来 Context View 若借 Node 暴露会绕过显式边界，Task/Context seam 仍泄漏。

#### 不合理点 / 实际影响

1. **构造耦合**：构造一个测试 Node 必须先伪造 `TaskExecutionGraph`，否则只能 `None # type: ignore`，节点级单测与图强耦合（7+ 处 ignore 可见）。
2. **节点级 API 含义模糊**：拿到 Node 理论上可经 `node_run_graph` 改图/读兄弟节点，"只操作节点"和"影响整图"无契约保证。
3. **序列化/复制风险**：Graph↔Node 环引用，deepcopy/json 递归需手动去环（serializers 现按手动只回填不留环），新增序列化场景易踩坑。
4. **死字段成本**：被写不被读，新人易误判"读它拿图"是合法 API；长期维护一个环引用字段。
5. **Context 边界泄漏**：Context 独立后 Node 仍能 reach 整图，绕过显式 Context View 拿全局。

#### 判断 / 处理

- 删除 `TaskNode.node_run_graph` 字段。
- 父子/兄弟/结构关系统一经 `TaskGraph`/`GraphReader`（基于 `relations` 分解树，docstring 本就说关系从 `relations` 派生）。
- Node 构造不再要求 graph；反序列化不再回填环。
- Context 读取走显式 Context View 接口（传 `task_id/node_id`），不经 Node 反查图。
- 保留：`TaskExecutionGraph`（Task 结构聚合）、`Relation`（分解树边）。

### 2. Graph 混入外部执行图

涉及文件：

```text
.../community/core/task/domain/models.py                       # TaskExecutionGraph.execution_graph
.../community/core/task/task_center/task_service.py           # :436 merge / :601-628 dashboard attach
.../community/core/task/task_runner/callback_adapter.py        # :131 上报 execution_graph
.../community/core/task/task_runner/client/callback_data_enricher.py  # 构建外部 DAG
```

#### 当前代码包含什么

```python
@dataclass            # models.py
class TaskExecutionGraph:
    ...
    tasks: list[TaskNode]
    relations: list[Relation]
    extend_props: dict[str, Any] = field(default_factory=dict)
    execution_graph: dict[str, Any] | None = None  # 回调审计图快照(BCN/ClawMind DAG,按 session_id)
    task_id: str = ""
```

`execution_graph` 不是 Avernet planner 产出的，来源链路：

```text
外部 BCN/ClawMind 回投
 → callback_data_enricher._build_bcn/_build_claw_mind_execution_graph(整理外部 flow_runs/node_executions)
 → callback_adapter 上报 → task_callback 表最新行(merge_manager_worker_execution_graph)
 → 仅 dashboard 读取时:用 root.run_info.extend_props['session_id']
   → callback_repo.get_latest_by_session(sid) → graph.execution_graph = rec.execution_graph
```

即它是 BCN/ClawMind 外部编排系统的执行 DAG 快照，按 `session_id` 反查挂在 graph 级，只读、按需 attach、不进 graph repo。

#### 是否涉及 Context

强相关：本质是"外部执行过程事实/投影"——外部系统写、Avernet 只读消费。属 Context 应承载的 External Execution Projection / Context Event，不是 Avernet Task Graph 拓扑。

#### 不合理点 / 实际影响

1. **一个对象表达两种拓扑**：`tasks/relations` 是 Avernet Task 结构分解树；`execution_graph` 是 BCN/ClawMind 外部执行 DAG。节点 ID、边含义、版本、生命周期、写入者全不同，放一起调用方分不清读的是"Task 结构"还是"外部执行过程"。
2. **生命周期错配**：Avernet 图是长生命周期编排态；`execution_graph` 是某次执行快照（每次回调覆盖最新）。同框后"外部图更新了但 Task 结构没变"会带来不一致视角。
3. **体积/持久化负担**：外部 DAG 可能很大，作为 Graph 字段，凡是序列化/缓存/拷贝都要带这份外部快照（目前靠"只 dashboard attach、不入 graph repo"部分缓解，但字段仍在领域模型上）。
4. **状态语义混淆**：外部 `session.completed` ≠ Avernet `Task SUCCESS`；两图同框后 dashboard 易把外部执行成功误判为 Task 验收成功（呼应第 6 章回调验收问题）。
5. **外部系统泄漏进 Task domain**：`execution_graph` shape 与 BCN/ClawMind 绑死；换执行系统要改 `TaskExecutionGraph` 字段，Task domain 被外部实现污染。
6. **边界没立住**：当前靠"只在 dashboard 读取时 attach"做隔离，但字段仍声明在领域模型上，等同对外宣称"Graph 含外部执行图"。

#### 判断 / 处理

- `TaskExecutionGraph` 保留：`tasks/relations/status/output/loop_round/extend_props` + 一个外部执行"引用"（`session_id`，已在 `run_info.extend_props['session_id']`）。
- 移出 `TaskExecutionGraph.execution_graph` 完整内容，不再作为 Graph 字段。
- 分层归属（不要简单"全塞 Context 再引用"）：
  - 原始外部回投事件 → Context Event / Source Record（`task_callback` 表可作 Context Store 后端）；
  - 整理后的外部当前图 → `ExternalExecutionProjection`（独立对象，按 `session_id` 查询，dashboard 由 projection service 提供）；
  - Task 只保留引用：`session_id/external_execution_id` 及状态映射所需最小字段。

## 判断

```text
TaskExecutionGraph 的 Task 结构部分：保留
Relation：保留
TaskNode.node_run_graph：删除
execution_graph：移出 TaskExecutionGraph
```

Task Graph 可以保留外部执行引用，但不应保存外部执行图的完整内容。

---

# 3. 初次规划和重规划

## Task 在做什么

根据目标、已有结果和 gap 生成或补充子任务。

## 是否涉及 Context

涉及。这是 Context 的第一个主要读取位点。

Planner 需要：

```text
Task 目标
当前节点位置
父子关系
已有执行结果
历史尝试
当前 gap
```

## 当前代码

Planner：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_plan/planner.py
```

Graph 查询：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_context/task_graph_service.py
```

Engine 调用：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_center/engine.py
```

## 当前交互

Planner 直接读取：

```text
TaskExecutionGraph
TaskNode
Relation
TaskNode.run_info.output
TaskNode.run_info.acceptance_result
```

## 问题

### 1. Context 事实藏在 Task Graph 里

Planner 通过读 Node 和 Graph 获取事实，没有明确的 Context View。

### 2. 上下文范围不明确

没有明确：

```text
读取哪个版本
是否包含历史 attempt
是否包含外部 Session 内容
只读当前节点还是整个 Task
```

### 3. `PlanResult` 表达能力不足

当前主要只有：

```text
children
has_gap
gap_detail
```

没有记录规划原因、策略和轮次。

## 判断

```text
TaskPlanner：保留
规划策略：保留
Planner 直接读取 run_info.output：逐步替换
PlanResult：升级为 PlanDecision
```

Context 在这里应该提供：

```text
resolve_view(purpose="planning")
```

但规划和重规划仍然是 Task 的职责。

---

# 4. 派发和执行绑定

## Task 在做什么

决定由哪个 Bot、协作群或 BBS 承接，以及使用什么执行模态。

## 是否涉及 Context

涉及，主要是保存执行关系：

```text
Task / Node
  ↔ Bot / Group / BBS
  ↔ Session / External Run
```

## 当前代码

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_dispatch/dispatcher.py
```

策略：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_dispatch/strategies.py
```

## 当前交互

```text
TaskDispatcher.dispatch()
  → TaskNode.run_info.run_mode
  → TaskNode.run_info.assignee
  → RuntimeInfo.extend_props
```

## 问题

派发决策和执行绑定没有分开。

当前无法清晰区分：

```text
为什么选这个 Bot
最终实际绑定了哪个 Session
本次是第几次 Execution
```

`assignee`、`run_mode`、`session_id` 分散在不同对象中。

## 判断

```text
TaskDispatcher：保留
SearchResult：升级为 DispatchDecision
ExecutionBinding：需要独立建模
```

Task 负责“选择谁执行”；Context 记录“实际绑定关系”。

---

# 5. 执行输入组装和执行启动

## Task 在做什么

为当前节点准备输入，调用 Bot、群、BBS 或 Workflow。

## 是否涉及 Context

涉及。这是 Context 最重要的消费位点之一。

需要读取：

```text
当前节点规格
父节点信息
兄弟节点已完成结果
历史 Execution
验收要求
Artifact
```

## 当前代码

Runner：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_runner/task_runner.py
```

方法：

```text
TaskRunner._build_context()
```

Engine：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_center/engine.py
```

方法：

```text
ExecutionEngine.build()
```

Prompt：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_runner/client/prompt_formatter.py
```

## 当前交互

Runner 和 Engine 都直接读取：

```text
parent
siblings
children
TaskNode.run_info.output
```

再自行拼装 Prompt。

## 问题

### 1. 上下文组装重复

`TaskRunner._build_context()` 和 `ExecutionEngine.build()` 都在决定节点能看到什么。

这是明确的冗余。

### 2. 上下文读取和 Prompt 格式化耦合

输入范围由 Runner/Engine 的代码分支决定，而不是由 Context View 定义。

### 3. 重试读取的是当前输出，不是某次 Execution 的输出

容易覆盖历史执行结果。

## 判断

```text
TaskRunner._build_context()
ExecutionEngine.build()
```

应该合并为：

```text
ContextViewResolver
```

Runner 保留“调用执行器”的职责，不再决定上下文范围。

---

# 6. 执行结果回调

## Task 在做什么

接收外部执行结果，决定是否继续推进。

## 是否涉及 Context

涉及，主要是记录执行事实：

```text
执行开始
过程消息
输出
错误
Artifact
原始回调
外部 Session 状态
```

## 当前代码

回调协议：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_loop_callback_protocol.py
```

回调实现：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_runner/callback_adapter.py
```

状态处理：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_center/engine.py
```

## 当前交互

```text
Callback
  → CallbackAdapter
  → TaskNodePatch
  → ExecutionEngine.on_report()
  → TaskNode / RuntimeInfo
```

## 问题

### 1. CallbackAdapter 职责过重

同时负责：

```text
解析
兼容旧协议
关联 Task/Node
判断成功失败
构造验收结果
构造 Patch
幂等
审计
fallback 持久化
```

### 2. 外部执行成功被当成 Task 验收成功

当前存在：

```text
success=True
  → AcceptanceVerdict.DONE
  → Task SUCCESS
```

这是语义错误。

外部 Runtime 只能证明“执行完成/返回成功”，不能天然证明“满足 Task 验收标准”。

### 3. `TaskNodePatch` 混合事实和控制

文件：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/domain/models.py
```

同时包含：

```text
output_patch
exec_error
acceptance_result
status
assignee
extend_props_patch
```

## 判断

保留：

```text
原始回调
幂等
Task/Node/Session 关联
协议兼容层
```

拆分：

```text
ExecutionObservation
AcceptanceDecision
TaskControlCommand
```

最终不应继续使用：

```text
CallbackAdapter → TaskNodePatch → 直接驱动 Task 状态
```

---

# 7. 验收、重试和状态迁移

## Task 在做什么

根据执行事实决定：

```text
验收通过
验收失败
重试
重规划
进入 BBS
HUNG
```

## 是否涉及 Context

需要读取 Context，但决策属于 Task。

Context 提供：

```text
output
artifact
执行历史
失败原因
验收证据
```

Task 负责：

```text
Acceptance Decision
Retry
Replan
BBS
状态迁移
```

## 当前代码

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_center/engine.py
```

主要方法：

```text
on_report()
_on_pass_collect()
_on_fail_collect()
_on_harness_collect()
on_harness()
```

## 问题

### 1. `AcceptanceResult` 信息不足

当前只有：

```text
verdict
acceptances_metric
gaps
```

没有明确：

```text
验收哪次 Execution
依据哪个 output 版本
使用哪些证据
谁做出的判断
使用哪个验收规则
```

### 2. 状态语义不稳定

当前不同路径可能出现：

```text
执行成功 → SUCCESS
验收失败 → HUNG
执行错误 → 重试
外部托管任务 → 保留外部状态
```

`DONE / SUCCESS / FAILED / HUNG` 的边界需要先冻结。

## 判断

```text
验收决策和状态迁移：保留在 Task
验收依据和证据：放入 Context
AcceptanceResult：升级为 AcceptanceDecision
```

---

# 8. BBS 接力

## Task 在做什么

```text
进入 BBS
claim
attach 子节点
接收回投
继续规划或收口
```

## 是否涉及 Context

涉及，主要是 BBS 过程事实：

```text
gap
交接说明
claim / handoff / result
BBS 消息
外部协作 Session
```

## 当前代码

HTTP DTO：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/adapters/http/task/schemas.py
```

TaskService：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_center/task_service.py
```

方法：

```text
claim_bbs_task()
attach_bbs_node()
report_bbs_result()
```

Engine：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_center/engine.py
```

## 当前交互

```text
BBS / manager_worker event
  → TaskCallback
  → TaskExecutionGraph.execution_graph
  → TaskGraphService
```

## 问题

### BBS 过程内容和 Task Graph 混合

`TaskExecutionGraph` 应保存：

```text
Task 节点结构
Task 控制状态
```

BBS 消息和 handoff 内容属于：

```text
外部协作过程事实
```

写入 Graph 会导致：

- Graph 变成消息存储，内容大小和 Task 结构耦合；
- 查询 Task 状态时被迫加载过程内容；
- BCN / ClawMind 数据结构泄漏进 Task 领域对象；
- 外部事实和 Avernet 状态混在一起，状态权威不清；
- 将来替换 BBS/BCN 实现时需要修改 Task Graph 模型。

## 判断

保留在 Task：

```text
是否进入 BBS
当前 claim owner
BBS 子节点关系
BBS 控制状态
```

移出 Task Graph：

```text
BBS 消息
handoff 内容
外部 execution_graph
原始 BBS 事件
```

这些内容进入 Context Event / BbsInteractionRecord / ExternalExecutionProjection。

---

# 9. Dashboard 和查询

## Task 在做什么

提供：

```text
当前 Task 状态
节点结构
执行概览
历史记录
```

## 是否涉及 Context

涉及读取，但不应改变 Task。

## 当前代码

TaskService：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_center/task_service.py
```

方法：

```text
get_task_dashboard()
list_tasks()
list_tasks_page()
list_bbs_tasks()
```

GraphService：

```text
/Users/guoke.gk/PycharmProjects/temp/Avernet/src/backend/src/agentclaw/community/core/task/task_context/task_graph_service.py
```

方法：

```text
query_task_dashboard()
list_task_summaries()
load_action_logs()
```

## 问题

`TaskGraphService` 同时负责：

```text
图存储
节点写入
查询
Dashboard 投影
BBS
ActionLog
回调审计桥
```

职责过宽。

## 判断

保留外部查询 API，但内部应合并为：

```text
TaskQueryService
```

查询结果使用独立 View，不直接把完整 `TaskExecutionGraph` 作为所有查询结果。

---

# 10. 当前应该合并、拆分和删除什么

## 应合并

```text
TaskRunner._build_context()
ExecutionEngine.build()
```

合并为：

```text
ContextViewResolver
```

---

```text
TaskGraphService 的查询逻辑
TaskService 的 Dashboard/List 逻辑
```

内部合并为：

```text
TaskQueryService
```

---

```text
外部 callback ingress
```

可以统一接收原始事件，内部再分成：

```text
RunStarted
RunCompleted
RunFailed
RunProgressed
```

当前 `start_run()` / `report_result()` 可以暂时保留兼容，但不应继续直接修改 Task 状态。

## 应拆分

```text
TaskNodePatch
```

拆成：

```text
ExecutionObservation
AcceptanceDecision
TaskControlCommand
```

---

```text
RuntimeInfo
```

拆出：

```text
ExecutionRuntime
DispatchInfo
RecoveryRuntime
BbsClaim
OpaqueMetadata
```

---

```text
TaskNodeRunInfo
```

逐步明确为：

```text
ExecutionAttempt
ExecutionBinding
ExecutionOutcome
```

## 应删除或移出

```text
TaskNode.node_run_graph
TaskExecutionGraph.execution_graph
TaskNodePatch 作为长期统一写协议
ExecutionConfigDTO.extra="allow"
owner_bot_id 的复合字符串格式
RuntimeInfo.extend_props 中的核心控制字段
```

---

# 11. 和之前 Context 协议的关系

之前协议中的以下内容仍然成立：

```text
Source Record
Execution Binding
Execution Outcome
Context View
Artifact
Event
```

它们对应到 Task 流程是：

| Task 位点 | Context 语义 |
|---|---|
| 任务创建 | Source Record |
| 派发和启动 | Execution Binding |
| 执行过程和回调 | Event / Execution Outcome |
| Planner / Runner / Verifier 读取 | Context View |
| BBS 消息、报告、大结果 | Event / Artifact |

之前主要的问题不是协议类型错了，而是：

```text
先讲 Context 抽象
后讲 Task 流程
```

导致 Context 看起来很多，也没有说明一次 Task 到底什么时候和 Context 交互。

另外，之前还缺少 Task 与 Context 之间的连接说明：

```text
Task 创建时如何记录输入
Task 如何创建 Execution Binding
Task 如何读取 Context View
Task 如何把 Observation 转成验收决策
Task 如何基于事实执行 Retry / Replan / BBS
```

这些不一定需要更多 Context API，但需要明确交互职责。

---

# 12. 最终模型

```text
Task 创建事务
  ├─ 创建 Task Snapshot / Task Graph
  └─ 写入初始 Source Record

Task Runtime
  ├─ 请求 Context View
  ├─ 做 Plan / Dispatch
  ├─ 创建 Execution Binding
  ├─ 接收 Execution Observation
  ├─ 做 Acceptance Decision
  └─ 做 Retry / Replan / BBS / 状态迁移

Context
  ├─ 保存 Source Record
  ├─ 保存 Execution Binding
  ├─ 保存 Event / Outcome / Artifact
  └─ 提供按用途裁剪的 View
```

最核心的判断：

> **Task 创建和初始 Context 记录可以是一次交互、一个事务；但后续执行事实和上下文读取应通过独立 Context 能力完成。不要为了“形式上的分离”把一次简单创建拆成多次 API 往返。**
