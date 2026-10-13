# KNOWLEDGE.md

## 游戏规则

默认 6 人（5 Bot + 1 human）、1 卧底、无白板、只告知本人词而不告知身份、最多 6 轮。

存活玩家按座位串行发言，随后并行秘密投票；不能投自己，可弃权，票面不写理由。
常规投票中至少两名玩家并列最高正票数时进入一次 PK：平票者按原顺序各额外发言一次，
全体存活玩家只投 PK 玩家，禁止自投和弃权；违规票作废不补投。PK 再平票或零有效票
无人出局，不再追加 PK。常规零有效票跳过 PK；PK 属于当前轮，最后一轮也先完成 PK 再结算。
全部卧底出局则平民胜；只剩两人卧底仍在或轮数用完则卧底胜。

一个 session 同时只有一个自定义协作。发言汇总与开票稿作为运行最终产物回灌，
唤醒裁判执行下一步；失败不自动唤醒，恢复需人类说"卡住了"。
Bot 出局且游戏继续时保留遗言；常规正票平票由回灌开始 PK；PK 结束无人出局、
常规零有效票或 human 出局由回灌开下一轮。

## GroupContext 条目约定

本局使用以下 GroupContext 条目。`group_id` 固定为 `game-room-7`；
`session_id` 每局独立（如 `sess-001`）。`scope` 由系统从 URL 参数自动生成，调用方不传。

| name | scope_level | content 样例 | visible_to | collect_from | 用途 |
|------|-------------|-------------|------------|--------------|------|
| `game_rule` | group | 【谁是卧底游戏规则】… | `{"userIds": []}` 全员 | `{"userIds": ["system"]}` | 游戏规则，群级别，由 systemset 写入 |
| `player_word_1` | session | 你的词语是：香蕉 | 卧底玩家 + 裁判 | 裁判 | 卧底词（私密） |
| `player_word_2` | session | 你的词语是：苹果 | 平民玩家 + 裁判 | 裁判 | 平民词（私密） |
| `player_role` | session | 卧底词=香蕉，平民词=苹果。卧底玩家=张三、赵六；平民玩家=李四、王五、钱七 | 仅裁判 | 裁判 | 身份映射（仅裁判可见） |
| `player_state` | session | 张三存活、李四存活、王五存活、赵六存活、钱七存活 | 全员 | 裁判 | 存活状态 |

## GroupContext API 要点

### scope 解析
- `scope_level=group` 时，`scope` = `{group_id}`（如 `game-room-7`）
- `scope_level=session` 时，`scope` = `{session_id}`（如 `sess-001`）
- `scope_level=round` 时，`scope` = `{round_id}`（如 `round_003`）
- `scope` 由系统根据 URL 参数自动填入，调用方只需传 `scope_level` 和对应的 ID 参数

### 权限模型
- `visible_to` 控制谁可以 **READ**：`userIds` 为空数组 = 全员可见；有名单 = 仅列出者可见
- `collect_from` 控制谁可以 **WRITE**：`userIds` 不可为空
- GroupContext 的 `userIds` 一律使用 Bot 名称（如 `裁判Bot`、`玩家张三`、`玩家李四`），不要使用 bot_uuid 或其他内部 ID。
- bot 在 create 和 update 时都可以设置/修改 `visible_to` 和 `collect_from`
- 框架按 `actor_id` 校验：不在 `visible_to` 中 → 看不到；不在 `collect_from` 中 → 写不了

### add — 新增 context
- 同 `(name, scope)` 已有活跃版本时返回 `conflict`；不可覆盖
- 必须同时传 `visible_to` 和 `collect_from`

### update — 更新 context（supersede）
- 按 `(name, scope)` 查旧条目活跃版本 → 不存在则 `not_found`
- 校验 `actor_id` 在旧条目的 `collect_from.userIds` 中 → 否则 `permission_denied`
- 原子操作：旧条目 `valid_to` 回填 → 新条目写入（`supersedes` 指向旧版本物理 `id`）
- `content` 不传则沿用旧值；`visible_to`/`collect_from` 不传则沿用旧值
- 并发 update 只有一条成功，其余返回 `conflict`

### list — 列出可见/可写 context
- 每个 `(name, scope)` 只返回一条活跃版本
- 每条附 `permission` 标签：`R`（可读）、`W`（可写）、`WR`（可读可写）
- 参数 `scope_levels` 默认 `["group","session","round"]`，可按需过滤

### get — 按 name 查询
- 保留 `actor_id` 在 `visible_to.userIds` 中的条目
- 每个 `(name, scope)` 只返回一条活跃版本
- 从细到粗排序（round → session → group）
- 不在 `visible_to.userIds` 中返回空 `items: []`

### systemset — 写入/更新（bcn 系统调用，bot 不可调）
- 本质是 update-or-create：首次 create，后续自动 supersede
- 用于 `game_rule` 等群级别的系统预设条目

### 错误码
- `permission_denied`（403）：调用方不在 collect_from 中
- `conflict`（409）：add 时同 (name, scope) 已有活跃版本；或 update 并发抢败
- `not_found`（404）：update 时没有可更新的 context
- `payload_too_large`（413）：content 超 4KB

## 信息隔离原理

- `player_role` 的 `visible_to` 只有 `[裁判]` → 玩家 get 不到，返回空 `items`
- `player_word_1` 的 `visible_to` 只有 `[裁判 + 卧底玩家]` → 平民 get 不到
- `player_word_2` 的 `visible_to` 只有 `[裁判 + 平民玩家]` → 卧底 get 不到
- `player_state` 的 `visible_to` 为空 `[]` → 全员可见
- `game_rule` 的 `visible_to` 为空 `[]` → 全员可见
- 玩家只知道自己是"去查 player_word_1"还是"去查 player_word_2"，不知道 1=卧底、2=平民
- 信息隔离完全靠 `visible_to.userIds`，不靠 API 隔离

## 一个 session = 一局游戏

- 新 session → 新游戏，GroupContext 条目重新创建
- session 结束时 GroupContext 条目随 session 生命周期管理
- 不跨 session 复用词对、身份分配、座位表