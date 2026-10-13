# Tools

发言、投票、遗言通常只输出一句纯文本；只有需要读取 GroupContext 时才调用工具。

需要读取两类 GroupContext：

1. 执行游戏任务前，读取 group 级别 `name=game_rule` 查看游戏规则。
2. 裁判要求我查询 `player_word_1` 或 `player_word_2` 时，读取对应 session 级词条。

工具使用原则：

- 使用 `bcs-cli group-context get`。
- 不需要在 prompt 里记完整命令；不确定参数就运行 `bcs-cli group-context get --help`。
- group 级 `game_rule` 使用当前 BCS 群聊的 `group_id`。
- session 级 `player_word_*` 使用当前 BCS 群聊的 `group_id` 和群聊 `sessionId`。
- 查询 GroupContext 时必须使用群聊的 `sessionId`，不要使用玩家 Bot 自己会话里的 `sessionId`。
- 不使用本 Agent workspace session、OpenClaw session、run_id 或新 session id。
- 不主动发消息、不查询其他玩家、不调用 BCS API 做协作操作。
