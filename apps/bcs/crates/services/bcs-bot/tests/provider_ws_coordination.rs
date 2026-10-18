use std::sync::Arc;
use bcs_bot::{Bot, BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::MemoryBotProviderStore;
use bcs_service_api::*;
use serde_json::json;

const TOOL: &str = "mcp__custom_bcs__bcs_assign_task";

struct Fixture {
    _dir: tempfile::TempDir,
    bots: Arc<MemoryBotRepo>,
    metadata: Arc<MemoryBotProviderStore>,
    registry: Arc<BotCore>,
    providers: Arc<MemoryProviderStore>,
    provider_id: String,
    bot_id: String,
}

impl Fixture {
    async fn new(mode: &str) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
        let providers = Arc::new(MemoryProviderStore::new());
        let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
        let registry = Arc::new(BotCore::with_provider_repos(bots.clone(), providers.clone(),
            providers.clone(), providers.clone()).with_bot_provider_repo(metadata.clone()));
        let admin = ProviderCore::new(providers.clone(), providers.clone(), providers.clone(), registry.clone())
            .with_bot_provider_repo(metadata.clone());
        let provider = admin.register_provider("Provider".into(), None, ProviderAuthMode::StaticBearer,
            "alice".into(), None, None).await.unwrap();
        let mut config: serde_json::Value = serde_json::from_str(&provider.provider.config).unwrap();
        config["coordination"] = if mode == "native_mcp" {
            json!({"mode":mode, "mcp_server":"custom_bcs", "tool_name_mapping":{TOOL:"bcs_assign_task"}})
        } else if mode == "mcporter_mcp" {
            json!({"mode":mode, "mcp_server":"custom_bcs", "mcporter_command":"mcporter"})
        } else { json!({"mode":mode}) };
        let config = config.to_string();
        providers.update_provider_metadata(&provider.provider.provider_id, None, Some(&config), None, 1)
            .await.unwrap();
        let (bot, _) = admin.register_provider_bot_with_bot_uuid(&provider.provider.provider_id,
            &provider.provider_admin_token, RegisterProviderBotParams {
                bot_name: "Manager".into(), provider_bot_ref: "manager:alice".into(),
                owners: vec!["alice".into()], connection_mode: ProviderBotConnectionMode::Plugin,
                ..Default::default()
            }).await.unwrap();
        assert!(providers.get_binding_by_bot_uuid(&bot.bot_uuid).await.unwrap().is_none());
        Self { _dir: dir, bots, metadata, registry, providers, provider_id: provider.provider.provider_id, bot_id: bot.bot_uuid }
    }

    async fn connect(&self, version: Option<u32>, kind: Option<&str>) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        Bot::new(self.registry.clone()).with_uplink_config(bcs_config_api::UplinkConfig {
            allowed_profiles: vec![bcs_config_api::UplinkProfile::NativeMcp],
        }).connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None, token: self.registry.load_token(&self.bot_id).await,
            bot_id: Some(self.bot_id.clone()), protocol_version: version,
            client_kind: kind.map(str::to_string),
        }).await
    }
}

#[tokio::test]
async fn upstream_metadata_resolves_provider_mapping_without_a_delivery_binding() {
    let f = Fixture::new("native_mcp").await;
    let surface = f.registry.resolve_coordination_surface(&f.bot_id).await.unwrap();
    assert_eq!(surface.mode, CoordinationMode::NativeMcp);
    assert_eq!(surface.mcp_server.as_deref(), Some("custom_bcs"));
    assert_eq!(surface.tool_name_mapping.get(TOOL).map(String::as_str), Some("bcs_assign_task"));
}

#[tokio::test]
async fn bridge_v3_without_client_kind_uses_its_authenticated_provider() {
    let f = Fixture::new("native_mcp").await;
    let result = f.connect(Some(3), None).await.unwrap();
    assert_eq!(result.negotiated_client_kind.as_deref(), Some("native_mcp"));
    assert_eq!(f.registry.get_bot_info(&f.bot_id, "client_kind").await.as_deref(), Some("native_mcp"));
}

#[tokio::test]
async fn provider_mcporter_mode_takes_precedence_over_client_native_mcp() {
    let f = Fixture::new("mcporter_mcp").await;
    let result = f.connect(Some(3), Some("native_mcp")).await.unwrap();
    assert_eq!(result.negotiated_client_kind.as_deref(), Some("mcporter_mcp"));
    let surface = f.registry.resolve_coordination_surface(&f.bot_id).await.unwrap();
    assert_eq!(surface.mcp_server.as_deref(), Some("custom_bcs"));
    assert_eq!(surface.mcporter_command.as_deref(), Some("mcporter"));
}

#[tokio::test]
async fn provider_policy_cannot_be_overridden_by_client_or_global_allowlist() {
    for mode in ["disabled", "native_tool"] {
        let f = Fixture::new(mode).await;
        assert_eq!(f.connect(Some(3), Some("native_mcp")).await.unwrap().negotiated_client_kind, None);
    }
    for version in [None, Some(1), Some(2)] {
        let f = Fixture::new("native_mcp").await;
        assert_eq!(f.connect(version, Some("native_mcp")).await.unwrap().negotiated_client_kind, None);
    }
}

#[tokio::test]
async fn disabled_provider_fails_closed_and_releases_streaming_slot() {
    let f = Fixture::new("native_mcp").await;
    f.providers.update_provider_disabled(&f.provider_id, true, 2).await.unwrap();
    assert!(f.connect(Some(3), Some("native_mcp")).await.is_err());
    assert!(f.registry.resolve_coordination_surface(&f.bot_id).await.is_err());
    assert!(f.registry.get_bot_info(&f.bot_id, "client_kind").await.is_none());
    f.providers.update_provider_disabled(&f.provider_id, false, 3).await.unwrap();
    assert_eq!(f.connect(Some(3), None).await.unwrap().negotiated_client_kind.as_deref(), Some("native_mcp"));
}

