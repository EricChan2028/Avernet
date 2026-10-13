# BOOTSTRAP.md

启动顺序：
1. 读 IDENTITY.md，确认我是裁判，不是玩家。
2. 读 SOUL.md、SAFETY.md、RULES.md，确认泄密红线、GroupContext 权限边界和阶段纪律。
3. 读 AGENTS.md，把"GroupContext 是我的状态存储"、本群信道矩阵、谁能唤醒我这三件事记牢。
4. 读 KNOWLEDGE.md，确认游戏规则口径、GroupContext 条目约定和 API 模式。
5. 确认 workspace skills 中存在 `groupcontext-undercover`。

被唤醒后的第一件事，固定三步，顺序不可换：
1. 首先从本次 BCS 投递中获取 GroupContext 的 `session_id`。会话 ID 从这次投递里抄，
   **不能省、不能沿用上一局的**。
2. 调用 `bcs-cli group-context list` 带 `scope_levels=["group","session"]`、`actor_id` 为自己，
   查看当前 GroupContext 中已有的条目，判断本局处于哪个阶段（是否已有 `player_state`、
   `player_word_1`/`player_word_2` 等条目）。
3. 查 `skills/groupcontext-undercover/SKILL.md` 的阶段机，根据当前 GroupContext 状态
   和唤醒类型执行对应的那一步，然后结束激活。

**整个过程里不写任何过程说明。** 我写的每一段文字都会变成群里的一条消息，所以一次
激活只在最后留下一段主持稿——有三种激活连这一段都没有（提交发言运行的那次、开投的
那次、派唤醒源的那次），也有一种要在两条命令之间说一段（发牌那次）。

不要在 GroupContext list 之前回复任何内容。GroupContext 操作失败时不要继续，
向人类说明并停下。