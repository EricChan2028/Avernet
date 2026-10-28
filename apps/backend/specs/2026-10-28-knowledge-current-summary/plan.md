# Plan: Current knowledge summary

## Approach
在知识 repository 内集中摘要编解码、作用域与唯一键查询。发布完成调用该组件，
不依赖会独立提交的通用配置 repository。沿用 Bot -> publication -> config -> binding 锁顺序。

## Data Model Changes
None. 只使用已有列和唯一索引；不执行 DDL。

```python
# core/bot_knowledge/repository/online_selection.py (new)
def config_scope(source_pk, bot_id, env): ...
def read_summary(session, bot): ...
def known_predicate(raw_value, scope): ...
def lock_summary(session, publish): ...
def replace_summary(session, row, source, publish, outcome, ids): ...
```

## Key Files & Functions
```diff
# core/bot_knowledge/repository/store.py
- result = type_coerce(BotPublishModel.ext, JSON)[...]
+ summary = read_summary(session, bot)
+ ids = summary.current_ids(bot)  # bounded + scope checked
+ query.filter(Binding.kb_id.in_(ids), known_predicate(...))
```
```diff
# core/repository/implementations/publishing/knowledge_selection.py
- _newer_observed_completion(...)  # JSON historical query
+ summary = lock_summary(session, publish)
+ reject_newer_observed_workflow(summary, workflow_id)
- clear_query.filter(Binding.kb_id.not_in(ids)).update(...)
+ clear_query.filter(Binding.kb_id.in_(previous_ids - ids)).update(...)
+ replace_summary(...)  # same Session after CAS
```

## Dependencies
None. Existing BotCommonConfig ORM model only. Reserved key constant in kernel.

## Risks & Mitigations
- 旧摘要不存在时不能有界推断历史：读取 UNKNOWN；可信重新发布显式初始化，无扫描回退。
- 通用配置管理覆盖风险：保留专用键，所有通用写入/删除路径拒绝。
- 双语句读取竞争：绑定查询同语句校验摘要原始值，变更时 fail closed。
- 部署前确认已有配置表；本地测试不能证明生产存在或性能。

## Alternatives Considered
发布单 ext 当前指针需要参与创建/删除/版本变更，不符合最小耦合；新增表/索引被排除。

## Test Strategy
先写有界工作量、缺失配置拒绝、差集唯一键写入、管理接口保护测试，再接入生产代码。
保留真实完成重试、rollback/restore、Owner A-B-A、外租户、故障事务与 artifact 回归。
