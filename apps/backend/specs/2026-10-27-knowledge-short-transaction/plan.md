# Technical plan

- Restore native publishing models and knowledge selection reads to the original
  PR schema: enabled/online_enabled in knowledge bindings, version snapshot in
  publication.ext. Delete journal DDL/columns, projector, lifecycle, retry CLI,
  protocol retry methods and obsolete async-specific tests/docs.
- Concentrate capture, snapshot interpretation, locked online replacement and
  draft restore in the existing publishing/knowledge_selection helper module.
  Use DI's existing default-off KnowledgePublicationPolicy, not another generic
  event bus or queue framework. The caller owns the Session and commit.
- Interpret KnowledgeError business validation failures as scoped UNKNOWN (not
  SQL errors). Persist a compact knowledge result under existing ext. Validate
  scope before using snapshots; never copy current draft to unknown history.
- Keep native status/ext CAS and native operation ledger checks. Use Bot before
  publication/bindings for shared locks; preserve full ext CAS and protected
  knowledge metadata without letting remote uploads run under added locks.
- Stage changed immutable offloaded artifacts before enabled DB transactions,
  with cheap stale/missing-record preflight checks; unchanged artifacts reuse
  storage. Raced pre-staged objects remain under existing record prefix cleanup.
  Disabled writers keep their original paths. No new storage cleanup system.
- Verify local ordinary/AUTOCOMMIT behavior, failure after partial binding
  writes, native completion retry without deploy, empty/unknown/legacy rollback,
  restore replay/deadline/attempt, external tenant, CAS, artifact failures/locks,
  unchanged native schema, default-off SQL, architecture and DI.

## Review corrections

- Preserve the compact result of actual successful completion across native
  status changes and new capture; stamp its observed native workflow ID in ext.
  Read the greatest observed result under source/Owner/environment scope in the
  same flags query. Native rollback admission is not a new observed result.
- An unresolved current source never revokes bindings by historical PK alone.
  A newer observed result is checked with a current locking read under the Bot
  lock, including replacement UUIDs; native ledger gates remain untouched.
  There is no new operation-history scan or operation lock on online completion.
- This no-schema approach has per-Bot publication-history query cost. Default-off
  is retained; actual deployed plans/history/latency/lock evidence is an explicit
  enablement prerequisite, not inferred from SQLite or deferred ORM indexes.
