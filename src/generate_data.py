"""
Synthetic job-cost / schedule-risk dataset generator for Ridgeline Custom
Homes (a fictional custom home builder / GC). All data produced here is
synthetic -- company, projects, dates, dollar figures, vendors and subs are
fictional and generated, not derived from any real employer's records.

Run: python src/generate_data.py [output_dir]
Writes 11 CSVs -- projects, cost_codes, project_budgets, budget_revisions,
schedule_milestones, allowances, change_orders, commitments,
commitment_lines, cost_transactions, subcontractors.

How the pieces hang together (the order below is the order they are built):
- Every project has a contract value, a target margin, and a budgeted cost
  that is contract x (1 - margin), split across the cost codes the project
  actually has scope in. A cottage has no pool; a commercial build has no
  fireplaces; a new home has no demolition.
- Every cost code has a phase window: the slice of the job's calendar in
  which its money is spent (framing 10-29%, drywall 55-61%, landscaping
  92-100%). The superintendent's per-line % complete, draw dates and the
  detector's "expected spend" all come from that window.
- The project's reported % complete is the cost-weighted roll-up of the
  per-line figures, the way a pay application rolls up the schedule of
  values. Baseline dates, the milestone forecast and that progress agree
  with each other by construction: the slip the job carries is split into
  weather (only on outdoor milestones) and a named cause.
- Subcontracted codes carry a commitment (the sub's contract, plus any sub
  change order). Sub draws on that code go to that sub, net of retention.
  A sub is active wherever it holds a signed contract or has been paid, so
  a lapsed certificate flags on every job the firm is really on.
- Change orders carry both what they cost and what they sell for.
- A handful of problems are planted at known severities so the detection
  logic has real signal and the tests have something exact to assert.
  They connect the way real ones do (see README).
- Seeded with numpy default_rng(SEED): every run is byte-identical. data/
  is not committed; it is generated on first run.
"""

import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from dataset_config import BASE_AS_OF as AS_OF
from dataset_config import SEED, TIMELINE_SHIFT_DAYS

rng = np.random.default_rng(SEED)

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(OUT_DIR, exist_ok=True)


def days_between(d1, d2):
    return (d2 - d1).days


def random_date(start, end):
    span = days_between(start, end)
    if span <= 0:
        return start
    return start + timedelta(days=int(rng.integers(0, span + 1)))


# Every date column written to CSV. The generator reasons on the base
# timeline so its date literals stay readable next to the stories they
# belong to; the shift is applied once, here, on the way out.
DATE_COLUMNS = {
    "start_date", "end_date", "date", "baseline_date", "forecast_date", "actual_date",
    "selection_due", "selection_date", "submitted_date", "approved_date", "signed_date",
    "insurance_expiry", "license_expiry",
}


def save(df, name):
    df = df.copy()
    for column in df.columns:
        if column in DATE_COLUMNS:
            shifted = pd.to_datetime(df[column], errors="coerce") + pd.Timedelta(
                days=TIMELINE_SHIFT_DAYS
            )
            df[column] = shifted.dt.date
    df.to_csv(os.path.join(OUT_DIR, name), index=False)


