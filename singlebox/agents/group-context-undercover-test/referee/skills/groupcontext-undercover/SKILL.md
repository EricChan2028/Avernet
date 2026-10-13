---
name: groupcontext-undercover-referee
description: 简化版谁是卧底裁判，通过 GroupContext 测试发牌、查询、状态更新，不使用 state_machine。
allowed-tools:
  - exec
---

# 谁是卧底 · 裁判简化版（GroupContext 测试用）

目标：用尽量简单、稳定的流程测试 GroupContext 的 add / get / list / update / visible_to。不要使用 state_machine，不要依赖复杂阶段机渲染。

## 运行上下文

- `group_id` 固定为 `game-room-7`。
- `session_id` 必须使用本次 BCS 群聊投递里的会话 ID，形如 `bcs_grp_xxxx:yyyy`；不要使用裁判 Agent 自己的 workspace session、run_id 或新建会话 ID。
- `referee_uuid` 使用本次 BCS 投递的 `GroupContext.recipient`，也就是裁判自己的正式 Bot ID。
- 所有 GroupContext 命令都带 `--group-id`；session 级别命令还必须带 `--session-id`。
- 需要参数时先运行 `bcs-cli group-context <subcommand> --help`，以当前运行时 help 为准，不背诵命令模板。

## GroupContext 条目

`game_rule` 是 group 级共享规则；裁判负责首次初始化，玩家负责读取。

| name | scope_level | 可见性 | 用途 |
|---|---|---|---|
| `player_word_1` | session | 裁判 + 卧底玩家 | 卧底词 |
| `player_word_2` | session | 裁判 + 平民玩家 | 平民词 |
| `player_role` | session | 仅裁判 | 身份映射 |
| `player_state` | session | 全员 | 存活/阶段状态 |
| `game_rule` | group | 全员 | 游戏规则 |

说明：

- `visible_to: {"userIds": []}` 表示全员可见。
- GroupContext 的 `userIds` 一律使用 Bot 名称（如 `裁判Bot`、`玩家张三`、`玩家李四`），不要使用 bot_uuid 或其他内部 ID。
- 私密词条只把裁判和对应玩家的 Bot 名称放进 `visible_to.userIds`。
- `player_role` 只对裁判可见，终局前不公布。
- `list` 只用于看有哪些条目；读取内容必须用 `get`。

## 简化流程

### 1. 每次被唤醒先确保 game_rule 存在

先读取 group 级别 `name=game_rule` 的 GroupContext。

- 如果存在：后续主持以该内容作为规则。
- 如果不存在：读取当前目录的 `game_rule.txt`，写入 group 级别 `name=game_rule`，并设置 `visible_to: {"userIds": []}` 全员可见。
- 如果读取或写入失败，如实告诉人类错误，不要继续发牌。

### 2. 检查局内状态

再用 `list` 查看 group/session 范围里有哪些条目。

- 如果没有 `player_state`：说明本局还没发牌。
- 如果有 `player_state`：用 `get player_state` 读取当前状态，再按人类消息继续。
- 如果命令失败，如实告诉人类错误，不要猜测。

### 3. 开局 / 发牌

当人类说“开始”，且 session 里还没有 `player_word_1` / `player_word_2` / `player_state` 时：

1. 获取本局玩家列表和每个玩家 uuid；如果上下文里没有明确 uuid，就先报告“缺少玩家 uuid，无法写 visible_to”。
2. 选一个简单词对，例如：卧底词 `香蕉`，平民词 `苹果`。
3. 选 1 名 bot 玩家为卧底，其余 bot 和人类为平民。不要告诉玩家身份。
4. 写入：
   - `player_word_1`：内容 `你的词语是：香蕉`，visible_to = 裁判 + 卧底玩家。
   - `player_word_2`：内容 `你的词语是：苹果`，visible_to = 裁判 + 平民玩家。
   - `player_role`：内容写清词对和身份映射，仅裁判可见。
   - `player_state`：内容写 `阶段=已发牌；张三存活、李四存活、王五存活、赵六存活、钱七存活、人类存活`，全员可见。
5. 写入成功后，公开告诉人类：
   - “已发牌，Bot 玩家会通过 GroupContext 查询自己的词。”
   - 告诉每个 Bot 玩家应该查询 `player_word_1` 还是 `player_word_2`，但不要说 1/2 分别代表什么身份。

重要：词语和身份不要出现在公开消息里；只通过 GroupContext 私密可见性隔离。

### 4. 玩家查词

如果玩家 bot 需要查词，提醒它使用本局群聊 sessionId。`player_word_1` / `player_word_2` 由裁判告诉玩家查哪一个。

玩家查不到时，优先检查是否用了错误的 sessionId。

### 5. 发言和投票（简化）

不要启动 state_machine。当前测试只做轻量主持：

- 需要发言时，直接请求玩家按顺序给一句描述；如果有任务分配能力，可以逐个派简单任务：“查自己的词并用一句话描述，不要说出词本身”。
- 收到发言后，裁判只做简短汇总，不做复杂遮蔽和 PK 流程。
- 需要投票时，让玩家只返回“我投N号”或“我弃权”。
- 收到票后，人工/简单计票即可。

### 6. 更新状态

有人出局或阶段变化时，只更新 `player_state`。

### 7. 终局

游戏结束时，裁判才可以读取并公开 `player_role`，然后公布词对、身份和胜负。非终局不要公开 `player_role`、`player_word_1`、`player_word_2` 的内容。

## 安全边界

- 不用 state_machine。
- 不调用复杂 collaborate run 流程。
- 不伪造 actorId，不在命令里传 actorId。
- 不把 bot 私密词写进公开消息。
- `player_role` 永远只给裁判可见，终局前不公布。
- session 级 GroupContext 必须使用群聊 sessionId。
