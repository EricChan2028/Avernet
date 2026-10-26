"""PRE-03 business invariants and cleanup must survive malformed responses."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import run_pre03
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import read
from bcs_e2e.write_transport import WriteTransportError


class FileStoryAssertionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        registry = patch("bcs_e2e.pool.pool_root", return_value=self.root / "pools")
        registry.start()
        self.addCleanup(registry.stop)
        self.count = 0

    def prepare(self):
        self.count += 1
        self.profile = load(ROOT / "profiles/local.mock.json")
        self.profile["policy"]["fixture_pool"] = f"files-{self.count}"
        self.run_id = f"file-story-{self.count}"
        self.state = self.root / self.run_id
        path = self.root / f"{self.run_id}.json"
        path.write_text(json.dumps(self.profile))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["prepare", "--profile", str(path), "--run-id", self.run_id,
                                   "--state-dir", str(self.state)]), 0)
        return LocalMockAdapter(self.profile)

    def execute(self, adapter):
        with patch("bcs_e2e.pilot.adapter_for", return_value=adapter):
            return run_pre03(self.profile, self.state, self.run_id)

    def remaining(self):
        with Ledger(self.state / "run.sqlite", self.run_id, fingerprint(self.profile)) as ledger:
            return ledger.cleanup_plan()

    def test_complete_and_metadata_reject_wrong_identity_fields_and_types(self):
        mutations = [("file_id", "another-file"), ("session_id", "another-session"),
                     ("file_name", "wrong-name.txt"), ("mime_type", "wrong/type"),
                     ("status", "pending"), ("size", True), ("sha256", "wrong-hash"),
                     ("owner", {"actor_kind": "human", "actor_id": "another-owner"}),
                     ("created_at", -1), ("updated_at", True), ("storage_backend", "wrong-backend")]
        for operation in ("file_complete", "file_metadata"):
            for field, invalid in mutations:
                with self.subTest(operation=operation, field=field):
                    adapter = self.prepare()
                    original = getattr(adapter, operation)
                    def wrong(*args):
                        data, record = original(*args)
                        return {**data, field: invalid}, record
                    with patch.object(adapter, operation, side_effect=wrong), \
                         patch.object(adapter, "shared_file_verify_deleted") as shared_verify:
                        report = self.execute(adapter)
                    self.assertEqual(report["status"], "FAIL")
                    self.assertEqual(report["cleanup"], "PASS")
                    shared_verify.assert_not_called()
                    self.assertEqual(self.remaining()["resources"], [])

    def test_file_owner_is_human_actor_id_not_authentication_user_id(self):
        adapter = self.prepare()
        original = adapter.file_complete
        def raw_user_owner(session_id, file_id):
            data, record = original(session_id, file_id)
            return {**data, "owner": {"actor_kind": "human", "actor_id": self.profile["human"]["user_id"]}}, record
        with patch.object(adapter, "file_complete", side_effect=raw_user_owner):
            report = self.execute(adapter)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["cleanup"], "PASS")

    def test_bad_page_counts_items_and_duplicates_are_assertion_failures_not_crashes(self):
        for mutation in ("text-total", "bool-total", "items-object", "duplicate"):
            with self.subTest(mutation=mutation):
                adapter = self.prepare()
                original = adapter.file_list
                def wrong(session_id):
                    data, record = original(session_id)
                    if mutation == "text-total":
                        data["total"] = "not-a-number"
                    elif mutation == "bool-total":
                        data["total"] = True
                    elif mutation == "items-object":
                        data["items"] = {}
                    else:
                        data["items"] *= 2
                        data["total"] = 2
                    return data, record
                with patch.object(adapter, "file_list", side_effect=wrong):
                    report = self.execute(adapter)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["cleanup"], "PASS")
                self.assertEqual(read(self.state / "run.json")["status"], "FAILED")

    def test_persisted_file_relationship_and_state_must_match_expected_business_values(self):
        for field, invalid in (("session_id", "another-session"), ("size", 999),
                               ("status", "pending"), ("sha256", "wrong-hash"),
                               ("storage_backend", "unexpected-backend")):
            with self.subTest(field=field):
                adapter = self.prepare()
                provider = adapter.semantic_observer()
                original = provider.observe
                def wrong(assertion_id, resource_id):
                    result = copy.deepcopy(original(assertion_id, resource_id))
                    result["rows"][0][field] = invalid
                    return result
                with patch.object(adapter, "semantic_observer", return_value=provider), \
                     patch.object(provider, "observe", side_effect=wrong):
                    report = self.execute(adapter)
                self.assertEqual(report["status"], "BLOCKED")
                self.assertEqual(report["functional"], "PASS")
                self.assertEqual(report["cleanup"], "PASS")
                self.assertEqual(report["evidence_status"], "BLOCKED")
                ob_assertion = next(a for a in report["assertions"] if a["id"] == "PRE-03.ob_file_matches")
                self.assertEqual(ob_assertion["status"], "BLOCKED")

    def test_nullable_hash_still_requires_exact_download_bytes(self):
        adapter = self.prepare()
        original = adapter.file_complete
        def complete(session_id, file_id):
            data, record = original(session_id, file_id)
            adapter.files[file_id]["sha256"] = None
            return {**data, "sha256": None}, record
        with patch.object(adapter, "file_complete", side_effect=complete):
            report = self.execute(adapter)
        self.assertEqual(report["status"], "PASS")
        manifest = json.loads((ROOT / "manifest.json").read_text())
        declared = next(c["assertions"] for c in manifest["cases"] if c["id"] == "PRE-03")
        ids = [a["id"] for a in report["assertions"]]
        self.assertTrue(set(declared) <= set(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_share_probe_when_upload_fails_before_share_is_issued(self):
        adapter = self.prepare()
        with patch.object(adapter, "file_upload", side_effect=WriteTransportError("upload failed")), \
             patch.object(adapter, "shared_file_verify_deleted") as shared_verify:
            report = self.execute(adapter)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["cleanup"], "PASS")
        shared_verify.assert_not_called()
        self.assertEqual(self.remaining()["resources"], [])

    def test_unconfirmed_delete_stops_verification_and_parent_deletions(self):
        for data in ({"deleted": False}, {}, {"deleted": 1}, None):
            with self.subTest(data=data):
                adapter = self.prepare()
                with patch.object(adapter, "file_delete", return_value=(data, {})), \
                     patch.object(adapter, "file_verify_deleted") as verify, \
                     patch.object(adapter, "session_delete") as parent:
                    report = self.execute(adapter)
                self.assertEqual(report["cleanup"], "BLOCKED")
                self.assertEqual(report["status"], "FAIL")
                verify.assert_not_called()
                parent.assert_not_called()
                self.assertEqual(len(self.remaining()["resources"]), 4)

    def test_unjournalable_returned_id_never_authorizes_delete(self):
        adapter = self.prepare()
        with patch.object(adapter, "file_prepare", return_value=({"file_id": "bad/id"}, {})), \
             patch.object(adapter, "file_delete") as delete, \
             patch.object(adapter, "session_delete") as parent:
            report = self.execute(adapter)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["cleanup"], "BLOCKED")
        delete.assert_not_called()
        parent.assert_not_called()
        self.assertEqual(len(self.remaining()["unresolved_handles"]), 1)


if __name__ == "__main__":
    unittest.main()
