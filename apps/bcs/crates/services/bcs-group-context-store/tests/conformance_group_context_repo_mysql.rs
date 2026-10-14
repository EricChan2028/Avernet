#![allow(clippy::expect_used, clippy::unwrap_used)]

mod common;

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use bcs_group_context_store::SqlGroupContextStore;
use mysql_async::Opts;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_group_context_store_passes_conformance() {
    let mysql_url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL contract");
    let opts = Opts::from_url(&mysql_url).expect("valid BCS_TEST_MYSQL_URL");
    let database = opts
        .db_name()
        .expect("BCS_TEST_MYSQL_URL includes a database name");
    let mysql_pass = opts.pass().map(str::to_string);
    let mut config = MysqlDbConfig::new()
        .with_database(database)
        .with_connection(MysqlConnectionConfig {
            connection_type: "direct".to_string(),
            host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string),
            password: mysql_pass,
            extra: BTreeMap::new(),
        })
        .with_statement_protocol(StatementProtocol::Text);
    config.pool_size = 1;
    config.min_pool_size = 1;

    let manager = MysqlDbManager::new(config)
        .await
        .expect("open MySQL contract datasource");
    let plugin = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
    apply_mysql_group_context_migration(plugin.as_ref()).await;
    plugin
        .execute(DbStatement::new("SET SESSION time_zone = '+08:00'"))
        .await
        .expect("set non-UTC MySQL session timezone");
    // Clean any leftover test data
    plugin
        .execute(DbStatement::new("DELETE FROM bcs_group_contexts"))
        .await
        .expect("clear test table");
    let repo = SqlGroupContextStore::oceanbase(plugin);

    common::create_duplicate_active_conflicts(&repo).await;
    common::supersede_preserves_history(&repo).await;
    common::supersede_stale_id_returns_conflict(&repo).await;
    common::list_active_by_multiple_scopes(&repo).await;
    common::current_row_with_expired_business_validity_still_blocks_and_is_returned(&repo).await;

    manager.close().await;
}

async fn apply_mysql_group_context_migration(db: &dyn DbPlugin) {
    let migration = include_str!("../../../../migrations/mysql/033_group_context.sql");
    for statement in migration
        .split(';')
        .map(str::trim)
        .filter(|sql| !sql.is_empty())
    {
        db.execute(DbStatement::new(statement))
            .await
            .unwrap_or_else(|error| {
                panic!("apply group_context migration statement: {error}\n{statement}")
            });
    }
}