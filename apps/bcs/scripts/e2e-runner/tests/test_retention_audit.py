"""Task 6.4 contract tests: per-resource retention & recycling audit.

The audit's structural rule is that a resource without a product
delete API is never cleaned by direct database access; every entry
must carry the DB-direct-cleanup prohibition, and disposal claims
must be review-mediated (or never-recycled for append-only audit
history), never automatic.
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcs_e2e.retention_audit import (
    AUDIT_SCHEMA_VERSION,
    DB_DIRECT_CLEANUP_FORBIDDEN,
    DISPOSITION_APPEND_ONLY,
    RESOURCE_AUDIT,
    retention_audit,
    validate_registry,
    entry,
)
from bcs_e2e.profile import ConfigError

FAMILIES = ["DM", "PROVIDER", "ORGANIZATION", "DEFINITION_VERSION",
            "MESSAGE_HISTORY", "AUDIT_HISTORY"]


class RetentionAuditRegistryTests(unittest.TestCase):
    def test_registry_enumerates_the_six_audited_families_in_order(self):
        record = retention_audit()
        self.assertEqual(record["schema_version"], AUDIT_SCHEMA_VERSION)
        self.assertEqual([row["resource_family"]
                          for row in record["resources"]], FAMILIES)

    def test_governing_rule_forbids_direct_database_cleanup(self):
        record = retention_audit()
        self.assertIn("direct database", record["governing_rule"])
        self.assertTrue(record["governing_rule"].strip())

    def test_registry_is_deterministic(self):
        self.assertEqual(retention_audit(), retention_audit())

    def test_registry_round_trips_through_validation(self):
        record = retention_audit()
        self.assertEqual(validate_registry(record), record)

    def test_every_entry_carries_the_db_direct_cleanup_prohibition(self):
        for row in retention_audit()["resources"]:
            self.assertEqual(row["db_direct_cleanup"],
                             DB_DIRECT_CLEANUP_FORBIDDEN)

    def test_provider_is_the_only_family_with_a_product_delete_api(self):
        rows = {row["resource_family"]: row
                for row in retention_audit()["resources"]}
        self.assertEqual(rows["PROVIDER"]["delete_api"],
                         ("member-remove", "bot-retire"))
        for family in FAMILIES:
            if family != "PROVIDER":
                self.assertIsNone(rows[family]["delete_api"],
                                  family + " must not claim a delete API")

    def test_provider_delete_is_a_business_transition_not_a_purge(self):
        provider = entry("PROVIDER")
        self.assertEqual(provider["e2e_disposition"], "RETAINED_UNRESOLVED")
        self.assertIn("DELETE_INTENDED", provider["recycling_mechanism"])
        self.assertIn("not physical erasure", provider["recycling_mechanism"])

    def test_no_delete_api_resources_require_owner_mediated_disposal(self):
        for family in FAMILIES:
            row = entry(family)
            if row["delete_api"] is None and row["e2e_disposition"] != DISPOSITION_APPEND_ONLY:
                self.assertIn("owner", row["recycling_mechanism"].lower(),
                              family)

    def test_audit_history_is_append_only_and_never_recycled(self):
        row = entry("AUDIT_HISTORY")
        self.assertEqual(row["e2e_disposition"], DISPOSITION_APPEND_ONLY)
        self.assertIn("never recycled", row["recycling_mechanism"].lower())
        self.assertIsNone(row["delete_api"])


class EntryValidationTests(unittest.TestCase):
    def test_unknown_family_rejected(self):
        for bad in ("UNKNOWN", "", None, 7):
            with self.assertRaises(ConfigError):
                entry(bad)

    def test_missing_key_rejected(self):
        for key in ("retention_mechanism", "recycling_mechanism",
                    "delete_api", "db_direct_cleanup", "e2e_disposition",
                    "evidence"):
            raw = copy.deepcopy(RESOURCE_AUDIT["DM"])
            del raw[key]
            original = RESOURCE_AUDIT["DM"]
            RESOURCE_AUDIT["DM"] = raw
            try:
                with self.assertRaises(ConfigError):
                    entry("DM")
            finally:
                RESOURCE_AUDIT["DM"] = original

    def test_non_string_mechanism_rejected(self):
        raw = copy.deepcopy(RESOURCE_AUDIT["DM"])
        raw["retention_mechanism"] = 42
        original = RESOURCE_AUDIT["DM"]
        RESOURCE_AUDIT["DM"] = raw
        try:
            with self.assertRaises(ConfigError):
                entry("DM")
        finally:
            RESOURCE_AUDIT["DM"] = original

    def test_wrong_db_direct_cleanup_value_rejected(self):
        raw = copy.deepcopy(RESOURCE_AUDIT["DM"])
        raw["db_direct_cleanup"] = "ALLOWED_IF_CONVENIENT"
        original = RESOURCE_AUDIT["DM"]
        RESOURCE_AUDIT["DM"] = raw
        try:
            with self.assertRaises(ConfigError):
                entry("DM")
        finally:
            RESOURCE_AUDIT["DM"] = original

    def test_unknown_disposition_rejected(self):
        raw = copy.deepcopy(RESOURCE_AUDIT["DM"])
        raw["e2e_disposition"] = "AUTOMATIC_CLEANUP"
        original = RESOURCE_AUDIT["DM"]
        RESOURCE_AUDIT["DM"] = raw
        try:
            with self.assertRaises(ConfigError):
                entry("DM")
        finally:
            RESOURCE_AUDIT["DM"] = original

    def test_no_delete_entry_without_owner_review_rejected(self):
        raw = copy.deepcopy(RESOURCE_AUDIT["DM"])
        raw["recycling_mechanism"] = "automatic TTL sweep removes it"
        original = RESOURCE_AUDIT["DM"]
        RESOURCE_AUDIT["DM"] = raw
        try:
            with self.assertRaises(ConfigError):
                entry("DM")
        finally:
            RESOURCE_AUDIT["DM"] = original

    def test_append_only_entry_must_state_never_recycled(self):
        raw = copy.deepcopy(RESOURCE_AUDIT["AUDIT_HISTORY"])
        raw["e2e_disposition"] = DISPOSITION_APPEND_ONLY
        raw["recycling_mechanism"] = "owner review may prune old rows"
        original = RESOURCE_AUDIT["AUDIT_HISTORY"]
        RESOURCE_AUDIT["AUDIT_HISTORY"] = raw
        try:
            with self.assertRaises(ConfigError):
                entry("AUDIT_HISTORY")
        finally:
            RESOURCE_AUDIT["AUDIT_HISTORY"] = original

    def test_delete_api_must_be_a_non_empty_tuple_of_names(self):
        for bad in ((), "member-remove", (None,), (7,)):
            raw = copy.deepcopy(RESOURCE_AUDIT["PROVIDER"])
            raw["delete_api"] = bad
            original = RESOURCE_AUDIT["PROVIDER"]
            RESOURCE_AUDIT["PROVIDER"] = raw
            try:
                with self.assertRaises(ConfigError):
                    entry("PROVIDER")
            finally:
                RESOURCE_AUDIT["PROVIDER"] = original


class RegistryValidationTests(unittest.TestCase):
    def test_non_mapping_rejected(self):
        with self.assertRaises(ConfigError):
            validate_registry(["not", "a", "registry"])

    def test_wrong_schema_version_rejected(self):
        record = retention_audit()
        record["schema_version"] = 2
        with self.assertRaises(ConfigError):
            validate_registry(record)

    def test_missing_governing_rule_rejected(self):
        record = retention_audit()
        del record["governing_rule"]
        with self.assertRaises(ConfigError):
            validate_registry(record)

    def test_resource_order_and_count_are_enforced(self):
        record = retention_audit()
        record["resources"] = list(reversed(record["resources"]))
        with self.assertRaises(ConfigError):
            validate_registry(record)
        record = retention_audit()
        del record["resources"][-1]
        with self.assertRaises(ConfigError):
            validate_registry(record)

    def test_malformed_resource_row_rejected(self):
        record = retention_audit()
        record["resources"][0]["db_direct_cleanup"] = "PERMITTED"
        with self.assertRaises(ConfigError):
            validate_registry(record)


if __name__ == "__main__":
    unittest.main()