#[tokio::test]
async fn missing_or_corrupt_provider_never_falls_back_to_client_profile() {
    for missing in [true, false] {
        let mut f = Fixture::new("native_mcp").await;
        if missing {
            f.registry = Arc::new(BotCore::with_provider_repos(f.bots.clone(),
                Arc::new(MemoryProviderStore::new()), f.providers.clone(), f.providers.clone())
                .with_bot_provider_repo(f.metadata.clone()));
        } else {
            f.providers.update_provider_metadata(&f.provider_id, None, Some("invalid-json"), None, 2)
                .await.unwrap();
        }
        assert!(f.connect(Some(3), Some("native_mcp")).await.is_err());
        assert!(f.registry.resolve_coordination_surface(&f.bot_id).await.is_err());
        assert!(f.registry.get_bot_info(&f.bot_id, "client_kind").await.is_none());
    }
}

#[tokio::test]
async fn metadata_is_authoritative_and_legacy_binding_is_only_an_absence_fallback() {
    use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
    let f = Fixture::new("native_mcp").await;
    let binding = ProviderBotBinding {
        bot_uuid:f.bot_id.clone(), provider_id:f.provider_id.clone(), provider_bot_ref:"legacy".into(),
        webhook_url:None, disabled:false, created_at:1, updated_at:1,
    };
    f.providers.insert_binding(binding.clone()).await.unwrap();
    let legacy = BotCore::with_provider_repos(f.bots.clone(), f.providers.clone(),
        f.providers.clone(), f.providers.clone());
    assert_eq!(legacy.resolve_coordination_surface(&f.bot_id).await.unwrap().mcp_server.as_deref(), Some("custom_bcs"));
    f.providers.update_binding_disabled(&f.bot_id, true, 2).await.unwrap();
    assert!(legacy.resolve_provider_coordination_surface(&f.bot_id).await.is_err());
    assert_eq!(f.registry.resolve_provider_coordination_surface(&f.bot_id).await.unwrap().unwrap()
        .mcp_server.as_deref(), Some("custom_bcs"), "upstream metadata precedes stale delivery projection");
    f.metadata.delete_provider_bot(&f.provider_id, &f.bot_id, 3).await.unwrap();
    assert!(f.registry.resolve_provider_coordination_surface(&f.bot_id).await.is_err(),
        "tombstones cannot fall back to legacy/client state");
}

#[tokio::test]
async fn provider_authorization_does_not_bypass_token_authentication() {
    let f = Fixture::new("native_mcp").await;
    let service = Bot::new(f.registry.clone());
    // Legacy plugin registration starts with a MOCK token. First complete its
    // existing promotion flow, then exercise rejection of an incorrect token.
    service.connect_streaming(BotRuntimeConnectCommand {
        caller_actor_id: None, token: None, bot_id: Some(f.bot_id.clone()),
        protocol_version: Some(3), client_kind: None,
    }).await.unwrap();
    service.disconnect_streaming(BotRuntimeDisconnectCommand { bot_id: f.bot_id.clone() }).await.unwrap();
    assert!(service.connect_streaming(BotRuntimeConnectCommand {
        caller_actor_id: None, token: Some("wrong-runtime-token".into()), bot_id: Some(f.bot_id.clone()),
        protocol_version: Some(3), client_kind: Some("native_mcp".into()),
    }).await.is_err());
    assert!(f.registry.get_bot_info(&f.bot_id, "client_kind").await.is_none());
    assert_eq!(f.connect(Some(3), None).await.unwrap().negotiated_client_kind.as_deref(), Some("native_mcp"));
}

struct UnavailableProviders;

#[async_trait::async_trait]
impl ProviderRepoPort for UnavailableProviders {
    async fn get_provider(&self, _: &str) -> ServiceResult<Option<ProviderRecord>> {
        Err(ServiceError::InternalError("provider store unavailable".into()))
    }
    async fn insert_provider(&self, _: ProviderRecord) -> ServiceResult<()> { unreachable!() }
    async fn get_provider_by_slug(&self, _: &str) -> ServiceResult<Option<ProviderRecord>> { unreachable!() }
    async fn list_providers(&self) -> ServiceResult<Vec<ProviderRecord>> { unreachable!() }
    async fn update_provider_metadata(&self, _: &str, _: Option<&str>, _: Option<&str>, _: Option<&str>, _: u64)
        -> ServiceResult<Option<ProviderRecord>> { unreachable!() }
    async fn update_provider_disabled(&self, _: &str, _: bool, _: u64)
        -> ServiceResult<Option<ProviderRecord>> { unreachable!() }
}

#[tokio::test]
async fn provider_read_failure_is_propagated_and_reconnect_can_recover() {
    let mut f = Fixture::new("native_mcp").await;
    let healthy_registry = f.registry.clone();
    f.registry = Arc::new(BotCore::with_provider_repos(f.bots.clone(), Arc::new(UnavailableProviders),
        f.providers.clone(), f.providers.clone()).with_bot_provider_repo(f.metadata.clone()));
    let error = f.connect(Some(3), Some("native_mcp")).await.unwrap_err();
    assert!(error.to_string().contains("provider store unavailable"), "{error}");
    assert!(f.registry.resolve_coordination_surface(&f.bot_id).await.is_err());
    assert!(!f.registry.is_connected(&f.bot_id).await);
    f.registry = healthy_registry;
    assert_eq!(f.connect(Some(3), None).await.unwrap().negotiated_client_kind.as_deref(), Some("native_mcp"));
}

