use std::collections::BTreeMap;

// ---------------------------------------------------------------------------
// Caller identity service configuration (spec §10.13.7, P1.7/P2-B assembly).
//
// The composition root reads this optional section; when absent, no caller
// capability is assembled and the anti-downgrade guard stays closed. When
// present, the selected plugin validates its fields and refuses invalid values (fail
// closed — never a permissive default and never a success Noop).
// ---------------------------------------------------------------------------

/// Speaking-lease sizing (spec §10.13.9, §15.99). One server-wide idle
/// window: it is granted at every acquire/preempt AND required at message
/// submission, so raising it widens both the hold window and the
/// submission grace. Frontends read `expiresAt_ms` per lease — they pick
/// the configured value up with zero changes.
#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpeakingLeaseConfig {
    pub idle_timeout_ms: i64,
}

impl Default for SpeakingLeaseConfig {
    fn default() -> Self {
        // Frozen legacy constant value: 60s, identical to the pre-§15.99
        // hard-coded `LEASE_IDLE_TIMEOUT_MS`.
        Self { idle_timeout_ms: 60_000 }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerIdentityServiceConfig {
    /// Name of one linked Caller Identity Plugin factory. Unknown names fail at startup.
    pub plugin: String,
    /// Required only for the JWT plugin; internal plugins do not need dummy JWT keys.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jwt: Option<CallerIdentityJwtConfig>,
    /// Owner→caller identity relay (spec L62 ledger direction): absent = the
    /// owner middle-hop never carries identity; present = an explicit,
    /// deployment-owned allowlist. Identity material itself still never
    /// travels through the middle bot — only BCS-instantiated handoff
    /// parks ever hold it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub relay: Option<CallerRelayConfig>,
    /// Implementation-owned settings. The selected factory must validate their schema.
    #[serde(default)]
    pub options: BTreeMap<String, serde_json::Value>,
}

/// Owner-root identity relay policy. `allow` is the trust decision, made by
/// deployment configuration — never by a model at runtime. A human speaking
/// into a session whose admission path captured the ingress may have that
/// identity relayed onto an allowed caller target ONLY through the trusted
/// task-assign edge; the middle bot receives and forwards no token.
#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerRelayConfig {
    pub enabled: bool,
    #[serde(default)]
    pub allow: Vec<CallerRelayRule>,
}

#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerRelayRule {
    pub from_bot: String,
    pub to_bot: String,
}

/// Startup-time fail-closed check: an enabled relay with an empty allowlist
/// is a configuration error ("enabled but allows nothing" is a footgun, not
/// a policy); a disabled or absent section always validates.
pub fn validate_caller_relay_policy(
    config: &CallerIdentityServiceConfig,
) -> Result<(), String> {
    match &config.relay {
        Some(relay) if relay.enabled && relay.allow.is_empty() => {
            Err("caller_identity.relay.enabled requires a non-empty allowlist".to_string())
        }
        _ => Ok(()),
    }
}

#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerIdentityJwtConfig {
    pub issuer: String,
    /// Key id signing headers and required in the trusted verification set.
    pub signing_kid: String,
    /// Ed25519 private key PEM (PKCS#8). Configuration-injected material;
    /// production deployments inject via secret provisioning rather than
    /// committing files.
    pub signing_key_pem: String,
    /// Trusted verification keys — at least one must match `signing_kid`.
    pub public_keys: Vec<CallerIdentityTrustedKeyConfig>,
    /// Independent pseudonym-derivation secret; must not be derived from the
    /// signing keys. Minimum 16 bytes, hex encoded.
    pub pseudonym_key_hex: String,
    /// Longest subject/proof lifetime seconds (spec example: 300).
    pub max_ttl_seconds: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerIdentityTrustedKeyConfig {
    pub kid: String,
    pub public_pem: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relay_section_parses_and_stays_closed_by_default() {
        // Absent section = relay stays disabled: the Owner middle-hop gains
        // nothing without an explicit, deployment-owned allowlist.
        let closed: CallerIdentityServiceConfig =
            toml::from_str("plugin = 'jwt'\n").unwrap();
        assert!(closed.relay.is_none());
        let config: CallerIdentityServiceConfig = toml::from_str(
            "plugin = 'jwt'\n[relay]\nenabled = false\n\
             allow = [{ from_bot = 'bot-a', to_bot = 'bot-b' }]",
        )
        .unwrap();
        let relay = config.relay.unwrap();
        assert!(!relay.enabled);
        assert_eq!(
            relay.allow,
            vec![CallerRelayRule { from_bot: "bot-a".into(), to_bot: "bot-b".into() }]
        );
        // Rule is strict too: unknown fields fail.
        assert!(toml::from_str::<CallerIdentityServiceConfig>(
            "plugin = 'jwt'\n[relay]\nenabled = true\n\
             allow = [{ from_bot = 'a', to_bot = 'b', extra = 1 }]",
        )
        .is_err());
    }

    #[test]
    fn an_enabled_relay_with_an_empty_allowlist_is_a_fail_closed_error() {
        // enabled=true with an empty allowlist is rejected: policy — never
        // the model — decides identity relay, and an "enabled but allows
        // nothing" section must fail closed instead of running as a footgun.
        let config: CallerIdentityServiceConfig = toml::from_str(
            "plugin = 'jwt'\n[relay]\nenabled = true\nallow = []",
        )
        .unwrap();
        assert_eq!(
            validate_caller_relay_policy(&config),
            Err("caller_identity.relay.enabled requires a non-empty allowlist".to_string())
        );
        let disabled: CallerIdentityServiceConfig = toml::from_str(
            "plugin = 'jwt'\n[relay]\nenabled = false\nallow = []",
        )
        .unwrap();
        assert!(validate_caller_relay_policy(&disabled).is_ok());
    }

    #[test]
    fn external_plugins_need_no_jwt_and_unknown_top_level_fields_fail() {
        let config: CallerIdentityServiceConfig = toml::from_str(
            "plugin = 'private-provider'\n[options]\nregion = 'test'",
        ).unwrap();
        assert!(config.jwt.is_none());
        assert_eq!(config.options["region"], "test");
        assert!(toml::from_str::<CallerIdentityServiceConfig>(
            "plugin = 'jwt'\nunknown = true",
        ).is_err());
    }

    #[test]
    fn existing_jwt_table_still_deserializes() {
        let config: CallerIdentityServiceConfig = toml::from_str(r#"
plugin = "jwt"
[jwt]
issuer = "test"
signing_kid = "test-key"
signing_key_pem = "injected-at-startup"
public_keys = [{kid = "test-key", public_pem = "injected-at-startup"}]
pseudonym_key_hex = "injected-at-startup"
max_ttl_seconds = 300
"#).unwrap();
        assert!(config.options.is_empty());
        assert_eq!(config.jwt.unwrap().max_ttl_seconds, 300);
    }
}
