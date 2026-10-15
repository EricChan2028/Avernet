//! `FriendshipWritebackService` contract: mirror add/remove into the legacy
//! friendship repo, with a best-effort relation-graph mirror.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_friend::{FriendshipWritebackService, MemoryFriendRepo};
use bcs_service_api::port::FriendshipWritebackPort;
use bcs_service_api::{FriendRepoPort, ServiceError, ServiceResult};
use bcs_test_support::NoopRelationCoreService;

fn writeback() -> (FriendshipWritebackService, Arc<MemoryFriendRepo>) {
    let repo = Arc::new(MemoryFriendRepo::new());
    let service = FriendshipWritebackService::new(
        repo.clone(),
        Arc::new(NoopRelationCoreService),
    );
    (service, repo)
}

#[tokio::test]
async fn add_friendship_writes_legacy_row_idempotently() {
    let (service, repo) = writeback();

    service
        .add_friendship("bot_a:1", "20260525_x:85020")
        .await
        .expect("add friendship");

    assert!(repo
        .are_friends("bot_a:1", "20260525_x:85020")
        .await
        .expect("are_friends"));

    // Idempotent: re-approve of the same pair must not error.
    service
        .add_friendship("20260525_x:85020", "bot_a:1")
        .await
        .expect("re-add friendship (idempotent)");

    assert_eq!(
        repo.list_friends("bot_a:1").await.expect("list friends"),
        vec!["20260525_x:85020".to_string()]
    );
}

#[tokio::test]
async fn remove_friendship_deletes_row_and_reports_once() {
    let (service, repo) = writeback();
    service
        .add_friendship("bot_a:1", "bot_b:2")
        .await
        .expect("add friendship");

    assert!(
        service
            .remove_friendship("bot_a:1", "bot_b:2")
            .await
            .expect("remove friendship")
    );
    assert!(
        !service
            .remove_friendship("bot_a:1", "bot_b:2")
            .await
            .expect("re-remove friendship")
    );
    assert!(!repo
        .are_friends("bot_a:1", "bot_b:2")
        .await
        .expect("are_friends"));
}

#[tokio::test]
async fn repo_failure_propagates_to_caller() {
    let service = FriendshipWritebackService::new(
        Arc::new(FailingFriendRepo),
        Arc::new(NoopRelationCoreService),
    );

    let err = service
        .add_friendship("bot_a:1", "bot_b:2")
        .await
        .expect_err("repo failure must surface");

    assert!(
        matches!(err, ServiceError::InternalError(ref message) if message == "friend store unavailable"),
        "got {err:?}"
    );
}

/// Minimal failing repo used by the propagation test.
struct FailingFriendRepo;

#[async_trait]
impl FriendRepoPort for FailingFriendRepo {
    async fn list_friends(&self, _bot_id: &str) -> ServiceResult<Vec<String>> {
        Err(ServiceError::InternalError(
            "friend store unavailable".to_string(),
        ))
    }

    async fn are_friends(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<bool> {
        Err(ServiceError::InternalError(
            "friend store unavailable".to_string(),
        ))
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "friend store unavailable".to_string(),
        ))
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }

    async fn remove_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<bool> {
        Ok(false)
    }
}