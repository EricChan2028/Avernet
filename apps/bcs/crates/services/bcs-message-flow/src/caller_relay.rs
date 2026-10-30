//! Owner→caller identity relay state hand-instantiated with the composition
//! (`caller_relay_ledger` owns the capture semantics). The policy is the
//! deployment's trust decision; the ledger is epoch-scoped memory. Absent
//! state = every session keeps today's fail-closed behavior.

use std::sync::Arc;

use async_trait::async_trait;

use bcs_config_api::{CallerRelayConfig, CallerRelayRule};

use crate::caller_relay_ledger::CallerRelayLedger;

/// Capture window default: twice the default caller JWT TTL — same scale as
/// the handoff's published reaping horizon, so a captured identity lives no
/// longer than the credential it would mint.
pub const DEFAULT_RELAY_CAPTURE_TTL_MS: i64 = 600_000;

pub struct CallerRelayState {
    pub ledger: Arc<CallerRelayLedger>,
    pub policy: CallerRelayConfig,
    pub capture_ttl_ms: i64,
}

impl CallerRelayState {
    pub fn new(policy: CallerRelayConfig, capture_ttl_ms: i64) -> Self {
        assert!(capture_ttl_ms > 0, "relay capture ttl must be positive");
        Self {
            // Same order of magnitude as the handoff slot map (256): one
            // live capture per active human session.
            ledger: Arc::new(CallerRelayLedger::new(256)),
            policy,
            capture_ttl_ms,
        }
    }

    /// The trust decision: deployment-owned allowlist, evaluated exactly at
    /// the two trusted seams (admission capture needs the from-side target
    /// to matter later; the redeem side re-checks both ends). Presence in
    /// the ledger decides the rest.
    pub fn allows(&self, rule: impl Fn(&CallerRelayRule) -> bool) -> bool {
        self.policy.enabled && self.policy.allow.iter().any(rule)
    }

    /// `true` only when the middle (from) bot appears on the allowance's
    /// from-side —relay captures only for sessions where some allowlist
    /// entry's from_bot is a routed participant.
    pub fn allows_relay_from(&self, from_bot: &str) -> bool {
        self.allows(|rule| rule.from_bot == from_bot)
    }

    /// The full relay check for one task-assign edge: exact (from, to) pair.
    pub fn allows_relay_edge(&self, from_bot: &str, to_bot: &str) -> bool {
        self.allows(|rule| rule.from_bot == from_bot && rule.to_bot == to_bot)
    }
}

/// The production `CallerRelayRedeemerPort`: the owner→caller relay's
/// orchestration-submission adapter (design 2026-10-30). The deployment
/// allowlist decides — every execution assignee of the submitted run must
/// be a declared `to_bot` of the submitting bot's rules — and the session
/// ledger owns the capture. Identity material never leaves BCS: the
/// middle (submitting) bot carries only the plain command.
pub struct CallerRelaySubmissionRedeemer {
    pub ledger: Arc<CallerRelayLedger>,
    pub policy: CallerRelayConfig,
}

impl CallerRelaySubmissionRedeemer {
    pub fn new(ledger: Arc<CallerRelayLedger>, policy: CallerRelayConfig) -> Self {
        Self { ledger, policy }
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::CallerRelayRedeemerPort for CallerRelaySubmissionRedeemer {
    async fn redeem_submission(
        &self,
        environment: &str,
        _group_id: &str,
        session_id: &str,
        submitted_by_bot: &str,
        execution_assignees: &[String],
        now_ms: i64,
    ) -> Option<bcs_service_api::port::CallerRelayCapture> {
        if !self.policy.enabled || submitted_by_bot.trim().is_empty() {
            return None;
        }
        // The to-side gate at submission time: every execution-policy
        // assignee of this run must be a declared to_bot of the submitter.
        if !execution_assignees.iter().all(|assignee| {
            self.policy
                .allow
                .iter()
                .any(|rule| rule.from_bot == submitted_by_bot && &rule.to_bot == assignee)
        }) {
            return None;
        }
        let (_turn, ingress, expires_at_ms) =
            self.ledger.redeem_capture(environment, session_id, now_ms)?;
        Some(bcs_service_api::port::CallerRelayCapture { ingress, expires_at_ms })
    }
}