def split(total, n_max, minimum=500.0):
    """Split a dollar total into a few uneven draws, none below `minimum`
    (nobody cuts a $17 sub draw). Returns a list of amounts."""
    if total <= 0:
        return []
    n = int(min(n_max, max(1, total // minimum)))
    if n == 1:
        return [total]
    parts = list(rng.dirichlet(np.ones(n) * 3) * total)
    # fold anything under the minimum into the largest draw
    while len(parts) > 1:
        small = [i for i, x in enumerate(parts) if x < minimum]
        if not small:
            break
        amount = parts.pop(small[0])
        parts[int(np.argmax(parts))] += amount
    # round to cents and keep the sum exact
    parts = [round(x, 2) for x in parts]
    parts[int(np.argmax(parts))] += round(round(total, 2) - sum(parts), 2)
    return parts


def dribble(total, median_item, sigma=0.85, minimum=25.0, cap=1200):
    """Split a total into invoices drawn from a realistic size
    distribution, rather than into a fixed number of equal-ish pieces.

    A construction ledger is not N draws of total/N. It is a long tail of
    small items with a few large ones carrying most of the money, and the
    LINE COUNT is an output of that distribution, not an input. Sizing by
    count is what produces a dataset whose smallest transaction is $500 --
    which tells you immediately that nobody ever reconciled it.

    The sum is exact to the cent, so granularity changes here never move
    a cost code's total and never move a number the detector reports."""
    if total <= 0:
        return []
    parts = []
    remaining = round(total, 2)
    while remaining > 0 and len(parts) < cap:
        # Drawn, never clamped. Clamping the tail to the floor is how a
        # ledger ends up with ninety-three invoices for exactly $25.00,
        # which is the same tell as having no small invoices at all --
        # it just moves it to the other end of the distribution.
        draw = round(float(rng.lognormal(mean=np.log(median_item), sigma=sigma)), 2)
        if draw < minimum:
            continue
        if remaining - draw < minimum:      # last item takes the remainder
            parts.append(remaining)
            remaining = 0.0
            break
        parts.append(draw)
        remaining = round(remaining - draw, 2)
    if remaining > 0:
        parts[int(np.argmax(parts))] = round(parts[int(np.argmax(parts))] + remaining, 2)
    return parts


# ---------------------------------------------------------------------------
# 1. PROJECTS
# ---------------------------------------------------------------------------
# margin_pct is the target gross margin priced into the contract; budgeted
# cost is what is left after it. slip_days is how far behind baseline each
# job is today. pct_complete is derived (section 4) from per-line % complete
# on the schedule of values; billed_to_date is set (section 11) against the
# revised contract with the seeded gap in BILLING_GAPS. retainage_pct is
# what the owner holds back from each draw until completion.
#
# Seeded stories by project:
#   P01 Alderwood    high-tier custom home, finishes underway: allowance
#                    overage with no CO, late appliance selection, a drywall
#                    code running hot.
#   P02 Cascade Ridge remodel + addition: framed through a wet winter,
#                    unforeseen conditions inside the existing house, a
#                    framing draw posted twice, the drywaller not invoicing,
#                    the roofing budget bumped with no paperwork, and
#                    under-billed.
#   P03 Harborview   premium custom home, just past framing: the millwork
#                    story (verbal upgrade, budget quietly revised, sub
#                    contract re-issued, CO to the owner covers a fraction,
#                    sub's insurance lapsed), a pool bought out over budget,
#                    plumbing fixtures picked well over allowance.
#   P04 Fairhaven    standard cottage, nearly done: clean. The control.
#   P05 Timberline   ground-up light commercial: HVAC redesign for the tenant
#                    (CO unsigned for 60 days), excavation sub's license
#                    lapsed, sprinkler code a little hot.
# project_manager: who the "This morning's calls" section is actually
# assigning work to, one level up. Two PMs, not five -- a five-job board is
# run by a super each carrying two or three sites, not one PM per house.
# Priya carries the bigger, harder sites (including the worst job in the
# portfolio); Owen carries the smaller, calmer ones. That split is what
# makes the PM rollup worth having: it is not an even three-two, it is one
# PM's morning looking very different from the other's.
projects = [
    {"project_id": "P01", "name": "The Alderwood Residence", "type": "New Custom Home",
     "finish_tier": "High", "contract_value": 2_100_000, "margin_pct": 0.20, "square_feet": 4_050,
     "start_date": date(2024, 11, 1), "end_date": date(2026, 1, 5),
     "retainage_pct": 0.05, "status": "Active", "project_manager": "Priya Chandra"},
    {"project_id": "P02", "name": "Cascade Ridge Remodel", "type": "Major Renovation + Addition",
     "finish_tier": "Standard", "contract_value": 780_000, "margin_pct": 0.18, "square_feet": 2_640,
     "start_date": date(2024, 10, 15), "end_date": date(2025, 12, 12),
     "retainage_pct": 0.05, "status": "Active", "project_manager": "Owen Vance"},
    {"project_id": "P03", "name": "Harborview Custom Build", "type": "New Custom Home",
     "finish_tier": "Premium", "contract_value": 3_400_000, "margin_pct": 0.22, "square_feet": 5_120,
     "start_date": date(2025, 2, 1), "end_date": date(2026, 8, 15),
     "retainage_pct": 0.05, "status": "Active", "project_manager": "Priya Chandra"},
    {"project_id": "P04", "name": "Fairhaven Cottage", "type": "New Custom Home",
     "finish_tier": "Standard", "contract_value": 650_000, "margin_pct": 0.18, "square_feet": 1_560,
     "start_date": date(2024, 12, 2), "end_date": date(2025, 11, 7),
     "float_days": 16, "retainage_pct": 0.05, "status": "Active", "project_manager": "Owen Vance"},
    {"project_id": "P05", "name": "Timberline Commercial Build", "type": "Light Commercial (ground-up)",
     "finish_tier": "Standard", "contract_value": 1_900_000, "margin_pct": 0.15, "square_feet": 8_200,
     "start_date": date(2024, 12, 1), "end_date": date(2025, 12, 31),
     "retainage_pct": 0.05, "status": "Active",   # RCW 60.30.010: 5% cap, private commercial
     "project_manager": "Priya Chandra"},
]
# float_days: schedule float carried in the baseline. A builder who
# prices a winter start and does not carry contingency days is pricing a
# job they will be late on. Weather comes off the float before it comes
# off the finish date, which is why a job can absorb a fortnight of rain
# and still forecast to its baseline.
DEFAULT_FLOAT_DAYS = 0

# Site complexity multiplies the ground-up divisions (concrete, earthwork,
# exterior, utilities). It is the single biggest reason two houses of the
# same contract value have different cost shapes: Harborview is a
# waterfront slope with a stepped foundation and a retaining system,
# Fairhaven is a flat infill lot.
SITE_FACTOR = {"P01": 1.02, "P02": 0.55, "P03": 1.22, "P04": 0.82, "P05": 1.10}
SITE_DIVISIONS = ("03", "31", "32", "33")

# General conditions are bought by the month, not as a percentage of the
# contract. A job carries a share of a superintendent (a small job shares
# one across two or three sites), plus PM time, plus the site itself --
# trailer, temp power, portables, dumpsters, weekly cleanup. Below, the
# supervision allocation by contract value and the monthly site cost.
GC_SUPER_MONTHLY = 13_000.0          # a loaded superintendent, full time
# A superintendent runs two or three custom homes at once; only the
# biggest job on the board gets most of one. This allocation is the
# single most important number in general conditions and the one most
# often got wrong, in both directions.
GC_SUPER_ALLOCATION = [(900_000, 0.26), (2_000_000, 0.46), (3_000_000, 0.68), (float("inf"), 0.80)]
GC_PM_MONTHLY = 11_000.0             # a loaded PM, full time
GC_PM_SHARE_OF_SUPER = 0.35          # PM time runs across more jobs than a super's
GC_SITE_MONTHLY = {"New Custom Home": 1_350.0, "Major Renovation + Addition": 900.0,
                   "Light Commercial (ground-up)": 1_900.0}       # trailer, temp power, fencing
GC_TEMP_MONTHLY = {"New Custom Home": 900.0, "Major Renovation + Addition": 620.0,
                   "Light Commercial (ground-up)": 1_250.0}       # portables, dumpsters, cleanup
# Two jobs of the same size in the same market do not price general
# conditions to the cent. This is the estimator's judgment on the site.
GC_JOB_FACTOR = {"P01": 1.00, "P02": 1.08, "P03": 1.12, "P04": 0.94, "P05": 1.05}

# Permits are priced off construction valuation and floor area, the way a
# jurisdiction actually assesses them, not off how long the job runs. A
# duration-priced permit fee means a job that slips budgets MORE permit,
# which is backwards, and it puts a bigger permit on a 4,000 sf house
# than on an 8,000 sf commercial building.
PERMIT_RATE_OF_VALUATION = 0.0098
PERMIT_PLAN_REVIEW_PER_SF = 1.30
PERMIT_TYPE_FACTOR = {"New Custom Home": 1.00, "Major Renovation + Addition": 0.55,
                      "Light Commercial (ground-up)": 1.45}
# Third-party special inspections on commercial work: a share of valuation.
TESTING_RATE_OF_VALUATION = 0.0042

BILLING_GAPS = {"P01": 0.015, "P02": 0.11, "P03": -0.02, "P04": 0.01, "P05": 0.015}  # +under, -over
# Retainage in Washington. RCW 60.30.010, added by SSB 5528 and effective
# July 2023, caps retainage on PRIVATE construction at five percent of the
# contract price, and expressly does not apply to single-family
# residential construction of fewer than 12 units. So the commercial job
# carries the statutory 5% and the four houses carry the 10% still common
# on private residential subcontracts. The cap can be waived by agreement,
# but writing 10% into a commercial subcontract without that agreement is
# not an aggressive term, it is an unenforceable one.
RETAINAGE_STATUTORY_CAP = 0.05
SUB_RETENTION_EXEMPT = 0.10      # single-family residential: no statutory cap


def retention_for(proj):
    """Retention rate a sub contract on this job can carry."""
    if proj["type"] == "Light Commercial (ground-up)":
        return RETAINAGE_STATUTORY_CAP
    return SUB_RETENTION_EXEMPT
proj_by_id = {p["project_id"]: p for p in projects}
ALL_PROJECTS = [p["project_id"] for p in projects]

# ---------------------------------------------------------------------------
# 2. COST CODES -- CSI MasterFormat scope for a GC running a custom home
#    start to finish: 24 divisions, 51 codes. Condensed on purpose; a live
#    job-cost system at this size runs 80-150 line items.
#
#    Each code: (description, share, phase_start, phase_end, trade, supplier, gc_buys_material)
#      share      typical share of its division's budget
#      phase_*    the slice of the job's calendar in which its money goes
#                 out, on the milestone template in section 5. Windows
#                 overlap the way trades do on site.
#      trade      the sub trade that performs it (None = self-performed,
#                 material-only, or a fee)
#      supplier   where material for it is bought
#      gc_buys    True where the GC buys the material direct (lumber, doors,
#                 tile, fixtures, appliances); False where the sub
#                 furnishes and installs
# ---------------------------------------------------------------------------
CSI_DIVISIONS = {
    "01": "General Requirements", "02": "Existing Conditions", "03": "Concrete",
    "04": "Masonry", "05": "Metals", "06": "Wood, Plastics & Composites",
    "07": "Thermal & Moisture Protection", "08": "Openings", "09": "Finishes",
    "10": "Specialties", "11": "Equipment", "12": "Furnishings",
    "13": "Special Construction", "14": "Conveying Equipment", "21": "Fire Suppression",
    "22": "Plumbing", "23": "HVAC", "25": "Integrated Automation", "26": "Electrical",
    "27": "Communications", "28": "Electronic Safety & Security", "31": "Earthwork",
    "32": "Exterior Improvements", "33": "Utilities",
}

LUMBER = "Cascade Lumber & Building Supply"
CODE_SPECS = {
    "01": [("Project Management & Supervision", 0.50, 0.00, 1.00, None, None, False),
           ("General Conditions", 0.17, 0.00, 1.00, None, "Peninsula Site Services", False),
           ("Permits & Fees", 0.14, 0.00, 0.08, None, "County Building Department", False),
           ("Temp Facilities & Controls", 0.11, 0.00, 1.00, None, "Peninsula Site Services", False),
           # Commercial only: third-party special inspections are a permit
           # condition on a ground-up commercial shell and are budgeted.
           ("Testing & Special Inspections", 0.08, 0.04, 0.62, None, "Cascade Materials Testing", False)],
    "02": [("Demolition", 0.85, 0.00, 0.10, "Earthwork/Excavation", "Evergreen Equipment Rental", False),
           ("Site Survey", 0.15, 0.00, 0.05, None, "Meridian Land Surveying", False)],
    "03": [("Foundations", 0.55, 0.04, 0.11, "Concrete", "Puget Ready-Mix", False),
           ("Slabs on Grade", 0.30, 0.07, 0.16, "Concrete", "Puget Ready-Mix", False),
           ("Concrete Flatwork", 0.15, 0.86, 0.96, "Concrete", "Puget Ready-Mix", False)],
    "04": [("Stone Veneer", 0.65, 0.40, 0.62, "Masonry", "Northwest Tile & Stone", False),
           ("Brick Masonry", 0.35, 0.38, 0.58, "Masonry", "Northwest Tile & Stone", False)],
    "05": [("Structural Steel", 0.75, 0.11, 0.27, "Structural/Ornamental Metals", "Cascade Steel Supply", False),
           ("Ornamental Metals & Railings", 0.25, 0.78, 0.92, "Structural/Ornamental Metals", "Cascade Steel Supply", False)],
    "06": [("Rough Framing", 0.54, 0.10, 0.29, "Framing", LUMBER, True),
           ("Custom Millwork & Cabinetry", 0.31, 0.30, 0.84, "Millwork/Cabinetry", LUMBER, False),
           ("Finish Carpentry", 0.15, 0.68, 0.88, "Finish Carpentry", LUMBER, True)],
    "07": [("Roofing", 0.55, 0.28, 0.36, "Roofing", "Rainier Roofing Supply", False),
           ("Insulation", 0.25, 0.52, 0.58, "Drywall/Paint", "Northwest Insulation Supply", False),
           ("Waterproofing & Air Barrier", 0.20, 0.09, 0.38, "Roofing", "Rainier Roofing Supply", False)],
    "08": [("Windows", 0.60, 0.24, 0.37, "Windows/Glazing", "Sound Window & Door Distributors", False),
           ("Exterior Doors", 0.20, 0.30, 0.37, "Windows/Glazing", "Sound Window & Door Distributors", False),
           ("Interior Doors & Hardware", 0.20, 0.68, 0.84, "Finish Carpentry", LUMBER, True),
           # Commercial only.
           ("Storefront & Curtainwall", 0.30, 0.26, 0.44, "Windows/Glazing",
            "Sound Window & Door Distributors", False)],
    "09": [("Drywall", 0.30, 0.55, 0.61, "Drywall/Paint", "Evergreen Drywall Supply", False),
           ("Flooring", 0.28, 0.72, 0.88, "Flooring", "Northwest Flooring Wholesale", True),
           ("Tile", 0.22, 0.70, 0.86, "Tile/Stone", "Northwest Tile & Stone", True),
           ("Paint & Wallcovering", 0.20, 0.66, 0.93, "Drywall/Paint", "Evergreen Drywall Supply", False),
           # Commercial only.
           ("Acoustical Ceilings", 0.18, 0.70, 0.86, "Drywall/Paint", "Evergreen Drywall Supply", False)],
    "10": [("Fireplaces", 0.55, 0.45, 0.75, "Masonry", "Northwest Tile & Stone", False),
           ("Closet Systems & Shelving", 0.20, 0.82, 0.92, "Millwork/Cabinetry", LUMBER, False),
           # Commercial only.
           ("Toilet Partitions & Accessories", 0.15, 0.76, 0.90, "Finish Carpentry", "Peninsula Site Services", False),
           ("Signage & Wayfinding", 0.10, 0.88, 0.98, None, "Cascade Sign & Graphics", True)],
    "11": [("Kitchen Appliances", 1.00, 0.80, 0.92, None, "Puget Appliance Distributors", True)],
    "12": [("Countertops & Solid Surfaces", 0.75, 0.80, 0.88, "Tile/Stone", "Northwest Tile & Stone", False),
           ("Window Treatments", 0.25, 0.92, 0.99, None, "Northwest Window Fashions", True)],
    "13": [("Pool & Spa", 0.70, 0.14, 0.94, "Pools/Water Features", "Cascade Pool Supply", False),
           ("Golf Simulator Room", 0.15, 0.80, 0.96, "Low-Voltage/Automation", "Sound Electrical Supply", False),
           ("Water Feature", 0.15, 0.86, 0.97, "Pools/Water Features", "Cascade Pool Supply", False)],
    "14": [("Residential Elevator", 1.00, 0.38, 0.86, "Elevators", "Meridian Elevator Supply", False)],
    "21": [("Fire Sprinkler System", 1.00, 0.42, 0.52, "Fire Protection", "Northwest Plumbing Supply", False)],
    "22": [("Plumbing Rough-In", 0.55, 0.38, 0.50, "Plumbing", "Northwest Plumbing Supply", False),
           ("Plumbing Fixtures", 0.45, 0.84, 0.93, "Plumbing", "Northwest Plumbing Supply", True)],
    "23": [("HVAC Equipment", 0.55, 0.44, 0.90, "HVAC", "Pacific HVAC Supply", False),
           ("HVAC Ductwork & Controls", 0.45, 0.38, 0.50, "HVAC", "Pacific HVAC Supply", False),
           # Commercial only: a building automation system is not a
           # residential smart-home package and is not bought like one.
           ("Building Automation System", 0.22, 0.62, 0.90, "Low-Voltage/Automation",
            "Pacific HVAC Supply", False)],
    "25": [("Smart Home / Automation System", 1.00, 0.44, 0.94, "Low-Voltage/Automation", "Sound Electrical Supply", False)],
    "26": [("Electrical Rough-In", 0.55, 0.38, 0.50, "Electrical", "Sound Electrical Supply", False),
           ("Electrical Fixtures & Panels", 0.45, 0.84, 0.94, "Electrical", "Sound Electrical Supply", True)],
    "27": [("Low-Voltage & AV Wiring", 1.00, 0.42, 0.52, "Low-Voltage/Automation", "Sound Electrical Supply", False)],
    "28": [("Security & Access Control", 1.00, 0.84, 0.94, "Low-Voltage/Automation", "Sound Electrical Supply", False)],
    "31": [("Site Clearing & Grading", 0.35, 0.00, 0.07, "Earthwork/Excavation", "Evergreen Equipment Rental", False),
           ("Excavation", 0.65, 0.02, 0.10, "Earthwork/Excavation", "Evergreen Equipment Rental", False)],
    "32": [("Landscaping", 0.55, 0.92, 1.00, "Landscaping", "Greenscape Nursery & Stone", False),
           ("Hardscape & Pavers", 0.45, 0.88, 0.97, "Landscaping", "Greenscape Nursery & Stone", False)],
    "33": [("Water & Sewer Utilities", 0.60, 0.03, 0.10, "Earthwork/Excavation", "Northwest Plumbing Supply", False),
           ("Site Electrical Utilities", 0.40, 0.04, 0.12, "Electrical", "Sound Electrical Supply", False)],
}

cost_codes = []
for div, specs in CODE_SPECS.items():
    for i, (desc, share, ps, pe, trade, supplier, gc_buys) in enumerate(specs, start=1):
        cost_codes.append({
            "code": f"{div}-{i:02d}", "division": div, "division_name": CSI_DIVISIONS[div],
            "description": desc, "typical_share_of_division": share,
            "phase_start": ps, "phase_end": pe, "trade": trade, "supplier": supplier,
            "gc_buys_material": gc_buys,
        })
cost_codes_df = pd.DataFrame(cost_codes)
save(cost_codes_df, "cost_codes.csv")
code_spec = {c["code"]: c for c in cost_codes}


def code_for(division, description):
    return cost_codes_df[
        (cost_codes_df["division"] == division) & (cost_codes_df["description"] == description)
    ]["code"].iloc[0]


def phase_progress(code, clock):
    """How far through this code's spend window the job's calendar is (0-1)."""
    spec = code_spec[code]
    span = spec["phase_end"] - spec["phase_start"]
    if span <= 0:
        return 1.0 if clock >= spec["phase_end"] else 0.0
    return float(min(max((clock - spec["phase_start"]) / span, 0.0), 1.0))


# ---------------------------------------------------------------------------
# 3. SUBCONTRACTORS
#    allowed_projects restricts where a firm can be used; it exists so the
#    seeded compliance cases land on exactly the jobs their story needs.
#    active_projects is derived at the end from contracts and draws.
# ---------------------------------------------------------------------------
SUB_FIRMS = [
    ("Cedarline Framing Co.", "Framing"),
    ("Summit Concrete & Flatwork", "Concrete"),
    ("Olympic Masonry", "Masonry"),
    ("Pinehurst Roofing", "Roofing"),
    ("Northgate Electric", "Electrical"),
    ("Salish Electric", "Electrical"),
    ("Blue Heron Plumbing", "Plumbing"),
    ("Cascadia HVAC Solutions", "HVAC"),
    ("Alpine Millwork Partners", "Millwork/Cabinetry"),
    ("Puget Sound Cabinetry", "Millwork/Cabinetry"),
    ("Northwest Trim & Door", "Finish Carpentry"),
    ("Sound Stone & Tile", "Tile/Stone"),
    ("Evergreen Drywall & Paint", "Drywall/Paint"),
    ("Summit Flooring Co.", "Flooring"),
    ("Harbor Glass & Glazing", "Windows/Glazing"),
    ("Timberwolf Excavation", "Earthwork/Excavation"),
    ("Bluewater Earthworks", "Earthwork/Excavation"),
    ("Aqua Blue Pool & Spa", "Pools/Water Features"),
    ("Pacific Smart Systems", "Low-Voltage/Automation"),
    ("Greenscape Landscape Design", "Landscaping"),
    ("Ironclad Metals & Railing", "Structural/Ornamental Metals"),
    ("Cascade Fire Protection", "Fire Protection"),
    ("Cascade Lift Systems", "Elevators"),
]

subcontractors = []
for i, (name, trade) in enumerate(SUB_FIRMS, start=1):
    subcontractors.append({
        "sub_id": f"SUB{i:02d}", "name": name, "trade": trade,
        "insurance_expiry": random_date(date(2025, 11, 15), date(2027, 3, 31)),
        "license_expiry": random_date(date(2025, 11, 15), date(2027, 6, 30)),
        "allowed_projects": list(ALL_PROJECTS),
    })
sub_by_name = {s["name"]: s for s in subcontractors}

# Seeded compliance cases (no cost impact, pure risk flags)
sub_by_name["Alpine Millwork Partners"]["insurance_expiry"] = date(2025, 6, 30)   # lapsed, on Harborview
sub_by_name["Alpine Millwork Partners"]["allowed_projects"] = ["P03"]
sub_by_name["Puget Sound Cabinetry"]["allowed_projects"] = ["P01", "P02", "P04", "P05"]
sub_by_name["Timberwolf Excavation"]["license_expiry"] = date(2025, 4, 15)       # lapsed, on Timberline
sub_by_name["Timberwolf Excavation"]["allowed_projects"] = ["P05"]
sub_by_name["Bluewater Earthworks"]["allowed_projects"] = ["P01", "P02", "P03", "P04"]
sub_by_name["Northgate Electric"]["insurance_expiry"] = date(2025, 10, 20)       # expires in 20 days
sub_by_name["Northgate Electric"]["allowed_projects"] = ["P01", "P05"]
sub_by_name["Salish Electric"]["allowed_projects"] = ["P02", "P03", "P04"]

subs_by_trade = {}
for s in subcontractors:
    subs_by_trade.setdefault(s["trade"], []).append(s)


def sub_for(trade, pid):
    candidates = [s for s in subs_by_trade.get(trade, []) if pid in s["allowed_projects"]]
    if not candidates:
        return None
    return candidates[int(rng.integers(0, len(candidates)))]


# ---------------------------------------------------------------------------
# 4. BUDGETS -- budgeted cost per project per code: contract less target
#    margin, split by a division mix shaped for the project's type and
#    finish tier, then across the codes in a division by each code's
#    typical share. Both get a little jitter per project so no two jobs
#    carry the same mix, and no two lines the same dollars. Codes outside
#    the project's scope are dropped, and the division shrinks by their
#    share rather than piling their money onto what is left.
# ---------------------------------------------------------------------------
# Share of hard cost by division, before division 01 -- general conditions
# are bought by the month and carved out separately below, so these weights
# are renormalized against whatever is left after that.
#
# The shape is a residential cost breakdown, not an even spread: structure
# and enclosure (03, 06, 07, 08) carry the job, concrete is 8-11% on a
# ground-up house and more on a difficult site, and special construction
# (a pool, a golf simulator) is a discretionary add that should never
# outweigh the foundation holding the house up.
DIVISION_WEIGHTS = {
    "02": 0.008, "03": 0.112, "04": 0.024, "05": 0.030,
    "06": 0.170, "07": 0.058, "08": 0.066, "09": 0.082, "10": 0.013,
    "11": 0.016, "12": 0.022, "13": 0.026, "14": 0.012, "21": 0.008,
    "22": 0.058, "23": 0.054, "25": 0.013, "26": 0.058, "27": 0.008,
    "28": 0.006, "31": 0.031, "32": 0.040, "33": 0.017,
}
# A ground-up commercial shell is a different building, not a house with a
# different label: steel and slab carry it, wood framing collapses to
# interior partitions and blocking, and the specialties nobody puts in a
# house (signage, toilet accessories, special inspections) appear.
COMMERCIAL_DIVISION_WEIGHTS = {
    "02": 0.010, "03": 0.150, "04": 0.018, "05": 0.115,
    "06": 0.052, "07": 0.072, "08": 0.058, "09": 0.088, "10": 0.030,
    "11": 0.012, "12": 0.010, "21": 0.030, "22": 0.048, "23": 0.086,
    "25": 0.008, "26": 0.072, "27": 0.016, "28": 0.013, "31": 0.040,
    "32": 0.026, "33": 0.028,
}
# A premium house buys a bigger cabinetry package; it does not get its
# structure framed for less. Tier moves the DIVISION up, and the share
# adjustments below move money within it -- reallocating inside a fixed
# division weight funds the millwork upgrade by cannibalising the frame,
# and ends with the premium home framed cheaper per square foot than the
# cottage, which no framer would bid and no estimator would sign.
TIER_ADJUSTMENTS = {
    "Premium":  {"06": 1.70, "07": 1.15, "08": 1.20, "09": 1.20, "10": 1.3, "12": 1.35,
                 "13": 1.25, "14": 1.2, "22": 1.15, "23": 1.15, "25": 1.8, "26": 1.15, "27": 1.5},
    "High":     {"06": 1.25, "09": 1.10, "12": 1.15, "25": 1.3},
    "Standard": {"06": 0.92, "09": 0.95, "12": 0.8, "27": 0.7},
}
TYPE_ADJUSTMENTS = {
    "New Custom Home": {},
    "Major Renovation + Addition": {"02": 6.0, "03": 0.6, "31": 0.4, "32": 0.5, "33": 0.3},
    "Light Commercial (ground-up)": {},   # has its own weight table
}
# Within a division, premium work shifts money toward the custom lines;
# commercial casework is a reception desk and a break room, not a kitchen.
SHARE_ADJUSTMENTS = {
    "Premium": {"06-02": 1.55, "06-01": 0.98, "09-03": 1.2, "09-01": 0.85},
    "High":    {"06-02": 1.1, "06-01": 1.0},
    # Commercial casework is a reception desk and a break room, not a
    # kitchen; the metals budget is structural frame, not railings.
    "Light Commercial (ground-up)": {"06-02": 0.22, "06-01": 1.15, "06-03": 0.5,
                                     "05-01": 1.6, "05-02": 0.35,
                                     "08-01": 0.35, "08-04": 2.2, "09-03": 0.5,
                                     "09-05": 1.6, "12-01": 0.4,
                                     "22-02": 0.6, "23-03": 1.4, "26-02": 0.8},
}
# Scope a project does not have, by code. Dropped, not zeroed.
# Commercial-only scope: no house has special inspections, toilet
# partitions or a monument sign, and the commercial shell has no
# fireplace, closet systems or elevator.
RESIDENTIAL_ONLY_EXCLUSIONS = ["01-05", "08-04", "09-05", "10-03", "10-04", "23-03"]
SCOPE_EXCLUSIONS = {
    ("type", "New Custom Home"): ["02-01"] + RESIDENTIAL_ONLY_EXCLUSIONS,
    ("type", "Major Renovation + Addition"): ["13-01", "13-02", "13-03", "14-01", "21-01"]
                                             + RESIDENTIAL_ONLY_EXCLUSIONS,
    # No tenant shell has a residential appliance package, window
    # treatments or a domestic cabinetry scope. What it does have is
    # storefront, acoustical ceilings and a building automation system,
    # which no house carries.
    ("type", "Light Commercial (ground-up)"): ["02-01", "10-01", "10-02", "11-01", "12-02",
                                               "13-01", "13-02", "13-03", "14-01"],
    ("finish_tier", "High"):     ["13-01", "13-02", "14-01"],
    ("finish_tier", "Standard"): ["13-01", "13-02", "13-03", "14-01", "25-01"],
}


def excluded_codes(proj):
    out = set()
    for (field, value), codes in SCOPE_EXCLUSIONS.items():
        if proj[field] == value:
            out.update(codes)
    if proj["type"] == "Light Commercial (ground-up)":
        # The finish-tier exclusions are written for houses; a commercial
        # shell keeps its automation and specialties whatever tier the
        # interior finishes are.
        out -= {"25-01", "10-03", "10-04", "01-05"}
    return out


def general_conditions_budget(proj):
    """Division 01 priced the way it is actually bought, code by code.

    A superintendent is a monthly cost for as long as the job is open, and
    a small job carries a share of one rather than a whole one -- the same
    super runs two or three cottages at a time. Pricing general conditions
    as a flat percentage of contract is the classic estimating error: it
    under-funds supervision on a long cheap job and over-funds it on a
    short expensive one, and the job that runs late then has nothing
    budgeted for the extra months it stands there.

    The build-up is carried through to the individual codes rather than
    totalled and re-split by percentage, because the split IS the
    estimate: supervision is a rate times months, permits are a rate
    times valuation, and re-splitting the total by fixed shares throws
    away both and quietly prices a $13,000 superintendent at $4,500."""
    months = days_between(proj["start_date"], proj["end_date"]) / 30.44
    allocation = next(a for cap, a in GC_SUPER_ALLOCATION if proj["contract_value"] <= cap)
    factor = GC_JOB_FACTOR.get(proj["project_id"], 1.0)
    valuation = proj["contract_value"]

    out = {
        # Supervision and PM time, by the month.
        "01-01": (GC_SUPER_MONTHLY * allocation
                  + GC_PM_MONTHLY * allocation * GC_PM_SHARE_OF_SUPER) * months * factor,
        # Site: trailer, temp power and water, fencing, safety.
        "01-02": GC_SITE_MONTHLY[proj["type"]] * months * factor,
        # Permits and plan review, off valuation and area.
        "01-03": (valuation * PERMIT_RATE_OF_VALUATION
                  + proj["square_feet"] * PERMIT_PLAN_REVIEW_PER_SF) * PERMIT_TYPE_FACTOR[proj["type"]],
        # Portables, dumpsters, weekly cleanup.
        "01-04": GC_TEMP_MONTHLY[proj["type"]] * months * factor,
    }
    if proj["type"] == "Light Commercial (ground-up)":
        out["01-05"] = valuation * TESTING_RATE_OF_VALUATION
    return {code: round(v, 2) for code, v in out.items()}


baseline_budgets = {}  # (project_id, code) -> original budgeted cost
codes_for_div = cost_codes_df.groupby("division")["code"].apply(list).to_dict()
for proj in projects:
    pid = proj["project_id"]
    budgeted_cost = proj["contract_value"] * (1 - proj["margin_pct"])
    skip = excluded_codes(proj)
    share_adj = dict(SHARE_ADJUSTMENTS.get(proj["finish_tier"], {}))
    share_adj.update(SHARE_ADJUSTMENTS.get(proj["type"], {}))
    weights = (COMMERCIAL_DIVISION_WEIGHTS
               if proj["type"] == "Light Commercial (ground-up)" else DIVISION_WEIGHTS)
    site_factor = SITE_FACTOR[pid]

    # Division 01 first, code by code, off the top.
    gc_budget = {c: v for c, v in general_conditions_budget(proj).items() if c not in skip}
    gc_total = sum(gc_budget.values())
    for c, v in gc_budget.items():
        baseline_budgets[(pid, c)] = v

    # Everything else splits what is left.
    raw = {}
    for div, base_weight in weights.items():
        weight = base_weight
        weight *= TIER_ADJUSTMENTS.get(proj["finish_tier"], {}).get(div, 1.0)
        weight *= TYPE_ADJUSTMENTS.get(proj["type"], {}).get(div, 1.0)
        if div in SITE_DIVISIONS:
            weight *= site_factor
        # Per-job variation wide enough that five jobs are five different
        # cost shapes, not one shape at five sizes.
        weight *= float(rng.normal(1.0, 0.18))
        codes = [c for c in codes_for_div[div] if c not in skip]
        if not codes:
            continue
        shares = {c: code_spec[c]["typical_share_of_division"] * share_adj.get(c, 1.0)
                  * float(rng.normal(1.0, 0.12)) for c in codes}
        in_scope = sum(code_spec[c]["typical_share_of_division"] for c in codes)
        norm = sum(shares.values())
        for c, sh in shares.items():
            raw[c] = weight * in_scope * (sh / norm)
    scale = (budgeted_cost - gc_total) / sum(raw.values())
    for code, share in raw.items():
        baseline_budgets[(pid, code)] = round(share * scale, 2)

# The superintendent reports % complete PER LINE on the schedule of values
# (the pay application); the project's % complete is the cost-weighted
# roll-up of those lines, not a number anyone types in. Each project has a
# calendar "clock" in [0, 1] along its milestone template -- where the job
# would be on its baseline today, less the days it has slipped. A line's
# progress is where that clock sits in the line's phase window, and the
# roll-up g(clock) = sum(budget x line progress) / total budget.


def sov_rollup(pid, clock):
    total = sum(amt for (q, _), amt in baseline_budgets.items() if q == pid)
    earned = sum(amt * phase_progress(code, clock) for (q, code), amt in baseline_budgets.items() if q == pid)
    return earned / total if total else 0.0


# ---------------------------------------------------------------------------
# 5. SCHEDULE MILESTONES -- built before the ledger because draw dates come
#    from the forecast schedule, and before the calendar clock because the
#    schedule is what the clock reads. Fractions are of the job's calendar,
#    laid out like a real sequence: framing is a fifth of the job, dry-in
#    is weeks after framing, the finishes phase is long.
# ---------------------------------------------------------------------------
# (milestone, fraction of baseline calendar, on critical path, weather-exposed)
MILESTONE_TEMPLATE = [
    ("Site Mobilization", 0.00, False, True),
    ("Foundation Complete", 0.10, True, True),
    ("Framing Complete", 0.28, True, True),
    ("Dry-In (Roof/Windows/Doors)", 0.36, True, True),
    ("Rough-Ins Complete (MEP)", 0.50, True, False),
    ("Insulation & Drywall Complete", 0.60, True, False),
    ("Interior Finishes Underway", 0.68, False, False),
    ("Cabinetry & Millwork Install", 0.80, True, False),
    ("Final Mechanical/Electrical/Plumbing", 0.90, True, False),
    # Exterior flatwork, hardscape and landscaping all have to be in
    # before the walkthrough, and in this climate that work is the most
    # weather-sensitive on the job after the shell.
    ("Punch List & Final Walkthrough", 0.97, True, True),
    ("Substantial Completion", 1.00, True, False),
]


# Pacific Northwest: heavier wet-season delay risk Nov-Feb, light Mar/Oct,
# negligible in summer.
WEATHER_WEIGHTS = {11: 5, 12: 6, 1: 6, 2: 4, 3: 2, 10: 2, 4: 1, 5: 0, 6: 0, 7: 0, 8: 0, 9: 1}


def seasonal_weather_delay(start, end):
    """Weather lost over an INTERVAL, not sampled at one date.

    Weather is a property of the calendar and of the work exposed to it.
    Drawing it from the month a template milestone happens to land in
    lets an eighteen-month job skip an entire winter because none of its
    milestone dates fell in one, which is how a waterfront slope ends up
    with fewer weather days than a job half its length."""
    if end <= start:
        return 0
    cursor, total = start, 0.0
    while cursor < end:
        step = min(timedelta(days=30), end - cursor)
        total += WEATHER_WEIGHTS.get(cursor.month, 1) * 0.45 * (step.days / 30.44)
        cursor += step
    return max(0, int(rng.poisson(max(total, 0.0))))


# Slip that is not weather. Whatever part of the job's slip the weather
# draws do not explain lands here, at a milestone that has already passed,
# with the reason the site would give. Each ties to another signal in the
# dataset.
# (milestone it lands on, the reason the site would give, days). These are
# declared; weather is drawn. A job's slip is what the weather actually
# cost plus the named delay, less whatever float the baseline carried --
# you do not get to decide in advance what the winter will take, and a
# generator that treats total slip as the input and backs the weather out
# of it will quietly delete the named cause in a bad winter.
OTHER_DELAY = {
    "P01": ("Rough-Ins Complete (MEP)",
            "late owner lighting/fixture selections held electrical rough-in", 6),
    "P02": ("Framing Complete", "unforeseen conditions in the existing structure", 21),
    "P03": ("Framing Complete", "owner-directed millwork redesign held framing details", 14),
    "P04": (None, None, 0),
    "P05": ("Rough-Ins Complete (MEP)", "HVAC zoning redesign for the tenant (see CO002)", 18),
}

# Weather is drawn first, because the job's slip is the sum of what the
# weather actually cost plus whatever the site is carrying for a named
# reason -- not a number decided in advance. slip_days on the project is
# the target for the named part; where a job has no named cause (P04),
# its slip is simply its weather, and the calendar clock has to agree
# with that or the reported % complete describes a different job from
# the one in the schedule.
weather_draws = {}
for proj in projects:
    pid = proj["project_id"]
    duration = days_between(proj["start_date"], proj["end_date"])
    rows, weather_total, prev = [], 0, proj["start_date"]
    for name, frac, critical, exposed in MILESTONE_TEMPLATE:
        baseline = proj["start_date"] + timedelta(days=int(duration * frac))
        wdelay = seasonal_weather_delay(prev, baseline) if exposed else 0
        prev = baseline
        if pid == "P02" and exposed and baseline.month in (11, 12, 1, 2):
            wdelay += int(rng.integers(6, 12))   # the addition framed through a wet winter
        weather_total += wdelay
        rows.append((name, frac, critical, exposed, baseline, wdelay))
    other_at, _, other_days = OTHER_DELAY[pid]
    if not other_at:
        other_days = 0
    proj.setdefault("float_days", DEFAULT_FLOAT_DAYS)
    float_days = proj["float_days"]
    weather_draws[pid] = (rows, other_days, float_days)
    # Float absorbs delay before the finish date moves.
    proj["slip_days"] = max(0, weather_total + other_days - float_days)

calendar_clock = {}
for proj in projects:
    pid = proj["project_id"]
    duration = days_between(proj["start_date"], proj["end_date"])
    elapsed = days_between(proj["start_date"], AS_OF)
    calendar_clock[pid] = round(min(max((elapsed - proj["slip_days"]) / duration, 0.0), 1.0), 4)
    proj["pct_complete"] = int(round(sov_rollup(pid, calendar_clock[pid]) * 100))

line_pct = {(pid, code): round(phase_progress(code, calendar_clock[pid]) * 100, 1)
            for (pid, code) in baseline_budgets}

milestones = []
forecast_curve = {}   # pid -> (fracs, dates reached or forecast) for date_at()
ms_id = 1
for proj in projects:
    pid = proj["project_id"]
    clock = calendar_clock[pid]
    rows, other_days, float_days = weather_draws[pid]
    other_at, other_reason, _ = OTHER_DELAY[pid]

    cumulative, fracs, dates = 0, [], []
    for name, frac, critical, exposed, baseline, wdelay in rows:
        odelay = other_days if name == other_at else 0
        cumulative += wdelay + odelay
        forecast = baseline + timedelta(days=max(0, cumulative - float_days))
        actual = forecast + timedelta(days=int(rng.integers(-2, 3))) if frac <= clock else None
        if actual is not None and actual < proj["start_date"]:
            actual = proj["start_date"]   # nothing happens before the job starts
        milestones.append({
            "milestone_id": f"M{ms_id:04d}", "project_id": pid, "milestone": name,
            "baseline_date": baseline, "forecast_date": forecast, "actual_date": actual,
            "critical_path": critical, "weather_exposed": exposed,
            "weather_delay_days": wdelay, "other_delay_days": odelay,
            "delay_reason": other_reason if odelay > 0 else None,
            # cumulative % of budgeted cost the baseline plans to have
            # earned by this milestone (cost-weighted, not calendar %)
            "planned_pct_complete": round(sov_rollup(pid, frac), 4),
        })
        fracs.append(frac)
        dates.append(actual or forecast)
        ms_id += 1
    forecast_curve[pid] = (fracs, dates)

save(pd.DataFrame(milestones), "schedule_milestones.csv")


def date_at(pid, frac):
    """Calendar date at which the project reached (or is forecast to reach)
    a given fraction of its calendar, interpolated along its milestones."""
    fracs, dates = forecast_curve[pid]
    start = proj_by_id[pid]["start_date"]
    day_nums = [days_between(start, d) for d in dates]
    return start + timedelta(days=int(round(np.interp(frac, fracs, day_nums))))


# ---------------------------------------------------------------------------
# 6. ALLOWANCES -- owner selections against the allowance in the contract.
#    An allowance is a CONTRACT number, not a cost number: it is what the
#    owner is told the tile is worth, so it carries the job's margin the
#    same way every other contract dollar does. Setting the allowance at
#    the builder's cost line is a mistake that costs real money -- the
#    owner spends the whole allowance, the builder earns nothing on that
#    scope, and on a premium house the allowances can be a tenth of the
#    contract. Here the budget line is the cost, and the allowance is that
#    cost at the job's priced margin.
#    selected_amount blank = not chosen yet; selection_due is when the
#    schedule needs the pick; selection_date is when it was actually made.
# ---------------------------------------------------------------------------
allowances = []


def add_allowance(pid, code, description, selected_multiple, due, chosen):
    base = baseline_budgets[(pid, code)] / (1 - proj_by_id[pid]["margin_pct"])
    allowance = int(base // 500) * 500
    selected = None if selected_multiple is None else round(allowance * selected_multiple / 10) * 10
    allowances.append({
        "allowance_id": f"AL{len(allowances) + 1:03d}", "project_id": pid, "code": code,
        "description": description, "allowance_amount": allowance,
        "selected_amount": selected, "selection_due": due, "selection_date": chosen,
    })
    return allowances[-1]


LIGHTING, PLUMB_FIX = code_for("26", "Electrical Fixtures & Panels"), code_for("22", "Plumbing Fixtures")
APPLIANCES, TILE = code_for("11", "Kitchen Appliances"), code_for("09", "Tile")
FLOORING, COUNTERTOPS = code_for("09", "Flooring"), code_for("12", "Countertops & Solid Surfaces")
AUTOMATION = code_for("25", "Smart Home / Automation System")

# P01 Alderwood
al_p01_lighting = add_allowance("P01", LIGHTING, "Decorative light fixtures", 1.28, date(2025, 6, 15), date(2025, 7, 10))  # +28%, no CO, chosen late
add_allowance("P01", PLUMB_FIX, "Plumbing fixtures", 1.12, date(2025, 5, 1), date(2025, 5, 6))                              # +12%, no CO
add_allowance("P01", APPLIANCES, "Kitchen appliance package", None, date(2025, 9, 5), None)                              # 25 days overdue
add_allowance("P01", TILE, "Tile", 1.06, date(2025, 6, 1), date(2025, 6, 2))                                             # inside tolerance
# P02 Cascade Ridge
add_allowance("P02", FLOORING, "Flooring", None, date(2025, 10, 30), None)                                                # pending, not due
add_allowance("P02", PLUMB_FIX, "Plumbing fixtures", 0.96, date(2025, 3, 15), date(2025, 3, 12))                          # under
# P03 Harborview
al_p03_plumb = add_allowance("P03", PLUMB_FIX, "Plumbing fixtures", 1.60, date(2025, 8, 1), date(2025, 8, 14))            # +60%, CO pending
add_allowance("P03", AUTOMATION, "Smart home / automation package", None, date(2025, 12, 1), None)                       # pending, not due
# P04 Fairhaven -- clean
add_allowance("P04", COUNTERTOPS, "Countertops", 0.94, date(2025, 4, 1), date(2025, 3, 28))
add_allowance("P04", TILE, "Tile", 1.04, date(2025, 4, 1), date(2025, 4, 3))
# P05 Timberline
al_p05_floor = add_allowance("P05", FLOORING, "Tenant flooring", 1.24, date(2025, 7, 15), date(2025, 8, 5))               # +24%, CO approved, chosen late

save(pd.DataFrame(allowances), "allowances.csv")

# ---------------------------------------------------------------------------
# 7. CHANGE ORDERS. Each carries what it costs (cost_amount, added to the
#    budget line) and what it sells for (amount, added to the contract):
#    cost marked up to the job's priced margin. Ordinary ones land on trade
#    lines the job has reached and are approved inside three weeks; the
#    seeded ones are the exposure. A CO written for an allowance overage
#    carries the allowance_id it covers.
# ---------------------------------------------------------------------------
CO_REASONS = ["Owner-directed finish upgrade", "Unforeseen site condition",
              "Design development refinement", "Code-required revision",
              "Owner-directed scope addition", "Field conflict resolved in the owner's favor",
              "Substitution for a long-lead item", "Owner-requested fixture relocation"]
# Credits happen: a scope the owner dropped, a substitution that came in
# cheaper, an allowance returned. A change-order log with no deducts in it
# is a log somebody has tidied.
CO_DEDUCT_REASONS = ["Scope deleted at owner's request", "Substitution credit",
                     "Allowance returned -- scope dropped"]
# Below this a change is not worth its own document; it rides on the
# monthly adjustments change order with everything else that size.
CO_MINIMUM_COST = 1_200.0

# P04 is the control. Nothing is planted on it and nothing random is
# allowed to drift onto it either, because its whole job in this dataset
# is to prove the detector stays quiet on a healthy build. A tool that
# cannot show you a clean job is a tool you stop believing.
CONTROL_PROJECT = "P04"
change_orders = []


def sell_price(pid, cost):
    return round(cost / (1 - proj_by_id[pid]["margin_pct"]), 2)


def add_co(pid, code, cost, submitted, approved, reason, allowance_id=None, sell=None,
           time_extension_days=0):
    change_orders.append({
        "co_id": f"CO{len(change_orders) + 1:03d}", "project_id": pid, "code": code,
        "cost_amount": round(cost, 2), "amount": sell if sell is not None else sell_price(pid, cost),
        "submitted_date": submitted, "approved_date": approved,
        "time_extension_days": time_extension_days,
        "reason": reason, "allowance_id": allowance_id,
    })


MILLWORK = code_for("06", "Custom Millwork & Cabinetry")
# The Harborview story in one number. The budget revision, the re-issued
# sub contract and the spend that follows it all read from this, because
# the whole point of the story is that those three agree with each other
# and the change order to the owner does not. Letting them drift apart
# silently breaks the cross-reference the drift flag depends on.
MILLWORK_UPLIFT = 1.72
HVAC_DUCT = code_for("23", "HVAC Ductwork & Controls")
POOL = code_for("13", "Pool & Spa")
FRAMING = code_for("06", "Rough Framing")
ROOFING = code_for("07", "Roofing")
DRYWALL = code_for("09", "Drywall")
SPRINKLER = code_for("21", "Fire Sprinkler System")
FRAMING_REPAIR = code_for("02", "Demolition")   # the unforeseen-conditions scope on the remodel
EXCAVATION = code_for("31", "Excavation")
SITE_CLEARING = code_for("31", "Site Clearing & Grading")

all_codes = list(baseline_budgets.keys())
# Lines with a planted story keep their paperwork exactly as planted.
seeded_lines = {("P03", MILLWORK), ("P02", ROOFING), ("P01", TILE), ("P02", FRAMING), ("P02", DRYWALL),
                ("P01", DRYWALL), ("P05", SPRINKLER), ("P03", POOL),
                ("P01", EXCAVATION), ("P03", EXCAVATION), ("P05", EXCAVATION),
                ("P01", SITE_CLEARING), ("P03", SITE_CLEARING), ("P05", SITE_CLEARING)}
co_eligible = [(pid, code) for (pid, code) in all_codes
               if line_pct[(pid, code)] > 0 and code_spec[code]["trade"] is not None
               and baseline_budgets[(pid, code)] >= 4_000 and (pid, code) not in seeded_lines]
# The seeded change orders come first so the ones the story names keep
# stable ids however many ordinary changes the job accumulates around
# them.
# Seeded: the owner-side paperwork on the Harborview millwork upgrade, a
# $38,500 change order that covers a fraction of the added cost (see the
# budget revision and the re-issued sub contract below).
add_co("P03", MILLWORK, 38_500 * (1 - proj_by_id["P03"]["margin_pct"]), date(2025, 7, 5), None,
       "Owner-directed cabinetry package upgrade (formal CO submitted, pending signature)",
       sell=38_500.0, time_extension_days=OTHER_DELAY["P03"][2])
# Seeded: Timberline HVAC redesign, unsigned 60 days
add_co("P05", HVAC_DUCT, 21_200 * (1 - proj_by_id["P05"]["margin_pct"]), date(2025, 8, 1), None,
       "HVAC zoning redesign for tenant improvement", sell=21_200.0,
       time_extension_days=OTHER_DELAY["P05"][2])
# The 21 days Cascade Ridge lost to what was behind the old walls is not
# a free delay: it was change-order work, and the paperwork says so.
add_co("P02", FRAMING_REPAIR, 16_800 * (1 - proj_by_id["P02"]["margin_pct"]), date(2025, 2, 10),
       date(2025, 2, 21), "Unforeseen conditions: rot and undersized headers in the existing structure",
       sell=16_800.0, time_extension_days=OTHER_DELAY["P02"][2])
# Seeded: allowance overage COs at cost plus the priced margin -- one
# pending, one approved
# The allowance and the selection are both contract numbers, so the change
# order to the owner is the difference between them, and what it costs the
# builder is that difference less the margin it carries.
def allowance_overage_co(pid, code, allowance, submitted, approved, reason):
    over_sell = allowance["selected_amount"] - allowance["allowance_amount"]
    add_co(pid, code, over_sell * (1 - proj_by_id[pid]["margin_pct"]), submitted, approved,
           reason, allowance["allowance_id"], sell=round(over_sell, 2))


allowance_overage_co("P03", PLUMB_FIX, al_p03_plumb, date(2025, 8, 20), None,
                     "Owner plumbing fixture selections over allowance")
allowance_overage_co("P05", FLOORING, al_p05_floor, date(2025, 8, 12), date(2025, 8, 26),
                     "Tenant flooring selection over allowance")

# Change-order volume on custom work runs at several percent of contract
# across dozens of individual changes, most of them small. A $2M house
# two-thirds built with three change orders on it does not exist.
for idx in rng.choice(len(co_eligible), size=min(240, len(co_eligible)), replace=False):
    pid, code = co_eligible[idx]
    proj = proj_by_id[pid]
    deduct = rng.random() < 0.15
    # Mostly small, with a tail: the log is full of two-thousand-dollar
    # items and carries a handful of real ones.
    pct = float(rng.lognormal(mean=-2.75, sigma=0.9))
    cost = round(baseline_budgets[(pid, code)] * min(pct, 0.35), -1)
    # Nobody papers a three-hundred-dollar change order on its own; those
    # get bundled into the monthly adjustments CO, so they never appear
    # in the log as separate documents.
    if cost < CO_MINIMUM_COST:
        continue
    if deduct:
        cost = -cost
    # A change order is written while the work it changes is live. One
    # dated six months after the frame was closed out is a typo, and it
    # is the same defect the budget-revision dates were fixed for.
    spec = code_spec[code]
    # A change can be written once the scope is close enough to be real
    # -- shop drawings, submittals and owner selections all run ahead of
    # the trade being on site -- and up to a little after it finishes,
    # while backcharges and final quantities settle.
    live_from = max(date_at(pid, max(spec["phase_start"] - 0.12, 0.0)), proj["start_date"])
    live_to = min(date_at(pid, min(spec["phase_end"] + 0.05, 1.0)), AS_OF)
    if live_to <= live_from:
        continue
    submitted = random_date(live_from, live_to)
    # Owners sit on change orders. The lag is right-skewed and some are
    # still unsigned, which is the whole reason the aging check exists.
    if pid == CONTROL_PROJECT:
        # The control job's paperwork is up to date, the same way nothing
        # else is planted on it. Its purpose is to show what a healthy
        # job looks like through this tool, and an unsigned change order
        # aging on it is a planted problem by accident.
        approved = submitted + timedelta(days=int(rng.integers(4, 18)))
        if approved > AS_OF:
            approved = AS_OF - timedelta(days=int(rng.integers(1, 20)))
            submitted = min(submitted, approved - timedelta(days=4))
    elif rng.random() < 0.12:
        approved = None
    else:
        approved = submitted + timedelta(days=int(min(round(rng.lognormal(np.log(11), 0.8)), 120)))
        if approved > AS_OF:
            approved = None
    reason = str(rng.choice(CO_DEDUCT_REASONS if deduct else CO_REASONS))
    # Only a change big enough to move the sequence buys time with it.
    days = (int(rng.integers(2, 12))
            if (not deduct and cost > 12_000 and rng.random() < 0.55) else 0)
    add_co(pid, code, cost, submitted, approved, reason, time_extension_days=days)

save(pd.DataFrame(change_orders), "change_orders.csv")

approved_co_cost = {}
for co in change_orders:
    if co["approved_date"] is not None:
        key = (co["project_id"], co["code"])
        approved_co_cost[key] = approved_co_cost.get(key, 0.0) + co["cost_amount"]

# ---------------------------------------------------------------------------
# 8. BUDGET REVISIONS -- the "quiet drift" table: someone edited the number.
# ---------------------------------------------------------------------------
budget_revisions = []


def add_revision(pid, code, new_amount, rev_date, reason):
    budget_revisions.append({
        "revision_id": f"BR{len(budget_revisions) + 1:03d}", "project_id": pid, "code": code,
        "revised_amount": round(new_amount, 2), "date": rev_date, "reason": reason,
    })


revision_pool = [k for k in all_codes if k not in seeded_lines and k[0] != CONTROL_PROJECT]
for idx in rng.choice(len(revision_pool), size=18, replace=False):
    pid, code = revision_pool[idx]
    proj = proj_by_id[pid]
    # The number somebody edits is the one on the report in front of
    # them, which already includes approved change orders. Revising off
    # the baseline produces "revised" budgets BELOW the value the owner
    # has already agreed to, which is not a revision, it is a mistake.
    base = baseline_budgets[(pid, code)] + approved_co_cost.get((pid, code), 0.0)
    drift_pct = rng.normal(0.03, 0.05)   # small, mostly-positive drift; a few cuts
    # A revision has to land while the line is still live. Revising
    # framing up 2% seven months after framing closed under budget is not
    # a budget revision, it is a typo, and it is the kind of thing that
    # makes a reviewer stop trusting the dates in the whole file.
    window_close = min(date_at(pid, min(code_spec[code]["phase_end"], calendar_clock[pid])), AS_OF)
    rev_start = max(proj["start_date"], date(2024, 10, 1))
    if window_close <= rev_start:
        window_close = min(AS_OF, rev_start + timedelta(days=30))
    add_revision(pid, code, base * (1 + drift_pct),
                 random_date(rev_start, window_close),
                 str(rng.choice(["Vendor pricing update", "Minor scope clarification",
                                 "Material substitution", "Field condition adjustment"])))

# HIGH: Harborview millwork. The owner asked for the upgraded cabinetry
# package on a phone call in April; the sub's contract was re-issued at
# +62% and the budget line edited to match. The change order to the owner
# (CO015) covers a fraction of that.
hv_base = baseline_budgets[("P03", MILLWORK)]
hv_revised = round(hv_base * MILLWORK_UPLIFT, 2)
add_revision("P03", MILLWORK, hv_revised, date(2025, 4, 18),
             "Owner-directed cabinetry package upgrade (verbal, not yet a formal CO)")

# MEDIUM: Cascade Ridge roofing -- the addition sat under tarps through the
# wet winter and the budget was bumped with no paperwork.
add_revision("P02", ROOFING, baseline_budgets[("P02", ROOFING)] * 1.24, date(2025, 1, 22),
             "Extended tarping/weatherproofing during wet-season delay")

# LOW: Alderwood tile, a small reorder.
add_revision("P01", TILE, baseline_budgets[("P01", TILE)] * 1.15, date(2025, 6, 3), "Minor tile overage reorder")

save(pd.DataFrame(budget_revisions), "budget_revisions.csv")

# ---------------------------------------------------------------------------
# 9. COMMITMENTS -- one contract per trade per job, with a scope line for
#    each cost code it covers.
#
#    This is how the paperwork actually works and it matters for reading
#    the data: a concrete sub signs ONE subcontract for the job and it
#    covers foundations, slabs and flatwork as three scope lines. Writing
#    a separate contract per CSI code would mean three certificates of
#    insurance, three retention accounts and three lien waivers from one
#    firm on one site, and it makes the commitment report unreadable --
#    you can no longer answer "what are we committed to with this sub"
#    without summing rows by hand.
#
#    Buyout is the estimator's moment of truth: the contract usually
#    comes in a little under the line where the GC buys some material
#    direct, sometimes over. An approved owner change order on a line
#    becomes a sub change order on that scope line, at cost.
# ---------------------------------------------------------------------------
commitments = []        # one row per (project, sub): the contract
commitment_lines = []   # one row per (contract, cost code): the scope
committed_sub = {}      # (pid, code) -> sub dict, so the ledger pays the same firm

# Where the GC buys the material direct, the sub contract is labor and
# installation only: the lumber package, the tile, the fixtures and the
# appliances are bought from the supplier and delivered to site. A sub
# contract written for the whole line on those codes would mean the GC
# was paying twice for the same material.
LABOR_ONLY_SHARE = 0.42
MILLWORK_REISSUE_PID = "P03"
MILLWORK_REISSUE_DATE = date(2025, 4, 18)   # same day as budget revision BR019

BUYOUT_OVERRIDES = {
    ("P03", MILLWORK): MILLWORK_UPLIFT,   # re-issued for the upgraded package == the budget revision
    ("P03", POOL): 1.18,       # pool sub's bid came in over the estimate; bought out anyway
    ("P02", FRAMING): 0.97,
}
# Below this, the paperwork is a purchase order, not a subcontract with a
# certificate of insurance and a retention clause. Nobody executes a
# subcontract for eleven hundred dollars.
SUBCONTRACT_THRESHOLD = 15_000.0
# And below THIS there is no commitment document at all: the trade does
# the work and sends an invoice. Carrying a signed contract for a
# seven-hundred-dollar scope is the kind of thing that makes a commitment
# report useless to read.
COMMITMENT_FLOOR = 2_500.0


def committed_share(code):
    """Share of the budget line the sub contract covers."""
    return LABOR_ONLY_SHARE if code_spec[code]["gc_buys_material"] else 1.0

# Scope lines first, grouped by the firm that will hold the contract.
scopes = {}   # (pid, sub_id) -> list of line dicts
for (pid, code), budget in baseline_budgets.items():
    trade = code_spec[code]["trade"]
    if trade is None:
        continue
    sub = sub_for(trade, pid)
    if sub is None:
        continue
    signed = max(date_at(pid, max(code_spec[code]["phase_start"] - 0.05, 0.0)), proj_by_id[pid]["start_date"])
    if (pid, code) == (MILLWORK_REISSUE_PID, MILLWORK):
        # The re-issued cabinetry contract is dated to the day the budget
        # was revised: the owner gave the direction verbally, the contract
        # was rewritten against it, and the deposit followed the contract.
        # Dating it later than its own deposit is the sort of thing an
        # auditor opens with.
        signed = MILLWORK_REISSUE_DATE
    if signed > AS_OF:
        continue   # not bought out yet
    # Buyout runs both ways. The estimate is not systematically high, and
    # a log where every single trade comes in under it is not a buyout log,
    # it is an estimator with a thumb on the scale.
    multiple = BUYOUT_OVERRIDES.get((pid, code), float(rng.normal(0.985, 0.105)))
    line_amount = round(budget * committed_share(code) * multiple, 2)
    if line_amount < COMMITMENT_FLOOR:
        continue   # invoiced work, no commitment document
    scopes.setdefault((pid, sub["sub_id"]), []).append({
        "code": code, "line_amount": line_amount, "signed_date": signed,
        "co_amount": round(approved_co_cost.get((pid, code), 0.0), 2), "sub": sub,
    })

for (pid, _sub_id), lines in scopes.items():
    sub = lines[0]["sub"]
    # The contract is signed when the first of its scopes is bought out;
    # later scopes on the same trade are covered by it, which is the
    # point of writing one contract for the trade.
    signed = min(line["signed_date"] for line in lines)
    contract_amount = round(sum(line["line_amount"] for line in lines), 2)
    co_amount = round(sum(line["co_amount"] for line in lines), 2)
    cid = f"SC{len(commitments) + 1:03d}"
    commitments.append({
        "commitment_id": cid, "project_id": pid,
        "sub_id": sub["sub_id"], "sub_name": sub["name"], "trade": sub["trade"],
        "contract_amount": contract_amount,
        "commitment_type": ("Subcontract" if contract_amount >= SUBCONTRACT_THRESHOLD
                            else "Purchase order"),
        "scope_lines": len(lines),
        "co_amount": co_amount,
        "signed_date": signed,
        # A purchase order is the document you write INSTEAD of a
        # subcontract with a retention clause, so it does not have one.
        "retention_pct": (retention_for(proj_by_id[pid])
                          if contract_amount >= SUBCONTRACT_THRESHOLD else 0.0),
    })
    for line in lines:
        committed_sub[(pid, line["code"])] = sub
        commitment_lines.append({
            "commitment_id": cid, "project_id": pid, "code": line["code"],
            "sub_id": sub["sub_id"], "sub_name": sub["name"],
            "line_amount": line["line_amount"], "co_amount": line["co_amount"],
        })

# ---------------------------------------------------------------------------
# 10. COST TRANSACTIONS -- batched draws, dated inside each code's phase
#     window on the project's actual schedule. Field labor is payroll,
#     material comes from the code's supplier (only where the GC buys it
#     direct), sub draws go to the committed sub, permits go to the county.
# ---------------------------------------------------------------------------
PAYROLL = "Ridgeline field crew (payroll)"
EQUIPMENT = "Evergreen Equipment Rental"
# General conditions is not two invoices a year. It is the dribble of
# small stuff that runs all the way through a job -- dumpster pulls,
# portables, fuel, small tools, weekly cleanup, the odd inspection fee --
# and it is most of the line count on a real AP ledger even though it is
# a small share of the money. A ledger with no small transactions in it
# has never been reconciled by anyone.
MISC_SITE_VENDORS = [
    ("Peninsula Site Services", "Equipment Rental"),     # portables, fencing, dumpsters
    ("Evergreen Equipment Rental", "Equipment Rental"),  # lifts, compactors, small plant
    ("Cascade Lumber & Building Supply", "Material"),    # blades, fasteners, stakes, visqueen
    ("Harbor Fuel & Lubricants", "Material"),            # fuel for site plant
    ("Ridgeline field crew (payroll)", "Labor"),         # cleanup and punch labor
]
MISC_CODES = ("01-02", "01-04")   # General Conditions, Temp Facilities & Controls
transactions = []


def add_transaction(pid, code, amount, txn_date, vendor, txn_type):
    transactions.append({
        "transaction_id": f"T{len(transactions) + 1:05d}", "project_id": pid, "code": code,
        "date": txn_date, "amount": round(amount, 2), "vendor": vendor, "type": txn_type,
    })


def own_vendor_and_type(code):
    """Vendor and type for a draw the GC pays itself (not a sub draw)."""
    spec = code_spec[code]
    desc = spec["description"].lower()
    if "permit" in desc:
        return spec["supplier"], "Permits & Fees"
    if "supervision" in desc:
        return PAYROLL, "Labor"
    if "general conditions" in desc or "temp facilities" in desc:
        return spec["supplier"], "Equipment Rental"
    if "survey" in desc or "inspection" in desc or "testing" in desc:
        # A special inspection carried out by the contractor being
        # inspected is not a special inspection.
        return spec["supplier"], "Professional Services"
    if spec["gc_buys_material"]:
        this_type = str(rng.choice(["Material", "Labor"], p=[0.88, 0.12]))
    else:
        this_type = str(rng.choice(["Labor", "Equipment Rental"], p=[0.7, 0.3]))
    if this_type == "Material":
        return spec["supplier"] or LUMBER, this_type
    if this_type == "Labor":
        return PAYROLL, this_type
    return EQUIPMENT, this_type


# Codes running hot or cold, as a multiple of the spend their line's
# progress supports. A committed code's sub draws follow the sub's
# contract, so Harborview millwork runs over pace by what the re-issued
# contract implies without a multiplier.
BURN_MULTIPLIERS = {
    ("P02", ROOFING): 1.30,       # MEDIUM: the tarping story
    ("P02", DRYWALL): 0.55,       # under pace: the drywaller has not invoiced
    ("P01", TILE): 1.12,          # small -- the tile reorder
    ("P05", SPRINKLER): 1.15,     # LOW: tenant layout change added heads
    ("P01", DRYWALL): 1.12,       # LOW: ran a little long
    ("P02", FRAMING): 1.02,       # on pace once the duplicate is stripped
    # The portfolio-level story. Earthwork is over on three separate jobs
    # by a similar margin, which is not three superintendents having a bad
    # month -- it is a haul-off and import rate the estimate has not kept
    # up with. On any one job it is a small flag worth a shrug; across
    # three it is the estimating conversation, and it is the one signal
    # here that no single project view can ever show you.
    ("P01", EXCAVATION): 1.24,
    ("P03", EXCAVATION): 1.20,
    ("P05", EXCAVATION): 1.32,
    ("P01", SITE_CLEARING): 1.16,
    ("P03", SITE_CLEARING): 1.14,
    ("P05", SITE_CLEARING): 1.19,
}
# Work the GC directed and paid for itself on a line a sub otherwise
# furnishes and installs. This is the honest mechanism for a
# furnish-and-install line running over: the sub is inside its contract,
# and the overage is the GC's own crew and rentals doing work nobody
# wrote a change order for. Every entry here is a real site situation,
# and the list is deliberately short -- if a trade line is over and this
# is not why, the money went somewhere the ledger should explain.
GC_DIRECTED_WORK = {
    ("P01", EXCAVATION): "additional haul-off and import at T&M beyond the estimate's allowance",
    ("P03", EXCAVATION): "additional haul-off and import at T&M beyond the estimate's allowance",
    ("P05", EXCAVATION): "additional haul-off and import at T&M beyond the estimate's allowance",
    ("P01", SITE_CLEARING): "additional haul-off and import at T&M beyond the estimate's allowance",
    ("P03", SITE_CLEARING): "additional haul-off and import at T&M beyond the estimate's allowance",
    ("P05", SITE_CLEARING): "additional haul-off and import at T&M beyond the estimate's allowance",
    ("P02", ROOFING): "extended tarping and weather protection during the wet-season delay",
    ("P01", DRYWALL): "extra patching and a third coat after the humidity in the framing",
}

# A sub does not send an invoice for a hundred and sixteen dollars of
# progress. Below this the scope has been started but not yet billed,
# which is the normal state of a trade in its first few weeks on site.
FIRST_SUB_INVOICE = 750.0

DUPLICATE_DRAW = 18_412.50

contract_for = {(line["project_id"], line["code"]): line["line_amount"] + line["co_amount"]
                for line in commitment_lines}

for (pid, code), budget in baseline_budgets.items():
    proj = proj_by_id[pid]
    clock = calendar_clock[pid]
    progress = line_pct[(pid, code)] / 100.0
    if progress <= 0:
        continue
    spec = code_spec[code]
    window_start = max(date_at(pid, spec["phase_start"]), proj["start_date"])
    window_end = min(date_at(pid, min(spec["phase_end"], clock)), AS_OF)
    if window_end < window_start:
        window_end = window_start

    # An approved change order is work that gets done and paid for.
    current_budget = budget + approved_co_cost.get((pid, code), 0.0)
    # The control job gets no planted problems and no random drift large
    # enough to read as one either: its whole purpose is to show what a
    # healthy job looks like through this tool.
    noise = float(rng.normal(1.0, 0.02 if pid == CONTROL_PROJECT else 0.05))
    target = current_budget * progress * BURN_MULTIPLIERS.get((pid, code), noise)
    sub = committed_sub.get((pid, code))

    if sub is not None:
        # The sub bills against its contract as the work progresses, fully
        # once the line is done. A multiplier under 1 is the sub not having
        # invoiced yet. What the GC spends on top (material it buys direct,
        # its own labor, rentals) is whatever the line's total needs beyond
        # the sub -- often nothing.
        contract = contract_for[(pid, code)]
        billed_share = min(BURN_MULTIPLIERS.get((pid, code), 1.0), 1.0)
        if progress >= 0.95:
            # A finished scope is not an invoiced-to-the-cent one. Most
            # closed lines are still short a final invoice, a backcharge
            # or a retention bill, which is why a commitment report and a
            # cost report never agree exactly. Billing to the cent is the
            # exception, reached only at formal closeout.
            billing_noise = (1.0 if rng.random() > 0.85
                             else float(rng.uniform(0.88, 0.995)))
        else:
            billing_noise = float(rng.normal(1.0, 0.03))
        sub_total = min(contract * progress * billed_share * billing_noise, contract)
        if spec["gc_buys_material"]:
            # The GC's own spend on this line is the material package it
            # buys direct, not labor: the sub is already installing it.
            own_total = max(target - contract * progress * billed_share, 0.0)
        elif (pid, code) in GC_DIRECTED_WORK:
            # Whatever the line runs over by is the GC's own work, whether
            # or not the sub's final invoice has landed yet.
            own_total = max(target - sub_total, 0.0)
        else:
            # The sub furnishes and installs. There is no GC labor on this
            # line, and a line bought out under budget simply comes in
            # under budget.
            own_total = 0.0

        if (pid, code) == ("P03", MILLWORK):
            # A deposit on the re-issued contract the week after it was
            # signed, then progress draws as shop drawings and the first
            # runs of casework were released.
            shares = (0.40, 0.20, 0.15, 0.10, 0.08, 0.07)
            draw_dates = [date(2025, 4, 25)] + sorted(
                random_date(date(2025, 6, 1), window_end) for _ in shares[1:]
            )
            for share, d in zip(shares, draw_dates, strict=True):
                add_transaction(pid, code, sub_total * share, d, sub["name"], "Subcontractor")
        elif (pid, code) == ("P02", FRAMING):
            # One legitimate $18,412.50 draw to the framer inside normal
            # spend; its duplicate is added after the loop.
            add_transaction(pid, code, DUPLICATE_DRAW, date(2025, 1, 14), sub["name"], "Subcontractor")
            for amt in split(max(sub_total - DUPLICATE_DRAW, 0.0), 2):
                add_transaction(pid, code, amt, random_date(window_start, window_end), sub["name"], "Subcontractor")
        else:
            # A sub bills monthly against its schedule of values for as
            # long as its scope is open, so the invoice count follows the
            # duration of the work, not the size of the contract. A
            # two-week purchase-order scope is one invoice; a six-month
            # framing package is six.
            # A sub bills monthly, but a large scope also bills in
            # proportion to its size: nobody pays a $135,000 foundation
            # on a single invoice with no schedule of values behind it.
            months = max(1, round(days_between(window_start, window_end) / 30.44))
            by_time = max(1, months)
            by_size = int(np.ceil(sub_total / 40_000))
            n_draws = int(min(max(by_time, by_size), max(1, sub_total // 2_500)))
            if sub_total >= FIRST_SUB_INVOICE:
                for amt in split(sub_total, n_draws, minimum=800.0):
                    add_transaction(pid, code, amt, random_date(window_start, window_end),
                                    sub["name"], "Subcontractor")

        if own_total >= 500:
            if spec["gc_buys_material"]:
                # Material arrives in deliveries and will-call pickups: a
                # few big loads and a steady trickle of small ones.
                for amt in dribble(own_total, median_item=420.0, sigma=1.3, minimum=35.0):
                    add_transaction(pid, code, amt, random_date(window_start, window_end),
                                    spec["supplier"] or LUMBER, "Material")
            else:
                # GC-directed T&M: own crew and rented plant, billed as it
                # is worked rather than in two lumps.
                for amt in dribble(own_total, median_item=700.0, sigma=1.05, minimum=90.0):
                    vendor, this_type = ((PAYROLL, "Labor") if rng.random() < 0.65
                                         else (EQUIPMENT, "Equipment Rental"))
                    add_transaction(pid, code, amt, random_date(window_start, window_end),
                                    vendor, this_type)
        continue

    if code in MISC_CODES:
        # Dumpster pulls, portables, fuel, fasteners, blades, weekly
        # cleanup, the odd inspection fee. Individually trivial, and most
        # of the line count on any real construction ledger.
        for amt in dribble(target, median_item=110.0, sigma=1.2, minimum=25.0):
            vendor, this_type = MISC_SITE_VENDORS[int(rng.integers(0, len(MISC_SITE_VENDORS)))]
            add_transaction(pid, code, amt, random_date(window_start, window_end), vendor, this_type)
        continue

    if "supervision" in spec["description"].lower():
        # Payroll posts every pay period, by labor class, for as long as
        # the job is open. One summary line a month is a ledger nobody
        # can answer a question from: "why is supervision over" needs to
        # be answerable by class and by week, or the only available
        # response is to go and look at timesheets.
        periods = max(2, days_between(window_start, window_end) // 7)
        # Supervision is supervision. Field labor is a different question
        # and a different answer, so it is coded to general conditions
        # rather than buried in the supervision variance.
        classes = [("Ridgeline payroll -- superintendent", 0.74),
                   ("Ridgeline payroll -- project management", 0.26)]
        for vendor, share in classes:
            for amt in split(target * share, int(periods), minimum=350.0):
                add_transaction(pid, code, amt, random_date(window_start, window_end), vendor, "Labor")
        continue

    if "permit" in spec["description"].lower():
        # Plan-review deposit, then the permit at issuance.
        for amt in split(target, 2):
            add_transaction(pid, code, amt, random_date(window_start, window_end),
                            spec["supplier"], "Permits & Fees")
        continue

    for amt in dribble(target, median_item=430.0, sigma=1.2, minimum=45.0):
        vendor, this_type = own_vendor_and_type(code)
        add_transaction(pid, code, amt, random_date(window_start, window_end), vendor, this_type)

# Seeded duplicate posting: the framer's January draw on Cascade Ridge
# entered a second time 13 days later. On the paper ledger this got paid twice.
add_transaction("P02", FRAMING, DUPLICATE_DRAW, date(2025, 1, 27), "Cedarline Framing Co.", "Subcontractor")

transactions_df = pd.DataFrame(transactions).sort_values(["project_id", "code", "date"]).reset_index(drop=True)
transactions_df["transaction_id"] = [f"T{i + 1:05d}" for i in range(len(transactions_df))]
save(transactions_df, "cost_transactions.csv")

# Commitments: what the sub has invoiced is what the ledger shows drawn to
# it on that code (gross, the cost the job has incurred); retention is held
# out of every payment until the scope is closed out.
sub_draws = (
    transactions_df[transactions_df["type"] == "Subcontractor"]
    .groupby(["project_id", "code", "vendor"])["amount"].sum()
)
# Retention is released when the scope closes out -- the sub is off the
# job, the punch is done, the lien release is in. A commitment report
# where every finished trade still shows ten percent held twelve months
# later describes a builder who is not releasing retention, which is a
# real and expensive thing to be, but not what is happening here.
last_draw = (
    transactions_df[transactions_df["type"] == "Subcontractor"]
    .groupby(["project_id", "code", "vendor"])["date"].max()
)
RETENTION_CLOSEOUT_DAYS = 45
# Retention releases at CLOSEOUT, and closeout is a property of the job,
# not of one trade's phase window. A concrete sub does not get their
# retention back ten months before the house tops out: the money is the
# owner's security against punch and warranty, the lender will not fund
# its release, and a commitment report showing 80% of the clause already
# paid out on five active jobs is the first thing that stops a controller
# reading. Early release does happen on a job in punch, trade by trade as
# each one signs off and hands in its lien release, so it is allowed only
# once the job itself is nearly done.
RETENTION_EARLY_RELEASE_PCT = 85
# Scope lines carry what was invoiced against them, because that is where
# the cost coding happens; the contract totals them, because that is where
# the retention and the payment happen.
for line in commitment_lines:
    key = (line["project_id"], line["code"], line["sub_name"])
    line["invoiced_to_date"] = round(float(sub_draws.get(key, 0.0)), 2)
    line["closed_out"] = bool(
        line_pct[(line["project_id"], line["code"])] >= 100
        and proj_by_id[line["project_id"]]["pct_complete"] >= RETENTION_EARLY_RELEASE_PCT
        and key in last_draw.index
        and days_between(pd.Timestamp(last_draw[key]).date(), AS_OF) >= RETENTION_CLOSEOUT_DAYS
    )
lines_by_contract = {}
for line in commitment_lines:
    lines_by_contract.setdefault(line["commitment_id"], []).append(line)
for c in commitments:
    own = lines_by_contract.get(c["commitment_id"], [])
    invoiced = round(sum(line["invoiced_to_date"] for line in own), 2)
    held_gross = round(invoiced * c["retention_pct"], 2)
    # Retention is released scope by scope as each closes out, so a trade
    # with one scope still open keeps retention on that scope only.
    closed_invoiced = sum(line["invoiced_to_date"] for line in own if line["closed_out"])
    released = round(closed_invoiced * c["retention_pct"], 2)
    c["invoiced_to_date"] = invoiced
    c["retention_held"] = round(held_gross - released, 2)
    c["retention_released"] = released
    c["paid_to_date"] = round(invoiced - c["retention_held"], 2)
save(pd.DataFrame(commitments), "commitments.csv")
save(pd.DataFrame(commitment_lines).drop(columns=["closed_out"]), "commitment_lines.csv")

# budgeted_amount is the baseline -- what the job was sold at. current_budget
# is that plus the COST of approved change orders on the line, which is the
# number a variance report has to be run against: measuring spend against a
# baseline the owner has already agreed to change manufactures overruns that
# are not there. The revisions table stays separate on purpose, because an
# informal budget edit is drift, not an approved change.
save(pd.DataFrame(
    [{"project_id": pid, "code": code, "budgeted_amount": amt,
      "approved_co_cost": round(approved_co_cost.get((pid, code), 0.0), 2),
      "current_budget": round(amt + approved_co_cost.get((pid, code), 0.0), 2),
      "pct_complete": line_pct[(pid, code)]}
     for (pid, code), amt in baseline_budgets.items()]
), "project_budgets.csv")

# ---------------------------------------------------------------------------
# 11. PROJECTS, finally -- billing is set against the revised contract
#     (contract plus approved change orders at their sell price) with the
#     seeded gaps.
# ---------------------------------------------------------------------------
approved_sell_by_project = {}
for co in change_orders:
    if co["approved_date"] is not None:
        approved_sell_by_project[co["project_id"]] = approved_sell_by_project.get(co["project_id"], 0.0) + co["amount"]
for proj in projects:
    pid = proj["project_id"]
    revised = proj["contract_value"] + approved_sell_by_project.get(pid, 0.0)
    earned = revised * proj["pct_complete"] / 100.0
    proj["billed_to_date"] = round(earned * (1 - BILLING_GAPS[pid]) / 500) * 500
    proj["retainage_held"] = round(proj["billed_to_date"] * proj["retainage_pct"], 2)
save(pd.DataFrame(projects).drop(columns=["slip_days"]), "projects.csv")

# Subcontractors: active on every job where they hold a signed contract or
# have been paid.
active_on = {}
for c in commitments:
    active_on.setdefault(c["sub_name"], set()).add(c["project_id"])
for vendor, pid in transactions_df[transactions_df["type"] == "Subcontractor"][["vendor", "project_id"]].itertuples(index=False):
    active_on.setdefault(vendor, set()).add(pid)
for s in subcontractors:
    s["active_projects"] = ";".join(sorted(active_on.get(s["name"], [])))
    del s["allowed_projects"]
save(pd.DataFrame(subcontractors), "subcontractors.csv")

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Synthetic dataset generated in {os.path.abspath(OUT_DIR)}:")
    for fname in ["projects.csv", "cost_codes.csv", "project_budgets.csv", "budget_revisions.csv",
                  "schedule_milestones.csv", "allowances.csv", "change_orders.csv",
                  "commitments.csv", "commitment_lines.csv", "cost_transactions.csv",
                  "subcontractors.csv"]:
        df = pd.read_csv(os.path.join(OUT_DIR, fname))
        print(f"  {fname:26s} {len(df):5d} rows")
