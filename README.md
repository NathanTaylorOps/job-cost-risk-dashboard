# Job-Cost & Schedule-Risk Dashboard for a Custom Home Builder

[![tests](https://github.com/NathanTaylorOps/job-cost-risk-dashboard/actions/workflows/tests.yml/badge.svg)](https://github.com/NathanTaylorOps/job-cost-risk-dashboard/actions/workflows/tests.yml)

**Live demo:** _(deploy link goes here)_

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
all three kinds of color blindness, and every mark carries its word. The cost model is calibrated against
published figures and the test suite pins it there ([how the numbers were
calibrated](#how-the-numbers-were-calibrated)).

## What it finds on the sample data

Forty-eight flags across the five jobs. One job is clean. The ones that
matter:

- **Harborview (P03), dried in and starting rough-ins.** The owner asked for
  the upgraded cabinetry package on a phone call in April. The budget line
  was revised up 72% ($138,462) the same day, the sub contract was re-issued
  at $330,769 against a $192,308 line, and the change order to the owner
  (CO001, $38,500) has sat unsigned for 87 days. That change order covers
  $30,030 of the added cost. The other $108,432 has no paperwork behind it
  at all. The same sub's insurance lapsed in June and they are still on
  site. Plumbing fixtures were picked at $124,000 against a $77,500
  allowance, thirteen days late, with the change order for that difference
  also unsigned. Projected margin is 17.5% against 22% priced, four and a
  half points gone.

  Thirteen flags on one job, and most of them are the same phone call.

- **Cascade Ridge (P02), a remodel that framed through a wet winter.**
  Forecast 55 days late: 34 of weather, 21 of unforeseen conditions in the
  existing structure, and that second one has a change order and a matching
  21-day time extension behind it. The framer's $18,412.50 January draw was
  posted twice, 13 days apart; with it in, the framing line reads +28%
  against pace, without it nothing, and it accounts for the $16,833 that sub
  appears to have invoiced beyond its contract. The roofing budget was moved
  up 24% with no change order anywhere. The job is $60,798 under-billed, 11%
  of what it has earned.

- **Alderwood (P01), finishes underway.** Light fixtures were selected
  $14,700 over allowance with no change order written, so the builder is
  absorbing it. The appliance package is 25 days overdue and still not
  picked, which holds the cabinet install behind it. Excavation ran 24%
  over, and plumbing rough-in is reported complete with 30% of its money
  unspent, which is either invoices that have not landed or work that has
  not happened.

- **Timberline (P05), light commercial.** The tenant's HVAC zoning redesign
  (CO002, $21,200) has been unsigned for 60 days with the work under way,
  and four more change orders on this job have been sitting for over five
  months. The excavation sub's license lapsed in April and they are still
  active. Excavation is 32% over pace.

- **Fairhaven (P04) is clean.** CLEAR on all seven signals: on schedule with
  five days of weather absorbed into float, SPI 1.00, margin holding. It is
  in the dataset deliberately, and nothing random is allowed to drift onto
  it either. A tool that flags everything is a tool nobody opens twice.

- **Across the portfolio:** earthwork is running over on three separate jobs
  at once, +25% and $30,887 in total. On any one of them that is a small
  flag worth a shrug. On three it is a haul-off rate the estimate has not
  kept up with, and it is the one signal no single-project view can show
  you.

## Quick start

Python 3.11 or newer.

```bash
git clone https://github.com/NathanTaylorOps/job-cost-risk-dashboard.git
cd job-cost-risk-dashboard
pip install -r requirements.txt

streamlit run app/streamlit_app.py     # generates the dataset if needed, opens the dashboard
python src/detection.py                # prints every flag to the terminal
pip install -r requirements-dev.txt && pytest tests/ -v    # 144 tests
```

The dataset is generated on first run and is not committed. Deploys to
[Streamlit Community Cloud](https://streamlit.io/cloud) from this repo
as-is: point it at `app/streamlit_app.py`.

## The problem

I took over operations at a custom home builder that ran job costing off a
paper ledger. General-contractor scope, start to finish, four or five
projects live at any time, and the only way to know whether a job was
making money was to pull its ledger and add it up.

That produced the same blind spots every month:

- **Cost overruns surfaced late.** A cost code running hot showed up when
  the invoices caught up with the ledger, weeks after the money was spent.
- **No portfolio view.** Five jobs meant five ledgers. Nobody could see at
  a glance which one was bleeding.
- **Schedule slip was history, not a forecast.** You found out the roof was
  late when the framer rang asking where the roofer was.

And because the role was the whole business, not just cost control, the gap
was wider than cost and schedule. A sub's insurance lapsing, a change order
nobody chased, an owner sitting on a selection, a job billing behind what it
had earned: each of those costs real money and none of them show up in a
cost report.

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
across, worst job on the left. That shape replaced five per-job cards,
because position is what lets a reader compare -- a row tells you at a
glance that change orders are a problem on three of five jobs, which five
separate cards cannot say. Under it, the flags are grouped by who has to
be called rather than by which detector fired, because one verbal
instruction from an owner shows up as a burn-rate miss, a quiet budget
edit, a re-issued sub contract and an unsigned change order, and those are
one phone call, not four.

Severity is drawn as an ordinal ladder rather than four colors: solid fill
needs a call today, a tint needs one this week, an outline is worth
knowing, and a bare check mark means the signal found nothing. It ranks
correctly in grayscale, it ranks correctly under all three kinds of color
blindness, and every mark carries its word beside it, so no reading
depends on color at all. The four hues were checked with a CVD simulator
rather than by eye: the worst adjacent pair separates by 17.5 in OKLab
under deuteranopia against a floor of 8.

Five charts carry the parts that do not fit in a sentence -- cost variance
against the job's materiality floor, milestone slip fanning out from one
early delay, cost to date against plan with the forecast, change orders by
age against the severity bands, and the portfolio grid itself. They are
hand-written SVG in [`app/charts.py`](app/charts.py) rather than a
plotting library: the page adds no chart dependency, the output renders
anywhere an image does, and the geometry is plain enough that the test
suite can parse it and check that nothing has run off the edge of its
frame, which is the only failure mode a chart has that does not raise.

Every distinct unit of content -- the portfolio grid, a group of calls to
one counterparty, a chart, the list of flags under it -- sits inside its
own rounded, bordered card lifted off the page with a soft shadow, so the
page reads as boxes to scan rather than one undifferentiated scroll of
lines. The rule is one box per thing: the calls under a job are grouped
one card per person to call, each with its own list of flags inside it,
and each of the six drill-down tabs is chart card, flags card, then a
native expander for the full detail table underneath. A row of loose
lines with no border reads as a single paragraph even when the content is
a list; the border, radius and shadow are what tell the eye "this is one
unit," lifted off a soft gray canvas the way a native macOS or Windows
settings panel groups its own controls -- a single set of design tokens
(`--rg-accent`, `--rg-card-bg`, `--rg-border`, `--rg-radius-lg`, and so on,
defined once in `streamlit_app.py`'s `<style>` block) drives every card,
chip and native Streamlit widget on the page, so the whole thing reads as
one consistent piece of software rather than a stack of default widgets.

## What you can do with it

The front screen is not read-only. Everything below lives in the sidebar
and reruns the whole detection pass the moment it changes -- there is no
separate "apply" step.

- **Thresholds move live.** Materiality floor, cost and buyout variance
  bands, and change-order aging are sliders and a number input, not
  numbers buried in a config file. Drag the floor from $5,000 to $10,000
  and every flag, every chip on the grid, and every open-flag count
  recomputes on the spot. An expander next to the controls explains where
  the shipped starting numbers came from and says plainly that they are a
  starting point, not a standard -- a GC running tighter margins would set
  them differently.
- **The portfolio grid filters to what needs attention.** One checkbox
  drops any job that reads CLEAR on all seven signals out of the grid,
  for a five-job board that is really fifteen or thirty.
- **Jobs roll up by project manager, not just by job.** Five houses map
  to two PMs on this dataset, and the split is not even -- the "By
  project manager" row says whose morning is worse without paging through
  five jobs to add it up by hand.
- **The call list is a working call sheet, not a read-only report.**
  Every flag gets a checkbox and a note field in the sidebar. Check one
  off as you make the call, jot what the sub said or why it's still open,
  and a progress bar tracks how much of the morning is left. Export what
  remains as a CSV for a spreadsheet or a PDF for an inbox or a shirt
  pocket -- both carry the note.
- **Cash not yet collected is its own number.** Retainage held plus
  anything earned but not yet invoiced, per job and rolled up across the
  portfolio, so "how much of our own money is sitting with the owner
  right now" has a straight answer instead of a mental sum across five
  WIP lines.
- **The portfolio trend chart is real history, not five as-of
  snapshots.** Every job's posted cost transactions, summed at every date
  any job posted one, against every schedule's planned value -- the one
  place on the page the portfolio moves through time instead of sitting
  at today. Earned value still plots as a single point, not a curve:
  there is no history of per-line percent-complete in this dataset, only
  today's, and a curve built from one point per job would be invented.
- **It works on a phone.** The layout, the sidebar and the five-metric
  headline row all collapse to one column under about 768px, because a
  call list is exactly the kind of thing that gets opened standing in a
  driveway.

## How the detection works

Every threshold is in [`src/detection.py`](src/detection.py) with the
reasoning written next to it. They came from running jobs, and another
business should calibrate them against its own history before trusting
them.

**Nothing is flagged on percentage alone.** A materiality floor scales with
the job: the larger of $5,000 and 0.25% of contract, 0.35% on commercial. So
a $6K miss matters on a $650K cottage and is noise on a $3.4M build. Above
the floor the percentage band applies, and large dollars promote it, so a
small percentage swing on a very large line is not buried under a large
percentage swing on a small one.

**Burn rate is measured per line, against that line's own reported
progress**, not against a project-wide percentage. Framing on a job past
dry-in is expected to be fully spent and landscaping on the same job
untouched; a flat project percentage would call both of those anomalies.
The per-line percentages are the superintendent's, off the schedule of
values on the pay application, and the project's percentage is their
cost-weighted roll-up rather than a number anyone types in.

**Cost is compared against cost.** A change order adds its cost to the
budget line and its sell price to the contract, and mixing those up is the
most common way a job-cost report lies to you. The same holds in reverse
for a credit: the owner gets the margin back too.

**Budget drift is its own signal.** An informal revision does not raise the
line a variance is measured against, because a quietly inflated budget
would then flatter its own burn rate. The flag names what change-order
paperwork covers the move and what is left uncovered.

**Allowances are where custom-home margin actually leaks.** An allowance is
a contract number, so it carries the job's margin like every other contract
dollar; setting it at the builder's cost line means the owner spends the
whole allowance and the builder earns nothing on that scope. Overages clear
only when a change order linked to that specific allowance is approved, not
when some other change order happens to land on the same cost code. Late
selections are flagged against the schedule's need-by date, because a late
appliance pick is a late cabinet install.

**Commitments are where the estimate stops being a guess.** Buyout is
compared to what the line was *sold* at, not to a revised budget, so editing
the budget up to match a contract cannot erase the flag. Where the GC buys
the material direct, the sub contract is labor only, and the material is
tracked as its own spend against the same line.

**Schedule.** SPI is earned percent over what the baseline planned to have
earned by today, interpolated between milestones. CPI is earned value over
actual cost. Slip is read off the terminal critical-path milestone rather
than summed across the schedule, because one delay that pushes framing
pushes everything behind it and adding those up counts it several times.
Weather comes off the job's float before it comes off the finish date,
which is why a job can absorb a fortnight of rain and still forecast to
baseline.

**Billing and forecast are the controller's two numbers.** Earned revenue
is the revised contract times percent complete, and the over/under is
measured as a share of what the job has earned rather than of contract, so
the same gap reads the same on a $780K job and a $3.4M one. A CPA's WIP
earns revenue cost-to-cost; that percentage is shown next to the
superintendent's, and a gap between them is its own flag.

Forecast at completion is built line by line: while a line is open it is
the worst of its budget, its sub contract and its spend extrapolated over
progress (capped, and only once the line is far enough along for the run
rate to mean anything); once it is done, what it cost plus what the sub has
not yet invoiced. Spend coded to a line with no budget at all is carried in
too, at zero budget, so a miscoded invoice cannot hide from projected
margin.

**Sub compliance is a hard stop, not a cost signal.** A lapsed certificate
on an active job is HIGH whatever the dollars, and expiring inside 30 days
is LOW so somebody chases the renewal. There is no such thing as a little
uninsured on an active site.

## Where it cries wolf

Every detector produces noise and I would rather say where than let you
find it.

- **The under-pace check is the one I would rip out first.** On a real
  ledger it fires on invoices that have not landed far more often than on
  work that has not happened, and I never found a threshold that separates
  the two. It sits at LOW for that reason and would need tuning against
  real AP timing in the first month.
- **The duplicate check is a suspicion, not an accusation.** Same project,
  same vendor, same cost code, same amount, inside 45 days. Two identical
  progress draws will trip it. It says "confirm before the next pay run"
  because that is all it has earned the right to say.
- **The same underlying problem raises several flags on purpose.** The
  Harborview cabinetry decision shows up as a burn-rate flag, a drift flag,
  a buyout flag and a change-order age flag. They are four different risks
  from one decision and I would rather see all four than have the tool pick
  one for me. It does mean the flag dollars must not be summed.

## What it would take to run this for real

- **The ledger has to come from somewhere.** QuickBooks, Sage, Procore or
  Buildertrend all export something close to these tables. The generator
  would be replaced by a loader and everything downstream would stand.
- **Per-line percent complete is the hard part.** The whole thing rests on
  superintendents filling in a schedule of values honestly and on time.
  That is a people problem before it is a software problem.
- **Vendor and change-order text would be real user input.** Everything the
  page renders from the ledger is HTML-escaped already for that reason.
- **Thresholds want a calibration period.** A month of running it alongside
  the existing process tells you which floors are wrong for your book of
  work. Fine for a demo of fictional data, not for a real one.
- **Acknowledge, assign, close.** A flag is a prompt for a phone call, and
  none of that state is modeled here, because a Monday review that cannot
  see last Monday's decisions is a report.
- **Closeout.** Every job here is live. Retainage release, punch-list
  holdbacks and warranty reserve are where the last few points of margin
  are won or lost, and the model only reaches as far as releasing sub
  retention at scope close-out.

## How the numbers were calibrated

Synthetic data is only worth anything if its shape is right, and "looks
about right to me" is not a standard anyone can check. The cost model here
is calibrated against published figures, and the test suite pins it there
so a later tweak to one division weight cannot quietly drift the whole
dataset away from them.

| What | Benchmark | This dataset |
|---|---|---|
| Cost breakdown by category | NAHB *Cost of Constructing a Home* (2024): interior finishes 24.1%, rough-ins 19.2%, framing 16.6%, exterior 13.4%, foundations 10.5%, site work 7.6% | The three ground-up houses land inside a custom-work band around those figures: framing 13-16%, foundations 9-13%, rough-ins 15-18%, interior finishes 31-34% |
| Cost per square foot | Seattle / Eastside custom homes: entry ~$350, mid ~$425, high $500-600, luxury $750+ | Standard $417, High $519, Premium $664, remodel $295 on affected area, light commercial $232 |
| General conditions | Commonly cited at 5-10% of project cost, and duration-driven | 9.2-13.9% of cost (7.8-11.4% of contract), priced by the month, highest on the two smallest jobs |
| Retainage | RCW 60.30.010 caps private-project retainage at 5% and exempts single-family residential under 12 units | Sub contracts: 5% on the commercial job, 10% on the four houses. Owner billing: 5% on all five, which is what this builder's contracts say |
| Change-order volume | AIA Contract Documents, from 18,229 completed projects and 892,457 change orders: average cost change of 3.20% on jobs under $500K, 4.36% on $500K-$1M, 5.04% on $1M-$5M | Additive change orders 3.3-6.4% of contract, with credits and time extensions alongside |

Two of those deserve their caveats stated rather than buried.

**Interior finishes run above the NAHB figure and two categories run
below it, for the same reason.** NAHB surveys production housing; this is
custom work, where the money moves inward to cabinetry, tile and millwork.
Interior finishes land at 31-34% against NAHB's 24.1%, and the two
categories that give that money up are site work (6.2-6.8% against 7.6%)
and exterior finishes (9.5-12.5% against 13.4%). The premium job also
carries a pool and an elevator. A custom build that matched a
production-home breakdown exactly would be the suspicious result, not this
one. The premium job's $664/sf sits between the "high grade" and "luxury"
tiers in the Seattle figures below, which is where a waterfront slope with
a stepped foundation belongs.

**General conditions sit at or above the top of the commonly cited band,
and that is the honest number rather than a tuned one.** The 5-10% figure
is quoted for commercial work; these are small, long-duration residential
jobs, and general conditions are a monthly cost that does not shrink with
the contract. The cottage carries 13% of its cost in general conditions
because a site costs what it costs for eleven months whatever is being
built on it. If that band matters to a reader, it is visible and arguable
rather than hidden.

Two places where the published figure changed the data rather than
confirming it. The commercial job originally carried 10% retainage;
Washington capped private-project retainage at 5% in July 2023 (SSB 5528),
so unless the parties have agreed to waive the cap, 10% is more than the
statute allows on that job. The four houses are single-family and fall
under the statute's exemption, so they keep the 10% that is still common on
private residential subcontracts. Separately, an earlier version of the
cost model put interior finishes at 42% and framing at 4%, because a
premium cabinetry package was being funded out of the structure inside a
fixed division weight; the NAHB comparison is what caught it.

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

| Table | What it holds |
|---|---|
| `projects` | Contract, margin, retainage, dates, reported % complete, project manager |
| `cost_codes` | 57 codes across 24 CSI divisions, with phase windows and trades |
| `project_budgets` | Baseline and current budget and % complete, per line |
| `budget_revisions` | Informal budget edits: the drift table |
| `schedule_milestones` | Baseline, forecast and actual dates, weather and other delay |
| `allowances` | Allowance, selection, due date, date selected |
| `change_orders` | Cost and sell price, submitted and approved dates, time extension |
| `commitments` | One contract per trade per job: invoiced, retention held and released |
| `commitment_lines` | The cost codes each contract covers, and what is billed against each |
| `cost_transactions` | The ledger: labor, material, subs, rentals, permits |
| `subcontractors` | Trade, insurance and license expiry, jobs active on |

Scope a job does not have is dropped, not carried at $0: the cottage has no
pool, the commercial shell has no fireplace, elevator or appliance package,
and the houses have none of the storefront, acoustical ceilings, building
automation, special inspections, toilet accessories or signage the
commercial job carries. The commercial job's biggest line is structural
steel; the houses' is wood framing.

A few things are modeled the way they are bought rather than the way they
are easy to generate, because each one is somewhere a synthetic dataset
usually gives itself away:

- **General conditions by the month.** A superintendent is a monthly cost
  for as long as the job is open, and a small job carries a share of one
  because the same super runs two or three at a time. Pricing general
  conditions as a percentage of contract under-funds supervision on a long
  cheap job and over-funds it on a short expensive one.
- **Permits off valuation and floor area**, the way a jurisdiction assesses
  them. Priced as a share of a duration-driven total, a job that slips
  would budget more permit fee, and an 8,200 sf commercial building would
  come in under a 4,000 sf house.
- **One subcontract per trade per job**, with a scope line per cost code.
  A concrete sub signs one contract covering foundations, slabs and
  flatwork. Writing three would mean three certificates of insurance and
  three retention accounts from one firm on one site.
- **Retention releases at closeout**, not when a trade's phase window
  shuts. It is the owner's security against punch and warranty, so on a
  portfolio of live jobs most of it is still held, and only the job in
  punch is releasing trade by trade. The rate follows the statute: 5% on
  the commercial job, 10% on the houses, which Washington exempts.
- **Weather comes off float before it moves a finish date**, and it is
  integrated over each phase rather than sampled on a milestone date, so a
  job whose exterior work lands in summer is genuinely luckier than one
  that frames in January.
- **The ledger is mostly small.** Line counts fall out of invoice sizes
  drawn from a realistic distribution rather than a fixed number of draws,
  so the median transaction is a few hundred dollars and the mean is a few
  thousand, which is what a GC's accounts payable actually looks like.

Generation is seeded (`numpy` `default_rng(42)`) and the dependency versions
are pinned, so every run produces byte-identical output and the test suite
asserts against specific planted problems rather than noise. CI regenerates
the dataset twice on every push and fails if the two runs differ by a byte.

The snapshot has a fixed as-of date rather than a rolling one, because a
rolling "today" would make the seeded stories drift: the change order that
has been unsigned for 87 days would be a different number every morning,
and the README and the tests would both be chasing it. The whole timeline
is shifted forward as a block instead, by one constant in
[`src/dataset_config.py`](src/dataset_config.py), so the snapshot can be
moved on a year without touching a single figure.

## Testing

[`tests/test_detection.py`](tests/test_detection.py) is 103 tests in two
halves, [`tests/test_app.py`](tests/test_app.py) adds ten that run the
dashboard itself, and [`tests/test_charts.py`](tests/test_charts.py) adds
thirty-one that parse the charts it draws. 144 in total.

The first half asserts that every planted problem is caught at the severity
it was planted at and that the explanation says what actually happened: the
Harborview drift flag has to name CO001 as unsigned and put the right
number, cost against cost, on the uncovered remainder; the framer's
duplicate has to be excluded from the burn-rate check and explain the
over-invoiced reading rather than raise a second flag. It also asserts the
dataset hangs together the way a real job does: reported progress is the
roll-up of the lines, forecast slip matches the milestones, framing money
lands between foundation and framing complete, concrete outweighs the pool,
the commercial job is not a house with a different label, and no
subcontract is signed after the work it paid for.

The first half also asserts the domain content, because that is the part a
builder checks and the part a generator gets wrong: concrete outweighs the
pool, the premium house does not frame cheaper per square foot than the
cottage, supervision is priced per month rather than as a slice of a
percentage, no scope arrives on a single six-figure invoice, and the
small-dollar tail is drawn rather than clamped to a floor.

The second half tests the arithmetic directly, on constructed inputs with
hand-computed answers. That half exists because the first half was not
enough. I mutated the detection code one line at a time, inverting CPI,
shifting a band edge by a hundredth, swapping a numerator, and the
severity-label tests stayed green through fourteen of twenty-five
deliberate errors. A suite that only checks labels will not notice a
formula that is upside down. The value tests were written against that
list and kill all of it.

The dashboard is executed by the suite too, not assumed to work:
[`tests/test_app.py`](tests/test_app.py) runs the real app file top to
bottom against a Streamlit stub for all five projects and asserts on what
it rendered. A health check against a running Streamlit server would not
catch anything: Streamlit answers `/_stcore/health` before it has executed
the script, so a `NameError` in the app returns healthy.

What those app tests assert is the class of fault that has no stack trace.
Streamlit runs every string through a markdown pass with LaTeX enabled, so
`$30,030 of cost ($38,500 to the owner)` renders as math with both dollar
signs eaten and the text between them in italics, and CommonMark stops
parsing markdown inside a block-level tag, so the backslash escape that
fixes it in ordinary text reaches the screen as a visible backslash in
every flag on this page. The same rule turns an indented `<style>` block
into a code listing printed on the page rather than a stylesheet applied
to it. None of that raises. The suite asserts that no payload carries a
bare dollar sign, that nothing wrapped in a `<div>` carries markdown
syntax, that no payload is indented far enough to become a code block, and
that no table shows a raw `nan` or `NaT`.

[`tests/test_charts.py`](tests/test_charts.py) does the same job for the
charts, which are hand-written SVG: a malformed string renders as blank
space rather than an error, and a label that runs off the edge of the
frame is invisible to every check except looking at it. Each chart on each
of the five projects is parsed as XML, and every bar and every label is
measured against the viewBox it has to fit inside.

CI runs the suite on Python 3.11 and 3.12, lints, checks the dataset is
byte-identical across two generations, and confirms the real Streamlit
runtime can start the app.

Every mutation the value tests were written against is listed in
[`tests/MUTATIONS.md`](tests/MUTATIONS.md), with the test that kills each
one, so the claim is checkable rather than asserted.

## About this project

I built this with an AI coding assistant during my time running operations
at a custom home builder. The data model, the detection rules, every
threshold and what counts as a problem worth a phone call are mine, from
doing the job; the assistant wrote most of the Python under my direction and
review, and rewrote it when the numbers it produced did not survive a
builder's read. Several rounds went in the bin for exactly that reason: an
early version priced general conditions as a percentage of contract and
budgeted two thousand dollars a month of supervision on a fifteen-month job,
which is not a number any builder would let past.

## Synthetic data

Every figure in this repository is generated by
[`src/generate_data.py`](src/generate_data.py). Ridgeline Custom Homes, its
projects, subcontractors, vendors and staff do not exist. Nothing here is
derived from any real employer's records, and no real project, client,
subcontractor or financial figure appears anywhere in it. The problems in
the dataset are planted deliberately so the detection logic can be tested
against known answers; they are patterns I have seen, not incidents I am
reporting.

## License

MIT. See [LICENSE](LICENSE).
