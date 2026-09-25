#!/usr/bin/env python3
"""Verifies every claim in tests/MUTATIONS.md by actually applying each
mutation to a scratch copy of src/detection.py and confirming the named
test goes red.

MUTATIONS.md asserted, in prose, that 25 specific one-line errors are each
caught by a specific test. That claim is only as good as someone manually
re-running it, which nobody does after the fifth time. This script is that
manual process, automated: for each entry it patches src/detection.py in
place, runs the one named test, checks it failed, and reverts -- so the
table in MUTATIONS.md is a checked fact, not an assertion.

Usage: python tests/run_mutations.py
Exit code is 0 if every mutation was killed as claimed, 1 otherwise.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DETECTION_PATH = REPO_ROOT / "src" / "detection.py"

# Each entry: (number, description, old text, new text, test node id).
# old/new must each appear exactly once in src/detection.py, or the
# mutation is refused rather than silently applied to the wrong spot.
MUTATIONS = [
    (1, "cpi = actual / earned_value (inverted)",
     "cpi = earned_value / actual if actual > 0 else 1.0",
     "cpi = actual / earned_value if actual > 0 else 1.0",
     "test_detection.py::test_cpi_is_earned_value_over_actual_cost_not_the_other_way_up"),
    (2, "spi = planned_pct / earned_pct (inverted)",
     "spi = earned_pct / planned_pct if planned_pct > 0 else 1.0",
     "spi = planned_pct / earned_pct if planned_pct > 0 else 1.0",
     "test_detection.py::test_spi_is_progress_against_the_baseline_curve"),
    (3, "np.interp fed forecast_date instead of baseline_date",
     '((pm["baseline_date"] - epoch).dt.days).to_numpy(),',
     '((pm["forecast_date"] - epoch).dt.days).to_numpy(),',
     "test_detection.py::test_spi_is_progress_against_the_baseline_curve"),
    (4, 'PCT_BANDS LOW floor 0.10 -> 0.11',
     '"LOW": (0.10, 0.20),\n    "MEDIUM": (0.20, 0.35),',
     '"LOW": (0.11, 0.20),\n    "MEDIUM": (0.20, 0.35),',
     "test_detection.py::test_pct_bands_sit_exactly_where_they_are_documented"),
    (5, '_band_for floor made exclusive (lo < v < hi)',
     "in_band = value >= lo if hi == float(\"inf\") else lo <= value < hi",
     "in_band = value >= lo if hi == float(\"inf\") else lo < value < hi",
     "test_detection.py::test_severity_bands_are_half_open_and_no_value_lands_in_two"),
    (6, "expected_spend_to_date uses original_budget (drops approved COs)",
     'm["expected_spend_to_date"] = m["current_budget"] * m["progress"]',
     'm["expected_spend_to_date"] = m["original_budget"] * m["progress"]',
     "test_detection.py::test_variance_is_measured_against_the_budget_plus_approved_changes"),
    (7, "buyout_var measured against current_budget",
     'buyout_var = row["committed_amount"] - row["original_budget"]',
     'buyout_var = row["committed_amount"] - row["current_budget"]',
     "test_detection.py::test_buyout_is_measured_against_the_original_budget"),
    (8, "earned uses contract_value instead of the revised contract",
     'earned = revised * p["pct_complete"] / 100.0',
     'earned = p["contract_value"] * p["pct_complete"] / 100.0',
     "test_detection.py::test_earned_revenue_uses_the_revised_contract_not_the_original"),
    (9, "variance_amount sign flipped",
     'm["variance_amount"] = m["actual_spend"] - m["expected_spend_to_date"]',
     'm["variance_amount"] = m["expected_spend_to_date"] - m["actual_spend"]',
     "test_detection.py::test_harborview_millwork_burn_rate_flagged_high"),
    (10, "under = billed - earned (billing over/under flipped)",
     "under = earned - billed",
     "under = billed - earned",
     "test_detection.py::test_cascade_ridge_under_billed"),
    (11, "dollar_floor_for uses min instead of max",
     "return max(t.dollar_floor, share * float(contract_value))",
     "return min(t.dollar_floor, share * float(contract_value))",
     "test_detection.py::test_dollar_floor_scales_with_contract_and_type"),
    (12, "combined_severity dollar gate removed",
     'if dollar_exposure < floor:\n        return "NONE"',
     'if False:\n        return "NONE"',
     "test_detection.py::test_dollars_promote_severity_but_never_demote_it"),
    (13, "DOLLAR_PROMOTION_TIERS promotion removed",
     'for level in ("HIGH", "MEDIUM"):\n        if multiple >= t.dollar_promotion_tiers[level] and SEVERITY_ORDER[sev] < SEVERITY_ORDER[level]:\n            return level',
     'for level in ():\n        if multiple >= t.dollar_promotion_tiers[level] and SEVERITY_ORDER[sev] < SEVERITY_ORDER[level]:\n            return level',
     "test_detection.py::test_dollars_promote_severity_but_never_demote_it"),
    (14, "FORECAST_EXTRAPOLATION_CAP 2.0 -> 1.0",
     "FORECAST_EXTRAPOLATION_CAP = 2.0",
     "FORECAST_EXTRAPOLATION_CAP = 1.0",
     "test_detection.py::test_forecast_extrapolation_is_capped"),
    (15, "FORECAST_MIN_PROGRESS 0.25 -> 0.75",
     "FORECAST_MIN_PROGRESS = 0.25",
     "FORECAST_MIN_PROGRESS = 0.75",
     "test_detection.py::test_forecast_does_not_extrapolate_off_a_line_barely_started"),
    (16, "progress.clip(0.0, 1.0) -> clip(0.0, 99.0)",
     '.clip(0.0, 1.0)',
     '.clip(0.0, 99.0)',
     "test_detection.py::test_reported_progress_above_one_hundred_percent_is_clipped"),
    (17, "UNDER_PACE_PCT -0.30 -> -0.20",
     "UNDER_PACE_PCT = -0.30",
     "UNDER_PACE_PCT = -0.20",
     "test_detection.py::test_under_pace_needs_a_real_gap_and_real_progress"),
    (18, "LARGE_DRAW_SHARE 0.25 -> 0.05",
     "LARGE_DRAW_SHARE = 0.25",
     "LARGE_DRAW_SHARE = 0.05",
     "test_detection.py::test_a_large_draw_is_named_but_never_moves_severity"),
    (19, "MARGIN_EROSION_BANDS LOW floor 0.02 -> 0.03",
     '"LOW": (0.02, 0.04),\n    "MEDIUM": (0.04, 0.08),',
     '"LOW": (0.03, 0.04),\n    "MEDIUM": (0.04, 0.08),',
     "test_detection.py::test_margin_erosion_bands_sit_where_they_are_documented"),
    (20, "Duplicate detection drops code from its key",
     'for (pid, vendor, code, amount), grp in tx.groupby(["project_id", "vendor", "code", "amount"]):',
     'for (pid, vendor, amount), grp in tx.groupby(["project_id", "vendor", "amount"]):',
     "test_detection.py::test_a_suspected_duplicate_needs_the_same_code_as_well"),
    (21, "DUPLICATE_WINDOW_DAYS 45 -> 30",
     "DUPLICATE_WINDOW_DAYS = 45",
     "DUPLICATE_WINDOW_DAYS = 30",
     "test_detection.py::test_thresholds_the_readme_quotes_are_pinned_to_their_values"),
    (22, "COMPLIANCE_LOOKAHEAD_DAYS 30 -> 60",
     "COMPLIANCE_LOOKAHEAD_DAYS = 30",
     "COMPLIANCE_LOOKAHEAD_DAYS = 60",
     "test_detection.py::test_thresholds_the_readme_quotes_are_pinned_to_their_values"),
    (23, "Terminal slip read off the first critical-path milestone",
     "term = cp.iloc[-1]",
     "term = cp.iloc[0]",
     "test_detection.py::test_slip_is_weather_plus_a_named_cause_less_float"),
    (24, "Allowance overage covered by CO cost instead of sell price",
     'if approved["amount"].sum() >= overage * 0.95:',
     'if approved["cost_amount"].sum() >= overage * 0.95:',
     "test_detection.py::test_allowance_overage_covered_by_linked_approved_co_is_not_flagged"),
    (25, "Suspected duplicates no longer deducted from spend",
     'm["actual_spend"] = m["ledger_spend"] - m["suspected_duplicates"]',
     'm["actual_spend"] = m["ledger_spend"]',
     "test_detection.py::test_duplicate_is_excluded_from_burn_rate"),
]


def run_test(node_id: str) -> bool:
    """True if the named test passed."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", node_id],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return result.returncode == 0


