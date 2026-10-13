---
name: groupcontext-undercover-player
description: 通过 GroupContext 获取自己的底牌词语，完成发言/投票/遗言任务
allowed-tools: []
---

# 谁是卧底 · 玩家动作

## 读规则

执行发言、投票、遗言前，先读取 group 级别 `name=game_rule` 的 GroupContext，规则正文以该 context 为准。

需要使用 bcs-cli 时，不背诵参数；先以运行时 help 为准：

```bash
bcs-cli group-context get --help
```

如果 `game_rule` 不存在或读取失败，如实说明错误，不凭记忆编规则。

## 查词

裁判会告诉我查 `player_word_1` 或 `player_word_2`。我只查裁判指定的 name。

需要使用 bcs-cli 时，不背诵参数；先以运行时 help 为准：

```bash
bcs-cli group-context get --help
```

查询必须使用当前 BCS 群聊的 `group_id` 和群聊 `sessionId`。不要使用 Agent 自己的 workspace session、OpenClaw session、run_id 或新 session id。
查询 GroupContext 时必须使用群聊的 `sessionId`，不要使用玩家 Bot 自己会话里的 `sessionId`。

如果 `get` 返回空 items，优先检查 sessionId 是否误用；确认 sessionId 正确后，再认为我不在该词条的 visible_to 中或裁判让我查错了 name。

## 发言/投票/遗言

所有任务由协作节点正文驱动。只输出最终一句纯文本。

- 常规发言：从日常角度描述我的词，不说出词本身，不出现类目名词、不下定义。
- 投票：只输出「我投N号」或「我弃权」。
- PK/遗言：按任务正文要求输出一句话。
- 不泄露词、连续两字、拆字、拼音、英文或谐音。
- 不宣称知道身份，不解释推理过程。

## 信息隔离

- 我看不到 `player_role`，所以不知道自己是卧底还是平民。
- 我只应看到裁判分配给我的那条 `player_word_*`。
- 我不知道 `player_word_1` / `player_word_2` 分别代表什么身份。
