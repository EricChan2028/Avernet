//! Credential-preserving Provider admission contract for streaming registries.

use bcs_service_api::{
    is_mock_token, BotConnectParams, BotRegistryCoreService, ProviderCheckedConnectError,
};

/// Both registries share one legacy Plugin registration; only the first has an
/// unavailable Provider repository. The client starts without a runtime token.
pub async fn provider_checked_streaming_core_contract_tests(
    unavailable: &dyn BotRegistryCoreService,
    recovered: &dyn BotRegistryCoreService,
    bot_id: &str,
) {
    let original = unavailable.load_token(bot_id).await.expect("pre-registration token");
    assert!(is_mock_token(&original));
    let request = || BotConnectParams {
        token: None, bot_id: Some(bot_id.into()), protocol_version: Some(3), client_kind: None,
    };
    assert!(matches!(unavailable.connect_streaming_with_provider(request()).await,
        Err(ProviderCheckedConnectError::Policy(_))));
    assert_eq!(unavailable.load_token(bot_id).await.as_deref(), Some(original.as_str()));
    assert!(!unavailable.is_connected(bot_id).await);

    // Retry the original client request; do not recover credentials from storage.
    let outcome = recovered.connect_streaming_with_provider(request()).await
        .expect("same credentials succeed after Provider recovery");
    assert_eq!(outcome.connection.bot_uuid, bot_id);
    assert!(!is_mock_token(&outcome.connection.token));
    assert!(outcome.provider_surface.is_some());
    assert!(recovered.is_connected(bot_id).await);
    assert_eq!(recovered.load_token(bot_id).await.as_deref(), Some(outcome.connection.token.as_str()));
    recovered.disconnect_streaming(bot_id).await;
}
