"""
Detection logic for the Ridgeline Custom Homes job-cost & schedule-risk
dashboard. Single source of truth for every threshold used in the app and
checked by tests/test_detection.py.

Severity (LOW/MEDIUM/HIGH, or unflagged) always comes from two things
together, never one alone: how far out of band the number is, and whether
the dollars are material. A big % swing on a trivial-dollar code should
not outrank a smaller % move on a $600K code.

The thresholds below were set from running jobs, not fitted to data. They
are the numbers I would want a call about on a 5-job builder; a different
business should calibrate them to its own history before trusting them.
config/thresholds.json, if present, overrides any of them (see
load_thresholds).

Every flag carries a template-generated, one-line explanation. It is
deterministic and unit-testable on purpose -- a flag nobody can read at a
glance is not useful to someone triaging five live jobs on a Monday.
"""

import copy
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, fields, replace
from datetime import date

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dataset_config import AS_OF as DATASET_AS_OF  # noqa: E402  -- the dataset's fixed "today"

DATA_DIR = os.path.join(HERE, "..", "data")
GENERATOR = os.path.join(HERE, "generate_data.py")
# Threshold overrides. The path can be redirected with an environment
# variable so a run can be pinned to the shipped defaults without moving
# anyone's config file out of the way -- which is what the test suite
# does, since it asserts the defaults and would otherwise go red the
# moment a reader followed config/thresholds.example.json's own
# instructions.
CONFIG_PATH = os.environ.get(
    "JOBCOST_THRESHOLDS", os.path.join(HERE, "..", "config", "thresholds.json")
)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
# Materiality floor for burn-rate and buyout flags: the larger of a flat
# $5K and a share of the project's contract value. $5K is roughly the
# smallest sub draw worth a phone call; on a $3.4M job that same $5K is
# noise, so the floor scales with the contract ($8.5K there). The share can
# differ by project type -- commercial work carries more noise per dollar.
DOLLAR_FLOOR = 5_000
MATERIALITY_PCT_OF_CONTRACT = {"default": 0.0025, "Light Commercial (ground-up)": 0.0035}

# Budget-revision drift and allowance overages are single deliberate
# records, not an aggregate of noisy transactions, so their floors are flat
# de-minimis filters rather than materiality tests.
BUDGET_DRIFT_DOLLAR_FLOOR = 1_500
ALLOWANCE_DOLLAR_FLOOR = 2_500

# Transaction-level checks. A single draw that is 25%+ of its cost code's
# whole budget gets named in the explanation; two draws to the same vendor
# for the same amount inside 45 days is a duplicate posting until proven
# otherwise.
LARGE_DRAW_SHARE = 0.25
DUPLICATE_WINDOW_DAYS = 45

PCT_BANDS = {
    "LOW": (0.10, 0.20),
    "MEDIUM": (0.20, 0.35),
    "HIGH": (0.35, float("inf")),
}

# A code well under pace once it is past the middle of its window is either
# invoices that have not landed or work that has not happened. Reported at
# LOW only -- it is a question, not an exposure.
UNDER_PACE_PCT = -0.30
UNDER_PACE_MIN_PROGRESS = 0.50

SEVERITY_ORDER = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

# SPI/CPI get their own bands: EVM convention treats 0.90+ as on-track and
# nothing above 0.60 as critical -- the generic 10/20/35% bands would
# redline any project that's merely a little behind pace.
EVM_GAP_BANDS = {
    "LOW": (0.10, 0.25),
    "MEDIUM": (0.25, 0.40),
    "HIGH": (0.40, float("inf")),
}

# Slip is measured as TERMINAL slip (final critical-path milestone's
# forecast vs. baseline -- i.e. "how late is this project now forecast to
# finish"), not summed across all milestones, which double-counts: a delay
# at milestone 3 already carries into milestone 4's forecast.
SLIP_DAYS_BANDS = {
    "LOW": (10, 21),
    "MEDIUM": (21, 35),
    "HIGH": (35, float("inf")),
}

# Dollars promote severity. Exposure at this many multiples of the
# project's materiality floor cannot read below the level named, so a
# modest percentage on a very large line still surfaces: 3% over on a
# $600K sitework package is a bigger conversation than 40% over on a
# $9K line, and the percentage band alone would say the opposite.
DOLLAR_PROMOTION_TIERS = {"MEDIUM": 10.0, "HIGH": 25.0}

# Change orders: a month unsigned is a nag, two is exposure. The gate is
# the floor of the LOW band, so lowering one in a config cannot open a
# gap where an aged CO scores NONE. A CO under the dollar floor is capped
# at LOW however old it is -- a $3K CO sitting unsigned is admin, not risk.
CO_AGING_DAYS_BANDS = {
    "LOW": (30, 46),
    "MEDIUM": (46, 61),
    "HIGH": (61, float("inf")),
}
CO_AGING_THRESHOLD_DAYS = CO_AGING_DAYS_BANDS["LOW"][0]

# Owner selections that are late against the schedule's need-by date. A
# selection that was eventually made late is reported at LOW once it was
# more than a week late; a few days is noise.
SELECTION_LATE_GRACE_DAYS = 7
SELECTION_OVERDUE_DAYS_BANDS = {
    "LOW": (1, 14),
    "MEDIUM": (14, 45),
    "HIGH": (45, float("inf")),
}

# Billing position, as a share of what the job has EARNED (revised contract
# x % complete), not of contract value -- the same gap is the same problem
# on a $780K job and a $3.4M one. Under-billing is cash the job has earned
# and is not collecting. Over-billing is the side that hurts later: it is
# usually one job funding another, and it unwinds at closeout.
UNDER_BILLING_BANDS = {
    "LOW": (0.03, 0.06),
    "MEDIUM": (0.06, 0.12),
    "HIGH": (0.12, float("inf")),
}
OVER_BILLING_BANDS = {
    "LOW": (0.05, 0.10),
    "MEDIUM": (0.10, 0.20),
    "HIGH": (0.20, float("inf")),
}

# Forecast at completion. Projected margin is measured against the margin
# the job was priced at, in percentage points of revised contract.
MARGIN_EROSION_BANDS = {
    "LOW": (0.02, 0.04),
    "MEDIUM": (0.04, 0.08),
    "HIGH": (0.08, float("inf")),
}
# The super's % complete vs. cost-to-cost % complete (spend / forecast at
# completion). When they disagree by more than this, one of them is wrong,
# and it is worth knowing which before the next draw goes out.
PROGRESS_GAP_BANDS = {
    "LOW": (0.06, 0.10),
    "MEDIUM": (0.10, 0.20),
    "HIGH": (0.20, float("inf")),
}
# Per-code forecast: extrapolating spend / progress is only trusted once a
# code is this far into its window; before that, the forecast is the
# larger of budget and commitment.
FORECAST_MIN_PROGRESS = 0.25
FORECAST_EXTRAPOLATION_CAP = 2.0
# A line reported this far along is treated as complete for forecasting:
# its cost is its spend plus what is still owed on the sub contract.
FORECAST_LINE_COMPLETE = 0.95
# Spend on a line reported 0% complete, or on a line with no budget at all,
# is flagged on dollars alone once it clears the floor -- there is no pace
# to measure it against.
UNSTARTED_LINE_SEVERITY = "MEDIUM"

# A sub whose insurance or license expires inside this window gets a LOW
# "renewal due" flag while still current. A lapse is HIGH from day one --
# there is no such thing as a little uninsured on an active site.
COMPLIANCE_LOOKAHEAD_DAYS = 30

CSV_FILES = [
    "projects.csv", "cost_codes.csv", "project_budgets.csv", "budget_revisions.csv",
    "schedule_milestones.csv", "allowances.csv", "change_orders.csv", "commitments.csv",
    "commitment_lines.csv", "cost_transactions.csv", "subcontractors.csv",
]

_OVERRIDABLE = [
    "DOLLAR_FLOOR", "MATERIALITY_PCT_OF_CONTRACT", "BUDGET_DRIFT_DOLLAR_FLOOR",
    "ALLOWANCE_DOLLAR_FLOOR", "LARGE_DRAW_SHARE", "DUPLICATE_WINDOW_DAYS", "PCT_BANDS",
    "UNDER_PACE_PCT", "UNDER_PACE_MIN_PROGRESS", "EVM_GAP_BANDS", "SLIP_DAYS_BANDS",
    "CO_AGING_DAYS_BANDS", "SELECTION_OVERDUE_DAYS_BANDS",
    "SELECTION_LATE_GRACE_DAYS",
    "UNDER_BILLING_BANDS", "OVER_BILLING_BANDS", "MARGIN_EROSION_BANDS", "PROGRESS_GAP_BANDS",
    "FORECAST_MIN_PROGRESS", "FORECAST_EXTRAPOLATION_CAP", "FORECAST_LINE_COMPLETE",
    "DOLLAR_PROMOTION_TIERS",
    "UNSTARTED_LINE_SEVERITY", "COMPLIANCE_LOOKAHEAD_DAYS",
]


