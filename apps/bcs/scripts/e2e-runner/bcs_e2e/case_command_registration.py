"""Command registration helpers for the cross-environment E2E runner."""

from __future__ import annotations

from pathlib import Path

from .edge_read_case_run import CASE_KEYS as EDGE_CASE_KEYS
from .network_read_case_run import CASE_KEYS as NETWORK_READ_CASE_KEYS


CASE_COMMANDS = frozenset({
    "dual-workspace-cli-status-run",
    "dual-workspace-run",
    "dual-workspace-group-run",
    "dual-workspace-story-run",
    "dual-session-run",
    "dual-session-public-run",
    "dual-channel-bindings-run",
    "dual-channel-run",
    "dual-structured-core-run",
    "dual-structured-templates-run",
    "dual-webhook-subscription-run",
    "dual-friend-connection-decisions-run",
    "dual-friend-public-accept-run",
    "dual-session-file-run",
    "dual-collaboration-team-run",
    "dual-group-context-run",
    "dual-edge-read-run",
    "dual-network-read-run",
    "dual-network-register-run",
    "dual-network-status-run",
    "dual-friend-run",
})


def register_case_commands(commands) -> None:
    dual = commands.add_parser("dual-workspace-cli-status-run", help="Run the reviewed Workspace status/termination story through Singlebox or preprod product CLI")
    dual.add_argument("--profile", type=Path, required=True)
    dual.add_argument("--cli-binary", type=Path, required=True)
    dual.add_argument("--cli-binary-sha256", required=True)
    dual.add_argument("--state-dir", type=Path, required=True)
    dual.add_argument("--run-id", required=True)
    dual.add_argument("--confirm-run-id", required=True)
    dual.add_argument("--allow-write", action="store_true")
    cli_case = commands.add_parser("dual-workspace-run", help="Run the reviewed dual Workspace create/detail/list/fuse/member stories through Singlebox or preprod product CLI")
    cli_case.add_argument("--profile", type=Path, required=True)
    cli_case.add_argument("--cli-binary", type=Path, required=True)
    cli_case.add_argument("--cli-binary-sha256", required=True)
    cli_case.add_argument("--case", choices=("create-get", "detail", "list", "fuse", "member"), required=True)
    cli_case.add_argument("--state-dir", type=Path, required=True)
    cli_case.add_argument("--run-id", required=True)
    cli_case.add_argument("--confirm-run-id", required=True)
    cli_case.add_argument("--allow-write", action="store_true")
    group_case = commands.add_parser("dual-workspace-group-run", help="Run the reviewed Workspace Group lifecycle/governance story through Singlebox or preprod HTTP")
    group_case.add_argument("--profile", type=Path, required=True)
    group_case.add_argument("--case", choices=("lifecycle", "governance"), required=True)
    group_case.add_argument("--state-dir", type=Path, required=True)
    group_case.add_argument("--run-id", required=True)
    group_case.add_argument("--confirm-run-id", required=True)
    group_case.add_argument("--allow-write", action="store_true")
    session_case = commands.add_parser(
        "dual-session-run",
        help="Run the reviewed dual Session product-CLI stories through Singlebox or preprod")
    session_case.add_argument("--profile", type=Path, required=True)
    session_case.add_argument("--cli-binary", type=Path, required=True)
    session_case.add_argument("--cli-binary-sha256", required=True)
    session_case.add_argument("--case", choices=("create-get", "bot-groups", "group-invite",
                                                "session-invite", "invite-join", "lifecycle",
                                                "complete-invoke", "collection"), required=True)
    session_case.add_argument("--state-dir", type=Path, required=True)
    session_case.add_argument("--run-id", required=True)
    session_case.add_argument("--confirm-run-id", required=True)
    session_case.add_argument("--allow-write", action="store_true")
    session_public_case = commands.add_parser(
        "dual-session-public-run",
        help="Run the reviewed public-v1 Session JWT/WS slice through owned-local Singlebox; Gateway remains admission-blocked")
    session_public_case.add_argument("--profile", type=Path, required=True)
    session_public_case.add_argument("--binary", type=Path, required=True)
    session_public_case.add_argument("--binary-sha256", required=True)
    session_public_case.add_argument("--state-dir", type=Path, required=True)
    session_public_case.add_argument("--run-id", required=True)
    session_public_case.add_argument("--confirm-run-id", required=True)
    session_public_case.add_argument("--allow-write", action="store_true")
    story_case = commands.add_parser(
        "dual-workspace-story-run",
        help="Run the reviewed dual Workspace incident/roundtrip stories through Singlebox or preprod HTTP")
    story_case.add_argument("--profile", type=Path, required=True)
    story_case.add_argument("--case", choices=("incident-core", "roundtrip"), required=True)
    story_case.add_argument("--state-dir", type=Path, required=True)
    story_case.add_argument("--run-id", required=True)
    story_case.add_argument("--confirm-run-id", required=True)
    story_case.add_argument("--allow-write", action="store_true")
    channel_bindings_case = commands.add_parser(
        "dual-channel-bindings-run",
        help="Run the reviewed Channel-binding quiet-state slice through Singlebox or preprod")
    channel_bindings_case.add_argument("--profile", type=Path, required=True)
    channel_bindings_case.add_argument("--state-dir", type=Path, required=True)
    channel_bindings_case.add_argument("--run-id", required=True)
    channel_bindings_case.add_argument("--confirm-run-id", required=True)
    channel_bindings_case.add_argument("--allow-write", action="store_true")
    channel_case = commands.add_parser(
        "dual-channel-run",
        help="Run the reviewed dual Channel product-CLI story through Singlebox or preprod")
    channel_case.add_argument("--profile", type=Path, required=True)
    channel_case.add_argument("--cli-binary", type=Path, required=True)
    channel_case.add_argument("--cli-binary-sha256", required=True)
    channel_case.add_argument("--state-dir", type=Path, required=True)
    channel_case.add_argument("--run-id", required=True)
    channel_case.add_argument("--confirm-run-id", required=True)
    channel_case.add_argument("--allow-write", action="store_true")
    structured_core_case = commands.add_parser(
        "dual-structured-core-run",
        help="Run the reviewed dual structured-collaboration core through Singlebox or preprod")
    structured_core_case.add_argument("--profile", type=Path, required=True)
    structured_core_case.add_argument("--state-dir", type=Path, required=True)
    structured_core_case.add_argument("--run-id", required=True)
    structured_core_case.add_argument("--confirm-run-id", required=True)
    structured_core_case.add_argument("--allow-write", action="store_true")
    structured_templates_case = commands.add_parser(
        "dual-structured-templates-run",
        help="Run the reviewed dual structured-template catalog slice through Singlebox or preprod")
    structured_templates_case.add_argument("--profile", type=Path, required=True)
    structured_templates_case.add_argument("--state-dir", type=Path, required=True)
    structured_templates_case.add_argument("--run-id", required=True)
    structured_templates_case.add_argument("--confirm-run-id", required=True)
    structured_templates_case.add_argument("--allow-write", action="store_true")
    webhook_subscription_case = commands.add_parser(
        "dual-webhook-subscription-run",
        help="Run the reviewed dual Event Webhook subscription lifecycle through Singlebox or preprod")
    webhook_subscription_case.add_argument("--profile", type=Path, required=True)
    webhook_subscription_case.add_argument("--receiver-origin", required=True)
    webhook_subscription_case.add_argument("--state-dir", type=Path, required=True)
    webhook_subscription_case.add_argument("--run-id", required=True)
    webhook_subscription_case.add_argument("--confirm-run-id", required=True)
    webhook_subscription_case.add_argument("--allow-write", action="store_true")
    friend_decision_case = commands.add_parser(
        "dual-friend-connection-decisions-run",
        help="Run the reviewed dual friend-connection decisions through Singlebox or preprod")
    friend_decision_case.add_argument("--profile", type=Path, required=True)
    friend_decision_case.add_argument("--state-dir", type=Path, required=True)
    friend_decision_case.add_argument("--run-id", required=True)
    friend_decision_case.add_argument("--confirm-run-id", required=True)
    friend_decision_case.add_argument("--allow-write", action="store_true")
    friend_public_accept_case = commands.add_parser(
        "dual-friend-public-accept-run",
        help="Run the reviewed dual public friendship auto-acceptance through Singlebox or preprod")
    friend_public_accept_case.add_argument("--profile", type=Path, required=True)
    friend_public_accept_case.add_argument("--state-dir", type=Path, required=True)
    friend_public_accept_case.add_argument("--run-id", required=True)
    friend_public_accept_case.add_argument("--confirm-run-id", required=True)
    friend_public_accept_case.add_argument("--allow-write", action="store_true")
    session_file_case = commands.add_parser(
        "dual-session-file-run",
        help="Run the reviewed dual Session-file core story through Singlebox or preprod product CLI")
    session_file_case.add_argument("--profile", type=Path, required=True)
    session_file_case.add_argument("--cli-binary", type=Path, required=True)
    session_file_case.add_argument("--cli-binary-sha256", required=True)
    session_file_case.add_argument("--state-dir", type=Path, required=True)
    session_file_case.add_argument("--run-id", required=True)
    session_file_case.add_argument("--confirm-run-id", required=True)
    session_file_case.add_argument("--allow-write", action="store_true")
    collaboration_team_case = commands.add_parser(
        "dual-collaboration-team-run",
        help="Run the reviewed dual collaboration-team product-CLI story through Singlebox or preprod")
    collaboration_team_case.add_argument("--profile", type=Path, required=True)
    collaboration_team_case.add_argument("--cli-binary", type=Path, required=True)
    collaboration_team_case.add_argument("--cli-binary-sha256", required=True)
    collaboration_team_case.add_argument("--state-dir", type=Path, required=True)
    collaboration_team_case.add_argument("--run-id", required=True)
    collaboration_team_case.add_argument("--confirm-run-id", required=True)
    collaboration_team_case.add_argument("--allow-write", action="store_true")
    group_context_case = commands.add_parser(
        "dual-group-context-run",
        help="Run the reviewed dual Group Context product-CLI story through Singlebox or preprod")
    group_context_case.add_argument("--profile", type=Path, required=True)
    group_context_case.add_argument("--cli-binary", type=Path, required=True)
    group_context_case.add_argument("--cli-binary-sha256", required=True)
    group_context_case.add_argument("--state-dir", type=Path, required=True)
    group_context_case.add_argument("--run-id", required=True)
    group_context_case.add_argument("--confirm-run-id", required=True)
    group_context_case.add_argument("--allow-write", action="store_true")
    edge_case = commands.add_parser(
        "dual-edge-read-run",
        help="Run the reviewed dual Edge read slices through Singlebox or preprod")
    edge_case.add_argument("--profile", type=Path, required=True)
    edge_case.add_argument("--case", choices=EDGE_CASE_KEYS, required=True)
    edge_case.add_argument("--state-dir", type=Path, required=True)
    edge_case.add_argument("--run-id", required=True)
    edge_case.add_argument("--confirm-run-id", required=True)
    edge_case.add_argument("--allow-write", action="store_true")
    network_read_case = commands.add_parser(
        "dual-network-read-run",
        help="Run the reviewed dual network read slices through Singlebox or preprod")
    network_read_case.add_argument("--profile", type=Path, required=True)
    network_read_case.add_argument("--case", choices=NETWORK_READ_CASE_KEYS, required=True)
    network_read_case.add_argument("--state-dir", type=Path, required=True)
    network_read_case.add_argument("--run-id", required=True)
    network_read_case.add_argument("--confirm-run-id", required=True)
    network_read_case.add_argument("--allow-write", action="store_true")
    network_register_case = commands.add_parser(
        "dual-network-register-run",
        help="Run the reviewed public-v1 register-onboard core through Singlebox or preprod")
    network_register_case.add_argument("--profile", type=Path, required=True)
    network_register_case.add_argument("--state-dir", type=Path, required=True)
    network_register_case.add_argument("--run-id", required=True)
    network_register_case.add_argument("--confirm-run-id", required=True)
    network_register_case.add_argument("--allow-write", action="store_true")
    network_case = commands.add_parser(
        "dual-network-status-run",
        help="Run the reviewed public-v1 actor-status story through Singlebox or preprod")
    network_case.add_argument("--profile", type=Path, required=True)
    network_case.add_argument("--state-dir", type=Path, required=True)
    network_case.add_argument("--run-id", required=True)
    network_case.add_argument("--confirm-run-id", required=True)
    network_case.add_argument("--allow-write", action="store_true")
    friend_case = commands.add_parser(
        "dual-friend-run",
        help="Run the reviewed dual friendship product-CLI story through Singlebox or preprod")
    friend_case.add_argument("--profile", type=Path, required=True)
    friend_case.add_argument("--cli-binary", type=Path, required=True)
    friend_case.add_argument("--cli-binary-sha256", required=True)
    friend_case.add_argument("--state-dir", type=Path, required=True)
    friend_case.add_argument("--run-id", required=True)
    friend_case.add_argument("--confirm-run-id", required=True)
    friend_case.add_argument("--allow-write", action="store_true")
