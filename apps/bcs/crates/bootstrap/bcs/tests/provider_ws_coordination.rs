mod helpers;

use std::{net::SocketAddr, time::{Duration, SystemTime, UNIX_EPOCH}};
use bcs::{BcsServer, BcsServerExtensions, InfrastructurePlugins};
use bcs_cache_api::CacheSetMode;
use bcs_service_api::{BotRunContext, CoordinationMode, ProviderAuthMode,
    ProviderBotConnectionMode, RegisterProviderBotParams};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, tungstenite::Message};

type Socket = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;
const TOOL: &str = "mcp__custom_bcs__bcs_assign_task";
const INTENT: &str = "bcs_intent_0123456789abcdef0123456789abcdef";

async fn receive(ws: &mut Socket) -> Value {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match ws.next().await.expect("socket open").expect("frame") {
                Message::Text(text) => return serde_json::from_str(&text).unwrap(),
                Message::Ping(data) => ws.send(Message::Pong(data)).await.unwrap(),
                _ => {}
            }
        }
    }).await.expect("frame within deadline")
}

async fn send(ws: &mut Socket, frame: Value) {
    ws.send(Message::Text(frame.to_string().into())).await.unwrap();
}

async fn connect(addr: SocketAddr, bot_id: &str, token: &str) -> Socket {
    let (mut ws, _) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws/bot")).await.unwrap();
    send(&mut ws, json!({"type":"req","id":"connect","method":"bot.connect",
        "params":{"bot_id":bot_id,"token":token,"protocol_version":3,"client_kind":null}})).await;
    loop {
        let frame = receive(&mut ws).await;
        if frame["id"] == "connect" {
            assert_eq!(frame["ok"], true, "{frame}");
            assert_eq!(frame["payload"]["capabilities"]["tool_result_task_intent"], true, "{frame}");
            return ws;
        }
    }
}

async fn barrier(ws: &mut Socket) {
    send(ws, json!({"type":"req","id":"barrier","method":"bot.status","params":{"status":"idle"}})).await;
    loop {
        let frame = receive(ws).await;
        if frame["id"] == "barrier" {
            assert_eq!(frame["ok"], true, "{frame}");
            return;
        }
    }
}

async fn emit_tool(ws: &mut Socket, session_id: &str, tool: &str, call: &str, seq: u64, is_error: bool) {
    send(ws, json!({"type":"event","event":"agent","payload":{
        "runId":"provider-run","sessionId":session_id,"seq":seq,"ts":123,"stream":"tool",
        "phase":"start","name":tool,"toolCallId":call,"args":{}}})).await;
    // Bridge's V3 wire contract includes the tool name. The v2 echo itself
    // carries only a stored reference, so dispatch must resolve its arguments.
    let echo = json!({"__bcs_coordination__":true,"v":2,"tool":"bcs_assign_task",
        "intent_id":INTENT,"status":"stored"}).to_string();
    send(ws, json!({"type":"event","event":"agent","payload":{
        "runId":"provider-run","sessionId":session_id,"seq":seq+1,"ts":124,"stream":"tool",
        "phase":"result","name":tool,"toolCallId":call,"isError":is_error,
        "result":{"content":[{"type":"text","text":echo}]}}})).await;
    barrier(ws).await;
}

