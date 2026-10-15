-- Workflow Repair migration 142: OceanBase MySQL-compatible mode.
-- 执行前选中目标数据库；本次预发为 agentclawdb。不要连接生产库执行预发变更。
-- 仅补建审核发布两张表；不包含旧迁移的 ALTER，不改写已有数据或 schema_version。
-- IF NOT EXISTS 不会修复已存在但结构不一致的表；已有表须先 SHOW CREATE TABLE 核对。

CREATE TABLE IF NOT EXISTS workflow_repair_release_records (
  record_id VARCHAR(64) PRIMARY KEY COMMENT '审核或发布记录ID',
  workflow_id VARCHAR(190) NOT NULL COMMENT '所属工作流',
  task_id VARCHAR(64) NOT NULL COMMENT '修复任务ID',
  revision BIGINT NOT NULL COMMENT '冻结候选版本',
  record_kind VARCHAR(32) NOT NULL COMMENT '记录类型:approval|deployment|verification',
  state_version BIGINT NOT NULL COMMENT '记录乐观锁版本',
  request_key VARCHAR(64) NOT NULL COMMENT '任务操作请求唯一摘要',
  request_digest VARCHAR(64) NOT NULL COMMENT '请求内容摘要',
  body_json MEDIUMTEXT NOT NULL COMMENT '可信审核发布或验证数据JSON',
  created_at_ms BIGINT NOT NULL COMMENT '创建时间毫秒',
  updated_at_ms BIGINT NOT NULL COMMENT '更新时间毫秒',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  UNIQUE INDEX uk_repair_release_request (request_key),
  INDEX idx_repair_release_task (task_id, record_kind)
) COMMENT='工作流修复审阅发布验证记录';

CREATE TABLE IF NOT EXISTS workflow_repair_publication_claims (
  deployment_id VARCHAR(64) PRIMARY KEY COMMENT '对应冻结发布请求ID',
  ticket_hash VARCHAR(64) NOT NULL COMMENT '一次性发布能力凭证摘要，不存原文',
  recipe_json TEXT NOT NULL COMMENT '冻结发布编号及候选文件身份JSON',
  worker_id VARCHAR(128) COMMENT '唯一领取工作进程身份',
  expires_at_ms BIGINT NOT NULL COMMENT '激活授权到期时间毫秒',
  reported_status VARCHAR(32) COMMENT '执行端报告状态，只供对账',
  receipt_json TEXT COMMENT '执行端回执JSON，仍需真实Git及DB校验',
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间'
) COMMENT='工作流修复发布一次性执行授权';
