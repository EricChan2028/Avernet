use std::sync::Arc;

use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_api_http::router;
use bcs_service_api::application::v1::{
    ApplicationError, BotRegistration, IssueRegisterToken, RegisterBot, RegisterService,
    RegisterTokenView,
};
use serde_json::{Value, json};
use tower::ServiceExt;

#[allow(dead_code)]
mod register_support;

struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        Err(ApplicationError::internal("register not configured"))
    }

    async fn register_bot(
        &self,
        _command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("register not configured"))
    }
}

fn app() -> axum::Router {
    router(register_support::test_state(Arc::new(NoopRegisterService)))
}

fn authed_post(uri: &str, body: Value) -> Request<Body> {
    register_support::authenticated_request("POST", uri, body)
}

#[tokio::test]
async fn groupcontext_routes_are_not_mounted_on_bcs_api_http_router() {
    // The CLI/runtime `/groupcontext/*` endpoints are mounted by
    // `bcs-http::router::build_api_routes` so they share the direct BCS bot-token
    // auth path used by `bcs-cli chat`. They must not be mounted again here:
    // bootstrap merges bcs-http and bcs-api-http routers, and duplicate concrete
    // paths panic in Axum with `Overlapping method route`.
    for path in [
        "/groupcontext/add",
        "/groupcontext/update",
        "/groupcontext/list",
        "/groupcontext/get",
        "/groupcontext/systemset",
    ] {
        let response = app()
            .oneshot(authed_post(path, json!({})))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{path}");
    }
}
