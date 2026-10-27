-- REVIEW ONLY: not executed. Apply before opting in to knowledge publication.
-- Optional fields are excluded from native ORM SELECT/INSERT statements.
-- No backfill from current draft: missing historical capture is UNKNOWN.
ALTER TABLE ac_bot_publish
  ADD COLUMN knowledge_tenant VARCHAR(128) NULL,
  ADD COLUMN knowledge_snapshot TEXT NULL,
  ADD COLUMN knowledge_projection TEXT NULL,
  ADD COLUMN knowledge_due DATETIME NULL,
  ADD COLUMN knowledge_deployment_id BIGINT NULL,
  ADD COLUMN knowledge_restore TEXT NULL,
  ADD COLUMN knowledge_revision BIGINT NULL,
  ADD INDEX idx_publish_knowledge_due (env, knowledge_due, id),
  ADD INDEX idx_publish_knowledge_deploy (env, source_bot_pk, knowledge_deployment_id, id);
-- Disable only after pending intents are drained; retain additive columns on rollback.

-- REVIEW ONLY: trusted routing metadata backfill, NOT selection reconstruction.
-- Historical snapshots stay NULL/UNKNOWN. Orphan source PKs remain unroutable.
UPDATE ac_bot_publish AS publication
JOIN ac_bots AS source
  ON source.id = publication.source_bot_pk AND source.env = publication.env
SET publication.knowledge_tenant = source.avernet_tenant
WHERE publication.knowledge_tenant IS NULL;
