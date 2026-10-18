use super::*;
use bcs_service_api::{ProviderCheckedConnectError, ProviderCheckedConnection};

impl BotCore {
    pub(super) async fn connect_with_policy(
        &self,
        params: BotConnectParams,
        kind: ConnectionKind,
        check_provider: bool,
    ) -> Result<ProviderCheckedConnection, ProviderCheckedConnectError> {
        let token_preview = params
            .token
            .as_ref()
            .map(|t| format!("{}...", &t[..t.len().min(4)]))
            .unwrap_or_else(|| "None".to_string());
        info!(token_present = params.token.is_some(), token_preview = %token_preview, "Processing connect_bot");

        let (is_new, bot_id, token): (bool, String, String) = if let Some(ref token_str) =
            params.token
        {
            if let Some(existing_bot_id) = self.repo.find_bot_by_token(token_str).await {
                info!(bot_id = %existing_bot_id, token_preview = %token_preview, "Valid token, reconnection");
                (false, existing_bot_id, token_str.clone())
            } else {
                let new_token = new_session_token();
                info!(token_preview = %token_preview, new_token_preview = %format!("{}...", &new_token[..4]), "Invalid/not found token, new connection");
                (true, String::new(), new_token)
            }
        } else {
            let new_token = new_session_token();
            info!(new_token_preview = %format!("{}...", &new_token[..4]), "No token, new connection");
            (true, String::new(), new_token)
        };

        let mut provider_surface = None;
        let (final_bot_id, final_token) = if is_new {
            let connect_bot_id = if let Some(ref provided_bot_id) = params.bot_id {
                if provided_bot_id.is_empty() {
                    return Err(ConnectError::InvalidBotId.into());
                }
                // Reject a provided bot_id that already exists, EXCEPT for the
                // plugin connection_mode promote path: if the existing bot's
                // stored token is the MOCK sentinel, a streaming connect is
                // allowed to promote it to a real token (§3.5). Everything else
                // (Http connections; real-token bots) keeps the historical
                // `AlreadyRegistered` refusal so duplicate ids cannot hijack.
                if self.repo.get(provided_bot_id).await.is_some() {
                    let allow_promote = matches!(kind, ConnectionKind::Streaming)
                        && self
                            .repo
                            .load_token(provided_bot_id)
                            .await
                            .map(|t| bcs_service_api::is_mock_token(&t))
                            .unwrap_or(false);
                    if !allow_promote {
                        return Err(ConnectError::AlreadyRegistered(
                            provided_bot_id.clone(),
                        ).into());
                    }
                }
                info!(bot_id = %provided_bot_id, "Using preconfigured bot_id");
                provided_bot_id.clone()
            } else {
                let temp_id = new_bot_uuid();
                info!(temp_id = %temp_id, "Generated bot_id");
                temp_id
            };

            // No credential or streaming-slot writes precede this policy check.
            if check_provider {
                provider_surface = self.provider_coordination_surface(&connect_bot_id).await?;
            }
            let registered_token = match kind {
                ConnectionKind::Streaming => {
                    // Recheck registration and streaming admission in the store
                    // after the asynchronous Provider policy read.
                    self.repo
                        .connect_or_promote_streaming(connect_bot_id.clone())
                        .await
                        .map_err(|e| match e {
                            ConnectStreamError::AlreadyConnected(id) => {
                                ConnectError::AlreadyConnected(id)
                            }
                            ConnectStreamError::AlreadyRegistered(id) => {
                                ConnectError::AlreadyRegistered(id)
                            }
                            ConnectStreamError::InternalError(m) => ConnectError::InternalError(m),
                        })?
                }
                ConnectionKind::Http => {
                    self.repo
                        .register_http_connection(connect_bot_id.clone(), token.clone())
                        .await
                }
            };

            (connect_bot_id, registered_token)
        } else {
            // The token-resolved identity is authoritative, not params.bot_id.
            if check_provider {
                provider_surface = self.provider_coordination_surface(&bot_id).await?;
            }
            self.repo.store_token_mapping(token.clone(), bot_id.clone()).await;
            match kind {
                ConnectionKind::Streaming => {
                    // The token was resolved above. An occupied streaming slot
                    // must reject this socket; never fall back to registration
                    // and report success without acquiring the slot.
                    let (reconnected_bot_id, tok) = self.repo
                        .reconnect_streaming(token.clone())
                        .await
                        .map_err(|()| ConnectError::AlreadyConnected(bot_id.clone()))?;
                    info!(bot_id = %reconnected_bot_id, "Reconnected via streaming transport");
                    (reconnected_bot_id, tok)
                }
                ConnectionKind::Http => {
                    self.repo
                        .store_token_mapping(token.clone(), bot_id.clone())
                        .await;
                    (bot_id, token)
                }
            }
        };

        if is_new {
            if self.repo.has_been_onboarded(&final_bot_id).await {
                if let Some(caps) = self.repo.load_from_storage(&final_bot_id).await {
                    let _ = self.repo.register(final_bot_id.clone(), caps).await;
                    info!(bot_id = %final_bot_id, "Loaded capabilities from storage for previously onboarded bot");
                }
            } else {
                info!(bot_id = %final_bot_id, "New bot connected, waiting for onboard");
            }
        } else if let Some(caps) = self.repo.load_from_storage(&final_bot_id).await {
            let _ = self.repo.register(final_bot_id.clone(), caps).await;
            info!(bot_id = %final_bot_id, "Loaded capabilities from storage for reconnecting bot");
        }

        Ok(ProviderCheckedConnection {
            connection: BotConnectResult {
                is_new,
                bot_uuid: final_bot_id,
                token: final_token,
            },
            provider_surface,
        })
    }
}
