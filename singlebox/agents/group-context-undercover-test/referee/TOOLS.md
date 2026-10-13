# TOOLS.md

BCS / GroupContext 操作优先使用运行环境提供的 `bcs-cli`。

- 与其他 Bot 玩家沟通、通知查词或派发游戏任务时，使用群聊任务/消息链路（例如 bcs_assign_task），确保玩家收到的消息携带本局群聊 sessionId；不要使用 `bcs-cli chat` 发送 1:1 单聊消息。

认证交给 CLI：不读取 token、不覆盖 BOT_DATA_DIR、不用 curl 绕过 CLI、不伪造 actorId。

不把 bcs-cli 手册写进 prompt。需要参数时运行：

```bash
bcs-cli group-context --help
bcs-cli group-context add --help
bcs-cli group-context update --help
bcs-cli group-context list --help
bcs-cli group-context get --help
```

GroupContext 场景约束：

- `group_id` 固定为 `game-room-7`。
- session 级 context 必须使用当前 BCS 群聊 sessionId，也就是玩家也会使用的同一个 `sessionId`。
- `visible_to: {"userIds":[]}` 表示全员可见。
- GroupContext 的 `userIds` 一律使用 Bot 名称（如 `裁判Bot`、`玩家张三`、`玩家李四`），不要使用 bot_uuid 或其他内部 ID。
- 私密词条只把裁判和对应玩家的 Bot 名称放进 `visible_to.userIds`。
- `player_role` 只对裁判可见。
- `list` 只看条目是否存在和权限；读取内容用 `get`。
