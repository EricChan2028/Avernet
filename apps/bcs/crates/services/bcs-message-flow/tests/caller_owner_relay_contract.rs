//! Owner→caller identity relay contract (spec L62 direction): a Human
//! speaking into an ordinary Owner session gets their authenticated ingress
//! captured (relay-enabled deployments only); the trusted task-assign edge
//! redeems it onto an ALLOWLISTED caller target's handoff scope, keyed from
//! the committed row's own facts. The middle bot carries no token; an
//! unallowed edge, an expired capture or a spent budget all stay exactly as
//! fail-closed as a relay-disabled deployment.

use std::sync::{Arc, atomic::Ordering};

use bcs_config::resolve_env_str as resolve_env;
use bcs_config_api::message_delivery::{BotDeliveryMode, DeliveryPolicy};
use bcs_config_api::{CallerRelayConfig, CallerRelayRule};
use bcs_service_api::{
    BotCallerConfiguration, BotCandidateReadQuery, BotControlPlaneCandidate,
    BotControlPlaneCoreService, BotControlPlaneRecord, BotControlPlaneView,
    CallerIdentityConfig, CallerIdentitySnapshot, CallerMinimumIsolation, ServiceResult,
};
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::{GroupStrategy, ParticipantRole};
use bcs_message_flow::caller_admission::CallerAdmissionDeps;
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_relay::CallerRelayState;
use bcs_message_flow::{
    BcsMessageFlow, MemoryBotRunContextStore, delivery_policy::LiveDeliveryPolicy,
    managed_delivery::ManagedMessageDelivery,
};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_handoff::{
    HandoffClaim, HandoffIdentityReadiness, HandoffScope, HumanCallerDeliveryHandoffService,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::{
    CallerContext, GroupCoreService, ManagedMessageDeliveryService, MessageFlowService,
    TaskDispatchCommand,
};
use serde_json::json;

#[path = "support/session.rs"]
mod session;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

const SESSION: &str = "group-1:task";

fn dispatch_command() -> TaskDispatchCommand {
    TaskDispatchCommand {
        caller_delegation: Some(bcs_service_api::CallerDelegationAssignment {
            parent_run_id: "run-manager".into(),
            parent_attempt: 1,
            parent_bot_id: "bot-driver".into(),
            invocation_id: "tool-call-relay".into(),
        }),
        driver_bot_id: "bot-driver".into(),
        group_id: "group-1".into(),
        target_bot_id: "bot-observer".into(),
        target_bot_name: None,
        payload: json!({"message":"RELAY_BODY", "bcs_session_id":SESSION, "response_mode":"full"}),
    }
}

fn ingress(actor: &str) -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: actor.into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        1,
        &resolve_env(),
    )
    .expect("valid ingress")
}

struct CallerEnabledControlPlane;

/// The pure-Owner deployment shape: every bot reads caller-unconfigured, so
/// the session fences as a Plain group and the Human's speech is ordinary
/// Owner work — exactly the H→A(owner) leg the relay capture targets.
struct CallerUnconfiguredControlPlane;

#[async_trait::async_trait]
impl BotControlPlaneCoreService for CallerUnconfiguredControlPlane {
    async fn get_caller_configuration(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(Some(BotCallerConfiguration { snapshot: None }))
    }

