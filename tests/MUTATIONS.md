# Mutation checklist

A test suite that only asserts severity labels stays green through an
inverted CPI. This suite was written against the list below: each entry is
a deliberate one-line error introduced into `src/detection.py`, and the
test named beside it is the one that fails when it is.

The first version of this suite, which asserted that the right things were
flagged at the right severity on the seeded dataset, survived **fourteen of
twenty-five** of these. The value tests in the second half of
`test_detection.py` were written to close that gap. All twenty-five below
are killed today.

To re-run the check, apply one mutation and run `pytest tests/ -q`. The
suite has to go red. `python tests/run_mutations.py` automates exactly
that for all twenty-five: it patches `src/detection.py` in place, runs the
one named test, confirms it failed, and restores the original file --
so this table is a checked fact rather than something to take on faith.

| # | Mutation in `src/detection.py` | Killed by |
|---|---|---|
| 1 | `cpi = actual / earned_value` (inverted) | `test_cpi_is_earned_value_over_actual_cost_not_the_other_way_up` |
| 2 | `spi = planned_pct / earned_pct` (inverted) | `test_spi_is_progress_against_the_baseline_curve` |
| 3 | `np.interp` fed `forecast_date` instead of `baseline_date` | `test_spi_is_progress_against_the_baseline_curve` |
| 4 | `PCT_BANDS` LOW floor `0.10 -> 0.11` | `test_pct_bands_sit_exactly_where_they_are_documented` |
| 5 | `_band_for` floor made exclusive (`lo < v < hi`) | `test_severity_bands_are_half_open_and_no_value_lands_in_two` |
| 6 | `expected_spend_to_date` uses `original_budget` (drops approved COs) | `test_variance_is_measured_against_the_budget_plus_approved_changes` |
| 7 | `buyout_var` measured against `current_budget` | `test_buyout_is_measured_against_the_original_budget` |
| 8 | `earned` uses `contract_value` instead of the revised contract | `test_earned_revenue_uses_the_revised_contract_not_the_original` |
| 9 | `variance_amount` sign flipped | `test_harborview_millwork_burn_rate_flagged_high` and others |
| 10 | `under = billed - earned` (billing over/under flipped) | `test_cascade_ridge_under_billed` |
| 11 | `dollar_floor_for` uses `min` instead of `max` | `test_dollar_floor_scales_with_contract_and_type` |
| 12 | `combined_severity` dollar gate removed | `test_dollars_promote_severity_but_never_demote_it` |
| 13 | `DOLLAR_PROMOTION_TIERS` promotion removed | `test_dollars_promote_severity_but_never_demote_it` |
| 14 | `FORECAST_EXTRAPOLATION_CAP` `2.0 -> 1.0` | `test_forecast_extrapolation_is_capped` |
| 15 | `FORECAST_MIN_PROGRESS` `0.25 -> 0.75` | `test_forecast_does_not_extrapolate_off_a_line_barely_started` |
| 16 | `progress.clip(0.0, 1.0) -> clip(0.0, 99.0)` | `test_reported_progress_above_one_hundred_percent_is_clipped` |
| 17 | `UNDER_PACE_PCT` `-0.30 -> -0.20` | `test_under_pace_needs_a_real_gap_and_real_progress` |
| 18 | `LARGE_DRAW_SHARE` `0.25 -> 0.05` | `test_a_large_draw_is_named_but_never_moves_severity` |
| 19 | `MARGIN_EROSION_BANDS` LOW floor `0.02 -> 0.03` | `test_margin_erosion_bands_sit_where_they_are_documented` |
| 20 | Duplicate detection drops `code` from its key | `test_a_suspected_duplicate_needs_the_same_code_as_well` |
| 21 | `DUPLICATE_WINDOW_DAYS` `45 -> 30` | `test_thresholds_the_readme_quotes_are_pinned_to_their_values` |
| 22 | `COMPLIANCE_LOOKAHEAD_DAYS` `30 -> 60` | `test_thresholds_the_readme_quotes_are_pinned_to_their_values` |
| 23 | Terminal slip read off the first critical-path milestone | `test_slip_is_weather_plus_a_named_cause_less_float` |
| 24 | Allowance overage cleared by CO cost instead of sell price | `test_allowance_overage_covered_by_linked_approved_co_is_not_flagged` |
| 25 | Suspected duplicates no longer deducted from spend | `test_duplicate_is_excluded_from_burn_rate` |

Entries 21 and 22 are the two that were added last: both survived the
first mutation run, because the seeded dataset sits well clear of either
edge and nothing asserted the numbers the README quotes.

Two notes on what this list deliberately leaves out.

Entry 5 used to read "top made inclusive (`lo <= v <= hi`)". That
mutation is unobservable and was removed rather than left on the list as
a kill that never happened: `_band_for` walks the bands from HIGH down,
so a value sitting exactly on a band's top edge is claimed by the band
above before the inclusive test is ever reached. The output is identical
either way. The floor is where the half-open interval is actually load
bearing, so that is what entry 5 now mutates.

Entry 3 did survive a re-run, and the fault was in the test rather than
the detector: the fixture gave the milestone the same baseline and
forecast date, so swapping the column the curve is built from changed
nothing. The fixture now moves the forecast 200 days out, which is the
only arrangement in which the question the test asks has an answer.
