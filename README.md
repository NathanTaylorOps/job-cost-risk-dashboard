# Job-Cost & Schedule-Risk Dashboard for a Custom Home Builder

[![tests](https://github.com/NathanTaylorOps/job-cost-risk-dashboard/actions/workflows/tests.yml/badge.svg)](https://github.com/NathanTaylorOps/job-cost-risk-dashboard/actions/workflows/tests.yml)

**Live demo:** https://job-cost-risk-dashboard.streamlit.app/

One screen for a builder running five jobs at once: which one needs a phone
call this morning, and why. I run operations at a custom home builder with
18 employees, 35 subcontractors and four or five jobs live at a time
between $400K and $4M. These are the checks I wanted and did not have,
written as a working tool over a synthetic job-cost extract.

> Ridgeline Custom Homes is fictional. Every project, subcontractor, vendor,
> date and dollar figure here is generated. See [Synthetic data](#synthetic-data).

> Built with an AI coding assistant. The data model, the detection rules and
> every threshold are mine, from doing the job; the assistant wrote most of
> the Python under my direction. [More below.](#about-this-project)

<!-- Add after cloning: screenshots of the portfolio grid and a project detail tab.
![Portfolio overview: five jobs, seven signals](docs/portfolio-overview.png)
![Project detail: Harborview Custom Build, Cost tab](docs/project-detail.png)
-->


**In thirty seconds:** every job gets rated on seven signals -- cost,
schedule, change orders, allowances, billing, forecast margin and sub
compliance -- and the portfolio sorts worst first, as a grid rather than
five separate cards, so the eye compares along a row. Each flag is one
line of plain English with the dollars in it, because the output of this
tool is a phone call, not a report. Severity is an ordinal fill ladder --
solid, tinted, outlined, bare -- so it survives a grayscale printout and
all three kinds of color blindness, and every mark carries its word. The
cost model is calibrated against published figures and the test suite
pins it there ([how the numbers were calibrated](#how-the-numbers-were-calibrated)).

## What it finds on the sample data

Forty-eight flags across the five jobs. One job is clean.

- **Harborview (P03)** carries thirteen flags from one decision: an
  upgraded cabinetry package the owner approved by phone, which shows up as
  a 72% budget revision, a re-issued sub contract, an unsigned change order
  covering less than a third of the added cost, and a lapsed sub insurance
  certificate the site is still operating under. Projected margin is down
  four and a half points.
- **Cascade Ridge (P02)** is forecast 55 days late from weather and an
  unforeseen-conditions change order, and a framer's draw posted twice 13
  days apart is inflating its framing burn rate until the duplicate is
  backed out.
- **Fairhaven (P04) is clean** on all seven signals -- deliberately, and
  nothing random is allowed to drift onto it. A tool that flags everything
  is a tool nobody opens twice.
- **Across the portfolio**, earthwork is running over on three separate
  jobs at once -- a small flag on any one of them, a haul-off rate the
  estimate hasn't kept up with when it's all three.

## Quick start

Python 3.11 or newer.

```bash
git clone https://github.com/NathanTaylorOps/job-cost-risk-dashboard.git
cd job-cost-risk-dashboard
pip install -r requirements.txt

streamlit run app/streamlit_app.py     # generates the dataset if needed, opens the dashboard
python src/detection.py                # prints every flag to the terminal
pip install -r requirements-dev.txt && pytest tests/ -v    # 162 tests
```

The dataset is generated on first run and is not committed. Deploys to
[Streamlit Community Cloud](https://streamlit.io/cloud) from this repo
as-is: point it at `app/streamlit_app.py`.

## The problem

I took over operations at a custom home builder that ran job costing off a
paper ledger. General-contractor scope, start to finish, four or five
projects live at any time, and the only way to know whether a job was
making money was to pull its ledger and add it up. That produced the same
blind spots every month: cost overruns surfaced weeks after the money was
spent, five jobs meant five ledgers with no portfolio view, and schedule
slip was something you heard about from the framer rather than something
forecast. And because the role was the whole business, not just cost
control, the gap was wider than cost and schedule: a lapsed sub
certificate, a change order nobody chased, an owner sitting on a
selection, a job billing behind what it had earned -- each of those costs
real money and none of them show up in a cost report.

## What this does

| | Paper ledger | This dashboard |
|---|---|---|
| Cost overruns | Found weeks later | Flagged against each line's reported progress |
| Portfolio view | Five separate ledgers | One screen, five jobs, worst first |
| Budget edits | Invisible | Flagged, with what paperwork covers them |
| Sub contracts | Filed | Buyout compared to the line it was sold at |
| Schedule | A wall calendar | Forecast slip with SPI and CPI |
| Change orders | A folder | Aged, with what the delay is costing |
| Allowances | A spreadsheet | Overage and late selections, against the schedule |
| Billing | Monthly draw | Over/under against earned revenue |
| Forecast | Closeout | Projected margin now, line by line |
| Double payments | Caught at reconciliation | Same vendor, same code, same amount, inside 45 days |
| Sub compliance | A binder | Lapsed and expiring certificates, by job |

Ten checks, rolled into seven signals on the front screen.

## What the screen shows

The front screen is a seven-by-five grid: seven checks down, five jobs
across, worst job on the left -- a shape that lets a reader compare at a
glance in a way five separate per-job cards cannot. Flags are grouped by
who has to be called rather than by which detector fired, because one
verbal instruction from an owner can show up as a burn-rate miss, a quiet
budget edit, a re-issued sub contract and an unsigned change order all at
once, and those are one phone call, not four.

Severity is drawn as an ordinal ladder rather than four colors: solid fill
needs a call today, a tint needs one this week, an outline is worth
knowing, and a bare check mark means the signal found nothing. It ranks
correctly in grayscale and under all three kinds of color blindness (the
four hues were checked with a CVD simulator), and every mark carries its
word beside it, so no reading depends on color alone.

Five charts carry the parts that don't fit in a sentence -- cost variance
against materiality, milestone slip, cost-to-date against plan, change
orders by age, and the portfolio grid itself. They're hand-written SVG in
[`app/charts.py`](app/charts.py) rather than a plotting library, so the
page adds no chart dependency and the test suite can parse the output
directly to check nothing has run off the edge of its frame.

Every distinct unit of content sits inside its own rounded, bordered card
lifted off the page with a soft shadow -- a single set of design tokens
drives every card, chip and native Streamlit widget, so the page reads as
one consistent piece of software rather than a stack of default widgets.

## What you can do with it

The front screen is not read-only, and everything below reruns the whole
detection pass the moment it changes.

- **Thresholds move live.** Materiality floor, cost and buyout variance
  bands, and change-order aging are sliders, not numbers buried in a
  config file. An expander next to the controls explains where the shipped
  starting numbers came from and says plainly they're a starting point,
  not a standard.
- **The portfolio grid filters to what needs attention**, dropping any job
  that reads CLEAR on all seven signals.
- **Jobs roll up by project manager**, so whose morning is worse has a
  straight answer without paging through five jobs by hand.
- **The call list is a working call sheet.** Every flag gets a checkbox
  and a note field; check one off as you make the call, and export what
  remains as a CSV or a PDF.
- **Cash not yet collected is its own number** -- retainage held plus
  earned-but-not-invoiced, per job and rolled up across the portfolio.
- **The portfolio trend chart is real history**, not five as-of snapshots:
  every job's posted cost transactions against every schedule's planned
  value.
- **It works on a phone.** The layout collapses to one column under about
  768px, because a call list is exactly the kind of thing that gets
  opened standing in a driveway.
- **Bring your own data.** The sidebar's uploader replaces Ridgeline
  Custom Homes with your own portfolio and runs it through the same
  detection pipeline -- see "Bring your own data" below for the schema.

## Bring your own data

Everything on this page runs against the sample dataset until you upload
your own. The sidebar's "Upload your own ledger" widget takes all 11 CSVs
at once (select them together in the file picker); as soon as every file
validates, the whole dashboard -- thresholds, charts, the call list --
switches to your data. Remove the upload and it falls back to the demo.

The schema below is exactly what [`src/detection.py`](src/detection.py)
reads and what [`src/generate_data.py`](src/generate_data.py) produces, so
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
`projects.csv` and `cost_codes.csv`; every date column parses with
`pandas.to_datetime`, so any common date format works. The validation
that backs this table lives in `detection.REQUIRED_COLUMNS` and
`detection.load_data_from_files`, exercised by
[`tests/test_detection.py`](tests/test_detection.py)'s upload tests.

## How the detection works

Every threshold is in [`src/detection.py`](src/detection.py) with the
reasoning written next to it. They came from running jobs, and another
business should calibrate them against its own history before trusting
them.

Nothing is flagged on percentage alone: a materiality floor scales with
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
complete; slip is read off the terminal critical-path milestone rather
than summed, so one delay pushing everything behind it isn't counted
several times. Forecast at completion is built line by line -- the worst
of budget, sub contract and extrapolated spend while a line is open, actual
cost plus what's still owed once it's substantially complete.

## Where it cries wolf

Every detector produces noise and I'd rather say where than let you find
it. The under-pace check is the one I'd rip out first for a real
deployment -- on a real ledger it fires on invoices that haven't landed
far more often than on work that hasn't happened, which is why it's capped
at LOW. The duplicate check is a suspicion, not an accusation: two
identical progress draws will trip it, and it only ever says "confirm
before the next pay run." And the same underlying problem often raises
several flags on purpose -- the Harborview cabinetry decision shows up as
four different flags from one decision, which is why flag dollars must
never be summed across a job.

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

Two deviations are deliberate rather than errors: interior finishes run
above the NAHB figure because this is custom work, where the money moves
inward to cabinetry, tile and millwork rather than following a
production-housing split; and general conditions sit at the top of the
commonly cited band because these are small, long-duration residential
jobs where supervision is a monthly cost that doesn't shrink with the
contract.

**Reading this from outside the US:** the dataset is deliberately
grounded in one real jurisdiction (Washington State) so every dollar
figure and legal citation is checkable against a real source rather than
invented. A few terms translate directly for an Australian reader: a
change order here is a variation, retainage is retention (Australian
retention regimes typically run under the security-of-payment
legislation in each state rather than a single national rule), and a CSI
division is roughly what a NATSPEC worksection covers. The dollar
figures, statute citation and NAHB benchmarks are US-specific and are not
restated in AUD -- the point of this section is that the numbers are
real and sourced, not that they're portable to another market unchanged.

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

Scope a job doesn't have is dropped, not carried at $0 -- the cottage has
no pool, the commercial shell has no elevator or appliance package. A few
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
rolling one -- a rolling "today" would make every seeded story (the change
order unsigned for 87 days) drift day to day -- shifted forward as a block
by one constant in [`src/dataset_config.py`](src/dataset_config.py) so the
snapshot can be moved on without touching a single figure.

## Testing

[`tests/test_detection.py`](tests/test_detection.py) is 106 tests in two
halves, [`tests/test_app.py`](tests/test_app.py) adds 25 that run the
dashboard itself, and [`tests/test_charts.py`](tests/test_charts.py) adds
31 that parse the charts it draws -- 162 in total.

The first half of the detection suite asserts that every planted problem
is caught at the severity it was planted at and that the explanation says
what actually happened. The second half tests the arithmetic directly on
constructed inputs with hand-computed answers, because the first half
wasn't enough on its own: mutating the detection code one line at a time
(inverting CPI, shifting a band edge, swapping a numerator) left the
severity-label tests green through fourteen of twenty-five deliberate
errors. Every one of those twenty-five mutations, and the test that kills
it, is listed in [`tests/MUTATIONS.md`](tests/MUTATIONS.md), so the claim
is checkable rather than asserted.

The app and chart tests catch the class of fault that has no stack trace:
Streamlit runs every string through a markdown pass with LaTeX enabled, so
an unescaped `$` can render as math with both dollar signs eaten, and a
label that runs off a chart's frame is invisible to everything except
looking at it. Neither failure raises an exception, so both are asserted
against directly rather than caught by a health check.

CI runs the suite on Python 3.11 and 3.12, lints, checks the dataset is
byte-identical across two generations, and confirms the real Streamlit
runtime can start the app.

## About this project

I built this with an AI coding assistant during my time running operations
at a custom home builder. The data model, the detection rules, every
threshold and what counts as a problem worth a phone call are mine, from
doing the job; the assistant wrote most of the Python under my direction
and review, and rewrote it when the numbers it produced didn't survive a
builder's read.

## Synthetic data

Every figure in this repository is generated by
[`src/generate_data.py`](src/generate_data.py). Ridgeline Custom Homes,
its projects, subcontractors, vendors and staff do not exist. Nothing here
is derived from any real employer's records, and no real project, client,
subcontractor or financial figure appears anywhere in it. The problems in
the dataset are planted deliberately so the detection logic can be tested
against known answers; they are patterns I have seen, not incidents I am
reporting.

## License

MIT. See [LICENSE](LICENSE).
