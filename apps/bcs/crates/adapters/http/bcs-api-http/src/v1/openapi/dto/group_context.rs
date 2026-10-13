//! Dto types for the Group Context OpenAPI endpoints.
//!
//! The request body and query-argument shapes re-use the exact serde
//! attributes from the application-layer types (which themselves re-export
//! the bcs_domain types with matching #[derive(Serialize, Deserialize)]).
//! This keeps the wire format identical between the HTTP adapter and the
//! application layer.

use serde::Deserialize;

// Re-export application-layer types — same wire shape as the service API.
#[allow(unused_imports)]
#[rustfmt::skip]
pub use bcs_service_api::application::v1::{
    AddGroupContext,
    GroupContextAck,
    GroupContextGetItem,
    GroupContextListResponse,
    GroupContextListItem,
    GroupContextPermission,
    GroupContextPolicy,
    GroupContextRequestContext,
    GroupContextStatus,
    GroupContextTime,
    GetGroupContext,
    ListGroupContexts,
    ScopeLevel,
    SystemSetGroupContext,
    UpdateGroupContext,
};

// Lightweight field deserializer: `null` → `None`, any other JSON value →
// `Some(parsed_value)`. Reuses the existing helper from the `group` dto.
use super::group::deserialize_present_non_null;

// ─── HTTP-layer request-body / query DTOs ───────────────────────────────────

/// URL query parameters shared by every Group Context endpoint.
///
/// `groupId` is always required; `sessionId` and `roundId` are conditionally
/// required depending on the `scope_level` value in the request body.
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct GroupContextQuery {
    pub group_id: String,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub session_id: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub round_id: Option<String>,
}

impl GroupContextQuery {
    #[allow(dead_code)]
    pub(crate) fn into_context(&self) -> GroupContextRequestContext {
        GroupContextRequestContext {
            group_id: self.group_id.clone(),
            session_id: self.session_id.clone(),
            round_id: self.round_id.clone(),
        }
    }
}

/// Request body for `POST /groupcontext/add`.
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AddGroupContextRequest {
    pub name: String,
    pub scope_level: ScopeLevel,
    pub content: String,
    pub visible_to: GroupContextPolicy,
    pub collect_from: GroupContextPolicy,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub valid_from: Option<GroupContextTime>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub valid_to: Option<GroupContextTime>,
}

/// Request body for `POST /groupcontext/update`.
///
/// Omitted optional fields keep their old values.
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct UpdateGroupContextRequest {
    pub name: String,
    pub scope_level: ScopeLevel,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub content: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub change_reason: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub visible_to: Option<GroupContextPolicy>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub collect_from: Option<GroupContextPolicy>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub valid_from: Option<GroupContextTime>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub valid_to: Option<GroupContextTime>,
}

/// Request body for `POST /groupcontext/list`.
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ListGroupContextsRequest {
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub scope_levels: Option<Vec<ScopeLevel>>,
}

/// Request body for `POST /groupcontext/get`.
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct GetGroupContextRequest {
    pub name: String,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub scope_levels: Option<Vec<ScopeLevel>>,
    #[serde(default)]
    pub limit: Option<u32>,
}

/// Request body for `POST /groupcontext/systemset`.
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SystemSetGroupContextRequest {
    pub name: String,
    pub scope_level: ScopeLevel,
    pub content: String,
    pub visible_to: GroupContextPolicy,
    pub collect_from: GroupContextPolicy,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub valid_from: Option<GroupContextTime>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub valid_to: Option<GroupContextTime>,
}