def main() -> int:
    original = DETECTION_PATH.read_text()

    # Baseline: every named test has to pass on the unmutated source, or a
    # "kill" below could just mean the test was already broken.
    print("Baseline: confirming the unmutated suite is green...")
    if not run_test("tests/"):
        print("FATAL: the unmutated test suite does not pass. Fix that first.")
        return 1

    failures = []
    for number, description, old, new, node_id in MUTATIONS:
        count = original.count(old)
        if count != 1:
            failures.append((number, description,
                              f"expected exactly one match for the old text, found {count}"))
            continue

        mutated = original.replace(old, new, 1)
        DETECTION_PATH.write_text(mutated)
        try:
            killed = not run_test(node_id)
        finally:
            DETECTION_PATH.write_text(original)

        status = "killed" if killed else "SURVIVED"
        print(f"  #{number:>2}  {status:<9} {description}")
        if not killed:
            failures.append((number, description, f"{node_id} still passed under this mutation"))

    # Belt and suspenders: whatever happened above, the file on disk must
    # be exactly what it started as before this script hands back control.
    if DETECTION_PATH.read_text() != original:
        DETECTION_PATH.write_text(original)
        failures.append((0, "cleanup", "detection.py was not restored correctly; forced a rewrite"))

    print()
    if failures:
        print(f"{len(failures)} of {len(MUTATIONS)} mutations did not behave as MUTATIONS.md claims:")
        for number, description, reason in failures:
            print(f"  #{number}: {description} -- {reason}")
        return 1

    print(f"All {len(MUTATIONS)} mutations in tests/MUTATIONS.md are killed as claimed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
