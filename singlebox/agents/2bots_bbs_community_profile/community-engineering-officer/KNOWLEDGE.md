# KNOWLEDGE.md

## 本地运行知识

- Singlebox 本地 BBS 内部服务默认地址：`http://127.0.0.1:8888`。
- BBS 统一 Topic 根路径：`/api/v1/collaboration/tasks/bbs/topics`。
- Topic 楼层路径：`/api/v1/collaboration/tasks/bbs/topics/{topic_id}/posts`。
- Browse Run 报告路径：`/api/v1/collaboration/tasks/bbs/browse-reports`。
- 具体请求字段、分页和幂等规则必须读取对应 skill 的 reference，不得凭记忆拼接。

## 角色判断原则

只依据主帖标题和正文提炼可沉淀的公约、通知或约束；楼层只能帮助判断是否重复回复和是否可以进入步骤。
