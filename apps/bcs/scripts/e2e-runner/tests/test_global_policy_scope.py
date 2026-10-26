"""Task 6.3 contract tests: global effective-delivery-policy scope.

Server-wide policy cases are not Case-isolable, so they are excluded
from the owned local full-suite denominator with the gap preserved,
never admitted to a Run, forbidden in operational environments, and
executable only through the operator-attested policy-check command
under a time-boxed exception of at most 24 hours.
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcs_e2e.global_policy_scope import (
    GLOBAL_POLICY_CASE_IDS,
    MAX_EXCUSION_SECONDS,
    GlobalPolicyScopeError,
    global_policy_view,
    is_global_policy_case,
    local_full_suite_denominator,
    refuse_run_admission,
    time_boxed_exception,
    validate_exception,
)
from bcs_e2e.profile import ConfigError


class DenominatorTests(unittest.TestCase):
    def test_splits_global_policy_gap_from_owned_denominator(self):
        result = local_full_suite_denominator(
            ["PRE-01", "EFFECTIVE-DELIVERY-POLICY-READ", "PRE-02"])
        self.assertEqual(result["denominator_cases"], ["PRE-01", "PRE-02"])
        self.assertEqual(result["retained_gap"]["excluded_case_ids"],
                         ["EFFECTIVE-DELIVERY-POLICY-READ"])

    def test_schema_marks_scope_isolation_and_admission(self):
        result = local_full_suite_denominator(GLOBAL_POLICY_CASE_IDS)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["scope"], "GLOBAL_EFFECTIVE_POLICY")
        self.assertEqual(result["isolation"], "NOT_CASE_ISOLABLE")
        self.assertEqual(result["local_full_suite"],
                         "EXCLUDED_FROM_OWNED_LOCAL_FULL_SUITE")
        self.assertEqual(result["run_admission"], "REFUSED_NO_RUN_ADMISSION")
        self.assertEqual(result["operational_environment"],
                         "FORBIDDEN_NOT_OPERATIONAL_ENVIRONMENT")

    def test_gap_is_preserved_not_silently_dropped(self):
        gap = local_full_suite_denominator(
            GLOBAL_POLICY_CASE_IDS)["retained_gap"]
        self.assertEqual(gap["excluded_case_ids"],
                         ["EFFECTIVE-DELIVERY-POLICY-READ"])
        self.assertTrue(gap["reason"].strip())
        self.assertTrue(all(item.strip()
                            for item in gap["compensating_local_evidence"]))
        self.assertEqual(gap["full_suite_coverage_claim"],
                         "GAP_PRESERVED_NOT_SILENTLY_DROPPED")

    def test_non_string_case_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            local_full_suite_denominator(["PRE-01", 7])

    def test_invalid_identifier_rejected(self):
        with self.assertRaises(ConfigError):
            local_full_suite_denominator(["bad case id"])


class AdmissionTests(unittest.TestCase):
    def test_global_policy_case_is_refused_run_admission(self):
        with self.assertRaises(GlobalPolicyScopeError):
            refuse_run_admission("EFFECTIVE-DELIVERY-POLICY-READ")

    def test_local_case_passes_run_admission(self):
        refuse_run_admission("PRE-01")

    def test_non_string_value_is_never_a_global_policy_case(self):
        self.assertFalse(is_global_policy_case(None))
        self.assertFalse(is_global_policy_case(list(GLOBAL_POLICY_CASE_IDS)))
        self.assertTrue(is_global_policy_case("EFFECTIVE-DELIVERY-POLICY-READ"))


class TimeBoxedExceptionTests(unittest.TestCase):
    def evidence(self):
        return ["delivery policy projection contracts validated against "
                "fixture snapshots"]

    def test_record_shape(self):
        before = int(time.time())
        record = time_boxed_exception(
            gap=["EFFECTIVE-DELIVERY-POLICY-READ"],
            compensating_evidence=self.evidence(),
            reason="operator-attested preprod policy-read review pending",
            valid_seconds=3600)
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["scope"], "GLOBAL_EFFECTIVE_POLICY")
        self.assertEqual(record["isolation"], "NOT_CASE_ISOLABLE")
        self.assertEqual(record["retained_gap_case_ids"],
                         ["EFFECTIVE-DELIVERY-POLICY-READ"])
        self.assertEqual(record["operational_environment"],
                         "FORBIDDEN_NOT_OPERATIONAL_ENVIRONMENT")
        self.assertEqual(record["full_suite_status"],
                         "GAP_RETAINED_WITH_TIME_BOXED_EXCEPTION")
        self.assertEqual(record["reason"],
                         "operator-attested preprod policy-read review pending")
        self.assertGreaterEqual(record["issued_at"], before)
        self.assertEqual(record["expires_at"], record["issued_at"] + 3600)
        self.assertTrue(record["exception_id"])

    def test_default_exception_window_is_eight_hours(self):
        record = time_boxed_exception(
            gap=GLOBAL_POLICY_CASE_IDS, compensating_evidence=self.evidence())
        self.assertEqual(record["expires_at"] - record["issued_at"], 8 * 3600)

    def test_empty_gap_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            time_boxed_exception(gap=[], compensating_evidence=self.evidence())

    def test_non_string_gap_entry_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            time_boxed_exception(gap=[7], compensating_evidence=self.evidence())

    def test_invalid_gap_identifier_rejected(self):
        with self.assertRaises(ConfigError):
            time_boxed_exception(gap=["bad gap id"],
                                 compensating_evidence=self.evidence())

    def test_missing_evidence_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            time_boxed_exception(gap=GLOBAL_POLICY_CASE_IDS,
                                 compensating_evidence=[])

    def test_blank_evidence_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            time_boxed_exception(gap=GLOBAL_POLICY_CASE_IDS,
                                 compensating_evidence=["  "])

    def test_blank_reason_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            time_boxed_exception(gap=GLOBAL_POLICY_CASE_IDS,
                                 compensating_evidence=self.evidence(),
                                 reason="   ")

    def test_exception_window_may_not_exceed_24h(self):
        with self.assertRaises(GlobalPolicyScopeError):
            time_boxed_exception(gap=GLOBAL_POLICY_CASE_IDS,
                                 compensating_evidence=self.evidence(),
                                 valid_seconds=MAX_EXCUSION_SECONDS + 1)

    def test_non_positive_or_non_integer_window_rejected(self):
        for bad in (0, -1, 1.5, "3600"):
            with self.assertRaises(GlobalPolicyScopeError):
                time_boxed_exception(gap=GLOBAL_POLICY_CASE_IDS,
                                     compensating_evidence=self.evidence(),
                                     valid_seconds=bad)


class ValidateExceptionTests(unittest.TestCase):
    def record(self, **overrides):
        base = {
            "exception_id": "exc-1",
            "scope": "GLOBAL_EFFECTIVE_POLICY",
            "reason": "standing non-isolable scope",
            "retained_gap_case_ids": ["EFFECTIVE-DELIVERY-POLICY-READ"],
            "compensating_local_evidence": ["projection contracts validated"],
            "issued_at": 1_800_000_000,
            "expires_at": 1_800_000_000 + 3600,
        }
        base.update(overrides)
        return base

    def test_valid_record_validates(self):
        record = validate_exception(self.record(), now=1_800_000_000 + 60)
        self.assertEqual(record["exception_id"], "exc-1")

    def test_non_mapping_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            validate_exception(["not", "a", "record"], now=1_800_000_000)

    def test_missing_required_field_rejected(self):
        incomplete = self.record()
        del incomplete["compensating_local_evidence"]
        with self.assertRaises(GlobalPolicyScopeError):
            validate_exception(incomplete, now=1_800_000_000)

    def test_wrong_scope_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            validate_exception(self.record(scope="LOCAL_SUITE"),
                               now=1_800_000_000)

    def test_empty_gap_or_evidence_rejected(self):
        for field in ("retained_gap_case_ids", "compensating_local_evidence"):
            with self.assertRaises(GlobalPolicyScopeError):
                validate_exception(self.record(**{field: []}),
                                   now=1_800_000_000)

    def test_invalid_time_box_span_rejected(self):
        for issued, expires in ((1_800_000_000, 1_800_000_000),
                                (1_800_000_000, 1_800_000_000 - 1),
                                (1_800_000_000,
                                 1_800_000_000 + MAX_EXCUSION_SECONDS + 1)):
            with self.assertRaises(GlobalPolicyScopeError):
                validate_exception(self.record(issued_at=issued,
                                               expires_at=expires),
                                   now=1_800_000_000)

    def test_non_integer_timestamps_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            validate_exception(self.record(expires_at="later"),
                               now=1_800_000_000)

    def test_expired_exception_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            validate_exception(self.record(), now=1_800_000_000 + 3600)

    def test_not_yet_issued_exception_rejected(self):
        with self.assertRaises(GlobalPolicyScopeError):
            validate_exception(self.record(), now=1_800_000_000 - 1)


class GlobalPolicyViewTests(unittest.TestCase):
    def test_view_carries_denominator_and_exception(self):
        view = global_policy_view()
        self.assertEqual(view["scope"], "GLOBAL_EFFECTIVE_POLICY")
        self.assertEqual(view["denominator_cases"], [])
        self.assertEqual(view["retained_gap"]["excluded_case_ids"],
                         list(GLOBAL_POLICY_CASE_IDS))
        validated = validate_exception(view["exception"])
        self.assertEqual(validated["retained_gap_case_ids"],
                         list(GLOBAL_POLICY_CASE_IDS))
        self.assertTrue(validated["exception_id"])

    def test_view_is_deterministic_within_the_day(self):
        first = global_policy_view()
        second = global_policy_view()
        self.assertEqual(first, second)
        self.assertEqual(first["exception"]["expires_at"]
                         - first["exception"]["issued_at"],
                         MAX_EXCUSION_SECONDS)
        self.assertEqual(first["exception"]["issued_at"] % MAX_EXCUSION_SECONDS, 0)


if __name__ == "__main__":
    unittest.main()
