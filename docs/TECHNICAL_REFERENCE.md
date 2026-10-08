# Technical reference

Detailed data-import specification, detection methodology, calibration sources, dataset design and validation strategy for the [Job-Cost & Schedule-Risk Dashboard](../README.md).

## Bring your own data

Everything on this page runs against the sample dataset until you upload
your own. The sidebar's "Upload your own ledger" widget takes all 11 CSVs
at once (select them together in the file picker). As soon as every file
validates, the whole dashboard switches to your data: thresholds, charts
and the call list all update together. Remove the upload and it falls
back to the demo.

The schema below is exactly what [`src/detection.py`](../src/detection.py)
reads and what [`src/generate_data.py`](../src/generate_data.py) produces, so
the fastest way to build a valid upload is to open one of the generated
files in `data/` (run the app once to create it) and match its columns.
Extra columns are ignored; a missing one is reported by name instead of
crashing the page, and `projects.csv`, `cost_codes.csv` and
`project_budgets.csv` need at least one row.

| File | Required columns |
| --- | --- |
| `projects.csv` | `project_id, name, type, finish_tier, contract_value, margin_pct, square_feet, start_date, end_date, retainage_pct, status, project_manager, float_days, pct_complete, billed_to_date, retainage_held` |
| `cost_codes.csv` | `code, division, division_name, description, typical_share_of_division, phase_start, phase_end, trade, supplier, gc_buys_material` |
| `project_budgets.csv` | `project_id, code, budgeted_amount, approved_co_cost, current_budget, pct_complete` |
| `budget_revisions.csv` | `revision_id, project_id, code, revised_amount, date, reason` |
| `schedule_milestones.csv` | `milestone_id, project_id, milestone, baseline_date, forecast_date, actual_date, critical_path, weather_exposed, weather_delay_days, other_delay_days, delay_reason, planned_pct_complete` |
| `allowances.csv` | `allowance_id, project_id, code, description, allowance_amount, selected_amount, selection_due, selection_date` |
| `change_orders.csv` | `co_id, project_id, code, cost_amount, amount, submitted_date, approved_date, time_extension_days, reason, allowance_id` |
| `commitments.csv` | `commitment_id, project_id, sub_id, sub_name, trade, contract_amount, commitment_type, scope_lines, co_amount, signed_date, retention_pct, invoiced_to_date, retention_held, retention_released, paid_to_date` |
| `commitment_lines.csv` | `commitment_id, project_id, code, sub_id, sub_name, line_amount, co_amount, invoiced_to_date` |
| `cost_transactions.csv` | `transaction_id, project_id, code, date, amount, vendor, type` |
| `subcontractors.csv` | `sub_id, name, trade, insurance_expiry, license_expiry, active_projects` |

`project_id` and `code` are the join keys tying every table back to
`projects.csv` and `cost_codes.csv`. Date fields are parsed with `pandas.to_datetime`; use unambiguous ISO 8601 dates
(`YYYY-MM-DD`) in uploaded ledgers. The bundled demonstration uses a fixed
reporting date for repeatability, while uploaded ledgers are assessed against
the current reporting date. The validation
that backs this table lives in `detection.REQUIRED_COLUMNS` and
`detection.load_data_from_files`, exercised by
[`tests/test_detection.py`](../tests/test_detection.py)'s upload tests.

## How the detection works

Every threshold is in [`src/detection.py`](../src/detection.py) with the
reasoning written next to it. They came from running jobs, and another
business should calibrate them against its own history before trusting
them.

Nothing is flagged on percentage alone. A materiality floor scales with
the job (the larger of $5,000 and a share of contract value), and above it
large dollars can promote severity so a small percentage on a very large
line isn't buried under a large percentage on a small one. Burn rate is
measured per line against that line's own reported progress, not a
project-wide percentage. A change order's cost and sell price are tracked
separately, since mixing them up is the most common way a job-cost report
lies. Budget drift (an informal revision, not a change order) is its own
signal, cross-referenced against what paperwork actually covers the move.
Buyout is compared to what a line was sold at, not a revised budget, so
editing the budget to match a contract can't erase the flag. SPI and CPI
follow standard EVM formulas off the superintendent's per-line percent
complete, and slip is read off the terminal critical-path milestone
rather than summed, so one delay pushing everything behind it isn't
counted several times. Forecast at completion is built line by line: the
worst of budget, sub contract, booked spend and extrapolated spend while
a line is open, and booked spend plus the uninvoiced commitment balance
once it's substantially complete. Approved change-order costs also enter
the cost-code budget and forecast even when they introduce a new code;
the corresponding approved sell prices change the revised contract value.

## Where it cries wolf

Every detector produces noise and I'd rather say where than let you find
it. The under-pace check is the one I'd rip out first for a real
deployment. On a real ledger it fires on invoices that haven't landed far
more often than on work that hasn't happened, which is why it's capped at
LOW. The duplicate check is a suspicion, not an accusation: two identical
progress draws will trip it, and it only ever says "confirm before the
next pay run." Suspected duplicate transactions remain in booked ledger
spend and the financial forecast until a verified reversal is recorded. The same underlying problem often raises several flags on
purpose, too. The Harborview cabinetry decision shows up as four
different flags from one decision, which is why flag dollars must never
be summed across a job.

## How the numbers were calibrated

The cost model is calibrated against published figures, and the test
suite pins it there so a later tweak can't quietly drift the dataset away
from them.