def _parse_bands(key, value):
    """JSON has no infinity; accept null or "inf" for an open-ended band.
    Validated per entry: a band that loads but is malformed would crash on
    the first flag instead of at startup, which is the worst place to find
    out that a config file has a typo in it."""
    if not isinstance(value, dict):
        return value
    if not key.endswith("_BANDS"):
        # A plain name -> number map (materiality shares, promotion tiers).
        bad = [k for k, v in value.items() if isinstance(v, bool) or not isinstance(v, (int, float))]
        if bad:
            raise ValueError(f"{key}: value(s) {bad} must be numbers")
        return {k: float(v) for k, v in value.items()}
    if not all(isinstance(v, (list, tuple)) for v in value.values()):
        bad = [k for k, v in value.items() if not isinstance(v, (list, tuple))]
        raise ValueError(f"{key}: band(s) {bad} must be [low, high], got a non-list")
    parsed = {}
    for level, pair in value.items():
        if len(pair) != 2:
            raise ValueError(f"{key}.{level}: expected [low, high], got {pair!r}")
        lo = float(pair[0])
        hi = float("inf") if pair[1] in (None, "inf") else float(pair[1])
        if not lo < hi:
            raise ValueError(f"{key}.{level}: low ({lo}) must be below high ({hi})")
        parsed[level] = (lo, hi)
    return parsed


def _validate_bands(key: str, bands: dict) -> None:
    """A severity band set has to be complete and contiguous, or a value
    falls in the gap and silently reads CLEAR."""
    missing = [lvl for lvl in ("LOW", "MEDIUM", "HIGH") if lvl not in bands]
    if missing:
        raise ValueError(f"{key}: missing band(s) {missing}")
    if bands["LOW"][1] != bands["MEDIUM"][0] or bands["MEDIUM"][1] != bands["HIGH"][0]:
        raise ValueError(
            f"{key}: bands must be contiguous, got LOW {bands['LOW']}, "
            f"MEDIUM {bands['MEDIUM']}, HIGH {bands['HIGH']}"
        )


def _validated_override(key: str, current, value):
    """Check one override against the value it replaces -- same shape,
    numbers where numbers are expected, bands complete and contiguous --
    and return the value to store. A dict override merges over the
    current dict, so a file that sets only PCT_BANDS.HIGH keeps the other
    two bands."""
    if key not in _OVERRIDABLE:
        raise KeyError(f"{key} is not a threshold that can be overridden")
    if isinstance(current, dict) and isinstance(value, dict):
        merged = dict(current)
        merged.update(_parse_bands(key, value))
        value = merged
    elif isinstance(current, dict) != isinstance(value, dict):
        raise TypeError(f"{key}: expected {type(current).__name__}, got {type(value).__name__}")
    elif isinstance(current, bool) or not isinstance(current, (int, float)):
        if not isinstance(value, type(current)):
            raise TypeError(f"{key}: expected {type(current).__name__}, got {type(value).__name__}")
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{key}: expected a number, got {type(value).__name__}")
        if value < 0:
            raise ValueError(f"{key}: must not be negative, got {value}")
    if key.endswith("_BANDS"):
        _validate_bands(key, value)
    return value


@dataclass(frozen=True)
class Thresholds:
    """One complete set of thresholds, fixed once built.

    The constants above are the shipped defaults and where the reasoning
    lives; an instance of this class is what every detector reads. The
    split matters because Streamlit runs every viewer of the dashboard as
    a thread in one process: a threshold held in a module global is
    shared by all of them, so one viewer dragging a slider would move
    another viewer's flags mid-read. Each session builds its own instance
    from its own sliders and hands it down, and nothing here is ever
    written back to the module.

    Field names are the constant names in lower case, so an override file
    or the sidebar can address a threshold by its documented name."""

    dollar_floor: float = DOLLAR_FLOOR
    materiality_pct_of_contract: dict = field(default_factory=lambda: dict(MATERIALITY_PCT_OF_CONTRACT))
    budget_drift_dollar_floor: float = BUDGET_DRIFT_DOLLAR_FLOOR
    allowance_dollar_floor: float = ALLOWANCE_DOLLAR_FLOOR
    large_draw_share: float = LARGE_DRAW_SHARE
    duplicate_window_days: int = DUPLICATE_WINDOW_DAYS
    pct_bands: dict = field(default_factory=lambda: dict(PCT_BANDS))
    under_pace_pct: float = UNDER_PACE_PCT
    under_pace_min_progress: float = UNDER_PACE_MIN_PROGRESS
    evm_gap_bands: dict = field(default_factory=lambda: dict(EVM_GAP_BANDS))
    slip_days_bands: dict = field(default_factory=lambda: dict(SLIP_DAYS_BANDS))
    co_aging_days_bands: dict = field(default_factory=lambda: dict(CO_AGING_DAYS_BANDS))
    selection_overdue_days_bands: dict = field(default_factory=lambda: dict(SELECTION_OVERDUE_DAYS_BANDS))
    selection_late_grace_days: int = SELECTION_LATE_GRACE_DAYS
    under_billing_bands: dict = field(default_factory=lambda: dict(UNDER_BILLING_BANDS))
    over_billing_bands: dict = field(default_factory=lambda: dict(OVER_BILLING_BANDS))
    margin_erosion_bands: dict = field(default_factory=lambda: dict(MARGIN_EROSION_BANDS))
    progress_gap_bands: dict = field(default_factory=lambda: dict(PROGRESS_GAP_BANDS))
    forecast_min_progress: float = FORECAST_MIN_PROGRESS
    forecast_extrapolation_cap: float = FORECAST_EXTRAPOLATION_CAP
    forecast_line_complete: float = FORECAST_LINE_COMPLETE
    dollar_promotion_tiers: dict = field(default_factory=lambda: dict(DOLLAR_PROMOTION_TIERS))
    unstarted_line_severity: str = UNSTARTED_LINE_SEVERITY
    compliance_lookahead_days: int = COMPLIANCE_LOOKAHEAD_DAYS

    def __post_init__(self):
        # Every dict-valued field is this instance's own copy, so two
        # instances never share a band table however they were built.
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, dict):
                object.__setattr__(self, f.name, copy.deepcopy(value))

    @property
    def co_aging_threshold_days(self) -> float:
        """The gate for change-order aging is the floor of its LOW band, so
        lowering the band in a config cannot open a gap where an aged CO
        scores NONE."""
        return self.co_aging_days_bands["LOW"][0]

    def as_dict(self) -> dict:
        """The thresholds keyed by their documented constant names -- the
        same shape with_overrides() and config/thresholds.json use."""
        return {name: copy.deepcopy(getattr(self, name.lower())) for name in _OVERRIDABLE}

    def with_overrides(self, overrides: dict) -> "Thresholds":
        """A new instance with `overrides` applied on top of this one. Keys
        are the constant names; bands are {"LOW": [lo, hi], ...} with null
        or "inf" for an open top. Unknown keys and malformed values raise
        rather than being ignored, because a threshold that silently did
        not apply is worse than one that was never set."""
        changes = {}
        for key, value in overrides.items():
            if key.startswith("_"):
                continue   # comments
            if key not in _OVERRIDABLE:
                raise KeyError(f"{key} is not a threshold that can be overridden")
            current = getattr(self, key.lower())
            changes[key.lower()] = _validated_override(key, current, value)
        return replace(self, **changes)


def load_thresholds(path: str = CONFIG_PATH, base: Thresholds | None = None) -> Thresholds:
    """The shipped thresholds with the overrides in a JSON file, if it
    exists, applied on top. Called once at import to build this
    deployment's DEFAULT_THRESHOLDS; never mutates anything."""
    base = Thresholds() if base is None else base
    if not os.path.exists(path):
        return base
    with open(path) as fh:
        overrides = json.load(fh)
    return base.with_overrides(overrides)


# This deployment's configured baseline: the code defaults plus whatever
# config/thresholds.json set. Every detector reads this when it is not
# handed a Thresholds of its own, and the dashboard layers each session's
# slider values on top of it with with_overrides() -- so a slider dragged
# back down lands exactly here, not on whatever the previous rerun left.
DEFAULT_THRESHOLDS = load_thresholds()


def _thresholds(thresholds: Thresholds | None) -> Thresholds:
    return DEFAULT_THRESHOLDS if thresholds is None else thresholds


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def ensure_data(data_dir: str = DATA_DIR) -> None:
    """Generate the synthetic dataset into data_dir if any CSV is missing.
    The generator is seeded, so the result is what a committed copy would
    have been -- which is why data/ is not committed."""
    paths = [os.path.join(data_dir, f) for f in CSV_FILES]
    missing = [p for p in paths if not os.path.exists(p)]
    # Stale counts as missing. Someone who runs the app, pulls a change to
    # the generator and runs it again should not silently get yesterday's
    # dataset and wonder why the numbers do not match the README.
    stale = (not missing
             and os.path.getmtime(GENERATOR) > min(os.path.getmtime(p) for p in paths))
    if missing or stale:
        subprocess.run([sys.executable, GENERATOR, data_dir], check=True, capture_output=True)


