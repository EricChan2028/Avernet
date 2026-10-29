"""Bounded product-HTTP read used only by collaboration-team transport."""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .collaboration_team_profile import validate_collaboration_team_profile
from .profile import identifier, unique_keys
from .transport import NoRedirect, tls_context


class CollaborationTeamHttpError(RuntimeError):
    pass


def read_formal_group_page(profile: dict, expected_group_id: str) -> dict:
    """Read exactly one formal bot-group page for a reviewed case."""
    profile = validate_collaboration_team_profile(profile)
    expected_group_id = identifier(expected_group_id, "formal Group ID")
    origin = profile["entrypoint"]["origin"]
    path = ("/bots/" + quote(profile["actors"]["driver"]["bot_id"], safe="")
            + "/groups?include_session_groups=false&offset=0&limit=20")
    if profile["environment"]["tier"] == "local":
        credential = os.environ.get(profile["auth"]["driver_token_env"], "")
        credential_kind = "owned_local_bot_token"
        if not credential:
            raise CollaborationTeamHttpError("owned driver Bot credential is absent")
        headers = {"Accept": "application/json", "Authorization": "Bearer " + credential}
        opener = build_opener(ProxyHandler({}), NoRedirect())
    else:
        credential = os.environ.get(profile["auth"]["cookie_env"], "")
        credential_kind = "human_cookie"
        if not credential:
            raise CollaborationTeamHttpError("Human Cookie is absent")
        headers = {"Accept": "application/json", "Cookie": credential}
        opener = build_opener(HTTPSHandler(context=tls_context()),
                              ProxyHandler({}), NoRedirect())
    request = Request(origin + path, method="GET", headers=headers)
    limit = profile["policy"]["max_response_bytes"]
    started = time.monotonic()
    try:
        response = opener.open(request, timeout=profile["policy"]["timeout_seconds"])
        with response:
            status = response.code
            raw = response.read(limit + 1)
        if status != 200 or len(raw) > limit:
            raise CollaborationTeamHttpError(
                "formal group response status or byte budget is invalid")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise CollaborationTeamHttpError("formal group response shape is invalid")
        found = any(isinstance(row, dict)
                    and row.get("id", row.get("group_id")) == expected_group_id
                    for row in data["items"])
        return {
            "data": {"item_count": len(data["items"]),
                     "requested_group_id": expected_group_id,
                     "requested_group_present": found},
            "evidence": {
                "transport": "PRODUCT_HTTP",
                "operation": "collaboration.formal_group_list",
                "caller_role": "driver",
                "route_declared": path,
                "http_method_declared": "GET",
                "http_status": status,
                "credential_kind": credential_kind,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "remote_validation": "NOT_RUN",
                "grants_release_credit": False,
            },
        }
    except HTTPError:
        raise CollaborationTeamHttpError("formal group read was rejected") from None
    except CollaborationTeamHttpError:
        raise
    except Exception:
        raise CollaborationTeamHttpError("formal group transport or decoding failed") from None


def delete_group(profile: dict, group_id: str) -> dict:
    """Delete the exact owned group produced by the Group Context story."""
    profile = validate_collaboration_team_profile(profile)
    group_id = identifier(group_id, "Group Context group ID")
    origin = profile["entrypoint"]["origin"]
    path = f"/groups/{group_id}?bot_id={quote(profile['actors']['driver']['bot_id'], safe='')}"
    if profile["environment"]["tier"] == "local":
        credential = os.environ.get(profile["auth"]["driver_token_env"], "")
        credential_kind = "owned_local_bot_token"
        if not credential:
            raise CollaborationTeamHttpError("owned driver Bot credential is absent")
        headers = {"Accept": "application/json", "Authorization": "Bearer " + credential}
        opener = build_opener(ProxyHandler({}), NoRedirect())
    else:
        credential = os.environ.get(profile["auth"]["cookie_env"], "")
        credential_kind = "human_cookie"
        if not credential:
            raise CollaborationTeamHttpError("Human Cookie is absent")
        headers = {"Accept": "application/json", "Cookie": credential}
        opener = build_opener(HTTPSHandler(context=tls_context()), ProxyHandler({}), NoRedirect())
    request = Request(origin + path, method="DELETE", headers=headers)
    started = time.monotonic()
    try:
        response = opener.open(request, timeout=profile["policy"]["timeout_seconds"])
        with response:
            status = response.code
            raw = response.read(profile["policy"]["max_response_bytes"] + 1)
        if status != 200 or len(raw) > profile["policy"]["max_response_bytes"]:
            raise CollaborationTeamHttpError("group delete response status or byte budget is invalid")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        if not isinstance(data, dict) or data.get("deleted") is not True:
            raise CollaborationTeamHttpError("group delete response is not exact")
        return {
            "data": {"group_id": group_id, "deleted": True},
            "evidence": {
                "transport": "PRODUCT_HTTP",
                "operation": "group_context.group_delete",
                "caller_role": "driver",
                "route_declared": path,
                "http_method_declared": "DELETE",
                "http_status": status,
                "credential_kind": credential_kind,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "remote_validation": "NOT_RUN",
                "grants_release_credit": False,
            },
        }
    except HTTPError:
        raise CollaborationTeamHttpError("group delete was rejected") from None
    except CollaborationTeamHttpError:
        raise
    except Exception:
        raise CollaborationTeamHttpError("group delete transport or decoding failed") from None