| What | Benchmark | This dataset |
|---|---|---|
| Cost breakdown by category | NAHB *Cost of Constructing a Home* (2024) | The three ground-up houses land inside a custom-work band around NAHB's production-housing figures |
| Cost per square foot | Seattle / Eastside custom homes, entry to luxury tiers | Standard $417, High $519, Premium $664, remodel $295/sf on affected area |
| General conditions | Commonly cited at 5-10% of project cost | 9.2-13.9% of cost, priced by the month, highest on the two smallest jobs |
| Retainage | RCW 60.30.010 caps private-project retainage at 5%, exempting single-family residential | Sub contracts: 5% commercial, 10% residential (exempt); owner billing: 5% on all five |
| Change-order volume | AIA Contract Documents: 3.2-5.0% of contract, by job size | Additive change orders 3.3-6.4% of contract |

Two deviations are deliberate rather than errors. Interior finishes run
above the NAHB figure because this is custom work, where the money moves
inward to cabinetry, tile and millwork rather than following a
production-housing split. General conditions sit at the top of the
commonly cited band because these are small, long-duration residential
jobs where supervision is a monthly cost that doesn't shrink with the
contract.

**Reading this from outside the US:** the dataset is deliberately
grounded in one real jurisdiction (Washington State) so every dollar
figure and legal citation is checkable against a real source rather than
invented. A few terms translate directly for an Australian reader. A
change order here is a variation, retainage is retention (Australian
retention regimes typically run under the security-of-payment
legislation in each state rather than a single national rule), and a CSI
division is roughly what a NATSPEC worksection covers. The dollar
figures, statute citation and NAHB benchmarks are US-specific and are not
restated in AUD. The point of this section is that the numbers are real
and sourced, not that they're portable to another market unchanged.

Sources: [NAHB, Cost of Constructing a Home
2024](https://eyeonhousing.org/2025/01/cost-of-constructing-a-home-in-2024/)  |
[RCW 60.30.010, Retainage](https://app.leg.wa.gov/RCW/default.aspx?cite=60.30.010)  |
[AIA Contract Documents, The Truth About Change
Orders](https://learn.aiacontracts.com/wp-content/uploads/2023/07/The-Truth-About-Change-Orders.pdf)  |
[CrewCost, general conditions in
construction](https://crewcost.com/blog/easy-guide-to-general-conditions-in-construction/)  |
[Emerald City Construction, custom home cost in Seattle and the
Eastside](https://www.emeraldcitybuild.com/blog/how-much-does-a-custom-cost-in-seattle-wa-and-the-eastside)

## Data model

Eleven tables, generated into `data/` on first run and gitignored:
`projects`, `cost_codes` (57 codes across 24 CSI divisions), `project_budgets`,
`budget_revisions` (the drift table), `schedule_milestones`, `allowances`,
`change_orders`, `commitments`, `commitment_lines`, `cost_transactions`,
and `subcontractors`.

Scope a job doesn't have is dropped, not carried at $0. The cottage has no
pool, the commercial shell has no elevator or appliance package. A few
things are modeled the way they're actually bought rather than the way
they're easy to generate: general conditions accrue by the month rather
than as a percentage of contract, permits price off valuation and floor
area the way a jurisdiction actually assesses them, one subcontract covers
one trade's whole scope rather than one per cost code, retention releases
at closeout rather than per phase, and weather comes off float before it
moves a finish date.

Generation is seeded (`numpy` `default_rng(42)`) and dependency versions
are pinned, so every run produces byte-identical output and the test suite
asserts against specific planted problems rather than noise. CI
regenerates the dataset twice on every push and fails if the two runs
differ by a byte. The snapshot has a fixed as-of date rather than a
rolling one. A rolling "today" would make every seeded story (the change
order unsigned for 87 days) drift day to day, so the snapshot is instead
shifted forward as a block by one constant in
[`src/dataset_config.py`](../src/dataset_config.py), and it can be moved on
without touching a single figure.

## Testing

[`tests/test_detection.py`](../tests/test_detection.py) is 113 tests in two
halves, [`tests/test_app.py`](../tests/test_app.py) adds 30 that run the
dashboard itself, and [`tests/test_charts.py`](../tests/test_charts.py) adds
36 that parse the charts it draws. That's 179 in total.

The first half of the detection suite asserts that every planted problem
is caught at the severity it was planted at and that the explanation says
what actually happened. The second half tests the arithmetic directly on
constructed inputs with hand-computed answers, because the first half
wasn't enough on its own. Mutating the detection code one line at a time
(inverting CPI, shifting a band edge, swapping a numerator) left the
severity-label tests green through fourteen of twenty-five deliberate
errors. Every one of those twenty-five mutations, and the test that kills
it, is listed in [`tests/MUTATIONS.md`](../tests/MUTATIONS.md), so the claim
is checkable rather than asserted.

The app and chart tests catch the class of fault that has no stack trace.
Streamlit runs every string through a markdown pass with LaTeX enabled,
so an unescaped `$` can render as math with both dollar signs eaten, and
a label that runs off a chart's frame is invisible to everything except
looking at it. Neither failure raises an exception, so both are asserted
against directly rather than caught by a health check.

CI runs the suite on Python 3.11 and 3.12, lints, checks the dataset is
byte-identical across two generations, and confirms the real Streamlit
runtime can start the app.