    async fn get_record(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneRecord>> {
        unimplemented!("not needed by this test")
    }
    async fn get(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn get_by_ids(&self, _: &[String], _: &str) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn list_candidates(&self, _: BotCandidateReadQuery)
        -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        unimplemented!("not needed by this test")
    }
    async fn list_by_creator(
        &self,
        _: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn patch(
        &self,
        _: &str,
        _: &str,
        _: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
}

#[async_trait::async_trait]
impl BotControlPlaneCoreService for CallerEnabledControlPlane {
    async fn get_caller_configuration(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(Some(BotCallerConfiguration {
            snapshot: Some(CallerIdentitySnapshot {
                source_revision: 1,
                caller_identity: CallerIdentityConfig {
                    enabled: true,
                    minimum_isolation: CallerMinimumIsolation::CallerContainer,
                },
            }),
        }))
    }

    async fn get_record(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneRecord>> {
        unimplemented!("not needed by this test")
    }
    async fn get(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn get_by_ids(&self, _: &[String], _: &str) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn list_candidates(&self, _: BotCandidateReadQuery)
        -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        unimplemented!("not needed by this test")
    }
    async fn list_by_creator(
        &self,
        _: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn patch(
        &self,
        _: &str,
        _: &str,
        _: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
}

struct Fixture {
    support: support::FlowTestSupport,
    flow: Arc<BcsMessageFlow>,
    service: Arc<ManagedMessageDelivery>,
    handoff: Arc<HumanCallerDeliveryHandoff>,
    ledger: Arc<bcs_message_flow::caller_relay_ledger::CallerRelayLedger>,
}

async fn fixture(allow: Vec<CallerRelayRule>, relay_enabled: bool) -> Fixture {
    fixture_with_plane(allow, relay_enabled, true).await
}

async fn fixture_with_plane(
    allow: Vec<CallerRelayRule>,
    relay_enabled: bool,
    caller_enabled_plane: bool,
) -> Fixture {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    for p in &mut group.participants {
        if p.bot_uuid == "bot-driver" { p.role = ParticipantRole::Manager; }
        if p.bot_uuid == "bot-observer" { p.role = ParticipantRole::Worker; }
    }
    support.group.upsert(group.clone()).await.unwrap();
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    live.scheduler_available.store(true, Ordering::SeqCst);
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let relay = CallerRelayState::new(
        CallerRelayConfig { enabled: relay_enabled, allow },
        600_000,
    );
    let ledger = relay.ledger.clone();
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
        .with_message_repo(repo).with_managed_deliveries(service.clone())
        .with_bot_run_context(Arc::new(MemoryBotRunContextStore::default()))
        .with_session_management(Arc::new(session::StaticSessionManagement::new(
            session::test_session(SESSION, "group-1", group.participants.clone()),
        )))
        .with_caller_relay(relay);
    let mut flow = Arc::new(flow);
    {
        let flow_mut = Arc::get_mut(&mut flow).unwrap();
        flow_mut.delivery_policy = Some(live);
        flow_mut.caller_admission = Some(Arc::new(CallerAdmissionDeps {
            handoff: handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            admission: service.clone(),
            control_plane: if caller_enabled_plane {
                Arc::new(CallerEnabledControlPlane)
            } else {
                Arc::new(CallerUnconfiguredControlPlane)
            },
            reserve_ttl_ms: 30_000,
        }));
    }
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true; policy.flow_enabled.system = true; policy.flow_enabled.task = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    policy.queue_ttl_ms = Some(300_000);
    let admin = CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() });
    flow.replace_delivery_policy(admin, 0, policy).await.unwrap();
    Fixture { support, flow, service, handoff, ledger }
}

async fn dispatch_row(f: &Fixture) -> (String, bcs_domain::message_delivery::PersistedMessageDelivery) {
    let mut cmd = dispatch_command();
    cmd.caller_delegation = cmd.caller_delegation.clone();
    let outcome = bcs_test_support::contract::application::queued_task::queued_task_dispatch_contract_tests(
        f.flow.as_ref(), cmd).await;
    let rows = f.service.snapshot(Some(SESSION)).await.unwrap();
    let row = rows.into_iter()
        .find(|r| r.semantic_projection_json["task"]["task_id"] == outcome.task_id)
        .expect("dispatched row");
    (outcome.task_id, row)
}

fn worker_scope(row: &bcs_domain::message_delivery::PersistedMessageDelivery) -> HandoffScope {
    HandoffScope {
        env: resolve_env(),
        session_id: row.session_id.clone(),
        group_id: Some(row.group_id.clone()),
        source_message_id: row.source_message_id.clone(),
        target_bot_id: row.target_bot_id.clone(),
    }
}

#[tokio::test]
async fn a_delegation_without_relay_state_stays_fail_closed() {
    // Present-day behavior pinned: with no relay state on the composition,
    // the owner middle-hop delegation parks nothing — the worker row's scope
    // reads Gone and nothing upgrades it.
    let mut f = fixture(vec![], false).await;
    // Detach the relay state entirely for the pure owner shape.
    Arc::get_mut(&mut f.flow).unwrap().caller_relay = None;
    let (_, row) = dispatch_row(&f).await;
    assert_eq!(row.semantic_projection_json["caller_delegation"]["parent_run_id"], "run-manager");
    assert_eq!(
        f.handoff.identity_readiness(&worker_scope(&row)).await,
        HandoffIdentityReadiness::Gone,
        "relay-disabled deployments never park identity through the middle hop"
    );
}

#[tokio::test]
async fn an_allowed_edge_redeems_the_session_capture_exactly_once() {
    // The core relay flow: the session's captured human ingress is redeemed
    // by the trusted task-assign edge and parked under the worker row's own
    // scope; the first committed claim consumes it and everything later
    // answers AlreadyClaimed.
    let f = fixture(vec![CallerRelayRule { from_bot: "bot-driver".into(), to_bot: "bot-observer".into() }], true).await;
    let now = chrono::Utc::now().timestamp_millis();
    assert!(f.ledger.record(&resolve_env(), SESSION, "turn-relay-1", ingress("human_alice"), now, 600_000));
    let (task_id, row) = dispatch_row(&f).await;
    let scope = worker_scope(&row);
    assert_eq!(
        f.handoff.identity_readiness(&scope).await,
        HandoffIdentityReadiness::Published,
        "the task with id {task_id} must have the session's captured identity parked"
    );
    let claimed = f.handoff.claim(&scope, HandoffClaim::new(
        row.delivery_id.clone(), row.state.state_version, row.run_id.clone(), 1, now + 1,
    )).await.expect("the relayed identity claims once");
    assert_eq!(claimed.actor().actor_id, "human_alice");
    assert_eq!(
        f.handoff.identity_readiness(&scope).await,
        HandoffIdentityReadiness::Gone,
        "the claim consumed the parked ingress; later attempts find the tombstone"
    );
}

#[tokio::test]
async fn a_disallowed_edge_never_redeems_the_capture() {
    // Policy decides, not the model: an allowlist that does not name this
    // exact (middle, target) edge leaves the capture untouched (budget
    // intact) and the worker scope stays Gone.
    let f = fixture(vec![CallerRelayRule { from_bot: "bot-other".into(), to_bot: "bot-observer".into() }], true).await;
    let now = chrono::Utc::now().timestamp_millis();
    assert!(f.ledger.record(&resolve_env(), SESSION, "turn-relay-1", ingress("human_alice"), now, 600_000));
    let (_, row) = dispatch_row(&f).await;
    assert_eq!(
        f.handoff.identity_readiness(&worker_scope(&row)).await,
        HandoffIdentityReadiness::Gone,
        "a disallowed relay edge must not park anything"
    );
    // The capture survives untouched for an actually-allowed edge later.
    assert!(f.ledger.redeem(&resolve_env(), SESSION, now).is_some());
}

#[tokio::test]
async fn an_expired_capture_fails_closed_at_the_edge() {
    // B5 at the relay boundary: a capture past its window redeems nothing —
    // the Human must speak again; the task stays fail-closed.
    let f = fixture(vec![CallerRelayRule { from_bot: "bot-driver".into(), to_bot: "bot-observer".into() }], true).await;
    let now = chrono::Utc::now().timestamp_millis();
    assert!(f.ledger.record(&resolve_env(), SESSION, "turn-relay-stale", ingress("human_alice"), now - 1_200_000, 600_000));
    let (_, row) = dispatch_row(&f).await;
    assert_eq!(
        f.handoff.identity_readiness(&worker_scope(&row)).await,
        HandoffIdentityReadiness::Gone,
        "an expired capture must not relay"
    );
}

#[tokio::test]
async fn an_authenticated_human_speech_is_captured_at_admission() {
    // The capture seam: an authenticated Human speaking into the owner
    // session, with the allowlist's middle bot among the mentions, lands in
    // the relay ledger under the session key — no caller target needs to be
    // routed, and nothing is handed to any bot yet.
    let f = fixture_with_plane(vec![CallerRelayRule { from_bot: "bot-driver".into(), to_bot: "bot-observer".into() }], true, false).await;
    let now = chrono::Utc::now().timestamp_millis();
    let cmd = bcs_service_api::WebSendCommand {
        caller_ingress: Some(ingress("human_alice")),
        speaking_lease: None,
        caller_execution: None,
        caller: CallerContext::Human(HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() }),
        group_id: "group-1".into(),
        session_id: Some(SESSION.into()),
        from_actor_id: "human_alice".into(),
        from_name: Some("Alice".into()),
        message: "please relay this".into(),
        mentions: vec!["bot-driver".into()],
        attachments: None,
        thinking: None,
        idempotency_key: Some("relay-capture-1".into()),
        source_im_message_id: Some("turn-relay-capture".into()),
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: vec![],
    };
    f.flow.handle_web_send(cmd).await.expect("web send admits");
    let (turn, captured) = f.ledger.redeem(&resolve_env(), SESSION, now)
        .expect("the human's speech must be captured for an allowlisted middle hop");
    assert_eq!(turn, "turn-relay-capture");
    assert_eq!(captured.actor().actor_id, "human_alice");
}

// Keep the unused-variable lint honest for fixtures that ignore status.
#[allow(dead_code)]
fn _status_shape(_: Status) {}
