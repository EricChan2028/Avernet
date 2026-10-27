# Tasks

## Group 1 — Native boundary
- [x] 回归复现知识库故障阻断原生推进与历史回滚。
- [x] 原子持久化原生成功与意图；移除知识库对主流程的硬门禁。

## Group 2 — Projection
- [x] 索引化有限发现、原子投影、重启恢复、DB-clock 有限重试和显式恢复。
- [x] 部署序号乱序防护、线上 fail-closed、草稿编辑 epoch 防护。

## Group 3 — Closeout
- [x] 更新旧强事务测试以验证新合约，不削弱原生 CAS 和权限断言。
- [x] 受影响完整测试、架构/DI、lint、源码行数及最终审查。
- [x] 记录实际结果与未验证部署边界；不推送/部署/执行线上 DDL。
