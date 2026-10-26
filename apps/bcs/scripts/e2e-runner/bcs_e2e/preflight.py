"""Discovery only: no fixture repair, business writes, or ready-to-run claims."""
from __future__ import annotations

from .profile import fingerprint
from .identity_binding import pilot_execution_blocker
from .transport import ObservationError, ReadClient


def check(profile: dict, client: ReadClient) -> dict:
    checks = []

    def record(name: str, ok: bool, reason: str) -> None:
        checks.append({"id": name, "status": "PASS" if ok else "BLOCKED", "reason": reason})

    record("fixture.exclusive", profile["policy"]["exclusive_use_confirmed"],
           "dedicated fixture-pool exclusive use must be confirmed; declaration is not a lock")
    record("observer.environment_binding", profile["observer"]["environment_binding_confirmed"],
           "OB physical environment mapping must be confirmed; declaration is not live validation")
    try:
        env = client.read("environment.read").data
        ok = env.get("env") == profile["environment"]["expected_bcs_env"]
        record("target.environment", ok, "manifest env must match configured target")
        if not ok:
            return result(profile, checks, client.evidence)
        identity = client.read("identity.read").data
        ok = identity.get("user_id") == profile["human"]["user_id"]
        record("identity.human", ok, "authenticated Human must match configured owner")
        if not ok:
            return result(profile, checks, client.evidence)
        blocker = pilot_execution_blocker(profile)
        if blocker:
            record("route.business_adapter", False, blocker)
            return result(profile, checks, client.evidence)
        for role, binding in profile["actors"].items():
            bot = client.read("bot.read", binding["bot_id"]).data
            ok = (bot.get("bot_id") == binding["bot_id"] and bot.get("kind") == "bot"
                  and bot.get("created_by") == profile["human"]["user_id"]
                  and bot.get("env") == profile["environment"]["expected_bcs_env"]
                  and bot.get("status") == "online")
            record(f"actor.{role}", ok, "Bot id, kind, owner, env and visible status must match")
            # Bot reachability is intentionally not a model-inference prerequisite.
    except ObservationError as error:
        record("http.observation", False, str(error))
    return result(profile, checks, client.evidence)


def result(profile: dict, checks: list[dict], evidence: list[dict]) -> dict:
    return {
        "schema_version": 1, "profile_digest": fingerprint(profile),
        "status": "BLOCKED" if any(c["status"] != "PASS" for c in checks) else "DISCOVERY_PASSED",
        "ready_to_execute": False,
        "checks": checks, "requests": evidence,
        "not_verified": ["write authorization", "resource quotas", "WebSocket connectivity",
                         "BaaS and signed URL reachability", "live OB schema/access",
                         "lease and resource cleanup", "retained-data disposition", "deployed commit",
                         "implicit Bot bootstrap suppression or termination"],
        "note": "Read-only discovery only; it does not grant write authority or prove retention/cleanup readiness.",
    }