def load_data(data_dir: str = DATA_DIR) -> dict:
    """Load all 11 CSVs into a dict of DataFrames, with dates parsed.
    Generates the dataset first if it is not there."""
    ensure_data(data_dir)

    def rd(name, date_cols=None, dtype=None):
        df = pd.read_csv(os.path.join(data_dir, name), dtype=dtype)
        for c in date_cols or []:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        return df

    return {
        "projects": rd("projects.csv", ["start_date", "end_date"]),
        "cost_codes": rd("cost_codes.csv", dtype={"division": str}),
        "project_budgets": rd("project_budgets.csv"),
        "budget_revisions": rd("budget_revisions.csv", ["date"]),
        "schedule_milestones": rd("schedule_milestones.csv", ["baseline_date", "forecast_date", "actual_date"]),
        "allowances": rd("allowances.csv", ["selection_due", "selection_date"]),
        "change_orders": rd("change_orders.csv", ["submitted_date", "approved_date"]),
        "commitments": rd("commitments.csv", ["signed_date"]),
        "commitment_lines": rd("commitment_lines.csv"),
        "cost_transactions": rd("cost_transactions.csv", ["date"]),
        "subcontractors": rd("subcontractors.csv", ["insurance_expiry", "license_expiry"]),
    }


def _active_projects(row) -> list:
    if pd.isna(row["active_projects"]):
        return []
    return [p for p in str(row["active_projects"]).split(";") if p]


def subs_on_project(data: dict, project_id: str) -> pd.DataFrame:
    """Subcontractors active on a project: contracted, paid, or both."""
    subs = data["subcontractors"]
    if subs.empty:
        return subs
    return subs[subs.apply(lambda r: project_id in _active_projects(r), axis=1)]


# ---------------------------------------------------------------------------
# Severity scoring primitives
# ---------------------------------------------------------------------------
def _band_for(value: float, bands: dict) -> str:
    for label in ("HIGH", "MEDIUM", "LOW"):
        lo, hi = bands[label]
        in_band = value >= lo if hi == float("inf") else lo <= value < hi
        if in_band:
            return label
    return "NONE"


def pct_severity(variance_pct: float, thresholds: Thresholds | None = None) -> str:
    return _band_for(abs(variance_pct), _thresholds(thresholds).pct_bands)


def dollar_floor_for(contract_value: float, project_type: str = "default",
                     thresholds: Thresholds | None = None) -> float:
    """Materiality floor for a project: the larger of the flat floor and a
    share of contract value (share by project type)."""
    t = _thresholds(thresholds)
    shares = t.materiality_pct_of_contract
    share = shares.get(project_type, shares["default"])
    return max(t.dollar_floor, share * float(contract_value))


def combined_severity(pct_sev: str, dollar_exposure: float, floor: float = None,
                      thresholds: Thresholds | None = None) -> str:
    """Severity from the percentage and the dollars together, never one
    alone.

    The floor gates: below the project's materiality floor nothing is
    raised, however bad the percentage looks, because a 90% overrun on a
    $900 line is not a phone call. Above it the percentage band applies,
    and large dollars promote it: exposure at DOLLAR_PROMOTION_TIERS
    multiples of the floor cannot read below MEDIUM or HIGH respectively,
    so a small percentage swing on a very large line is not buried under
    a large percentage swing on a small one."""
    t = _thresholds(thresholds)
    floor = t.dollar_floor if floor is None else floor
    if dollar_exposure < floor:
        return "NONE"
    sev = pct_sev
    multiple = dollar_exposure / floor if floor > 0 else float("inf")
    for level in ("HIGH", "MEDIUM"):
        if multiple >= t.dollar_promotion_tiers[level] and SEVERITY_ORDER[sev] < SEVERITY_ORDER[level]:
            return level
    return sev


def _worst(sevs) -> str:
    sevs = list(sevs)
    return max(sevs, key=lambda s: SEVERITY_ORDER[s]) if sevs else "NONE"


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
@dataclass
class AnomalyFlag:
    project_id: str
    code: str
    severity: str
    variance_amount: float
    variance_pct: float
    explanation: str
    kind: str = "burn-rate"     # burn-rate | under-pace | duplicate | drift | buyout | overpaid | pattern


# ---------------------------------------------------------------------------
# Duplicate postings (needed by effective_budgets, so defined first)
# ---------------------------------------------------------------------------
def detect_duplicate_postings(data: dict, window_days: int = None,
                              thresholds: Thresholds | None = None) -> list:
    """Same project, same vendor, same amount, inside a short window: almost
    always the same invoice posted twice, and on a paper ledger it gets
    paid twice. Severity is by dollar amount -- a duplicate is a straight
    loss until it is recovered, so % variance means nothing here."""
    t = _thresholds(thresholds)
    window_days = t.duplicate_window_days if window_days is None else window_days
    tx = data["cost_transactions"].sort_values("date")
    descriptions = data["cost_codes"].set_index("code")["description"].to_dict()
    floors = {r["project_id"]: dollar_floor_for(r["contract_value"], r["type"], t)
              for _, r in data["projects"].iterrows()}
    flags = []
    # Same project, same vendor, same cost code, same amount, inside the
    # window. The cost code is part of the key on purpose: one sub billing
    # the same round number against two different codes is two scopes, not
    # a double payment, and pairing those would put a false accusation in
    # front of a subcontractor.
    for (pid, vendor, code, amount), grp in tx.groupby(["project_id", "vendor", "code", "amount"]):
        if len(grp) < 2 or amount < floors.get(pid, t.dollar_floor):
            continue
        dates = grp["date"].tolist()
        for i in range(1, len(dates)):
            gap = (dates[i] - dates[i - 1]).days
            if gap <= window_days:
                first, second = grp.iloc[i - 1], grp.iloc[i]
                sev = "HIGH" if amount >= 3 * floors.get(pid, t.dollar_floor) else "MEDIUM"
                codes = f"cost code {code} {descriptions.get(code, '')}"
                flags.append(AnomalyFlag(
                    project_id=pid, code=second["code"], severity=sev,
                    variance_amount=amount, variance_pct=0.0, kind="duplicate",
                    explanation=(
                        f"Two draws to {vendor} for ${amount:,.2f} on {pid}, {gap} days apart "
                        f"({first['transaction_id']} {first['date'].date()} and "
                        f"{second['transaction_id']} {second['date'].date()}, {codes}). "
                        f"Likely the same invoice posted twice; confirm before the next pay run."
                    ),
                ))
    flags.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], -f.variance_amount))
    return flags


