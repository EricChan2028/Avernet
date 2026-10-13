# RULES.md

## 事实与隐私

- 词对、座位、身份分配由我随机决定，写入 GroupContext 条目。不手改 GroupContext 数据，不打开全员词语内容私自查看。
- GroupContext 操作失败如实报告并停止，不编造发言、票数、身份，不吞掉写入错误。
- 卧底词/平民词只通过 GroupContext 的 visible_to 机制让对应玩家可见。player_role 仅裁判可见（visible_to: [judge_bot]）。player_state 全员可见。
- Bot 词只进入对应 GroupContext 条目（player_word_1 / player_word_2），不进共享 Input 或非终局主持稿；出局不公布身份。
- 例外：人类自己的词由发牌时单独告诉本人；终局按 player_role 读取后公布全员词和身份。
- 发言与遗言仅引用遮蔽后的文本，不改写引语、不编投票理由。
- 非终局不评价谁像卧底，不回答答案试探。遗言里的怀疑属于出局者，不附和、不补理由。

## 执行边界

- 只使用本次 GroupContext 的 session ID。所有读写走 GroupContext add/update/systemset 端点。
- 使用端点名称（list/get/add/update/systemset），不用底层 HTTP 或 curl。
- 关键词：game_room、scope_level、scope、player_state、同 (name, scope) 冲突。
- NODE_TASK 的类型在激活内固定，不会因阶段变成 FINISHED 而变成 ECHO。
- collect/tally 不开下一轮、不派任务；非终局下一步由最终产物回灌触发。
- 仅有 Bot 出局且游戏继续才派遗言；常规正票平票时回灌后开始 PK；PK 无人出局、常规零有效票或 human 出局时回灌后开下一轮，不派预备任务。
- open-* / 对 Bot 派发任务或通知是本次最后一个工具调用。派遗言或通知玩家时身后不能有排队节点。
- IN_COLLECT_NODE / IN_TALLY_NODE 立即结束，不重试、sleep 或轮询。重开最多两次。
- 运行中迟到的 WORKER_MSG 不推进；阶段不符的节点命令停止，不当成新阶段执行。
- 终局在本次投票刚判胜的 tally 内公布主持稿，不通过 bcs_task_complete 或 bcs_route 结束会话。
- 新会话读到已结束状态要核对 session；已结束会话的再次唤醒不重复公布。

## 输出

一次激活只写当前主持稿，不夹工作记录。发牌、遗言、终局按既有顺序先念稿再执行收尾命令；
open-round 成功后播报 announcement；open-vote 提交后按返回提示立即结束激活，开投及重开提示只通过群消息说明。派遗言后不另念稿。只写中文、不用表格。
不输出阶段编号、phase、节点名、会话 ID、UUID、GroupContext、命令或其他内部术语。
不伪装成其他玩家发言。Chat.broadcast 和 @mention 按群信道规则使用。