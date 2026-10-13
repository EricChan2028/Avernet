use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{GroupContextPolicy, ScopeLevel};
use bcs_group_context_store::{
    GroupContextRepo, GroupContextStoreError, MemoryGroupContextRepo, NewGroupContextEntry,
    SqlGroupContextStore,
};
use chrono::{TimeZone, Utc};

fn entry(context_id: &str, name: &str, scope: &str, content: &str) -> NewGroupContextEntry {
    let now = Utc
        .timestamp_millis_opt(1_728_374_400_000)
        .single()
        .unwrap();
    NewGroupContextEntry {
        context_id: context_id.into(),
        name: name.into(),
        scope_level: ScopeLevel::Session,
        scope: scope.into(),
        content: content.into(),
        visible_to: GroupContextPolicy::users(["judge_bot"]),
        collect_from: GroupContextPolicy::users(["judge_bot"]),
        supersedes: None,
        change_reason: None,
        valid_from: now,
        valid_to: None,
        tx_time: now,
    }
}

fn replacement(name: &str, scope: &str, content: &str) -> NewGroupContextEntry {
    let now = Utc
        .timestamp_millis_opt(1_728_374_460_000)
        .single()
        .unwrap();
    NewGroupContextEntry {
        context_id: "ignored-by-supersede".into(),
        name: name.into(),
        scope_level: ScopeLevel::Session,
        scope: scope.into(),
        content: content.into(),
        visible_to: GroupContextPolicy::users(Vec::<String>::new()),
        collect_from: GroupContextPolicy::users(["judge_bot"]),
        supersedes: None,
        change_reason: Some("第一轮投票".into()),
        valid_from: now,
        valid_to: None,
        tx_time: now,
    }
}

async fn sqlite_repo() -> SqlGroupContextStore {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    apply_sqlite_group_context_migration(db.as_ref()).await;
    SqlGroupContextStore::sqlite(db)
}

async fn apply_sqlite_group_context_migration(db: &dyn DbPlugin) {
    for sql in include_str!("../../../../migrations/sqlite/034_group_context.sql")
        .split(';')
        .map(str::trim)
        .filter(|sql| !sql.is_empty())
    {
        db.execute(DbStatement::new(sql))
            .await
            .expect("migration sql");
    }
}

async fn create_duplicate_active_conflicts(repo: &dyn GroupContextRepo) {
    repo.create_active(entry("ctx-1", "player_state", "sess-001", "alive"))
        .await
        .expect("create active");
    let err = repo
        .create_active(entry("ctx-2", "player_state", "sess-001", "duplicate"))
        .await
        .expect_err("duplicate active must conflict");
    assert!(matches!(
        err,
        GroupContextStoreError::DuplicateActive { .. }
    ));
}

async fn supersede_preserves_history(repo: &dyn GroupContextRepo) {
    let first = repo
        .create_active(entry("ctx-1", "player_state", "sess-001", "张三存活"))
        .await
        .expect("create active");
    let second = repo
        .supersede_active(
            "player_state",
            "sess-001",
            replacement("player_state", "sess-001", "张三出局"),
        )
        .await
        .expect("supersede active");

    assert_eq!(second.context_id, first.context_id);
    assert_eq!(second.supersedes, Some(first.id));
    assert_eq!(second.change_reason.as_deref(), Some("第一轮投票"));
    assert_eq!(second.content, "张三出局");

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("get active")
        .expect("active row");
    assert_eq!(active.id, second.id);
    assert!(!active.is_superseded);
    assert!(active.gmt_superseded.is_none());

    let versions = repo
        .list_versions(&first.context_id)
        .await
        .expect("list versions");
    assert_eq!(versions.len(), 2);
    assert!(versions.iter().any(|row| row.id == first.id
        && row.is_superseded
        && row.gmt_superseded == Some(second.tx_time)
        && row.valid_to.is_none()));
    assert!(
        versions
            .iter()
            .any(|row| row.id == second.id && !row.is_superseded && row.gmt_superseded.is_none())
    );
}

async fn list_active_by_multiple_scopes(repo: &dyn GroupContextRepo) {
    repo.create_active(entry("ctx-g", "game_rule", "game-room-7", "规则"))
        .await
        .unwrap();
    repo.create_active(entry("ctx-s", "player_state", "sess-001", "状态"))
        .await
        .unwrap();
    repo.create_active(entry("ctx-other", "other", "sess-002", "其他"))
        .await
        .unwrap();

    let scopes = vec!["game-room-7".to_string(), "sess-001".to_string()];
    let rows = repo.list_active_by_scopes(&scopes).await.unwrap();
    let names = rows.into_iter().map(|row| row.name).collect::<Vec<_>>();
    assert_eq!(names.len(), 2);
    assert!(names.iter().any(|name| name == "game_rule"));
    assert!(names.iter().any(|name| name == "player_state"));
}

async fn current_row_with_expired_business_validity_still_blocks_and_is_returned(
    repo: &dyn GroupContextRepo,
) {
    let now = Utc
        .timestamp_millis_opt(1_728_374_400_000)
        .single()
        .unwrap();
    let expired = Utc
        .timestamp_millis_opt(1_700_000_000_000)
        .single()
        .unwrap();
    let mut current = entry(
        "ctx-expired",
        "business_window",
        "sess-001",
        "expired-but-current",
    );
    current.valid_from = expired;
    current.valid_to = Some(expired);
    current.tx_time = now;

    let created = repo.create_active(current).await.expect("create current");
    assert!(!created.is_superseded);
    assert_eq!(created.valid_to, Some(expired));

    let duplicate = repo
        .create_active(entry(
            "ctx-dupe",
            "business_window",
            "sess-001",
            "duplicate",
        ))
        .await
        .expect_err("current row with expired business valid_to still blocks duplicate");
    assert!(matches!(
        duplicate,
        GroupContextStoreError::DuplicateActive { .. }
    ));

    let active = repo
        .get_active("business_window", "sess-001")
        .await
        .expect("get active")
        .expect("active row");
    assert_eq!(active.id, created.id);
    assert_eq!(active.valid_to, Some(expired));

    let rows = repo
        .list_active_by_scopes(&["sess-001".to_string()])
        .await
        .expect("list active");
    assert!(rows.iter().any(|row| row.id == created.id));
}

#[tokio::test]
async fn memory_duplicate_active_conflicts() {
    create_duplicate_active_conflicts(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn memory_supersede_preserves_history() {
    supersede_preserves_history(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn memory_lists_active_by_multiple_scopes() {
    list_active_by_multiple_scopes(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn sqlite_duplicate_active_conflicts_from_partial_unique_index() {
    let repo = sqlite_repo().await;
    create_duplicate_active_conflicts(&repo).await;
}

#[tokio::test]
async fn sqlite_supersede_preserves_history() {
    let repo = sqlite_repo().await;
    supersede_preserves_history(&repo).await;
}

#[tokio::test]
async fn sqlite_lists_active_by_multiple_scopes() {
    let repo = sqlite_repo().await;
    list_active_by_multiple_scopes(&repo).await;
}

#[tokio::test]
async fn memory_expired_business_validity_still_current() {
    current_row_with_expired_business_validity_still_blocks_and_is_returned(
        &MemoryGroupContextRepo::new(),
    )
    .await;
}

#[tokio::test]
async fn sqlite_expired_business_validity_still_current() {
    let repo = sqlite_repo().await;
    current_row_with_expired_business_validity_still_blocks_and_is_returned(&repo).await;
}