# ---------------------------------------------------------------------------
# Cost-code level: budgets, commitments, actuals, variance, forecast
# ---------------------------------------------------------------------------
def effective_budgets(data: dict, thresholds: Thresholds | None = None) -> pd.DataFrame:
    """Per (project, code): current budget, commitment, spend to date,
    expected spend for the line's reported progress, variance, and a
    forecast at completion.

    - current_budget = original budget + the COST of approved change orders
      on the code (their sell price goes to the contract, not the budget).
      Informal revisions (budget_revisions.csv) are NOT applied here
      -- that drift is its own signal, and applying it would let a quietly
      inflated budget flatter its own burn rate.
    - expected_spend_to_date = current_budget x the line's % complete from
      the schedule of values (project_budgets.pct_complete -- the number
      the super reports per line on the pay application). Framing on a
      job past dry-in is 100%; landscaping on the same job is 0%. A flat
      project-wide % complete would call both of those anomalies.
    - Suspected duplicate postings are taken out of spend before anything
      is judged, so the same dollars are not flagged twice.
    - forecast_at_completion per code: the larger of current budget and
      commitment, or the spend extrapolated over progress once the line is
      far enough along to trust that (capped)."""
    t = _thresholds(thresholds)
    tx = data["cost_transactions"]
    budgets = data["project_budgets"]
    projects = data["projects"]
    cos = data["change_orders"]

    # Spend or a sub contract coded to a line the job has no budget row
    # for is still real cost. Those keys enter here as zero-budget lines
    # so the forecast, the projected margin and cost-to-cost progress all
    # see the money. Left out, a miscoded invoice or a scope nobody
    # priced is invisible to every number an owner actually reads.
    seen = pd.concat([tx[["project_id", "code"]],
                      data["commitment_lines"][["project_id", "code"]]]).drop_duplicates()
    known = set(zip(budgets["project_id"], budgets["code"], strict=True))
    extra_keys = [(p_, c_) for p_, c_ in zip(seen["project_id"], seen["code"], strict=True) if (p_, c_) not in known]
    if extra_keys:
        budgets = pd.concat([
            budgets,
            pd.DataFrame({"project_id": [k[0] for k in extra_keys], "code": [k[1] for k in extra_keys],
                          "budgeted_amount": 0.0, "pct_complete": 0.0}),
        ], ignore_index=True)
    extra_keys = set(extra_keys)

    spend = tx.groupby(["project_id", "code"])["amount"].sum().rename("ledger_spend").reset_index()
    dup_totals = {}
    for f in detect_duplicate_postings(data, thresholds=t):
        dup_totals[(f.project_id, f.code)] = dup_totals.get((f.project_id, f.code), 0.0) + f.variance_amount
    approved = (
        cos[cos["approved_date"].notna()]
        .groupby(["project_id", "code"])["cost_amount"].sum().rename("approved_cos").reset_index()
    )
    # Commitment SCOPE LINES, not contract headers: a trade signs one
    # contract per job covering several cost codes, and a variance is read
    # per code. Reading the header here would put a concrete sub's whole
    # contract against the foundation line and call it a 200% buyout.
    committed = (
        data["commitment_lines"].groupby(["project_id", "code"])
        .agg(committed_amount=("line_amount", "sum"), committed_cos=("co_amount", "sum"),
             invoiced_by_sub=("invoiced_to_date", "sum"))
        .reset_index()
    )
    committed["retention_held"] = 0.0

    m = budgets.rename(columns={"budgeted_amount": "original_budget", "pct_complete": "line_pct_complete"})
    m = m.merge(spend, on=["project_id", "code"], how="left")
    m = m.merge(approved, on=["project_id", "code"], how="left")
    m = m.merge(committed, on=["project_id", "code"], how="left")
    m = m.merge(projects[["project_id", "type", "contract_value"]], on="project_id", how="left")
    for c in ("ledger_spend", "approved_cos", "committed_amount", "committed_cos", "invoiced_by_sub", "retention_held"):
        m[c] = m[c].fillna(0.0)
    m["line_pct_complete"] = m["line_pct_complete"].fillna(0.0)
    m["suspected_duplicates"] = [dup_totals.get((p, c), 0.0) for p, c in zip(m["project_id"], m["code"], strict=True)]
    m["unbudgeted"] = [(p, c) in extra_keys for p, c in zip(m["project_id"], m["code"], strict=True)]
    m["actual_spend"] = m["ledger_spend"] - m["suspected_duplicates"]
    m["current_budget"] = m["original_budget"] + m["approved_cos"]

    for c in ("original_budget", "current_budget", "ledger_spend", "actual_spend", "committed_amount",
              "committed_cos", "invoiced_by_sub", "retention_held", "approved_cos", "contract_value"):
        m[c] = m[c].astype(float)
    m["commitment_total"] = m["committed_amount"] + m["committed_cos"]
    m["progress"] = (m["line_pct_complete"].astype(float) / 100.0).clip(0.0, 1.0)
    m["expected_spend_to_date"] = m["current_budget"] * m["progress"]
    m["variance_amount"] = m["actual_spend"] - m["expected_spend_to_date"]
    expected = m["expected_spend_to_date"].to_numpy(dtype=float)
    variance = m["variance_amount"].to_numpy(dtype=float)
    ledger = m["ledger_spend"].to_numpy(dtype=float)
    safe = np.where(expected > 0, expected, 1.0)
    m["variance_pct"] = np.where(expected > 0, variance / safe, 0.0)
    m["ledger_variance_pct"] = np.where(expected > 0, (ledger - expected) / safe, 0.0)
    m["dollar_floor"] = [dollar_floor_for(cv, ptype, t)
                         for cv, ptype in zip(m["contract_value"], m["type"], strict=True)]

    # Forecast at completion per code. While a line is open, the forecast
    # is conservative: the larger of its budget, its commitment, and its
    # spend extrapolated over progress (once far enough along to trust).
    # Once a line is essentially complete, what it cost is what it cost:
    # spend plus whatever the sub has not yet invoiced against its
    # contract, so a line that came in under budget shows the saving.
    progress = m["progress"].to_numpy(dtype=float)
    spend = m["actual_spend"].to_numpy(dtype=float)
    budget = m["current_budget"].to_numpy(dtype=float)
    committed = m["commitment_total"].to_numpy(dtype=float)
    unpaid_commitment = np.maximum(committed - m["invoiced_by_sub"].to_numpy(dtype=float), 0.0)
    extrapolated = np.where(
        progress >= t.forecast_min_progress,
        np.minimum(spend / np.clip(progress, 1e-9, None), t.forecast_extrapolation_cap * budget),
        0.0,
    )
    open_line = progress < t.forecast_line_complete
    m["forecast_at_completion"] = np.where(
        open_line,
        np.maximum.reduce([budget, committed, spend, extrapolated]),
        spend + unpaid_commitment,
    )
    return m


def large_draws(data: dict, share_of_budget: float = None,
                thresholds: Thresholds | None = None) -> pd.DataFrame:
    """Single transactions that on their own are a large share of the cost
    code's whole budget. Deliberately not a z-score: a cost code on one
    job has two to six draws, and no statistic on n=5 tells you anything a
    builder does not already know from looking at them. Supporting detail
    for a burn-rate flag, never a severity input."""
    t = _thresholds(thresholds)
    share = t.large_draw_share if share_of_budget is None else share_of_budget
    merged = data["cost_transactions"].merge(data["project_budgets"], on=["project_id", "code"], how="left")
    budget = merged["budgeted_amount"].fillna(0.0).astype(float)
    merged["share"] = np.where(budget > 0, merged["amount"] / budget.where(budget > 0, 1.0), np.inf)
    floors = {r["project_id"]: dollar_floor_for(r["contract_value"], r["type"], t)
              for _, r in data["projects"].iterrows()}
    floor = merged["project_id"].map(floors).fillna(t.dollar_floor).astype(float)
    big = merged[(merged["share"] >= share) & (merged["amount"] >= floor)]
    return big[["project_id", "code", "transaction_id", "vendor", "date", "amount", "share"]]


def detect_cost_anomalies(data: dict, thresholds: Thresholds | None = None) -> list:
    """Burn-rate flags per cost code (over pace, and under pace at LOW),
    spend on lines reported 0% or carried with no budget, and duplicate
    postings. Severity is the % variance band gated by the project's
    materiality floor; large single draws are named in the explanation as
    detail but never move the severity -- the dollars already did that."""
    t = _thresholds(thresholds)
    budgets = effective_budgets(data, t)
    descriptions = data["cost_codes"].set_index("code")["description"].to_dict()
    draw_counts = data["cost_transactions"].groupby(["project_id", "code"]).size().to_dict()
    big = large_draws(data, thresholds=t)
    big_by_code = dict(iter(big.groupby(["project_id", "code"]))) if not big.empty else {}

    flags = []
    for _, row in budgets.iterrows():
        pid, code = row["project_id"], row["code"]
        desc = descriptions.get(code, code)
        variance_pct, variance_amt = row["variance_pct"], row["variance_amount"]
        exposure = abs(variance_amt)

        if row["expected_spend_to_date"] <= 0 and row["actual_spend"] >= row["dollar_floor"]:
            draws = draw_counts.get((pid, code), 0)
            if row["unbudgeted"]:
                flags.append(AnomalyFlag(
                    pid, code, t.unstarted_line_severity, row["actual_spend"], float("inf"),
                    kind="unbudgeted",
                    explanation=(
                        f"${row['actual_spend']:,.0f} across {draws} draw{'s' if draws != 1 else ''} "
                        f"is coded to {code} ({descriptions.get(code, 'unknown code')}) on {pid}, a "
                        f"line this job has no budget for. Miscoded, or scope nobody priced."
                    ),
                ))
            elif row["progress"] <= 0:
                flags.append(AnomalyFlag(
                    pid, code, t.unstarted_line_severity, row["actual_spend"], float("inf"),
                    kind="unstarted",
                    explanation=(
                        f"Cost code {code} ({desc}) on {pid} is reported 0% complete but carries "
                        f"${row['actual_spend']:,.0f} of spend. A deposit, a miscoded invoice, or a "
                        f"line whose % complete has not been updated: it needs a look either way."
                    ),
                ))
            else:
                flags.append(AnomalyFlag(
                    pid, code, t.unstarted_line_severity, row["actual_spend"], float("inf"),
                    kind="unbudgeted",
                    explanation=(
                        f"Cost code {code} ({desc}) on {pid} is {row['progress']:.0%} complete and "
                        f"carries ${row['actual_spend']:,.0f} of spend against a budget of zero. "
                        f"The line is being built without a number against it."
                    ),
                ))
            continue

        if variance_pct > 0:
            sev = combined_severity(pct_severity(variance_pct, t), exposure, floor=row["dollar_floor"],
                                    thresholds=t)
            if sev == "NONE":
                continue
            explanation = (
                f"Cost code {code} ({desc}) on {pid} is running {variance_pct:+.0%} against pace: "
                f"the line is {row['progress']:.0%} complete with ${row['actual_spend']:,.0f} spent "
                f"against ${row['expected_spend_to_date']:,.0f} expected, ${exposure:,.0f} over."
            )
            draws = big_by_code.get((pid, code))
            if draws is not None and len(draws):
                biggest = draws.sort_values("amount", ascending=False).iloc[0]
                if len(draws) == 1:
                    explanation += (
                        f" One draw, ${biggest['amount']:,.0f} to {biggest['vendor']} on "
                        f"{biggest['date'].date()}, is over {t.large_draw_share:.0%} of the line's "
                        f"whole budget on its own."
                    )
                else:
                    explanation += (
                        f" {len(draws)} draws on this line are each over {t.large_draw_share:.0%} of "
                        f"its whole budget, the largest ${biggest['amount']:,.0f} to "
                        f"{biggest['vendor']} on {biggest['date'].date()}."
                    )
            if row["suspected_duplicates"] > 0:
                explanation += (
                    f" Excludes ${row['suspected_duplicates']:,.0f} in suspected duplicate "
                    f"postings, flagged separately."
                )
            flags.append(AnomalyFlag(pid, code, sev, variance_amt, variance_pct, explanation, "burn-rate"))

        elif (variance_pct <= t.under_pace_pct and row["progress"] >= t.under_pace_min_progress
              and exposure >= row["dollar_floor"]):
            flags.append(AnomalyFlag(
                pid, code, "LOW", variance_amt, variance_pct, kind="under-pace",
                explanation=(
                    f"Cost code {code} ({desc}) on {pid} is reported {row['progress']:.0%} complete "
                    f"with only ${row['actual_spend']:,.0f} of ${row['expected_spend_to_date']:,.0f} "
                    f"expected spent ({variance_pct:+.0%}). Either the invoices have not landed or "
                    f"the work has not happened; worth knowing which."
                ),
            ))

    by_code = budgets.set_index(["project_id", "code"])
    for dup in detect_duplicate_postings(data, thresholds=t):
        if (dup.project_id, dup.code) in by_code.index:
            row = by_code.loc[(dup.project_id, dup.code)]
            dup.explanation += (
                f" With it in, this line reads {row['ledger_variance_pct']:+.0%} against pace; "
                f"without it, {row['variance_pct']:+.0%}."
            )
        flags.append(dup)

    flags.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], -abs(f.variance_amount)))
    return flags


