use super::*;

#[async_trait]
impl BotRuntimeConnectionService for Bot {
    async fn connect_streaming(
        &self,
        command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        let BotRuntimeConnectCommand {
            caller_actor_id: _,
            token,
            bot_id,
            protocol_version,
            client_kind,
        } = command;

        if let Some(bot_id) = bot_id.as_deref() {
            self.validate_connect_bot_id(bot_id).await?;
        }

        let requested_client_kind = client_kind
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(|value| value.to_ascii_lowercase());

        let params = BotConnectParams {
            token,
            bot_id,
            protocol_version,
            client_kind: None,
        };
        let result = self
            .registry
            .connect_bot(params, ConnectionKind::Streaming)
            .await
            .map_err(BotUseCaseError::Connect)?;

        if let Some(version) = protocol_version {
            self.registry
                .set_protocol_version(&result.bot_uuid, version)
                .await;
        }
        // Authenticate first, then authorize from this Bot's server-owned Provider.
        // A missing client_kind (as sent by bridge) cannot hide that policy, and
        // a client declaration cannot override a disabled Provider surface.
        let provider_surface = match self.registry
            .resolve_provider_coordination_surface(&result.bot_uuid).await {
            Ok(surface) => surface,
            Err(error) => {
                self.registry.set_bot_info(&result.bot_uuid, "client_kind", None).await;
                self.registry.disconnect_streaming(&result.bot_uuid).await;
                return Err(error.into());
            }
        };
        let negotiated_client_kind = match provider_surface {
            Some(surface) => match (protocol_version, surface.mode) {
                (Some(3), bcs_service_api::CoordinationMode::NativeMcp) => Some("native_mcp".into()),
                (Some(3), bcs_service_api::CoordinationMode::McporterMcp) => Some("mcporter_mcp".into()),
                _ => None,
            },
            None => self.uplink.negotiate(protocol_version, requested_client_kind),
        };
        self.registry
            .set_bot_info(
                &result.bot_uuid,
                "client_kind",
                negotiated_client_kind.clone(),
            )
            .await;

        let mut outcome = BotRuntimeConnectOutcome::from_connect_result(result);
        outcome.negotiated_client_kind = negotiated_client_kind;
        Ok(outcome)
    }

    async fn update_runtime_status(
        &self,
        command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        let BotRuntimeStatusCommand {
            caller_actor_id,
            bot_id,
            status,
        } = command;

        match self.registry.get(&bot_id).await {
            Some(bot) => authorize_bot_management(caller_actor_id.as_deref(), &bot)?,
            None if caller_actor_id.as_deref() == Some(bot_id.as_str()) => {}
            None => return Err(ServiceError::BotNotFound(bot_id).into()),
        }

        let updated = self.registry.update_status(&bot_id).await;

        Ok(BotRuntimeStatusOutcome {
            updated,
            bot_uuid: bot_id,
            status,
        })
    }

    async fn disconnect_streaming(
        &self,
        command: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        // Clear the active profile before releasing the streaming slot. Once
        // the slot is released a reconnect may negotiate a new profile, which
        // an older connection's cleanup must never erase.
        self.registry
            .set_bot_info(&command.bot_id, "client_kind", None)
            .await;
        self.registry.disconnect_streaming(&command.bot_id).await;
        Ok(())
    }

    async fn is_provider_downlink_bot(&self, bot_id: &str) -> ServiceResult<bool> {
        let Some(bot_core) = self.bot_core.as_ref() else {
            return Ok(false);
        };
        let Some(bindings) = bot_core.provider_bindings_repo() else {
            return Ok(false);
        };
        let binding = bindings.get_binding_by_bot_uuid(bot_id).await?;
        Ok(binding.is_some_and(|binding| !binding.disabled))
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        self.registry.resolve_delivery_target(bot_id).await
    }
}
