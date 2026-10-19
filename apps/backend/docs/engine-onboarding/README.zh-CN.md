# 自带引擎接入指南

[English](README.md)

本指南面向**自己运行 Agent 引擎**、希望把 bot 接入 Avernet 平台的团队。接入后，
用户在 Avernet 工作台里创建、配置你们的 bot 并与之对话，体验与其他 bot 一致；
Agent 本身由你们的引擎运行。

如果你们只想让 bot 加入多 bot 群聊，可以跳过本指南的大部分内容，直接阅读
[Bot 接入指南](../../../../docs/bot-integration.zh-CN.md)（见[第 6 步](#第-6-步可选加入群体协作bcn)）。

> **状态说明。**第 2 至 5 步描述的是平台与第一个外部引擎（teclaw）之间已经在用
> 的契约。第 1 步（注册**由你们自己托管**的引擎）**尚未定案**。标注 **待确认** 的
> 事项须在上线前与 Avernet 团队确定，见[待确认事项](#待确认事项)。

---

## 目录

- [接入原理](#接入原理)
- [接入清单](#接入清单)
- [第 1 步：注册引擎](#第-1-步注册引擎)
- [第 2 步：接收 bot 配置（`POST /api/v1/bot/apply`）](#第-2-步接收-bot-配置)
- [第 3 步：理解配置 artifact](#第-3-步理解配置-artifact)
- [第 4 步：实现文件接口](#第-4-步实现文件接口)
- [第 5 步：实现运行时接口](#第-5-步实现运行时接口)
- [第 6 步（可选）：加入群体协作（BCN）](#第-6-步可选加入群体协作bcn)
- [第 7 步：联调自查](#第-7-步联调自查)
- [通用约定](#通用约定)
- [待确认事项](#待确认事项)
- [参考文档](#参考文档)

---

## 接入原理

```text
 Avernet 工作台（用户）
          │
          ▼
 Avernet 平台 ──── 1. POST /api/v1/bot/apply （整包 bot 配置）────▶ ┐
  （backend） ──── 2. /api/v1/file/*          （编辑文件）    ────▶ │  你们的引擎
              ──── 3. /api/sessions、/api/{engine}/ws（对话） ────▶ │ （一个 HTTP 服务，
                                                                    │   承载一个或多个 bot）
 内容存储（OSS）◀──── 引擎下载 skill / 文件 ─────────────────────── ┘
```

需要理解三点：

1. **配置归平台，应用归引擎。**用户在 Avernet 里管理 bot 的 skill、MCP 服务、
   身份文件、资源文件和 CLI 工具。任何一项变化，平台都会组装出该 bot 的**完整**
   配置（即 *artifact*），发给你们的引擎。
2. **artifact 是全量快照，不是增量。**每次下发都描述完整的期望状态，引擎让 bot
   与之完全一致。
3. **大内容按引用传递。**artifact 不含文件内容，只含 `{store, path}` 引用，由
   引擎从内容存储下载。

---

## 接入清单

| # | 要做的事 | 是否必须 |
|---|---|---|
| 1 | 注册引擎（引擎类型、访问地址、鉴权方式） | 必须 |
| 2 | 实现 `POST /api/v1/bot/apply`，按整包替换语义应用 artifact | 必须 |
| 3 | 从内容存储下载 artifact 引用的内容 | 必须 |
| 4 | 实现文件接口（`/api/v1/file/*`） | 工作台文件编辑需要 |
| 5 | 实现运行时接口（健康检查、会话、对话） | 工作台对话需要 |
| 6 | 接入 BCN | 仅多 bot 协作需要 |
| 7 | 通过联调自查清单 | 必须 |

---

## 第 1 步：注册引擎

平台要把请求路由到你们的引擎，需要以下信息：

| 项 | 说明 | 状态 |
|---|---|---|
| **引擎类型** | 简短的小写标识，如 `teclaw`。它会作为 `engine_type` 出现在每份 artifact 里。 | 与 Avernet 团队商定 |
| **访问地址** | 平台调用的 HTTPS 地址。本指南中的路径都相对于它。 | **待确认** |
| **鉴权** | 平台如何向引擎证明身份，以及引擎如何向平台证明身份。 | **待确认** |
| **bot 寻址** | 一个服务承载多个 bot 时，一次请求如何指定某个 bot。 | **待确认** |

**当前机制（平台托管的引擎）。**目前外部引擎的容器由平台托管，经平台的代理网关
访问。容器收到的请求带 `x-proxypass-token` 请求头；bot 由请求体中的 `bot_id`
字段标识（skill 包上传则用 `x-target-bot-id` 请求头）。对于**你们自己托管**的
引擎，访问地址的注册方式和鉴权方案都还没有设计。请在开始第 2 步之前与 Avernet
团队确定，因为它们决定了下文每个请求的请求头。

---

## 第 2 步：接收 bot 配置

### `POST /api/v1/bot/apply`

bot 配置每次变化时平台都会调用此接口：创建 bot、用户编辑 skill / MCP 服务 /
身份文件 / 资源文件 / 渠道、发布 bot。

**请求**

```http
POST /api/v1/bot/apply
Content-Type: application/json
```

```json
{
  "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
  "operation": "UPDATE",
  "bot_config": { "...": "artifact，见第 3 步" }
}
```

| 字段 | 说明 |
|---|---|
| `bot_id` | 本次配置对应的 bot。 |
| `operation` | 目前固定为 `"UPDATE"`。遇到不认识的值按错误处理。 |
| `bot_config` | 完整的 artifact（见[第 3 步](#第-3-步理解配置-artifact)）。 |

**响应**

- **整份 artifact 应用完成后**才返回 HTTP `2xx`。
- 应用失败时返回非 `2xx`。平台会把失败告知用户，并可能稍后重新下发。
- 平台等待 **30 秒**。如果应用可能更久（例如下载较大的 skill 包），请与 Avernet
  团队沟通超时时间。

目前平台不读取响应体。建议使用标准信封（见[通用约定](#通用约定)）：
`{"success": true}`。

**首次配置。**对于平台托管的引擎，第一份 artifact 在容器创建时交给容器，早于
bot 启动。自托管引擎如何收到第一份 artifact **待确认**。在此之前，请预期会收到
一个未见过的 bot 的 `POST /api/v1/bot/apply`，届时创建该 bot。

### 应用 artifact 的规则

以下五条是契约的核心，引擎必须全部遵守。

| # | 规则 | 含义 |
|---|---|---|
| R1 | **整包替换** | 应用后，bot 恰好拥有 artifact 列出的内容。artifact 列出 skill `{A, B}`、bot 原有 `{B, C}`，结果是 `{A, B}`，`C` 被移除。不做合并。 |
| R2 | **永不触碰 `MEMORY.md` 与 `IDENTITY.md`** | 它们是引擎生成的运行期状态，不是配置。不得因 artifact 写入它们，也不得删除它们，即使 `identity_files` 没有列出。这是 R1 唯一的例外。 |
| R3 | **幂等** | 同一份 artifact 应用两次与应用一次结果相同，不留残留文件、不产生重复条目。重复下发是正常情况，不是错误。 |
| R4 | **先应用，后就绪** | 整份 artifact 应用完成之前，不得报告 bot 已就绪。 |
| R5 | **忽略未知字段** | 契约会演进。遇到不认识的字段直接忽略，不要拒绝整份 artifact。 |

### 「替换」的范围按类目划分

R1 替换的是*每个类目自己区域内*的状态，各类目的区域不同：

| 类目 | 被替换的区域 |
|---|---|
| `skills` | bot 的已激活 skill 集合。未列出的 skill 被停用。 |
| `identity_files` | bot 的身份文件，**不含** `MEMORY.md` 与 `IDENTITY.md`。 |
| `resources` | **此前你们作为资源投递过的路径**，加上本次 artifact 列出的路径。 |
| `mcp.servers` | bot 已启用的 MCP 服务集合。 |
| `cli_tools` | 平台下发的 CLI 工具。镜像自带的命令不受影响。 |

> **`resources` 需要特别注意。**它的区域有两条边，缺一不可。
>
> - **不能过窄。**`resources` 从 `[data/a.csv]` 变成 `[]` 时，必须删除
>   `data/a.csv`。新 artifact 不再提及它，所以引擎要自己记录哪些路径是作为资源
>   投递的。
> - **不能过宽。**工作区属于 bot 和它的用户。用户或模型在其中创建的文件不是资源，
>   即使紧挨着某个资源路径，也绝不能删除。

### 每个类目归谁管理：`ownership`

artifact 可能带一个 `ownership` 映射，按类目说明平台给出的列表是否权威：

```json
"ownership": {
  "mcp": "platform",
  "skills": "platform",
  "resources": "engine",
  "identity_files": "engine"
}
```

| 值 | 引擎的处理 |
|---|---|
| `"platform"` | 本次 artifact 中该类目的列表就是完整期望状态，按 R1 处理。空列表表示清空该区域。 |
| `"engine"` | 该类目由引擎管理。忽略 artifact 中的列表，保持引擎当前状态。 |
| 缺失（整个映射或其中某个键） | 保持引擎原有行为。 |

`mcp` 永远是 `"platform"`。用户在工作台里编辑单个文件时，文件类目为
`"engine"`，因为运行中的 bot 是其文件的真相源。应用 bot 配置清单（manifest）时，
所有类目都是 `"platform"`。

---

## 第 3 步：理解配置 artifact

artifact 的正式定义是 JSON Schema
[`artifact.schema.json`](../../src/agentclaw/community/kernel/bot_config/artifact.schema.json)，
请在测试中用它校验。本节解释各字段。

### 示例

```json
{
  "schema_version": 4,
  "version": 7,
  "engine_type": "teclaw",
  "stores": {
    "bot-data":   { "type": "oss", "bucket": "avernet-prod", "base": "teclaw/prod/bolt_data" },
    "skill-repo": { "type": "oss", "bucket": "avernet-skills", "base": "skills" }
  },
  "skills": [
    { "name": "weather", "scope": "shared", "store": "skill-repo", "path": "team/weather" }
  ],
  "identity_files": [
    { "name": "RULES.md", "store": "bot-data", "path": "staff_u1/bot7/identity/RULES.md" }
  ],
  "resources": [
    { "name": "data/faq.csv", "store": "bot-data", "path": "staff_u1/bot7/workspace/data/faq.csv" }
  ],
  "mcp": {
    "servers": [
      { "server_code": "search", "transport": "http",
        "endpoint": "https://mcp.example.com/search", "headers": { "x-api-key": "..." } },
      { "server_code": "local-db", "transport": "stdio",
        "command": "python3", "args": ["-m", "db_mcp"], "env": { "DB_PATH": "/data/db" } }
    ]
  },
  "cli_tools": [
    { "name": "mycli", "store": "bot-data", "path": "staff_u1/bot7/cli/mycli",
      "md5": "9f2c1b7d4e5a60318c2f0ab4d7e9c135", "version": "1.4.2" }
  ],
  "engine_overrides": { "channels": { "...": "..." } },
  "ownership": { "mcp": "platform", "skills": "engine", "resources": "engine", "identity_files": "engine" },
  "engine_ext": {
    "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
    "owner_id": "u1",
    "bot_name": "Support Bot",
    "stage": "draft"
  }
}
```

### 顶层字段

| 字段 | 必有 | 说明 |
|---|---|---|
| `schema_version` | 是 | 契约版本，当前为 `4`。部分字段（`cli_tools`、`ownership`）是在不升版的情况下加入的，所以要**判断字段是否存在**，不要依赖版本号。 |
| `version` | 是 | 该 bot 的内容版本。已发布的 bot 为整数；用户仍在编辑的草稿 bot 为 `null`。 |
| `engine_type` | 是 | 你们的引擎类型（第 1 步确定）。 |
| `stores` | 是 | 内容存放位置，以 store ID 为键。见[下载内容](#下载内容)。 |
| `skills` | 是 | 要激活的 skill。 |
| `identity_files` | 是 | 用户或平台编写的工作区文件（如 `RULES.md`、`AGENTS.md`）。 |
| `resources` | 是 | 用户上传到 bot 工作区的文件。 |
| `mcp.servers` | 是 | bot 可用的 MCP 服务。 |
| `cli_tools` | 否 | 要放到 Agent `PATH` 上的命令行工具。 |
| `engine_overrides` | 是 | bot 级设置（如渠道），叠加在引擎自己的基础配置之上。基础配置文件归引擎所有，平台从不下发。 |
| `ownership` | 否 | 各类目的归属，见[第 2 步](#每个类目归谁管理ownership)。 |
| `engine_ext` | 是 | 归引擎所有的自由 JSON，见下文。 |

### 文件引用（`skills`、`identity_files`、`resources`）

每一项是 `{name, store, path}`：`store` 是 `stores` 中的键，`path` 是相对于该
store 的路径。**文件在引擎内落到哪里由你们决定。**

- `skills[]` 多一个 `scope` 字段：`"shared"`（来自 skill 目录）或 `"user"`
  （用户为该 bot 上传）。skill 的 `path` 是一个**目录前缀**：下载其下所有对象
  即得到完整的 skill 包。
- `identity_files[]` 与 `resources[]` 指向单个文件。`name` 是该文件在 bot 身份
  区域或工作区内的路径（如 `data/faq.csv`）。

### MCP 服务

每个服务有一个 `server_code`，并且恰好是以下两种形态之一：

| 形态 | `transport` | 字段 | 引擎的处理 |
|---|---|---|---|
| 远程 | `"http"` 或 `"sse"` | `endpoint`（必有）、`headers` | 带这些请求头连接该 URL。 |
| 本地 | `"stdio"` | `command`（必有）、`args`、`env` | 以子进程启动 `command`，通过 stdin/stdout 进行 JSON-RPC 通信。 |

> **敏感信息。**远程条目可能内联凭证，位于 `headers` 中或 `endpoint` 的
> `?authorization=` 查询参数里。请把 artifact 当作机密：不要完整打印到日志，
> 不要明文存储。

### CLI 工具

每一项就是**一个命令、一个可执行文件**：

| 字段 | 说明 |
|---|---|
| `name` | 命令名。Agent 以 `mycli` 而非绝对路径调用。同一 bot 内唯一。 |
| `store`、`path` | 可执行文件。平台已经完成下载、校验和解包，`path` 永远指向单个文件，不会是目录或压缩包。 |
| `md5` | 文件的 MD5。与已有同名工具一致则跳过下载；不一致则替换文件。 |
| `version` | 仅用于展示。 |

引擎需要：下载文件、设置可执行权限（对象存储不保留文件权限位）、以 `name` 为
命令名放到 Agent 进程的 `PATH` 上。不再列出的工具要移除。`"cli_tools": []`
表示移除所有平台下发的工具。镜像自带的命令不受影响。

### `engine_ext`

`engine_ext` 归你们所有，平台只负责保存并原样返回，从不解读。平台总会写入四个键：

| 键 | 值 |
|---|---|
| `bot_id` | 平台侧的 bot ID。 |
| `owner_id` | 所有者的用户 ID。 |
| `bot_name` | bot 的显示名称。 |
| `stage` | `draft`（编辑中）、`canary`（验证）、`release`（已发布）或 `eval`。 |

### 下载内容

`stores` 给出每个 store 的位置，**从不包含凭证**：

| 字段 | 说明 |
|---|---|
| `type` | `oss` 或 `nas`。 |
| `bucket` | OSS bucket。 |
| `base` | bucket 内的 key 前缀。 |
| `endpoint`、`region` | 可选。缺失时使用引擎自己的配置。 |

一个引用对应的对象 key 为 `base` + `/` + `path`。

自托管引擎如何获得这些 store 的**读权限**（凭证、网络连通）**待确认**。

---

## 第 4 步：实现文件接口

用户可以在工作台里浏览和编辑 bot 的文件。平台通过引擎上的文件接口完成这些操作，
每个文件一次请求。运行中的 bot 是其文件的真相源，所以这些编辑不经过
`/api/v1/bot/apply`。

### 路径

平台发出的每个路径都以下列三个命名空间之一开头：

| 命名空间 | 内容 |
|---|---|
| `/workspace/...` | bot 的工作区（资源文件）。用户上传的 skill 位于 `/workspace/skills-local/<skill 名>/`。 |
| `/identity/...` | 身份文件（`AGENTS.md`、`RULES.md`、`MEMORY.md`、`IDENTITY.md` 等）。 |
| `/config/...` | 引擎为该 bot 保存的自有配置（如果开放的话），例如 `/config/<engine>.json`。 |

请把它们映射到引擎内的真实位置，并拒绝越出命名空间的路径（例如含 `..`）。

### 接口

所有请求均为 `POST`。

| 接口 | 请求 | 成功响应 |
|---|---|---|
| `/api/v1/file/upload` | multipart：`file`（文件内容）与 `target_path`（表单字段，以它为准） | `2xx`。自动创建父目录；文件已存在则覆盖。 |
| `/api/v1/file/read` | JSON `{"file_path": "/workspace/a.txt"}` | `2xx`，响应体为**文件原始字节**。文件不存在返回 `404`。 |
| `/api/v1/file/list` | JSON `{"dir_path": "/workspace", "recursive": false}` | `2xx`，返回 `{"success": true, "data": {"files": [...]}}`（见下）。 |
| `/api/v1/file/remove` | JSON `{"target_path": "/workspace/old"}` | `2xx`。删除文件**或整个目录树**。 |
| `/api/v1/file/skill-package` | multipart：`file`（`.zip`）、表单字段 `skills_name`、请求头 `x-target-bot-id` | 见下。 |

**列表条目。**`data.files` 中的每一项：

```json
{ "name": "faq.csv", "path": "/workspace/data/faq.csv",
  "relative_path": "data/faq.csv", "size": 1024, "is_dir": false }
```

**skill 包上传。**把 zip 解压到 `/workspace/skills-local/<skills_name>/`，替换
原有内容。响应：

```json
{ "success": true, "skill_name": "weather", "action": "created",
  "sha256": "<上传 zip 的十六进制 sha256>",
  "target_path": "/workspace/skills-local/weather" }
```

`action` 取 `created`、`replaced` 或 `unchanged`。平台会校验 `skill_name` 与
`sha256` 是否与发送的一致。错误：包无效返回 `400`，包过大返回 `413`。

---

## 第 5 步：实现运行时接口

这些接口支撑工作台中的对话和 bot 管理。平台把用户请求转发给引擎并返回引擎的
应答，所以引擎没有实现的接口，对应功能在你们的 bot 上就不可用。

| 接口 | 用途 |
|---|---|
| `GET /health` | 存活检查，返回 `{"status": "ok"}`。 |
| `GET /api/engine/status` | 该 bot 的引擎状态。 |
| `GET /api/engine/capabilities` | `{"success": true, "data": {"supported": [...]}}`：引擎支持的功能。 |
| `GET /api/models`、`GET /api/models/{model_id}` | bot 可用的模型。 |
| `GET /api/sessions`、`POST /api/sessions` | 列出、创建会话。 |
| `GET`/`DELETE /api/sessions/{session_id}` | 读取、删除单个会话。 |
| `POST /api/sessions/{session_id}/update` | 更新会话元数据。 |
| `GET`/`DELETE /api/sessions/{session_id}/messages` | 读取、清空会话消息。 |
| `WebSocket /api/{engine_type}/ws` | 实时对话（发送消息、流式回复、中止）。 |

**会话 ID。**会话 ID 可能包含冒号（如 `agent:default:default:c_01M2F9:user:272471`）。
部分代理会拒绝路径中的冒号，因此平台可能以**去掉填充的 base64url 编码**发送 ID。
所有会话路由都要同时接受这两种形式。

平台对这组接口的参考实现是 Avernet 引擎适配层（`engine/adapter/`，见其
[开源指南](../../../../engine/adapter/docs/open-source-guide.zh-CN.md)）。各运行时
接口的具体请求/响应结构，以及对话 WebSocket 的帧协议，**待与 Avernet 团队确认**。
在书面规范出来之前，以引擎适配层为参考。

---

## 第 6 步（可选）：加入群体协作（BCN）

要让 bot 参与多 bot 群聊、路由与协作，需要让每个 bot 通过 WebSocket 连接 Bot
协作网络（BCN）。这一步与第 2 至 5 步相互独立，在
[Bot 接入指南](../../../../docs/bot-integration.zh-CN.md)中有完整说明和可运行的
快速入门。

---

## 第 7 步：联调自查

上线前请逐项检查。Avernet 团队联调时也会验证同样的项目。

**应用 artifact**

- [ ] 应用后，artifact 不再列出的内容已消失（R1）。
- [ ] 每次应用后 `MEMORY.md` 与 `IDENTITY.md` 都还在且未被修改，包括
      `identity_files` 未列出它们的情况（R2）。
- [ ] `resources` 从 `[data/a.csv]` 变成 `[]` 后，`data/a.csv` 被删除。
- [ ] 所有资源整体移到新目录后，旧目录下没有残留。
- [ ] 用户或模型在工作区创建的文件从未被删除。
- [ ] 同一份 artifact 连续应用两次，第二次没有任何变化（R3）。
- [ ] 整份 artifact 应用完成后 bot 才报告就绪（R4）。
- [ ] 含未知字段的 artifact 被正常接受（R5）。
- [ ] `ownership.resources = "engine"` 时，`resources` 列表不产生任何效果。
- [ ] `ownership.identity_files = "platform"` 且列表为空时，除 `MEMORY.md` 与
      `IDENTITY.md` 外的身份文件全部被移除。
- [ ] 不带 `ownership` 的 artifact 行为与以前完全一致。

**CLI 工具**

- [ ] Agent 可以按 `name` 直接调用每个工具。
- [ ] `md5` 未变的工具不会重复下载；`md5` 变化时文件被替换。
- [ ] 从列表中移除的工具无法再被调用。
- [ ] `"cli_tools": []` 移除所有平台下发的工具，且不影响镜像自带命令。

**文件与运行时**

- [ ] 在 `/workspace` 与 `/identity` 下上传、读取、列出、删除均正常。
- [ ] 读取不存在的文件返回 `404`。
- [ ] `remove` 能删除整个目录。
- [ ] 会话路由同时接受原始与 base64url 编码的会话 ID。

---

## 通用约定

**JSON 信封。**JSON 接口返回：

```json
{ "success": true, "data": { "...": "..." }, "message": "可选说明" }
```

请求失败时返回 `"success": false` 并附 `message`（可选附 `error` 错误码）。
**即使 HTTP 状态为 `200`，平台也把 `"success": false` 视为失败。**例外是
`/api/v1/file/read`（返回原始字节）和 `/api/v1/file/skill-package`（字段位于
顶层）。

**错误码。**输入无效用 `400`，路径被禁止用 `403`，对象不存在用 `404`，引擎
内部故障用 `5xx`。

**安全。**artifact 和部分文件内容可能包含用户的敏感信息。请使用 HTTPS，不要
完整打印请求内容，不要明文保存凭证。

---

## 待确认事项

上线前需与 Avernet 团队确定：

| # | 事项 |
|---|---|
| O1 | 自托管引擎如何向平台注册访问地址。 |
| O2 | 平台如何向引擎鉴权，以及引擎如何向平台鉴权。 |
| O3 | 一个服务承载多个 bot 时，一次请求如何指定某个 bot。 |
| O4 | 自托管引擎如何收到 bot 的第一份 artifact（以及如何得知 bot 已被删除）。 |
| O5 | 引擎如何获得内容存储的读权限。 |
| O6 | 运行时接口响应结构及对话 WebSocket 协议的书面规范。 |

---

## 参考文档

| 文档 | 内容 |
|---|---|
| [`artifact.schema.json`](../../src/agentclaw/community/kernel/bot_config/artifact.schema.json) | artifact 的正式定义。 |
| [跨引擎收敛语义契约](../bot-config-manifest/engine-convergence-contract.zh-CN.md) | 第 2 步应用规则的完整论证。 |
| [CLI 工具契约](../bot-config-manifest/teclaw-cli-contract.zh-CN.md) | `cli_tools` 的完整契约及用例。 |
| [Bot 接入指南](../../../../docs/bot-integration.zh-CN.md) | 将 bot 接入 BCN 参与群体协作。 |
| [引擎适配层开源指南](../../../../engine/adapter/docs/open-source-guide.zh-CN.md) | 运行时接口的参考实现。 |
