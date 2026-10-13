# MEMORY.md

长期保留：
- GroupContext 权限模型：visible_to 控制谁看得见，collect_from 控制谁能写。player_role 只有 [裁判] 可见；player_word_1 只有 [裁判 + 卧底] 可见；player_word_2 只有 [裁判 + 平民] 可见；player_state 和 game_rule 全员可见。
- 谁是卧底的规则书与判定口径。
- 一个 session = 一局游戏，GroupContext 条目随 session 生命周期。
- GroupContext API 模式：add（新增，同名同 scope 冲突）、update（supersede 取代链）、list（带 R/W/WR 权限标签）、get（按 name 查，不可见返回空）、systemset（update-or-create，仅系统调用）。
- 阶段机的形状：一次唤醒只推进一步，并且要安排好下一次唤醒源。

禁止长期保留：
- 任何一局的词对、身份分配、座位表、发言和票数。
- 运行时 Bot UUID、session_id、run_id。
- token 或任何凭据。

单局状态一律在 GroupContext 条目里，随 session 结束而失效。