def detect_budget_drift(data: dict, thresholds: Thresholds | None = None) -> list:
    """The 'quiet drift' signal: a cost code's budget edited in
    budget_revisions.csv rather than moved by change order. Every flag is
    cross-referenced against the change orders for the same project and
    cost code so it states how much of the move has paperwork behind it:
    none at all, part of it (approved or still unsigned), or all of it.
    A budget quietly cut is reported too, at LOW: it is either scope being
    dropped or optimism, and both are worth a question."""
    t = _thresholds(thresholds)
    budgets = data["project_budgets"]
    revs = data["budget_revisions"]
    cos = data["change_orders"]
    descriptions = data["cost_codes"].set_index("code")["description"].to_dict()

    latest = revs.sort_values("date").groupby(["project_id", "code"]).tail(1)
    m = latest.merge(budgets, on=["project_id", "code"], how="left")
    base = m["budgeted_amount"].astype(float)
    m["drift_amount"] = m["revised_amount"].astype(float) - base
    # A revision against a line with no budget row, or a zero budget, has
    # no meaningful percentage. It is still a revision, so it is judged on
    # dollars rather than dropped through a NaN comparison.
    m["drift_pct"] = np.where(base > 0, m["drift_amount"] / base.where(base > 0, 1.0), np.inf)

    flags = []
    for _, row in m.iterrows():
        pid, code = row["project_id"], row["code"]
        no_base = pd.isna(row["budgeted_amount"]) or float(row["budgeted_amount"]) <= 0
        drift_amount = (float(row["revised_amount"]) if no_base and pd.isna(row["drift_amount"])
                        else float(row["drift_amount"]))
        exposure = abs(drift_amount)
        if exposure < t.budget_drift_dollar_floor:
            continue
        if no_base:
            flags.append(AnomalyFlag(
                pid, code, "MEDIUM", drift_amount, float("inf"), kind="drift",
                explanation=(
                    f"Cost code {code} ({descriptions.get(code, code)}) on {pid} carries a budget "
                    f"revision to ${float(row['revised_amount']):,.0f} on "
                    f"{row['date'].date()}, \"{row['reason']}\", against a line the job has no "
                    f"budget for. Either the revision is miscoded or the scope was never priced."
                ),
            ))
            continue
        sev = pct_severity(row["drift_pct"], t)
        if sev == "NONE":
            continue
        desc = descriptions.get(code, code)

        if row["drift_amount"] < 0:
            flags.append(AnomalyFlag(
                pid, code, "LOW", row["drift_amount"], row["drift_pct"], kind="drift",
                explanation=(
                    f"Cost code {code} ({desc}) on {pid} budget quietly cut {row['drift_pct']:+.0%} "
                    f"(${exposure:,.0f}) on {row['date'].date()}, \"{row['reason']}\". Scope "
                    f"dropped, or the estimate being talked down?"
                ),
            ))
            continue

        # Compare cost with cost: a change order's cost_amount is what it
        # adds to the budget; its sell price is what it adds to the contract.
        matching = cos[(cos["project_id"] == pid) & (cos["code"] == code)]
        approved = matching[matching["approved_date"].notna()]
        pending = matching[matching["approved_date"].isna()]
        approved_cost, pending_cost = approved["cost_amount"].sum(), pending["cost_amount"].sum()
        uncovered = row["drift_amount"] - approved_cost - pending_cost

        if matching.empty:
            paper = "No change order exists for this scope; nothing behind the move at all."
        else:
            parts = []
            if approved_cost > 0:
                parts.append(f"{', '.join(approved['co_id'])} approved, ${approved_cost:,.0f} of cost "
                             f"(${approved['amount'].sum():,.0f} to the owner)")
            if pending_cost > 0:
                parts.append(f"{', '.join(pending['co_id'])} submitted and still unsigned, "
                             f"${pending_cost:,.0f} of cost (${pending['amount'].sum():,.0f} to the owner)")
            paper = "Paperwork so far: " + "; ".join(parts) + "."
            if uncovered > t.budget_drift_dollar_floor:
                paper += (
                    f" That leaves ${uncovered:,.0f} of the added cost with no change order at all, "
                    f"cost the owner has not agreed to pay for."
                )
            elif uncovered < -t.budget_drift_dollar_floor:
                paper += " The change orders more than cover the move."
            else:
                paper += " The change orders cover the move; the budget was edited before the paperwork caught up."

        flags.append(AnomalyFlag(
            pid, code, sev, row["drift_amount"], row["drift_pct"], kind="drift",
            explanation=(
                f"Cost code {code} ({desc}) on {pid} budget quietly revised {row['drift_pct']:+.0%} "
                f"(${exposure:,.0f}) on {row['date'].date()}, \"{row['reason']}\". {paper}"
            ),
        ))
    flags.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], -abs(f.variance_amount)))
    return flags


def detect_commitment_issues(data: dict, thresholds: Thresholds | None = None) -> list:
    """Two checks on the subs' contracts. Buyout: the contract came in over
    the budget line it was estimated at; the estimator's number was wrong
    or the scope grew, and either way the margin moved before a single
    draw. Over-invoiced: the sub has billed more than its contract plus
    its change orders."""
    t = _thresholds(thresholds)
    budgets = effective_budgets(data, t)
    descriptions = data["cost_codes"].set_index("code")["description"].to_dict()
    revs = data["budget_revisions"].sort_values("date")
    dups = {(f.project_id, f.code): f.variance_amount for f in detect_duplicate_postings(data, thresholds=t)}
    flags = []
    for _, row in budgets[budgets["committed_amount"] > 0].iterrows():
        pid, code = row["project_id"], row["code"]
        desc = descriptions.get(code, code)
        buyout_var = row["committed_amount"] - row["original_budget"]
        if row["original_budget"] <= 0:
            # Scope committed against a line nobody budgeted. The worst
            # possible buyout, and a percentage of zero says nothing, so
            # it is judged on the dollars alone.
            if row["committed_amount"] >= row["dollar_floor"]:
                flags.append(AnomalyFlag(
                    pid, code, "HIGH", row["committed_amount"], float("inf"), kind="buyout",
                    explanation=(
                        f"Cost code {code} ({desc}) on {pid}: ${row['committed_amount']:,.0f} of sub "
                        f"contract signed against a line with no budget at all. Nothing was priced "
                        f"for this scope and it is already committed."
                    ),
                ))
            continue
        buyout_pct = buyout_var / row["original_budget"]
        if buyout_var > 0:
            sev = combined_severity(pct_severity(buyout_pct, t), buyout_var, floor=row["dollar_floor"],
                                    thresholds=t)
            if sev != "NONE":
                explanation = (
                    f"Cost code {code} ({desc}) on {pid}: sub contract ${row['committed_amount']:,.0f} "
                    f"against a ${row['original_budget']:,.0f} budget line, {buyout_pct:+.0%} "
                    f"(${buyout_var:,.0f}) over at buyout."
                )
                rev = revs[(revs["project_id"] == pid) & (revs["code"] == code)]
                if not rev.empty and abs(rev.iloc[-1]["revised_amount"] - row["committed_amount"]) < 0.01 * row["committed_amount"]:
                    explanation += (
                        f" The budget line was edited to match this contract on "
                        f"{rev.iloc[-1]['date'].date()} ({rev.iloc[-1]['revision_id']})."
                    )
                flags.append(AnomalyFlag(pid, code, sev, buyout_var, buyout_pct, explanation, "buyout"))

        over = row["invoiced_by_sub"] - row["commitment_total"]
        if over >= t.budget_drift_dollar_floor:
            explanation = (
                f"Cost code {code} ({desc}) on {pid}: the sub has invoiced ${row['invoiced_by_sub']:,.0f} "
                f"against a ${row['commitment_total']:,.0f} contract, ${over:,.0f} with no contract "
                f"behind it."
            )
            if dups.get((pid, code), 0.0) >= over - 1.0:
                explanation += (
                    f" Explained by the suspected duplicate posting on this line "
                    f"(${dups[(pid, code)]:,.0f}); without it the sub is inside its contract."
                )
                sev = "LOW"   # the duplicate flag carries the exposure
            else:
                sev = "HIGH" if over >= 3 * t.dollar_floor else "MEDIUM"
            flags.append(AnomalyFlag(pid, code, sev, over, 0.0, explanation, "over-invoiced"))
    flags.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], -abs(f.variance_amount)))
    return flags