#[tokio::test]
async fn provider_bridge_stored_intent_reaches_worker_once_through_real_ws() {
    let dir = tempfile::tempdir().unwrap();
    // This test binary contains one test; initialize its dedicated key before
    // starting any server task, as required by the production composition root.
    unsafe { std::env::set_var("BCS_SECRET_PROVIDER_WS_TEST", "test-only-provider-ws-signing-key"); }
    let mut config = helpers::create_test_config(&dir.path().to_path_buf());
    config.secret.provider = "env".into();
    config.gateway_principal.signing_key_secret = Some("provider-ws-test".into());
    config.group_session_ws.signing_key_secret = "provider-ws-test".into();
    config.session_files.backend.insert("data_dir".into(),
        toml::Value::String(dir.path().join("files").to_string_lossy().into_owned()));
    assert!(config.uplink.allowed_profiles.is_empty());
    // Use production DB/cache composition and retain the cache handle to model
    // the MCP producer's insert-only v2 intent write, without mocking dispatch.
    let infrastructure = InfrastructurePlugins::local_for_tests();
    let cache = infrastructure.cache().unwrap();
    let (addr, server, state) = BcsServer::new_with_infrastructure(config, infrastructure,
        BcsServerExtensions::default()).await.unwrap()
        .run_on_random_port_with_state().await.unwrap();
    struct Stop(tokio::task::AbortHandle);
    impl Drop for Stop { fn drop(&mut self) { self.0.abort(); } }
    let _stop = Stop(server.abort_handle());
    let provider = state.services.provider_core.register_provider("Custom Provider".into(), None,
        ProviderAuthMode::StaticBearer, "alice".into(), None,
        Some(serde_json::from_value(json!({"mode":"native_mcp","mcp_server":"custom_bcs",
            "tool_name_mapping":{TOOL:"bcs_assign_task"}})).unwrap())).await.unwrap();
    let (registered, _) = state.services.provider_bot_core.register_provider_bot_with_bot_uuid(
        &provider.provider.provider_id, &provider.provider_admin_token, RegisterProviderBotParams {
            bot_name:"Manager".into(), provider_bot_ref:"manager:alice".into(),
            owners:vec!["alice".into()], connection_mode:ProviderBotConnectionMode::Plugin,
            ..Default::default()
        }).await.unwrap();
    let bot_id = registered.bot_uuid;
    let token = state.services.registry.load_token(&bot_id).await.unwrap();
    let mut manager = connect(addr, &bot_id, &token).await;
    let client = bcs_cli::BcsClient::with_token(format!("http://{addr}"), &token);
    client.set_visibility(&bot_id, "public").await.unwrap();
    let mut worker = helpers::MockBot::connect_v3(addr).await;
    worker.register("Worker", &["review"], addr).await;
    let surface = state.services.registry.resolve_coordination_surface(&bot_id).await.unwrap();
    assert_eq!(surface.mode, CoordinationMode::NativeMcp);
    assert_eq!(surface.tool_name_mapping.get(TOOL).map(String::as_str), Some("bcs_assign_task"));
    assert_eq!(state.services.registry.resolve_coordination_surface(&worker.bot_id).await.unwrap().mode,
        CoordinationMode::LegacyUpstream, "worker profile must not choose manager's tool mapping");
    let response = reqwest::Client::new().post(format!("http://{addr}/groups"))
        .bearer_auth(&token).json(&json!({"driver_bot":bot_id,"group_strategy":"manager_worker",
            "participants":[{"bot_uuid":bot_id,"role":"manager"},{"bot_uuid":worker.bot_id,"role":"worker"}]}))
        .send().await.unwrap();
    let status = response.status();
    let group: Value = response.json().await.unwrap();
    assert!(status.is_success(), "{group}");
    let group_id = group["id"].as_str().unwrap();
    let session_id = group["session_id"].as_str().unwrap();
    loop {
        let frame = receive(&mut manager).await;
        if frame["method"] == "chat.send" && frame.to_string().contains("<GroupContext>") {
            assert!(frame.to_string().contains("custom_bcs"), "{frame}");
            break;
        }
    }
    while worker.recv_frame_short().await.is_some() {}
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64;
    state.services.bot_run_context.put_context(BotRunContext {
        run_id:"provider-run".into(), bot_id:bot_id.clone(), group_id:group_id.into(),
        bcs_session_id:Some(session_id.into()), deadline_ms:now+60000, terminal:false,
    }).await;
    let key = |suffix: &str| format!("bcs:coordination:v2:{INTENT}:{suffix}");
    cache.set_value(&key("payload"), serde_json::to_vec(&json!({
        "intent_id":INTENT,"v":2,"tool":"bcs_assign_task",
        "arguments":{"target_bot":worker.bot_id,"message":"provider task: compute 17 + 25"},
        "created_at_ms":now,"expires_at_ms":now+60000
    })).unwrap(), Some(Duration::from_secs(60)), CacheSetMode::InsertOnly).await.unwrap();

    emit_tool(&mut manager, session_id, "mcp__bcs__bcs_assign_task", "wrong-name", 1, false).await;
    assert!(cache.get_value(&key("claim")).await.unwrap().is_none(), "unmapped tool claimed intent");
    assert!(worker.recv_frame_short().await.is_none(), "unmapped tool dispatched");
    emit_tool(&mut manager, session_id, TOOL, "failed-call", 3, true).await;
    assert!(cache.get_value(&key("claim")).await.unwrap().is_none(), "failed tool claimed intent");
    assert!(worker.recv_frame_short().await.is_none(), "failed tool dispatched");

    emit_tool(&mut manager, session_id, TOOL, "valid-call", 5, false).await;
    let stored = cache.get_value(&key("result")).await.unwrap();
    assert!(stored.is_some(), "missing receipt; claim={:?}", cache.get_value(&key("claim")).await.unwrap());
    let dispatch = worker.recv_frame().await.expect("worker receives task");
    assert_eq!(dispatch["method"], "chat.send", "{dispatch}");
    assert!(dispatch.to_string().contains("provider task: compute 17 + 25"), "{dispatch}");
    let receipt: Value = serde_json::from_slice(&cache.get_value(&key("result")).await.unwrap()
        .expect("applied receipt")).unwrap();
    assert_eq!(receipt["status"], "applied", "{receipt}");
    assert!(receipt["task_id"].as_str().is_some_and(|id| !id.is_empty()), "{receipt}");
    emit_tool(&mut manager, session_id, TOOL, "valid-call", 7, false).await;
    assert!(worker.recv_frame_short().await.is_none(), "duplicate intent dispatched again");
    let repeated: Value = serde_json::from_slice(&cache.get_value(&key("result")).await.unwrap().unwrap()).unwrap();
    assert_eq!(receipt, repeated, "duplicate must preserve original task receipt");

    manager.close(None).await.unwrap();
    worker.disconnect().await;
    server.abort();
}

