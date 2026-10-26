"""Rendering contract for the owned-local workspace parent Story."""
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.reporting import render_junit, render_markdown


REQUIREMENTS = [f"WORKSPACE.{name}" for name in (
    "create", "detail", "patch", "list", "members", "label", "visibility",
    "cli-create-get", "cli-add-member", "cli-fuse", "cli-status",
    "incident-group", "participant-mode", "workspace", "routing-service",
    "messages", "incident-delete", "proposal",
)]


def summary(case_status="PASS"):
    nonpassing = case_status != "PASS"
    return {
        "status": "LOCAL_SUITE_PASS" if not nonpassing else "BLOCKED",
        "run_id": "workspace-run",
        "target": {"tier": "local", "environment_id": "owned-workspace"},
        "scope": {
            "suite": "owned-local-workspace-v1",
            "full_release_status": "NOT_QUALIFIED",
            "manifest_digest": "manifest",
            "legacy_inventory_count": 34,
        },
        "runner": {"executor_digest": "executor"},
        "observer_binding": {},
        "cases": [{
            "id": "LOCAL-WORKSPACE-FLOW-01",
            "version": 1,
            "status": case_status,
            "functional": "PASS" if not nonpassing else case_status,
            "evidence_status": "PASS" if not nonpassing else "INCOMPLETE",
            "cleanup": "PASS" if not nonpassing else "BLOCKED",
            "resource_disposition": "RETAINED_LOCAL_DATABASE",
            "retained_visibility": {
                "scope": "OWNED_LOCAL_FIXTURES",
                "roles": "CEO,PM,ENG,QA",
                "setup_visibility": "public",
                "restoration": "NOT_PERFORMED",
                "current_state_verification": "NOT_PERFORMED",
            },
        }],
        "requirements": [{"id": requirement, "status": "PASS"} for requirement in REQUIREMENTS],
        "reports": {
            "case_count": 1,
            "declared_case_count": 1,
            "expected_assertion_count": 18,
            "observed_assertion_count": 18,
        },
        "cleanup": {
            "verification_scope": "OWNED_INCIDENT_AND_PROPOSAL_ONLY",
            "physical_data_erasure": "NOT_VERIFIED",
            "associated_history_purge": "NOT_PERFORMED",
        },
        "routes": [],
        "timing": {"interpretation": "LOCAL_ONLY"},
        "mock_boundary": {"not_proven": ["remote deployment behavior"]},
        "execution_safety": {"status": "PASS"},
    }


class WorkspaceReportRenderingTests(unittest.TestCase):
    def test_junit_and_markdown_render_workspace_contract(self):
        report = summary()
        xml = ET.fromstring(render_junit(report))
        self.assertEqual(xml.get("name"), "bcs-e2e-workspace")
        self.assertEqual(xml.get("tests"), "1")
        self.assertEqual(xml.get("failures"), "0")
        self.assertEqual(xml.get("errors"), "0")
        self.assertEqual(xml.get("skipped"), "0")

        suite_properties = {
            item.get("name"): item.get("value")
            for item in xml.findall("./properties/property")
        }
        self.assertEqual(suite_properties["expected_assertion_count"], "18")
        self.assertEqual(suite_properties["observed_assertion_count"], "18")
        self.assertEqual(len([name for name in suite_properties if name.startswith("requirement.")]), 18)
        for requirement in REQUIREMENTS:
            self.assertEqual(suite_properties[f"requirement.{requirement}"], "PASS")

        case_properties = {
            item.get("name"): item.get("value")
            for item in xml.findall("./testcase/properties/property")
        }
        self.assertEqual(case_properties["cleanup_verification_scope"],
                         "OWNED_INCIDENT_AND_PROPOSAL_ONLY")
        self.assertEqual(case_properties["associated_history_purge"], "NOT_PERFORMED")
        self.assertEqual(case_properties["retained_visibility.scope"], "OWNED_LOCAL_FIXTURES")
        self.assertEqual(case_properties["retained_visibility.roles"], "CEO,PM,ENG,QA")
        self.assertEqual(case_properties["retained_visibility.setup_visibility"], "public")
        self.assertEqual(case_properties["retained_visibility.restoration"], "NOT_PERFORMED")
        self.assertEqual(case_properties["retained_visibility.current_state_verification"],
                         "NOT_PERFORMED")

        markdown = render_markdown(report)
        self.assertIn("- Required assertions: 18", markdown)
        self.assertIn("- Observed assertions: 18", markdown)
        for requirement in REQUIREMENTS:
            self.assertIn(f"| {requirement} | PASS |", markdown)
        self.assertEqual(sum(line.startswith("| WORKSPACE.") for line in markdown.splitlines()), 18)
        self.assertIn("scope=OWNED_LOCAL_FIXTURES", markdown)
        self.assertIn("roles=CEO,PM,ENG,QA", markdown)
        self.assertIn("setup_visibility=public", markdown)
        self.assertIn("restoration=NOT_PERFORMED", markdown)
        self.assertIn("current_state_verification=NOT_PERFORMED", markdown)

    def test_nonpassing_workspace_cases_are_not_rendered_green(self):
        for status, element, count in (
            ("FAIL", "failure", ("1", "0")),
            ("BLOCKED", "skipped", ("0", "1")),
            ("CANCELLED", "skipped", ("0", "1")),
        ):
            with self.subTest(status=status):
                report = summary(status)
                xml = ET.fromstring(render_junit(report))
                self.assertEqual((xml.get("failures"), xml.get("skipped")), count)
                self.assertIsNotNone(xml.find(f"testcase/{element}"))
                markdown = render_markdown(report)
                self.assertIn(f"| LOCAL-WORKSPACE-FLOW-01 | 1 | {status} |", markdown)
                self.assertNotIn("- Status: **LOCAL_SUITE_PASS**", markdown)


if __name__ == "__main__":
    unittest.main()