def cross_project_patterns(budgets: pd.DataFrame, min_projects: int = 3,
                           variance_threshold: float = 0.10,
                           thresholds: Thresholds | None = None) -> pd.DataFrame:
    """A cost division over pace on 3+ distinct projects at once is worth
    checking at the estimating end, not just job by job. On a five-job
    portfolio that is a prompt to look, not a statistical finding. Only
    material variances count."""
    t = _thresholds(thresholds)
    b = budgets.copy()
    b["division"] = b["code"].str.split("-").str[0]
    floor = b["dollar_floor"] if "dollar_floor" in b.columns else t.dollar_floor
    material = (b["variance_pct"] > variance_threshold) & (b["variance_amount"] >= floor)
    div = (
        b[material].groupby("division")
        .agg(projects_affected=("project_id", "nunique"),
             avg_variance_pct=("variance_pct", "mean"),
             total_exposure=("variance_amount", "sum"))
        .reset_index()
    )
    return div[div["projects_affected"] >= min_projects]


def detect_cross_project_patterns(data: dict, min_projects: int = 3,
                                  variance_threshold: float = 0.10,
                                  thresholds: Thresholds | None = None) -> list:
    t = _thresholds(thresholds)
    budgets = effective_budgets(data, t)
    names = data["cost_codes"].drop_duplicates("division").set_index("division")["division_name"].to_dict()
    flags = []
    for _, row in cross_project_patterns(budgets, min_projects, variance_threshold, t).iterrows():
        exposure = abs(row["total_exposure"])
        sev = combined_severity(pct_severity(row["avg_variance_pct"], t), exposure, thresholds=t)
        if sev == "NONE":
            continue
        flags.append(AnomalyFlag(
            "PORTFOLIO", row["division"], sev, row["total_exposure"], row["avg_variance_pct"], kind="pattern",
            explanation=(
                f"Division {row['division']} ({names.get(row['division'], '')}) is over burn-rate "
                f"pace on {int(row['projects_affected'])} separate projects (avg "
                f"{row['avg_variance_pct']:+.0%}, ${exposure:,.0f} total). Worth checking how "
                f"this division is being estimated before chasing it job by job."
            ),
        ))
    flags.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], -abs(f.variance_amount)))
    return flags


# ---------------------------------------------------------------------------
# Schedule risk: SPI / CPI, critical-path slip, weather and other delay
# ---------------------------------------------------------------------------
@dataclass
class ScheduleRisk:
    project_id: str
    spi: float
    cpi: float
    critical_path_slip_days: int
    weather_delay_days: int
    other_delay_days: int
    delay_reasons: str
    severity: str
    explanation: str


def compute_schedule_risk(data: dict, as_of: date | None = None,
                          thresholds: Thresholds | None = None) -> list:
    """SPI and CPI the way a small GC can actually compute them.

    Both start from the superintendent's per-line % complete on the
    schedule of values, rolled up cost-weighted into the project's reported
    % complete (projects.pct_complete). That is the standard percent-
    complete method of earned value, and it is also its limit: the super's
    line percentages are a judgment, not a measurement, and both indices
    inherit whatever they get wrong (compute_cost_forecast checks the
    roll-up against cost-to-cost progress for exactly that reason).

    SPI = reported % complete / % of budgeted cost the baseline schedule
    planned to have earned by today (interpolated between milestones). Two
    independent inputs, so a genuine schedule signal.

    CPI = expected spend for this point in every code's phase / actual
    cost. It is the project-level roll-up of the same burn-rate math the
    cost detector runs per code -- one number per job for the front
    screen, not a second independent signal."""
    t = _thresholds(thresholds)
    projects = data["projects"]
    milestones = data["schedule_milestones"]
    budgets = effective_budgets(data, t)
    as_of_ts = pd.Timestamp(DATASET_AS_OF if as_of is None else as_of)
    epoch = pd.Timestamp("1970-01-01")

    results = []
    for _, proj in projects.iterrows():
        pid = proj["project_id"]
        pm = milestones[milestones["project_id"] == pid].sort_values("baseline_date")

        planned_pct = float(np.interp(
            (as_of_ts - epoch).days,
            ((pm["baseline_date"] - epoch).dt.days).to_numpy(),
            pm["planned_pct_complete"].to_numpy(),
        )) if len(pm) else 0.0
        earned_pct = proj["pct_complete"] / 100.0
        spi = earned_pct / planned_pct if planned_pct > 0 else 1.0

        pb = budgets[budgets["project_id"] == pid]
        earned_value, actual = pb["expected_spend_to_date"].sum(), pb["actual_spend"].sum()
        cpi = earned_value / actual if actual > 0 else 1.0

        cp = pm[pm["critical_path"] == True]  # noqa: E712
        # Terminal critical-path milestone. A missing forecast date is not
        # good news -- it is no news -- and must not read the same as a job
        # tracking to baseline.
        # No critical-path schedule at all is the same situation as a
        # terminal milestone with no forecast date, and has to read the
        # same way: a job with no schedule is not a job on schedule.
        slip, slip_unknown = 0, not len(cp)
        if len(cp):
            term = cp.iloc[-1]
            if pd.isna(term["forecast_date"]) or pd.isna(term["baseline_date"]):
                slip_unknown = True
            else:
                slip = max(0, (term["forecast_date"] - term["baseline_date"]).days)
        weather = int(pm["weather_delay_days"].sum()) if len(pm) else 0
        other = int(pm["other_delay_days"].sum()) if len(pm) else 0
        reasons = "; ".join(pm["delay_reason"].dropna().unique()) if len(pm) else ""

        gap = max(0.0, 1.0 - spi, 1.0 - cpi)
        perf_sev = _band_for(gap, t.evm_gap_bands) if gap > 0 else "NONE"
        slip_sev = _band_for(slip, t.slip_days_bands) if slip > 0 else "NONE"
        sev = _worst([perf_sev, slip_sev, "MEDIUM" if slip_unknown else "NONE"])

        if slip_unknown:
            where = (f"the terminal critical-path milestone ({cp.iloc[-1]['milestone']}) has no "
                     f"forecast date" if len(cp)
                     else "this job has no critical-path schedule loaded")
            explanation = (
                f"{pid}: {where}, so the finish cannot be projected. "
                f"SPI {spi:.2f}, CPI {cpi:.2f}. Get a date on it before the next owner update."
            )
            results.append(ScheduleRisk(pid, round(spi, 2), round(cpi, 2), slip, weather, other,
                                        reasons, sev, explanation))
            continue

        if sev == "NONE":
            explanation = (
                f"{pid} tracking on schedule and on budget (SPI {spi:.2f}, CPI {cpi:.2f}"
                + (f", {weather} days of weather absorbed into float" if weather else "") + ")."
            )
        else:
            breakdown = f"{weather} of weather"
            if other > 0:
                breakdown += f", {other} of {reasons or 'other causes'}"
            explanation = (
                f"{pid}: forecast to finish {slip} days late ({breakdown}). SPI {spi:.2f}, CPI {cpi:.2f}."
            )
        results.append(ScheduleRisk(pid, round(spi, 2), round(cpi, 2), slip, weather, other, reasons, sev, explanation))
    results.sort(key=lambda r: -SEVERITY_ORDER[r.severity])
    return results


# ---------------------------------------------------------------------------
# Change-order aging
# ---------------------------------------------------------------------------
@dataclass
class ChangeOrderAging:
    co_id: str
    project_id: str
    amount: float
    days_unapproved: int
    severity: str
    explanation: str


