# OKR.md

目标：让一局 6 人谁是卧底在主从协作群里完整跑完，全部游戏状态通过 GroupContext KV 管理，人类玩家全程只需要发言和投票。

关键结果：
- 零泄密：终局前卧底词/平民词绝不在群聊中出现，全靠 GroupContext 的 visible_to 隔离。
- 所有游戏状态通过 GroupContext 条目管理：player_word_1、player_word_2、player_role、player_state，每条都有正确的 visible_to 和 collect_from。
- 每轮发言念完自动开投，投票收齐自动计票、更新 player_state、判定胜负——人类不需要喊。
- 每位玩家只通过 GroupContext 的 visible_to 机制看到自己的词，卧底看不到平民词、平民看不到卧底词、谁都不知道 player_role。
- 身份、词对、座位顺序真随机，同一词对不在连续两局重复。
- 胜负判定以 GroupContext 中的 player_state 和 player_role 为唯一真相源，不是靠记忆。
- 任何一次卡住都能在人类一句话之内恢复，或者明确告知无法恢复。