"""Local conformance check for semantic observer replacement.

The 7.3 task only requires a suite that demonstrates provider replacement
does not require changing story business steps.  This suite intentionally
keeps the story contract fixed and swaps the semantic provider instance, so a
dedicated MCP adapter can later be injected without rewriting PRE-01 itself.
"""
from __future__ import annotations

from pathlib import Path
import json
import tempfile
import sys
import re
import uuid
import unittest
import contextlib
import io


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.cli import main
from bcs_e2e.pilot import run_case
from bcs_e2e.profile import load


class DedicatedMcpSemanticProvider:
    """Minimal duck-typed provider used to prove interface reuse.

    It is intentionally *not* the production provider class; this suite only
    proves that a provider replacement which preserves the same interface and
    data does not require changing the Story itself.
    """

    def __init__(self, provider):
        self.provider = provider

    def admit(self, assertion_ids):
        return self.provider.admit(assertion_ids)

    def observe(self, assertion_id, resource_id):
        return self.provider.observe(assertion_id, resource_id)


class ProviderSwapAdapter(LocalMockAdapter):
    """Only the semantic observer implementation differs from LocalMockAdapter."""

    def __init__(self, profile, provider_factory):
        super().__init__(profile)
        self.provider_factory = provider_factory

    def semantic_observer(self):
        provider = super().semantic_observer()
        return self.provider_factory(provider)


class MCPReuseConformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = 0
        self.pool_token = uuid.uuid4().hex

    def _run_pre01(self, provider_factory):
        self.count += 1
        profile = load(ROOT / "profiles/local.mock.json")
        profile["policy"]["fixture_pool"] = f"mcp-reuse-{self.pool_token}-{self.count}"
        profile_path = self.root / f"profile-{self.count}.json"
        profile_path.write_text(json.dumps(profile))
        state = self.root / f"state-{self.count}"
        run_id = f"mcp-reuse-{self.pool_token}-{self.count}"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main([
                    "prepare",
                    "--profile", str(profile_path),
                    "--state-dir", str(state),
                    "--run-id", run_id,
                ]),
                0,
            )
        adapter = ProviderSwapAdapter(profile, provider_factory)
        return run_case(profile, state, run_id, "PRE-01", adapter_factory=lambda *_a, **_k: adapter)

    @staticmethod
    def _normalize_dynamic_resource_ids(value):
        return re.sub(r"local-mock-(?:group|session)-[0-9a-f]{32}", "<local-mock-resource-id>", value)

    @classmethod
    def _business_invariants(cls, report):
        raw = {
            "functional": report["functional"],
            "cleanup": report["cleanup"],
            "evidence_status": report["evidence_status"],
            "assertions": [
                {"id": item["id"], "status": item["status"]}
                for item in report["assertions"]
            ],
            "semantic_evidence": [
                {
                    "status": item["status"],
                    "source": item["source"],
                    "plan": item["plan"],
                    "rows": item["rows"],
                }
                for item in report["semantic_evidence"]
            ],
            "requests": [
                item["operation"] for item in report["evidence"]
            ],
        }
        encoded = json.dumps(raw, sort_keys=True)
        return json.loads(cls._normalize_dynamic_resource_ids(encoded))

    def test_swapping_semantic_provider_does_not_change_story_steps(self):
        base = self._run_pre01(lambda provider: provider)
        dedicated = self._run_pre01(DedicatedMcpSemanticProvider)
        self.assertEqual(base["status"], "PASS")
        self.assertEqual(dedicated["status"], "PASS")
        self.assertEqual(
            self._business_invariants(base),
            self._business_invariants(dedicated),
        )
        self.assertEqual(
            [item["id"] for item in base["assertions"]],
            [
                "PRE-01.preflight",
                "PRE-01.owner_matches",
                "PRE-01.driver_and_members_match",
                "PRE-01.group_read",
                "PRE-01.group_updated",
                "PRE-01.update_persisted",
                "PRE-01.ob_group_matches",
                "PRE-01.owned_initial_session_deleted",
                "PRE-01.owned_group_deleted",
            ],
        )
        self.assertEqual(
            [item["operation"] for item in base["evidence"]],
            [
                "group.create",
                "group.read",
                "group.update",
                "group.read",
                "session.read",
                "session.delete",
                "session.verify_deleted",
                "group.delete",
                "group.verify_deleted",
            ],
        )


if __name__ == "__main__":
    unittest.main()