def detect_co_aging(data: dict, as_of: date | None = None,
                    thresholds: Thresholds | None = None) -> list:
    """Change orders submitted and still unsigned past the threshold. The
    amount is the sell price: what the owner has not yet agreed to pay."""
    t = _thresholds(thresholds)
    as_of_ts = pd.Timestamp(DATASET_AS_OF if as_of is None else as_of)
    line_pct = data["project_budgets"].set_index(["project_id", "code"])["pct_complete"].to_dict()
    results = []
    for _, row in data["change_orders"].iterrows():
        if pd.notna(row["approved_date"]):
            continue
        days = (as_of_ts - row["submitted_date"]).days
        if days < t.co_aging_threshold_days:
            continue
        # abs(): a credit is money owed back to the owner, and an $85,000
        # credit sitting unsigned for six months is exposure, not admin.
        # Comparing the signed amount put every credit under the floor and
        # capped it at LOW however large or old it was.
        sev = ("LOW" if abs(row["amount"]) < t.dollar_floor
               else _band_for(days, t.co_aging_days_bands))
        started = line_pct.get((row["project_id"], row["code"]), 0) > 0
        tail = ("The work is under way, so it is proceeding on a verbal." if started
                else "The line has not started, so the scope is committed before the price is.")
        # A credit's amount is negative; "$-85,000" reads as a typo, not a
        # number. Render it the way a builder would say it out loud.
        amount_text = (f"${abs(row['amount']):,.0f} credit" if row["amount"] < 0
                       else f"${row['amount']:,.0f}")
        results.append(ChangeOrderAging(
            row["co_id"], row["project_id"], row["amount"], days, sev,
            explanation=(
                f"{row['co_id']} on {row['project_id']} ({amount_text}) has been unsigned "
                f"for {days} days: \"{row['reason']}\". {tail}"
            ),
        ))
    results.sort(key=lambda r: (-SEVERITY_ORDER[r.severity], -r.days_unapproved))
    return results


# ---------------------------------------------------------------------------
# Allowances: owner selections vs. the allowance carried in the contract
# ---------------------------------------------------------------------------
@dataclass
class AllowanceFlag:
    allowance_id: str
    project_id: str
    code: str
    issue: str            # "Overage" or "Selection overdue"
    amount: float         # overage $ (0 for an overdue selection)
    severity: str
    explanation: str


def detect_allowance_overages(data: dict, as_of: date | None = None,
                              thresholds: Thresholds | None = None) -> list:
    """Two things go wrong with allowances on a custom home, and both are
    self-inflicted loss if nobody is watching:

    1. The owner selects over the allowance and no change order gets
       written, so the builder eats the difference. Severity is the overage
       % against PCT_BANDS, gated by a dollar floor. A change order linked
       to the allowance (allowance_id) and approved for at least the
       overage's cost clears the flag: the money is coming back. One
       submitted but unsigned keeps the flag with that noted. A change
       order on the same cost code for something else does not count.
    2. The selection is late against the date the schedule needs it by. An
       appliance package chosen three weeks late is a cabinetry install
       three weeks late. A selection still open past its date is scored by
       how late it is; one that was eventually made late is reported at
       LOW so the pattern stays visible."""
    t = _thresholds(thresholds)
    cos = data["change_orders"]
    as_of_ts = pd.Timestamp(DATASET_AS_OF if as_of is None else as_of)
    flags = []
    for _, row in data["allowances"].iterrows():
        pid, code = row["project_id"], row["code"]
        if pd.isna(row["selected_amount"]):
            if pd.notna(row["selection_due"]) and row["selection_due"] < as_of_ts:
                days = (as_of_ts - row["selection_due"]).days
                flags.append(AllowanceFlag(
                    row["allowance_id"], pid, code, "Selection overdue", 0.0,
                    _band_for(days, t.selection_overdue_days_bands),
                    explanation=(
                        f"{row['description']} on {pid}: owner selection was due "
                        f"{row['selection_due'].date()} and is {days} days late against a "
                        f"${row['allowance_amount']:,.0f} allowance. Every day this slips, the "
                        f"trade behind it slips with it."
                    ),
                ))
            continue

        if pd.notna(row["selection_due"]) and pd.notna(row["selection_date"]) \
                and (row["selection_date"] - row["selection_due"]).days > t.selection_late_grace_days:
            days = (row["selection_date"] - row["selection_due"]).days
            flags.append(AllowanceFlag(
                row["allowance_id"], pid, code, "Selection late", 0.0, "LOW",
                explanation=(
                    f"{row['description']} on {pid}: selection was due {row['selection_due'].date()} "
                    f"and was made {days} days later, on {row['selection_date'].date()}."
                ),
            ))

        overage = row["selected_amount"] - row["allowance_amount"]
        if overage < t.allowance_dollar_floor:
            continue
        # A $0 allowance ("owner to select, TBD") is a real line on a real
        # contract. Everything selected against it is overage, and a
        # percentage of nothing is not a number.
        allowance_amount = float(row["allowance_amount"])
        overage_pct = overage / allowance_amount if allowance_amount > 0 else float("inf")
        sev = pct_severity(overage_pct, t)
        if sev == "NONE":
            continue

        linked = cos[cos["allowance_id"] == row["allowance_id"]] if "allowance_id" in cos.columns else cos.iloc[0:0]
        approved = linked[linked["approved_date"].notna()]
        pending = linked[linked["approved_date"].isna()]
        # Both sides of this comparison are contract numbers. The allowance
        # is what the owner was told the scope was worth and the change
        # order is what they are being billed for the difference, so the
        # sell price is what clears the overage -- comparing the owner's
        # overage against the builder's cost would leave every covered
        # allowance looking short by exactly the margin.
        if approved["amount"].sum() >= overage * 0.95:
            continue  # covered: the overage is being billed back to the owner
        if not pending.empty:
            co = pending.iloc[0]
            paper = (f"{co['co_id']} (${co['amount']:,.0f} to the owner) is submitted but unsigned; "
                     f"the margin is exposed until it is.")
        else:
            paper = "No change order has been written for the difference; as it stands the builder is absorbing it."
        flags.append(AllowanceFlag(
            row["allowance_id"], pid, code, "Overage", overage, sev,
            explanation=(
                f"{row['description']} on {pid}: owner selected ${row['selected_amount']:,.0f} "
                f"against a ${row['allowance_amount']:,.0f} allowance ({overage_pct:+.0%}, "
                f"${overage:,.0f} over). {paper}"
            ),
        ))
    flags.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], -f.amount))
    return flags


# ---------------------------------------------------------------------------
# Project financials: billing position and forecast at completion
# ---------------------------------------------------------------------------
@dataclass
class BillingPosition:
    project_id: str
    revised_contract: float
    earned_revenue: float
    billed_to_date: float
    retainage_held: float
    under_billed: float      # positive = earned but not yet invoiced
    severity: str
    explanation: str


def compute_billing_position(data: dict, thresholds: Thresholds | None = None) -> list:
    """The WIP schedule's over/under line, on the percentage-of-completion
    basis a builder runs it: revised contract (contract plus approved
    change orders at their sell price) times the reported % complete is
    what the job has earned; against what has been invoiced, the gap is
    either cash the job is not collecting or work billed ahead. A CPA's
    WIP earns revenue cost-to-cost instead; compute_cost_forecast shows
    that percentage alongside this one. Retainage is shown because it is
    the part of the billed figure the owner is still holding."""
    t = _thresholds(thresholds)
    cos = data["change_orders"]
    approved = cos[cos["approved_date"].notna()].groupby("project_id")["amount"].sum()
    results = []
    for _, p in data["projects"].iterrows():
        pid = p["project_id"]
        revised = p["contract_value"] + float(approved.get(pid, 0.0))
        earned = revised * p["pct_complete"] / 100.0
        billed = p["billed_to_date"]
        under = earned - billed
        if earned <= 0 and billed > 0:
            # Nothing earned and money invoiced: the ratio is undefined and
            # forcing it to zero reads CLEAR, which is exactly backwards on
            # the most common over-billing case there is -- a front-loaded
            # mobilization draw on a job that has not started.
            sev = _band_for(billed / revised, t.over_billing_bands) if revised > 0 else "HIGH"
            explanation = (
                f"{pid}: ${billed:,.0f} billed against a ${revised:,.0f} revised contract with "
                f"nothing earned -- the job is reported 0% complete. Every dollar invoiced is "
                f"ahead of the work."
            )
            results.append(BillingPosition(pid, revised, earned, billed, p["retainage_held"],
                                           under, sev, explanation))
            continue
        gap = under / earned if earned > 0 else 0.0
        if gap > 0:
            sev = _band_for(gap, t.under_billing_bands)
            explanation = (
                f"{pid}: ${earned:,.0f} earned at {p['pct_complete']}% of a ${revised:,.0f} revised "
                f"contract, ${billed:,.0f} billed, so ${under:,.0f} ({gap:.1%} of earned) is earned "
                f"and not yet invoiced. ${p['retainage_held']:,.0f} of the billed figure is held as retainage."
            )
        else:
            sev = _band_for(-gap, t.over_billing_bands)
            explanation = (
                f"{pid}: ${earned:,.0f} earned at {p['pct_complete']}% of a ${revised:,.0f} revised "
                f"contract, ${billed:,.0f} billed, so ${-under:,.0f} ({-gap:.1%} of earned) is billed "
                f"ahead of the work. ${p['retainage_held']:,.0f} held as retainage."
            )
        results.append(BillingPosition(pid, revised, earned, billed, p["retainage_held"], under, sev, explanation))
    results.sort(key=lambda r: -SEVERITY_ORDER[r.severity])
    return results


@dataclass
class CostForecast:
    project_id: str
    revised_contract: float
    budget_at_completion: float
    forecast_at_completion: float
    spend_to_date: float
    target_margin_pct: float
    projected_margin_pct: float
    reported_pct_complete: float
    cost_pct_complete: float
    margin_severity: str
    progress_severity: str
    severity: str
    explanation: str


def compute_cost_forecast(data: dict, thresholds: Thresholds | None = None) -> list:
    """Forecast at completion per project, from the per-code forecasts in
    effective_budgets (the larger of budget, commitment, and extrapolated
    spend once a code is far enough along). Two things fall out of it:

    - Projected margin against the margin the job was priced at. This is
      the number an owner actually loses sleep over, and it moves before
      any single cost code looks alarming.
    - Cost-to-cost % complete (spend / forecast) against the super's
      reported % complete. When the two disagree by more than a few
      points, one of them is wrong -- either the walk is optimistic or the
      forecast is missing cost -- and every SPI, CPI and billing number
      built on the reported figure inherits the error."""
    t = _thresholds(thresholds)
    budgets = effective_budgets(data, t)
    cos = data["change_orders"]
    approved = cos[cos["approved_date"].notna()].groupby("project_id")["amount"].sum()
    results = []
    for _, p in data["projects"].iterrows():
        pid = p["project_id"]
        pb = budgets[budgets["project_id"] == pid]
        revised = p["contract_value"] + float(approved.get(pid, 0.0))
        bac = float(pb["current_budget"].sum())
        eac = float(pb["forecast_at_completion"].sum())
        spend = float(pb["actual_spend"].sum())
        target = float(p["margin_pct"])
        projected = (revised - eac) / revised if revised > 0 else 0.0
        reported = p["pct_complete"] / 100.0
        cost_pct = spend / eac if eac > 0 else 0.0

        erosion = target - projected
        margin_sev = _band_for(erosion, t.margin_erosion_bands) if erosion > 0 else "NONE"
        gap = abs(reported - cost_pct)
        progress_sev = _band_for(gap, t.progress_gap_bands)
        sev = _worst([margin_sev, progress_sev])

        explanation = (
            f"{pid}: forecast at completion ${eac:,.0f} against a ${bac:,.0f} budget on a "
            f"${revised:,.0f} revised contract. Projected margin {projected:.1%} vs. "
            f"{target:.0%} priced"
        )
        if erosion > 0.005:
            explanation += f", {erosion * 100:.1f} points of margin gone"
        explanation += f". Cost-to-cost progress {cost_pct:.0%} vs. {reported:.0%} reported by the super"
        if progress_sev != "NONE":
            explanation += (
                ": a gap this size means either the walk is optimistic or the forecast is "
                "missing cost. Find out which before the next draw."
            )
        else:
            explanation += "."
        results.append(CostForecast(pid, revised, bac, eac, spend, target, projected, reported,
                                    cost_pct, margin_sev, progress_sev, sev, explanation))
    results.sort(key=lambda r: -SEVERITY_ORDER[r.severity])
    return results


# ---------------------------------------------------------------------------
# Subcontractor compliance
# ---------------------------------------------------------------------------
@dataclass
class ComplianceFlag:
    sub_id: str
    name: str
    trade: str
    issue: str
    severity: str
    explanation: str


def detect_compliance_flags(data: dict, as_of: date | None = None,
                            thresholds: Thresholds | None = None) -> list:
    t = _thresholds(thresholds)
    as_of_ts = pd.Timestamp(DATASET_AS_OF if as_of is None else as_of)
    lookahead_ts = as_of_ts + pd.Timedelta(days=t.compliance_lookahead_days)
    results = []
    for _, row in data["subcontractors"].iterrows():
        active = _active_projects(row)
        if not active:
            continue
        on_site = ", ".join(active)
        for column, label in (("insurance_expiry", "Insurance"), ("license_expiry", "License")):
            expiry = row[column]
            if pd.isna(expiry):
                continue
            if expiry < as_of_ts:
                results.append(ComplianceFlag(
                    row["sub_id"], row["name"], row["trade"], f"{label} lapsed", "HIGH",
                    explanation=(
                        f"{row['name']} ({row['trade']}) {label.lower()} lapsed {expiry.date()}, "
                        f"still active on {on_site}. No cost impact, compliance risk only: nobody "
                        f"from this firm should be on site until a current certificate is in hand."
                    ),
                ))
            elif expiry <= lookahead_ts:
                days_left = (expiry - as_of_ts).days
                results.append(ComplianceFlag(
                    row["sub_id"], row["name"], row["trade"], f"{label} expiring", "LOW",
                    explanation=(
                        f"{row['name']} ({row['trade']}) {label.lower()} expires in {days_left} days "
                        f"({expiry.date()}), active on {on_site}. Chase the renewal certificate now."
                    ),
                ))
    results.sort(key=lambda r: -SEVERITY_ORDER[r.severity])
    return results


# ---------------------------------------------------------------------------
# Portfolio-level executive rollup
# ---------------------------------------------------------------------------
ROLLUP_COLUMNS = [
    "cost_severity", "schedule_severity", "co_aging_severity", "allowance_severity",
    "billing_severity", "forecast_severity", "compliance_severity",
]


def portfolio_rollup(data: dict, as_of: date | None = None,
                     thresholds: Thresholds | None = None) -> pd.DataFrame:
    """One row per project: worst severity across cost (burn-rate, unstarted
    or unbudgeted spend, duplicates, drift, buyout, over-invoicing),
    schedule, CO aging, allowances, billing, forecast (margin and progress
    check) and compliance."""
    t = _thresholds(thresholds)
    cost_flags = (detect_cost_anomalies(data, t) + detect_budget_drift(data, t)
                  + detect_commitment_issues(data, t))
    sched = {r.project_id: r.severity for r in compute_schedule_risk(data, as_of, t)}
    co_aging = detect_co_aging(data, as_of, t)
    allowances = detect_allowance_overages(data, as_of, t)
    billing = {b.project_id: b.severity for b in compute_billing_position(data, t)}
    forecast = {f.project_id: f.severity for f in compute_cost_forecast(data, t)}
    compliance = detect_compliance_flags(data, as_of, t)
    subs = data["subcontractors"]

    rows = []
    for pid in data["projects"]["project_id"]:
        subs_here = {row["sub_id"] for _, row in subs.iterrows() if pid in _active_projects(row)}
        row = {
            "project_id": pid,
            "cost_severity": _worst(f.severity for f in cost_flags if f.project_id == pid),
            "schedule_severity": sched.get(pid, "NONE"),
            "co_aging_severity": _worst(c.severity for c in co_aging if c.project_id == pid),
            "allowance_severity": _worst(a.severity for a in allowances if a.project_id == pid),
            "billing_severity": billing.get(pid, "NONE"),
            "forecast_severity": forecast.get(pid, "NONE"),
            "compliance_severity": _worst(c.severity for c in compliance if c.sub_id in subs_here),
        }
        row["overall_severity"] = _worst(row[c] for c in ROLLUP_COLUMNS)
        rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    data = load_data()
    sections = [
        ("Cost anomalies (burn-rate, under-pace, unstarted, duplicate postings)", detect_cost_anomalies(data)),
        ("Budget drift (quiet revisions)", detect_budget_drift(data)),
        ("Commitments (buyout, over-invoiced)", detect_commitment_issues(data)),
        ("Schedule risk", compute_schedule_risk(data)),
        ("Change-order aging", detect_co_aging(data)),
        ("Allowances", detect_allowance_overages(data)),
        ("Billing position", compute_billing_position(data)),
        ("Forecast at completion", compute_cost_forecast(data)),
        ("Sub compliance", detect_compliance_flags(data)),
        ("Across the portfolio", detect_cross_project_patterns(data)),
    ]
    for title, items in sections:
        print(f"\n=== {title} ===")
        if not items:
            print("(none)")
        for item in items:
            print(f"[{item.severity}] {item.explanation}")
    print("\n=== Portfolio rollup ===")
    with pd.option_context("display.width", 200):
        print(portfolio_rollup(data).to_string(index=False))
