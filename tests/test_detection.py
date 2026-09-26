"""
Tests for the seeded anomalies. These are not "does the function run"
tests -- generate_data.py deliberately plants known problems at specific
severities, and every test here asserts a specific one of those is caught
at the severity it should be caught at, with an explanation that says what
actually happened. If a threshold change in detection.py stops one firing
(or fires it at the wrong severity), this is the suite that says so.

The last sections are the other half: the data has to be internally
coherent (a builder spots incoherent data in seconds), the detector must
stay quiet on the 200-odd cost codes that are fine, and nothing may fall
over on empty or edge-case data.
"""
import io
import json
import os

import numpy as np
import pandas as pd
import pytest

import detection as det


# ---------------------------------------------------------------------------
# Severity-scoring primitives
# ---------------------------------------------------------------------------
def test_pct_severity_bands():
    assert det.pct_severity(0.05) == "NONE"
    assert det.pct_severity(0.15) == "LOW"
    assert det.pct_severity(0.25) == "MEDIUM"
    assert det.pct_severity(0.50) == "HIGH"
    assert det.pct_severity(-0.25) == "MEDIUM"  # symmetric on sign


def test_dollar_floor_gates_severity():
    assert det.combined_severity("HIGH", 100) == "NONE"
    assert det.combined_severity("HIGH", 10_000) == "HIGH"
    assert det.combined_severity("HIGH", 6_000, floor=8_500) == "NONE"


def test_dollar_floor_scales_with_contract_and_type():
    assert det.dollar_floor_for(650_000) == det.DOLLAR_FLOOR
    assert det.dollar_floor_for(3_400_000) == pytest.approx(8_500)
    assert det.dollar_floor_for(1_900_000, "Light Commercial (ground-up)") == pytest.approx(6_650)


def test_band_for_handles_open_ended_top_band():
    assert det._band_for(10**9, det.PCT_BANDS) == "HIGH"
    assert det._band_for(0.0, det.PCT_BANDS) == "NONE"


def test_threshold_override_file(tmp_path):
    """config/thresholds.json overrides constants; bands accept null for an
    open top; unknown keys are rejected rather than silently ignored; and
    none of it touches the module -- the file builds a Thresholds."""
    cfg = tmp_path / "thresholds.json"
    cfg.write_text(json.dumps({
        "_comment": "ignored",
        "DOLLAR_FLOOR": 9_000,
        "PCT_BANDS": {"LOW": [0.05, 0.15], "MEDIUM": [0.15, 0.30], "HIGH": [0.30, None]},
    }))
    loaded = det.load_thresholds(str(cfg))
    assert loaded.dollar_floor == 9_000
    assert loaded.pct_bands["HIGH"] == (0.30, float("inf"))
    assert det.pct_severity(0.07, loaded) == "LOW"
    assert det.pct_severity(0.07) == "NONE"                 # the defaults did not move
    assert det.DOLLAR_FLOOR == 5_000 and det.DEFAULT_THRESHOLDS.dollar_floor == 5_000
    cfg.write_text(json.dumps({"NOT_A_THRESHOLD": 1}))
    with pytest.raises(KeyError):
        det.load_thresholds(str(cfg))
    assert det.load_thresholds(str(tmp_path / "missing.json")) == det.Thresholds()


def test_threshold_overrides_are_validated_on_the_way_in():
    """A band that loads but is malformed would crash on the first flag
    instead of at startup, which is the worst place to find out a config
    file has a typo in it."""
    t = det.Thresholds()
    with pytest.raises(ValueError):
        t.with_overrides({"PCT_BANDS": {"LOW": [0.10, 0.10]}})          # empty band
    with pytest.raises(ValueError):
        t.with_overrides({"PCT_BANDS": {"LOW": [0.10, 0.25]}})          # gap against MEDIUM
    with pytest.raises(ValueError):
        t.with_overrides({"DOLLAR_FLOOR": -1})
    with pytest.raises(TypeError):
        t.with_overrides({"DOLLAR_FLOOR": "5000"})
    with pytest.raises(TypeError):
        t.with_overrides({"PCT_BANDS": 0.2})
    # A partial band override merges over the rest.
    merged = t.with_overrides({"PCT_BANDS": {"HIGH": [0.35, "inf"]}})
    assert merged.pct_bands == det.PCT_BANDS
    assert merged.co_aging_threshold_days == det.CO_AGING_THRESHOLD_DAYS


# ---------------------------------------------------------------------------
# Harborview (P03): one root cause, four views. The owner asked for the
# upgraded cabinetry on a phone call; the sub contract was re-issued at
# +62%, the budget line edited to match, a CO for a fraction of it sits
# unsigned, and the millwork line is running well over pace.
# ---------------------------------------------------------------------------
def test_harborview_millwork_burn_rate_flagged_high(data):
    flags = det.detect_cost_anomalies(data)
    match = [f for f in flags if f.project_id == "P03" and f.code == "06-02" and f.kind == "burn-rate"]
    assert match, "Harborview Custom Millwork & Cabinetry burn-rate anomaly not detected"
    assert match[0].severity == "HIGH"
    assert 0.55 <= match[0].variance_pct < 0.95, f"got {match[0].variance_pct:.0%}"
    assert "06-02" in match[0].explanation


def test_harborview_drift_states_what_the_paperwork_covers(data):
    """The drift is cost; the CO's cost_amount (not its sell price) is what
    covers it, and the uncovered remainder is stated to the dollar."""
    flags = det.detect_budget_drift(data)
    high = [f for f in flags if f.project_id == "P03" and f.code == "06-02"][0]
    assert high.severity == "HIGH"
    assert "cabinetry" in high.explanation.lower()
    assert "CO001" in high.explanation and "unsigned" in high.explanation
    assert "no change order at all" in high.explanation   # the uncovered remainder is named
    co = data["change_orders"].set_index("co_id").loc["CO001"]
    uncovered = high.variance_amount - co["cost_amount"]
    assert f"${uncovered:,.0f}" in high.explanation
    assert f"${co['cost_amount']:,.0f} of cost" in high.explanation


def test_harborview_buyout_matches_the_revision(data):
    flags = det.detect_commitment_issues(data)
    buyout = [f for f in flags if f.project_id == "P03" and f.code == "06-02" and f.kind == "buyout"]
    assert buyout and buyout[0].severity == "HIGH"
    assert 0.60 <= buyout[0].variance_pct <= 0.85
    assert "edited to match this contract" in buyout[0].explanation


def test_harborview_is_the_worst_project_in_portfolio(data):
    rollup = det.portfolio_rollup(data).set_index("project_id")
    assert rollup.loc["P03", "overall_severity"] == "HIGH"
    assert rollup.loc["P03", "cost_severity"] == "HIGH"
    assert rollup.loc["P03", "forecast_severity"] in ("MEDIUM", "HIGH")   # margin is actually going


def test_harborview_forecast_shows_margin_erosion(data):
    fc = {f.project_id: f for f in det.compute_cost_forecast(data)}
    assert fc["P03"].projected_margin_pct < fc["P03"].target_margin_pct - 0.04
    assert "points of margin gone" in fc["P03"].explanation


# ---------------------------------------------------------------------------
# Cascade Ridge (P02): a framing draw posted twice, the drywaller not
# invoicing, the roofing tarping story, and under-billed.
# ---------------------------------------------------------------------------
def test_duplicate_posting_flagged(data):
    dups = det.detect_duplicate_postings(data)
    match = [f for f in dups if f.project_id == "P02" and f.code == "06-01"]
    assert len(match) == 1, "the seeded $18,412.50 duplicate framing draw was not caught"
    assert match[0].severity == "HIGH"
    assert match[0].variance_amount == pytest.approx(18_412.50)
    assert "Cedarline Framing" in match[0].explanation


def test_duplicate_is_excluded_from_burn_rate(data):
    """The same dollars must not be flagged twice. With the duplicate in,
    framing reads well over pace; without it the line is on pace."""
    budgets = det.effective_budgets(data)
    row = budgets[(budgets["project_id"] == "P02") & (budgets["code"] == "06-01")].iloc[0]
    assert row["suspected_duplicates"] == pytest.approx(18_412.50)
    assert row["ledger_variance_pct"] > 0.20
    assert abs(row["variance_pct"]) < 0.10
    burn = [f for f in det.detect_cost_anomalies(data)
            if f.project_id == "P02" and f.code == "06-01" and f.kind == "burn-rate"]
    assert burn == []


def test_duplicate_explains_the_over_invoiced_sub(data):
    """The duplicate also makes the framer look over-invoiced against its
    contract; the commitment check must attribute that to the duplicate at
    LOW, not raise a second HIGH for the same $18,412.50."""
    over = [f for f in det.detect_commitment_issues(data)
            if f.project_id == "P02" and f.code == "06-01" and f.kind == "over-invoiced"]
    assert over and over[0].severity == "LOW"
    assert "duplicate" in over[0].explanation


def test_no_false_duplicates_on_seeded_dataset(data):
    assert [(f.project_id, f.code) for f in det.detect_duplicate_postings(data)] == [("P02", "06-01")]


def test_under_pace_line_flagged_low(data):
    flags = [f for f in det.detect_cost_anomalies(data) if f.kind == "under-pace"]
    drywall = [f for f in flags if f.project_id == "P02" and f.code == "09-01"]
    assert drywall and drywall[0].severity == "LOW"
    assert all(f.severity == "LOW" for f in flags), "under-pace is a question, never an exposure"


def test_cascade_roofing_drift_and_burn(data):
    drift = [f for f in det.detect_budget_drift(data) if f.project_id == "P02" and f.code == "07-01"]
    assert drift and drift[0].severity == "MEDIUM"
    assert "No change order exists" in drift[0].explanation
    burn = [f for f in det.detect_cost_anomalies(data) if f.project_id == "P02" and f.code == "07-01"]
    assert burn and burn[0].severity == "MEDIUM"


def test_cascade_ridge_under_billed(data):
    pos = {b.project_id: b for b in det.compute_billing_position(data)}
    assert pos["P02"].severity == "MEDIUM"
    assert 0.08 < pos["P02"].under_billed / pos["P02"].earned_revenue < 0.12
    assert pos["P04"].severity == "NONE"
    assert pos["P02"].revised_contract >= 780_000   # approved COs revise the contract


# ---------------------------------------------------------------------------
# Budget drift: the seeded spread and the change-order cross-reference
# ---------------------------------------------------------------------------
def test_budget_drift_severity_spread(data):
    flags = det.detect_budget_drift(data)
    assert {"HIGH", "MEDIUM", "LOW"}.issubset({f.severity for f in flags})
    low = [f for f in flags if f.project_id == "P01" and f.code == "09-03"]   # Alderwood tile, +15%
    assert low and low[0].severity == "LOW"
    assert "No change order exists" in low[0].explanation


# ---------------------------------------------------------------------------
# Allowances
# ---------------------------------------------------------------------------
def test_allowance_overage_with_pending_co_is_high(data):
    hv = [a for a in det.detect_allowance_overages(data) if a.project_id == "P03" and a.issue == "Overage"]
    assert hv and hv[0].severity == "HIGH"
    assert "CO004" in hv[0].explanation and "unsigned" in hv[0].explanation


def test_allowance_overage_with_no_co_names_the_exposure(data):
    lighting = [a for a in det.detect_allowance_overages(data) if a.project_id == "P01" and a.code == "26-02"]
    assert lighting and lighting[0].severity == "MEDIUM"
    assert "absorbing" in lighting[0].explanation


def test_allowance_overage_covered_by_linked_approved_co_is_not_flagged(data):
    """Timberline flooring came in 24% over allowance, but a change order
    LINKED to that allowance is approved for the difference."""
    assert [a for a in det.detect_allowance_overages(data) if a.project_id == "P05" and a.issue == "Overage"] == []


def test_unrelated_co_on_same_code_does_not_clear_an_overage(data):
    """A change order on the same cost code for something else must not
    clear an allowance overage -- only one linked to the allowance does."""
    d = {k: v.copy() for k, v in data.items()}
    al = d["allowances"]
    target = al[(al["project_id"] == "P01") & (al["code"] == "26-02")].iloc[0]
    overage = target["selected_amount"] - target["allowance_amount"]
    extra = pd.DataFrame([{
        "co_id": "CO999", "project_id": "P01", "code": "26-02", "cost_amount": overage * 2,
        "amount": overage * 2.5, "submitted_date": pd.Timestamp("2025-06-01"),
        "approved_date": pd.Timestamp("2025-06-10"), "reason": "Something else entirely", "allowance_id": None,
    }])
    d["change_orders"] = pd.concat([d["change_orders"], extra], ignore_index=True)
    still = [a for a in det.detect_allowance_overages(d) if a.project_id == "P01" and a.code == "26-02"]
    assert still, "an unrelated CO on the same code wrongly cleared the overage"


def test_overdue_selection_flagged(data):
    late = [a for a in det.detect_allowance_overages(data) if a.project_id == "P01" and a.issue == "Selection overdue"]
    assert late and late[0].severity == "MEDIUM"
    assert "appliance" in late[0].explanation.lower()


def test_selection_made_late_is_reported_at_low(data):
    """A selection that was eventually made, but more than a week after the
    schedule needed it, is reported at LOW; a day or two late is not."""
    flags = det.detect_allowance_overages(data)
    late = {(a.project_id, a.code): a for a in flags if a.issue == "Selection late"}
    assert ("P01", "26-02") in late and late[("P01", "26-02")].severity == "LOW"   # 25 days
    assert ("P05", "09-02") in late                                                # 21 days
    assert ("P04", "09-03") not in late                                            # 2 days
    assert all(a.severity == "LOW" for a in late.values())


def test_allowances_are_priced_at_the_contract_not_at_cost(data):
    """An allowance is what the owner is told the scope is worth, so it
    carries the job's margin like every other contract dollar. Setting it
    at the builder's cost line means the owner spends the whole allowance
    and the builder earns nothing on that scope -- and on a premium house
    the allowances can be a tenth of the contract."""
    merged = (data["allowances"]
              .merge(data["project_budgets"], on=["project_id", "code"])
              .merge(data["projects"][["project_id", "margin_pct"]], on="project_id"))
    sell = merged["budgeted_amount"] / (1 - merged["margin_pct"])
    assert (merged["allowance_amount"] > merged["budgeted_amount"]).all()
    # rounded down to the nearest $500, so within that of the sell price
    assert ((sell - merged["allowance_amount"]).between(0, 500)).all()


# ---------------------------------------------------------------------------
# Commitments
# ---------------------------------------------------------------------------
def test_pool_bought_out_over_budget(data):
    pool = [f for f in det.detect_commitment_issues(data) if f.project_id == "P03" and f.code == "13-01"]
    assert pool and pool[0].kind == "buyout" and pool[0].severity == "LOW"


def test_subs_never_invoice_beyond_contract_except_the_duplicate(data):
    """True of the data itself, not just of the flags: invoiced-to-date
    never exceeds contract plus sub change orders anywhere but the seeded
    duplicate."""
    lines = data["commitment_lines"]
    over = lines[lines["invoiced_to_date"] > lines["line_amount"] + lines["co_amount"] + 0.01]
    assert list(zip(over["project_id"], over["code"], strict=True)) == [("P02", "06-01")]
    flags = [f for f in det.detect_commitment_issues(data) if f.kind == "over-invoiced"]
    assert [(f.project_id, f.code) for f in flags] == [("P02", "06-01")]
    # And the same has to hold at the contract, which is the document the
    # sub actually bills against.
    c = data["commitments"]
    over_contract = c[c["invoiced_to_date"] > c["contract_amount"] + c["co_amount"] + 0.01]
    assert list(over_contract["sub_name"]) == ["Cedarline Framing Co."]


def test_a_trade_holds_one_contract_per_job_covering_its_scopes(data):
    """A concrete sub signs one subcontract for the job and it covers
    foundations, slabs and flatwork as three scope lines. Writing a
    separate contract per CSI code would mean three certificates of
    insurance and three retention accounts from one firm on one site, and
    it makes "what are we committed to with this sub" a question you
    cannot answer without summing rows by hand."""
    c = data["commitments"]
    assert not c.duplicated(subset=["project_id", "sub_id"]).any()
    lines = data["commitment_lines"]
    # The header totals its own scope lines, to the cent.
    rolled = lines.groupby("commitment_id")[["line_amount", "co_amount"]].sum()
    joined = c.set_index("commitment_id")[["contract_amount", "co_amount"]].join(
        rolled, rsuffix="_lines")
    assert ((joined["contract_amount"] - joined["line_amount"]).abs() <= 0.01).all()
    assert ((joined["co_amount"] - joined["co_amount_lines"]).abs() <= 0.01).all()
    counts = lines.groupby("commitment_id").size()
    assert (c.set_index("commitment_id")["scope_lines"] == counts).all()
    # Multi-scope contracts have to actually exist, or the grouping is
    # doing nothing.
    assert (c["scope_lines"] > 1).sum() >= 20
    # Every scope line belongs to a contract, and every contract to a sub
    # that exists.
    assert set(lines["commitment_id"]) == set(c["commitment_id"])
    assert set(c["sub_id"]) <= set(data["subcontractors"]["sub_id"])


def test_retention_is_held_on_every_sub_draw(data):
    c = data["commitments"]
    gross = c["invoiced_to_date"] * c["retention_pct"]
    assert ((c["retention_held"] + c["retention_released"] - gross).abs() <= 0.01).all()
    assert ((c["paid_to_date"] - (c["invoiced_to_date"] - c["retention_held"])).abs() <= 0.01).all()
    assert (c["retention_held"] >= -0.01).all()
    # Retention is released when a scope closes out, not carried forever.
    assert (c["retention_released"] > 0).any(), "no scope has ever been closed out"
    open_scopes = c[(c["retention_released"] == 0) & (c["retention_pct"] > 0)]
    billed = open_scopes[open_scopes["invoiced_to_date"] > 0]
    assert (billed["paid_to_date"] < billed["invoiced_to_date"]).all()
    # A purchase order is the document written INSTEAD of a subcontract
    # with a retention clause, so it does not carry one.
    pos = c[c["commitment_type"] == "Purchase order"]
    assert (pos["retention_pct"] == 0).all()
    assert (pos["retention_held"] == 0).all()
    # Retention is the owner's security against punch and warranty. It
    # comes back at closeout, not when one trade's phase window shuts, so
    # on a portfolio of live jobs most of the clause is still held.
    clause = (c["invoiced_to_date"] * c["retention_pct"]).sum()
    assert c["retention_held"].sum() / clause > 0.80
    released = c[c["retention_released"] > 0]
    nearly_done = set(data["projects"].loc[data["projects"]["pct_complete"] >= 85, "project_id"])
    assert set(released["project_id"]) <= nearly_done


def test_no_commitment_is_signed_in_the_future(data):
    assert (data["commitments"]["signed_date"] <= pd.Timestamp(det.DATASET_AS_OF)).all()


def test_change_orders_carry_cost_and_sell_price(data):
    """A change order adds its cost to the budget and its sell price to the
    contract, marked up at the job's priced margin. That holds in both
    directions: a credit hands the owner back the margin too, so a deduct
    returns more to the contract than it takes off the budget."""
    co = data["change_orders"].merge(data["projects"][["project_id", "margin_pct"]], on="project_id")
    assert (co["amount"].abs() >= co["cost_amount"].abs()).all()
    assert (np.sign(co["amount"]) == np.sign(co["cost_amount"])).all()
    expected = co["cost_amount"] / (1 - co["margin_pct"])
    assert (abs(co["amount"] - expected) < 1.0).all()


def test_the_change_order_log_looks_like_a_real_one(data):
    """Custom work generates change orders continuously. A log with three
    items on a two-thirds-built house, all additive, is a log nobody kept.
    Credits exist, and a change big enough to move the sequence buys time
    with it."""
    co = data["change_orders"]
    per_project = co.groupby("project_id").size()
    assert per_project.min() >= 6, "too few change orders to be a real job"
    volume = (co.groupby("project_id")["amount"].sum()
              / data["projects"].set_index("project_id")["contract_value"])
    assert (volume.abs() > 0.015).all(), "change-order volume too low to be custom work"
    assert (co["amount"] < 0).sum() >= 4, "no credits anywhere in the log"
    assert (co["time_extension_days"] > 0).any(), "no change order ever bought time"
    # The remodel's unforeseen-conditions delay has paperwork behind it.
    p02 = co[(co["project_id"] == "P02") & (co["time_extension_days"] > 0)]
    assert not p02.empty and p02["time_extension_days"].max() >= 14


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------
def test_lapsed_insurance_flagged_with_no_cost_impact(data):
    flags = det.detect_compliance_flags(data)
    sub = [f for f in flags if f.name == "Alpine Millwork Partners"]
    assert sub and sub[0].severity == "HIGH" and sub[0].issue == "Insurance lapsed"
    assert "no cost impact" in sub[0].explanation.lower()


def test_lapsed_license_flagged(data):
    sub = [f for f in det.detect_compliance_flags(data) if f.name == "Timberwolf Excavation"]
    assert sub and sub[0].severity == "HIGH" and sub[0].issue == "License lapsed"


def test_expiring_soon_is_low_not_high(data):
    ng = [f for f in det.detect_compliance_flags(data) if f.name == "Northgate Electric"]
    assert ng and ng[0].severity == "LOW" and ng[0].issue == "Insurance expiring"


def test_clean_subcontractors_not_flagged(data):
    assert {f.name for f in det.detect_compliance_flags(data)} == {
        "Alpine Millwork Partners", "Timberwolf Excavation", "Northgate Electric",
    }


def test_sub_activity_matches_contracts_and_ledger(data):
    """A sub is active exactly where it holds a signed contract or has been
    paid, no more and no less, so a lapsed certificate flags on every job
    the firm is really on and on no job it is not."""
    subs = data["subcontractors"].set_index("name")
    paid = data["cost_transactions"][data["cost_transactions"]["type"] == "Subcontractor"]
    expected = {}
    for name, grp in paid.groupby("vendor"):
        expected.setdefault(name, set()).update(grp["project_id"])
    for _, c in data["commitments"].iterrows():
        expected.setdefault(c["sub_name"], set()).add(c["project_id"])
    for name, row in subs.iterrows():
        assert set(det._active_projects(row)) == expected.get(name, set()), name
    assert det._active_projects(subs.loc["Alpine Millwork Partners"]) == ["P03"]


def test_active_project_match_is_exact_not_substring():
    row = pd.Series({"active_projects": "P010;P02"})
    assert "P01" not in det._active_projects(row)


# ---------------------------------------------------------------------------
# Change-order aging
# ---------------------------------------------------------------------------
def test_co_aging_exposure_detected(data):
    flags = {f.co_id: f for f in det.detect_co_aging(data)}
    assert flags["CO001"].severity == "HIGH" and flags["CO001"].days_unapproved > 60
    assert flags["CO002"].severity == "MEDIUM" and 45 < flags["CO002"].days_unapproved <= 60
    assert flags["CO004"].severity == "LOW" and 30 <= flags["CO004"].days_unapproved <= 45


def test_approved_change_orders_never_age(data):
    co = data["change_orders"]
    approved_ids = set(co[co["approved_date"].notna()]["co_id"])
    assert approved_ids.isdisjoint({f.co_id for f in det.detect_co_aging(data)})


def test_change_orders_fall_inside_their_project(data):
    co = data["change_orders"].merge(data["projects"][["project_id", "start_date"]], on="project_id")
    assert (co["submitted_date"] >= co["start_date"]).all()


# ---------------------------------------------------------------------------
# Schedule risk and data coherence
# ---------------------------------------------------------------------------
def test_schedule_risk_severity_spread(data):
    assert len({r.severity for r in det.compute_schedule_risk(data)}) >= 3


def test_cascade_ridge_worst_schedule_risk(data):
    r = {x.project_id: x for x in det.compute_schedule_risk(data)}
    assert r["P02"].severity == "HIGH"
    assert r["P02"].weather_delay_days >= r["P04"].weather_delay_days
    assert r["P02"].other_delay_days > 0 and "existing structure" in r["P02"].explanation


def test_reported_pct_complete_is_the_sov_rollup(data):
    """The project's % complete is the cost-weighted roll-up of per-line %
    complete on the schedule of values, not a number anyone typed in."""
    b = data["project_budgets"]
    rollup = (b["budgeted_amount"] * b["pct_complete"]).groupby(b["project_id"]).sum() \
        / b.groupby("project_id")["budgeted_amount"].sum()
    for _, p in data["projects"].iterrows():
        assert abs(rollup[p["project_id"]] - p["pct_complete"]) < 0.6


def test_schedule_data_is_internally_coherent(data):
    """Reported progress, baseline dates and forecast slip have to agree:
    a job this far into its baseline with this much earned IS this many
    days behind, and its forecast finish must carry that slip."""
    as_of = pd.Timestamp(det.DATASET_AS_OF)
    ms = data["schedule_milestones"]
    for _, p in data["projects"].iterrows():
        pm = ms[ms["project_id"] == p["project_id"]].sort_values("baseline_date")
        # invert the cost-weighted planned curve to find where the job's
        # reported progress sits on the baseline calendar
        import numpy as np
        planned = pm["planned_pct_complete"].to_numpy()
        days = (pm["baseline_date"] - p["start_date"]).dt.days.to_numpy()
        day_at_reported = float(np.interp(p["pct_complete"] / 100.0, planned, days))
        implied_slip = (as_of - p["start_date"]).days - day_at_reported
        terminal = pm.iloc[-1]
        forecast_slip = (terminal["forecast_date"] - terminal["baseline_date"]).days
        assert abs(forecast_slip - max(implied_slip, 0)) <= 12, (
            f"{p['project_id']}: progress implies ~{implied_slip:.0f}d behind, forecast carries {forecast_slip}d"
        )


def test_transactions_are_phased_to_the_schedule(data):
    """Framing money goes out between foundation-complete and framing-
    complete, not before the foundation is poured."""
    tx, ms = data["cost_transactions"], data["schedule_milestones"]
    for pid in data["projects"]["project_id"]:
        framing = tx[(tx["project_id"] == pid) & (tx["code"] == "06-01")]
        if framing.empty:
            continue
        m = ms[ms["project_id"] == pid].set_index("milestone")
        foundation = m.loc["Foundation Complete", "actual_date"]
        framing_done = m.loc["Framing Complete", "actual_date"]
        assert framing["date"].min() >= foundation - pd.Timedelta(days=21)
        assert framing["date"].max() <= framing_done + pd.Timedelta(days=21)


def test_budgets_sit_below_contract_by_the_priced_margin(data):
    b = data["project_budgets"].groupby("project_id")["budgeted_amount"].sum()
    for _, p in data["projects"].iterrows():
        assert b[p["project_id"]] == pytest.approx(p["contract_value"] * (1 - p["margin_pct"]), rel=0.001)


def test_projects_only_carry_scope_they_have(data):
    """Scope a job does not have is absent from its budget, not carried at
    zero, and scope it does have is present: the commercial shell has the
    special inspections, toilet accessories and signage no house has, and
    no house carries any of them."""
    b = data["project_budgets"]
    remodel = set(b[b["project_id"] == "P02"]["code"])
    commercial = set(b[b["project_id"] == "P05"]["code"])
    cottage = set(b[b["project_id"] == "P04"]["code"])
    houses = set(b[b["project_id"].isin(["P01", "P03", "P04"])]["code"])
    assert not {"13-01", "13-02", "14-01"} & remodel
    assert not {"13-01", "14-01", "10-01"} & commercial     # no pool, lift or fireplace
    assert not {"13-01", "25-01"} & cottage
    assert {"01-05", "10-03", "10-04"} <= commercial        # inspections, accessories, signage
    assert not {"01-05", "10-03", "10-04"} & houses
    assert (b["budgeted_amount"] > 0).all()


def test_commercial_is_not_a_house_with_a_different_label(data):
    """The ground-up commercial job has to read as a different building:
    steel and slab carry it, wood framing is partitions and blocking. A
    commercial shell whose biggest line is rough framing is a residential
    budget with the name changed, and a builder spots that immediately."""
    b = data["project_budgets"].merge(data["cost_codes"][["code", "division"]], on="code")
    by_div = b[b["project_id"] == "P05"].groupby("division")["budgeted_amount"].sum()
    total = by_div.sum()
    assert by_div["05"] > by_div["06"], "structural steel must outweigh wood framing"
    assert by_div["05"] / total > 0.07, "metals should be a real share of a commercial shell"
    assert by_div["06"] / total < 0.08, "wood framing should be a minor share"
    assert by_div["03"] / total > 0.10, "slab and foundations carry a commercial building"

    house = b[b["project_id"] == "P01"].groupby("division")["budgeted_amount"].sum()
    house_total = house.sum()
    assert house["06"] / house_total > 0.12, "wood framing carries a house"
    assert house["06"] > house["05"] * 4


def test_concrete_outweighs_discretionary_scope(data):
    """Foundations hold the house up; a pool does not. A dataset where the
    pool costs more than all the concrete, or the elevator more than the
    foundation, is one no builder reads past."""
    b = data["project_budgets"].merge(data["cost_codes"][["code", "division"]], on="code")
    for pid in data["projects"]["project_id"]:
        p = b[b["project_id"] == pid]
        concrete = p[p["division"] == "03"]["budgeted_amount"].sum()
        total = p["budgeted_amount"].sum()
        special = p[p["division"].isin(["13", "14"])]["budgeted_amount"].sum()
        foundations = p[p["code"] == "03-01"]["budgeted_amount"].sum()
        assert concrete > special, f"{pid}: special construction outweighs all concrete"
        for code in ("13-01", "14-01"):
            line = p[p["code"] == code]["budgeted_amount"].sum()
            assert line < foundations, f"{pid}: {code} outweighs the foundation"
        if pid != "P02":   # a remodel keeps the foundation it already has
            assert 0.06 <= concrete / total <= 0.18, f"{pid}: concrete at {concrete / total:.1%}"


def test_general_conditions_are_bought_by_the_month(data):
    """Division 01 is a monthly cost, not a percentage of contract. The
    test that matters is the rate: a job carrying two thousand dollars a
    month of supervision is not being supervised."""
    b = data["project_budgets"].merge(data["cost_codes"][["code", "division"]], on="code")
    for _, p in data["projects"].iterrows():
        months = (p["end_date"] - p["start_date"]).days / 30.44
        div01 = b[(b["project_id"] == p["project_id"]) & (b["division"] == "01")]["budgeted_amount"].sum()
        per_month = div01 / months
        assert 5_500 <= per_month <= 20_000, f"{p['project_id']}: ${per_month:,.0f}/month of general conditions"


def test_fairhaven_reads_clear(data):
    """P04 (Fairhaven, no seeded anomalies) has to read CLEAR on every
    signal, or the detection logic is crying wolf on a healthy job, and
    the schedule line has to say so in words."""
    row = det.portfolio_rollup(data).set_index("project_id").loc["P04"]
    assert row["overall_severity"] == "NONE"
    for col in det.ROLLUP_COLUMNS:
        assert row[col] == "NONE", f"Fairhaven {col} should be clear, got {row[col]}"
    sched = {r.project_id: r for r in det.compute_schedule_risk(data)}["P04"]
    assert "tracking on schedule" in sched.explanation


def test_progress_check_is_quiet_when_the_walk_matches_cost(data):
    """On the seeded data the super's roll-up and cost-to-cost progress agree
    within a few points on every job; the divergence flag must not fire on
    ordinary jobs."""
    for f in det.compute_cost_forecast(data):
        assert f.progress_severity == "NONE", f"{f.project_id}: {f.explanation}"


def test_rollup_overall_is_worst_of_its_signals(data):
    for _, row in det.portfolio_rollup(data).iterrows():
        assert row["overall_severity"] == det._worst(row[c] for c in det.ROLLUP_COLUMNS)


# ---------------------------------------------------------------------------
# Cross-project pattern detection
# ---------------------------------------------------------------------------
def test_cross_project_pattern_detected_on_constructed_data():
    budgets = pd.DataFrame([
        {"project_id": "P01", "code": "06-01", "variance_pct": 0.25, "variance_amount": 20_000},
        {"project_id": "P02", "code": "06-01", "variance_pct": 0.30, "variance_amount": 25_000},
        {"project_id": "P03", "code": "06-01", "variance_pct": 0.22, "variance_amount": 18_000},
        {"project_id": "P04", "code": "06-01", "variance_pct": 0.02, "variance_amount": 500},
        {"project_id": "P01", "code": "09-02", "variance_pct": 0.40, "variance_amount": 12_000},
    ])
    patterns = det.cross_project_patterns(budgets, min_projects=3, variance_threshold=0.10)
    assert len(patterns) == 1 and patterns.iloc[0]["division"] == "06"


def test_cross_project_pattern_ignores_immaterial_dollars():
    budgets = pd.DataFrame([
        {"project_id": p, "code": "10-01", "variance_pct": 0.15, "variance_amount": 300}
        for p in ("P01", "P02", "P03")
    ])
    assert det.cross_project_patterns(budgets).empty


def test_cross_project_pattern_finds_the_estimating_problem(data):
    """The one signal no single project view can show you: earthwork is
    over on three separate jobs by a similar margin. On any one of them it
    is a small flag; across three it is a unit rate in the estimate, and
    the point of the check is to tell those two situations apart."""
    flags = det.detect_cross_project_patterns(data)
    assert len(flags) == 1, [f.explanation for f in flags]
    pattern = flags[0]
    assert pattern.code == "31" and pattern.project_id == "PORTFOLIO"
    assert pattern.variance_amount > 20_000
    assert "3 separate projects" in pattern.explanation
    assert "estimated" in pattern.explanation


def test_cross_project_pattern_needs_more_than_one_bad_job(data):
    """And it must not fire on a division that is over on one job only,
    which is the whole difference between a site problem and a pricing
    problem."""
    budgets = det.effective_budgets(data)
    hit = det.cross_project_patterns(budgets, min_projects=3, variance_threshold=0.10)
    for _, row in hit.iterrows():
        assert row["projects_affected"] >= 3
    # Harborview's cabinetry is the largest single overrun in the dataset
    # and appears on exactly one job, so division 06 must not be here.
    assert "06" not in set(hit["division"])


# ---------------------------------------------------------------------------
# Noise control and edge cases
# ---------------------------------------------------------------------------
def test_unseeded_cost_codes_mostly_quiet(data):
    flags = det.detect_cost_anomalies(data)
    total = len(det.effective_budgets(data))
    assert len(flags) <= total * 0.05, f"{len(flags)} of {total} lines flagged"


def test_the_ledger_has_the_shape_of_a_real_ledger(data):
    """Nobody cuts a $200 sub draw or splits a permit fee four ways. But a
    construction AP ledger is mostly small: dumpster pulls, fuel, fasteners,
    portables. A dataset whose smallest transaction is $500 has never been
    reconciled by anyone, and a detector tuned on one will not survive
    contact with a real extract."""
    tx = data["cost_transactions"]
    subs = tx[tx["type"] == "Subcontractor"]
    assert subs["amount"].median() >= 4_000, "sub draws are progress payments, not petty cash"
    assert subs["amount"].min() >= 200
    permits = tx[tx["type"] == "Permits & Fees"].groupby("project_id").size()
    assert (permits <= 2).all()
    assert (tx["amount"] < 500).mean() > 0.15, "no small-dollar tail at all"
    assert tx["amount"].min() >= 25, "and nothing so small it is a rounding artifact"
    # Enough line volume per job that the burn-rate checks are reading a
    # ledger rather than a handful of lumps, and an average invoice size
    # in the range a GC's accounts payable actually runs at. Both matter:
    # a small line count with a realistic average means the small stuff is
    # missing, and a large line count with a tiny average means the ledger
    # has been padded to look busy.
    per_project = tx.groupby("project_id").size()
    assert per_project.min() >= 200
    assert 1_200 <= tx["amount"].mean() <= 4_000
    assert tx["amount"].median() <= 1_200
    months = ((pd.Timestamp(det.DATASET_AS_OF) - data["projects"].set_index("project_id")["start_date"])
              .dt.days / 30.44)
    per_month = per_project / months
    assert per_month.min() >= 15, f"thin ledger: {per_month.round(1).to_dict()}"
    # Payroll is coded by labor class and pay period, not as one lump a
    # month: "why is supervision over" has to be answerable from the
    # ledger rather than from a box of timesheets.
    labor = tx[tx["type"] == "Labor"]
    assert labor["vendor"].nunique() >= 3


def _tiny_dataset(line_pct=50, transactions=None):
    """A one-project, one-code dataset for edge-case tests."""
    tx = transactions if transactions is not None else []
    return {
        "projects": pd.DataFrame([{
            "project_id": "PX", "name": "Edge", "type": "New Custom Home", "finish_tier": "Standard",
            "contract_value": 1_250_000, "margin_pct": 0.20,
            "start_date": pd.Timestamp("2025-01-01"), "end_date": pd.Timestamp("2025-12-31"),
            "pct_complete": line_pct, "billed_to_date": 1_250_000 * line_pct / 100,
            "retainage_pct": 0.05, "retainage_held": 0.0, "status": "Active",
        }]),
        "cost_codes": pd.DataFrame([{
            "code": "06-01", "division": "06", "division_name": "Wood", "description": "Rough Framing",
            "typical_share_of_division": 0.45, "phase_start": 0.1, "phase_end": 0.3,
            "trade": "Framing", "supplier": "X", "gc_buys_material": True,
        }]),
        "project_budgets": pd.DataFrame([{"project_id": "PX", "code": "06-01",
                                          "budgeted_amount": 1_000_000, "approved_co_cost": 0.0,
                                          "current_budget": 1_000_000, "pct_complete": line_pct}]),
        "budget_revisions": pd.DataFrame(columns=["revision_id", "project_id", "code", "revised_amount", "date", "reason"]),
        "schedule_milestones": pd.DataFrame(columns=[
            "milestone_id", "project_id", "milestone", "baseline_date", "forecast_date", "actual_date",
            "critical_path", "weather_exposed", "weather_delay_days", "other_delay_days", "delay_reason",
            "planned_pct_complete"]),
        "allowances": pd.DataFrame(columns=["allowance_id", "project_id", "code", "description",
                                            "allowance_amount", "selected_amount", "selection_due", "selection_date"]),
        "change_orders": pd.DataFrame(columns=["co_id", "project_id", "code", "cost_amount", "amount",
                                               "submitted_date", "approved_date", "time_extension_days",
                                               "reason", "allowance_id"]),
        "commitments": pd.DataFrame(columns=["commitment_id", "project_id", "sub_id", "sub_name", "trade",
                                             "contract_amount", "commitment_type", "scope_lines",
                                             "co_amount", "signed_date", "retention_pct",
                                             "invoiced_to_date", "retention_held", "retention_released",
                                             "paid_to_date"]),
        "commitment_lines": pd.DataFrame(columns=["commitment_id", "project_id", "code", "sub_id",
                                                  "sub_name", "line_amount", "co_amount",
                                                  "invoiced_to_date"]),
        "cost_transactions": pd.DataFrame(tx, columns=["transaction_id", "project_id", "code", "date",
                                                       "amount", "vendor", "type"]),
        "subcontractors": pd.DataFrame(columns=["sub_id", "name", "trade", "insurance_expiry",
                                                "license_expiry", "active_projects"]),
    }


def test_no_transactions_means_no_cost_flags():
    data = _tiny_dataset()
    burn = [f for f in det.detect_cost_anomalies(data) if f.kind == "burn-rate"]
    assert burn == []
    assert det.detect_duplicate_postings(data) == []
    assert det.detect_cross_project_patterns(data) == []


def test_spend_on_a_zero_percent_line_is_flagged():
    """Money on a line reported 0% complete has no pace to be measured
    against; it is flagged on dollars alone, without dividing by zero."""
    tx = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
           "date": pd.Timestamp("2025-02-01"), "amount": 20_000, "vendor": "V", "type": "Subcontractor"}]
    data = _tiny_dataset(line_pct=0, transactions=tx)
    row = det.effective_budgets(data).iloc[0]
    assert row["expected_spend_to_date"] == 0 and row["forecast_at_completion"] >= 1_000_000
    flags = det.detect_cost_anomalies(data)
    assert flags and flags[0].kind == "unstarted" and flags[0].severity == det.UNSTARTED_LINE_SEVERITY
    assert "$20,000" in flags[0].explanation


def test_spend_on_an_unbudgeted_line_is_flagged():
    tx = [{"transaction_id": "T1", "project_id": "PX", "code": "09-99",
           "date": pd.Timestamp("2025-02-01"), "amount": 12_000, "vendor": "V", "type": "Material"}]
    flags = det.detect_cost_anomalies(_tiny_dataset(transactions=tx))
    stray = [f for f in flags if f.kind == "unbudgeted"]
    assert stray and stray[0].code == "09-99" and "no budget for" in stray[0].explanation


def test_empty_side_tables_yield_no_flags():
    """Nothing in a table means nothing to say about it -- with one
    deliberate exception, below."""
    tx = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
           "date": pd.Timestamp("2025-03-01"), "amount": 500_000, "vendor": "V", "type": "Subcontractor"}]
    data = _tiny_dataset(transactions=tx)
    assert det.detect_budget_drift(data) == []
    assert det.detect_co_aging(data) == []
    assert det.detect_compliance_flags(data) == []
    assert det.detect_allowance_overages(data) == []
    assert det.detect_commitment_issues(data) == []


def test_a_job_with_no_schedule_does_not_read_as_a_job_on_schedule():
    """The exception. Missing data and good news must not look the same:
    an empty schedule used to return SPI 1.00 and CLEAR, which is the
    reading a superintendent would most like and the one an owner would
    least forgive. It is surfaced instead, in the words that say what is
    actually wrong."""
    tx = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
           "date": pd.Timestamp("2025-03-01"), "amount": 500_000, "vendor": "V", "type": "Subcontractor"}]
    data = _tiny_dataset(transactions=tx)
    sched = det.compute_schedule_risk(data)
    assert len(sched) == 1
    assert sched[0].severity != "NONE"
    assert "no critical-path schedule" in sched[0].explanation
    assert det.portfolio_rollup(data).iloc[0]["schedule_severity"] != "NONE"


def test_critical_path_finish_ignores_a_later_non_critical_milestone():
    """The app's charts read the job's finish date off the same terminal
    critical-path milestone compute_schedule_risk uses for slip -- never
    the last row by baseline_date and never the latest forecast_date
    across every milestone, either of which a non-critical marker (added
    after the real finish, or simply forecast later than it) can win."""
    pm = pd.DataFrame([
        {"milestone_id": "M1", "project_id": "PX", "milestone": "Framing",
         "baseline_date": pd.Timestamp("2025-03-01"), "forecast_date": pd.Timestamp("2025-03-10"),
         "critical_path": True},
        {"milestone_id": "M2", "project_id": "PX", "milestone": "Substantial Completion",
         "baseline_date": pd.Timestamp("2025-06-01"), "forecast_date": pd.Timestamp("2025-06-15"),
         "critical_path": True},
        # Sorts last by baseline_date AND carries the latest forecast_date
        # of any row, but is not on the critical path -- an owner walkthrough
        # penciled in after closeout, say.
        {"milestone_id": "M3", "project_id": "PX", "milestone": "Owner Walkthrough",
         "baseline_date": pd.Timestamp("2025-07-01"), "forecast_date": pd.Timestamp("2025-08-01"),
         "critical_path": False},
    ]).sort_values("baseline_date")
    finish = det.critical_path_terminal_finish(pm)
    assert finish == pd.Timestamp("2025-06-15")
    # Both bugs this guards against, made explicit:
    assert pm.iloc[-1]["forecast_date"] != finish  # last row by baseline_date
    assert pm["forecast_date"].max() != finish  # max() across every milestone


def test_critical_path_finish_is_none_without_a_critical_path_or_a_date():
    empty_cp = pd.DataFrame([
        {"milestone_id": "M1", "project_id": "PX", "milestone": "Framing",
         "baseline_date": pd.Timestamp("2025-03-01"), "forecast_date": pd.Timestamp("2025-03-10"),
         "critical_path": False},
    ])
    assert det.critical_path_terminal_finish(empty_cp) is None
    no_forecast = pd.DataFrame([
        {"milestone_id": "M1", "project_id": "PX", "milestone": "Substantial Completion",
         "baseline_date": pd.Timestamp("2025-03-01"), "forecast_date": None,
         "critical_path": True},
    ])
    assert det.critical_path_terminal_finish(no_forecast) is None


def test_load_data_generates_into_the_requested_directory(tmp_path):
    """data/ is not committed; load_data() must build it on first run, in
    the directory it was asked for, without touching the repo's copy."""
    repo_copy = os.path.join(det.DATA_DIR, "projects.csv")
    before = os.stat(repo_copy).st_mtime_ns
    target = tmp_path / "somewhere"
    data = det.load_data(str(target))
    assert (target / "projects.csv").exists()
    assert len(data["projects"]) == 5
    assert os.stat(repo_copy).st_mtime_ns == before


# ---------------------------------------------------------------------------
# Bring-your-own-data upload path
# ---------------------------------------------------------------------------
def _demo_files():
    """Open every bundled CSV as a fresh file handle, keyed by filename --
    stands in for what Streamlit's file_uploader hands the app when a
    visitor picks their own files."""
    det.ensure_data(det.DATA_DIR)
    return {name: open(os.path.join(det.DATA_DIR, name), "rb") for name in det.CSV_FILES}


def test_load_data_from_files_accepts_the_demo_dataset_as_its_own_upload():
    """The bundled dataset is itself valid input to the upload path -- the
    schema the README promises visitors is exactly the schema load_data
    already produces, checked here so the two can never quietly drift."""
    files = _demo_files()
    try:
        data, errors = det.load_data_from_files(files)
    finally:
        for f in files.values():
            f.close()
    assert errors == []
    assert set(data) == {n[:-4] for n in det.CSV_FILES}
    assert len(data["projects"]) == 5
    # Dates come back parsed, not as strings, same as load_data.
    assert pd.api.types.is_datetime64_any_dtype(data["projects"]["start_date"])


def test_load_data_from_files_reports_every_missing_file_by_name():
    files = _demo_files()
    try:
        del files["subcontractors.csv"]
        data, errors = det.load_data_from_files(files)
    finally:
        for f in files.values():
            f.close()
    assert data is None
    assert len(errors) == 1 and "subcontractors.csv" in errors[0]


def test_load_data_from_files_reports_a_missing_column_by_name_not_a_traceback():
    files = _demo_files()
    try:
        df = pd.read_csv(files["projects.csv"])
        files["projects.csv"].close()
        df = df.drop(columns=["contract_value"])
        files["projects.csv"] = io.StringIO(df.to_csv(index=False))
        data, errors = det.load_data_from_files(files)
    finally:
        for f in files.values():
            f.close()
    assert data is None
    assert any("projects.csv" in e and "contract_value" in e for e in errors)


def test_load_data_from_files_rejects_an_unreadable_file_without_raising():
    """A file that cannot be parsed as a CSV at all is reported by name,
    not raised -- a visitor sees a message, not a stack trace."""
    files = _demo_files()
    try:
        files["projects.csv"].close()
        files["projects.csv"] = io.BytesIO(b"\xff\xfe\x01\x02\x03\x04not utf-8 at all")
        data, errors = det.load_data_from_files(files)
    finally:
        for f in files.values():
            f.close()
    assert data is None
    assert any("projects.csv" in e for e in errors)


def test_load_data_from_files_rejects_an_empty_load_bearing_table():
    files = _demo_files()
    try:
        files["projects.csv"].close()
        files["projects.csv"] = io.StringIO(",".join(det.REQUIRED_COLUMNS["projects.csv"]) + "\n")
        data, errors = det.load_data_from_files(files)
    finally:
        for f in files.values():
            f.close()
    assert data is None
    assert any("projects.csv" in e and "no rows" in e for e in errors)


def test_generator_and_detector_share_one_as_of_date():
    import dataset_config
    assert det.DATASET_AS_OF == dataset_config.AS_OF


def test_generator_is_byte_identical(tmp_path):
    import hashlib
    a, b = tmp_path / "a", tmp_path / "b"
    det.ensure_data(str(a))
    det.ensure_data(str(b))
    for name in det.CSV_FILES:
        assert hashlib.md5((a / name).read_bytes()).hexdigest() == hashlib.md5((b / name).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# The arithmetic itself.
#
# Everything above checks that the right things get flagged on the seeded
# dataset. That is not the same as checking the formulas, and a suite that
# only asserts severity labels will stay green through an inverted CPI, a
# swapped numerator, or a band edge moved by a hundredth -- all of which
# change the number a builder acts on while leaving the label alone. These
# assert computed values against hand arithmetic on constructed inputs.
# ---------------------------------------------------------------------------
def _evm_dataset(budget, line_pct, spend):
    """One line, one draw. Earned value and actual cost are then known
    exactly, so CPI is known exactly."""
    tx = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
           "date": pd.Timestamp("2025-03-01"), "amount": spend, "vendor": "V", "type": "Subcontractor"}]
    data = _tiny_dataset(line_pct=line_pct, transactions=tx)
    data["project_budgets"].loc[0, "budgeted_amount"] = budget
    data["project_budgets"].loc[0, "current_budget"] = budget
    data["schedule_milestones"] = pd.DataFrame([
        {"milestone_id": "M1", "project_id": "PX", "milestone": "Start",
         "baseline_date": pd.Timestamp("2025-01-01"), "forecast_date": pd.Timestamp("2025-01-01"),
         "actual_date": pd.Timestamp("2025-01-01"), "critical_path": True, "weather_exposed": False,
         "weather_delay_days": 0, "other_delay_days": 0, "delay_reason": None,
         "planned_pct_complete": 0.0},
        {"milestone_id": "M2", "project_id": "PX", "milestone": "Substantial Completion",
         "baseline_date": pd.Timestamp("2025-12-31"), "forecast_date": pd.Timestamp("2025-12-31"),
         "actual_date": None, "critical_path": True, "weather_exposed": False,
         "weather_delay_days": 0, "other_delay_days": 0, "delay_reason": None,
         "planned_pct_complete": 1.0},
    ])
    return data


def test_cpi_is_earned_value_over_actual_cost_not_the_other_way_up():
    """CPI below 1.00 means the job is spending more than it is earning.
    Inverting it turns every overrun into good news, and no severity label
    in this suite would notice."""
    data = _evm_dataset(budget=1_000_000, line_pct=50, spend=625_000)
    risk = det.compute_schedule_risk(data)[0]
    # earned value = 1,000,000 x 0.50 = 500,000; actual = 625,000
    assert risk.cpi == pytest.approx(0.80, abs=0.005)
    assert risk.cpi < 1.0

    under = det.compute_schedule_risk(_evm_dataset(1_000_000, 50, 400_000))[0]
    assert under.cpi == pytest.approx(1.25, abs=0.005)


def test_spi_is_progress_against_the_baseline_curve():
    """SPI reads earned percent against what the BASELINE plan said would
    be earned by now. Feeding it the forecast curve instead makes every
    late job score 1.00, because the forecast has already absorbed the
    delay -- the job always looks on time against its own slip."""
    data = _evm_dataset(budget=1_000_000, line_pct=40, spend=400_000)
    # The two curves have to actually differ or this test cannot see which
    # one is being read. Baseline finishes on the as-of date, so it plans
    # 80% earned by now; the forecast finishes 200 days later, so the same
    # interpolation against it plans well under 80% and flatters the job.
    finish_baseline = pd.Timestamp(det.DATASET_AS_OF)
    finish_forecast = finish_baseline + pd.Timedelta(days=200)
    data["schedule_milestones"] = pd.DataFrame([
        {"milestone_id": "M1", "project_id": "PX", "milestone": "Start",
         "baseline_date": pd.Timestamp("2025-01-01"), "forecast_date": pd.Timestamp("2025-01-01"),
         "actual_date": pd.Timestamp("2025-01-01"), "critical_path": True, "weather_exposed": False,
         "weather_delay_days": 0, "other_delay_days": 0, "delay_reason": None,
         "planned_pct_complete": 0.0},
        {"milestone_id": "M2", "project_id": "PX", "milestone": "Substantial Completion",
         "baseline_date": finish_baseline, "forecast_date": finish_forecast,
         "actual_date": None, "critical_path": True, "weather_exposed": False,
         "weather_delay_days": 0, "other_delay_days": 0, "delay_reason": None,
         "planned_pct_complete": 0.80},
    ])
    risk = det.compute_schedule_risk(data)[0]
    assert risk.spi == pytest.approx(0.40 / 0.80, abs=0.01)
    # And the forecast curve would have scored it materially better, which
    # is the whole reason the column matters.
    off_forecast = 0.40 / float(np.interp(
        (finish_baseline - pd.Timestamp("2025-01-01")).days,
        [0, (finish_forecast - pd.Timestamp("2025-01-01")).days], [0.0, 0.80]))
    assert off_forecast > risk.spi + 0.15


def test_severity_bands_are_half_open_and_no_value_lands_in_two():
    """Every band is [low, high). A band edge made inclusive at the top
    puts a value in two bands at once and the answer becomes whichever one
    the loop happens to reach first."""
    for name in ("PCT_BANDS", "EVM_GAP_BANDS", "SLIP_DAYS_BANDS", "CO_AGING_DAYS_BANDS",
                 "SELECTION_OVERDUE_DAYS_BANDS", "UNDER_BILLING_BANDS", "OVER_BILLING_BANDS",
                 "MARGIN_EROSION_BANDS", "PROGRESS_GAP_BANDS"):
        bands = getattr(det, name)
        assert bands["LOW"][1] == bands["MEDIUM"][0], f"{name} has a gap or overlap at LOW/MEDIUM"
        assert bands["MEDIUM"][1] == bands["HIGH"][0], f"{name} has a gap or overlap at MEDIUM/HIGH"
        for level in ("LOW", "MEDIUM", "HIGH"):
            lo, hi = bands[level]
            assert det._band_for(lo, bands) == level, f"{name} {level}: floor {lo} not in its own band"
            if hi != float("inf"):
                assert det._band_for(hi, bands) != level, f"{name} {level}: top {hi} is in two bands"
        assert det._band_for(bands["LOW"][0] - 1e-9, bands) == "NONE"


def test_pct_bands_sit_exactly_where_they_are_documented():
    """Pinned to the documented numbers. A band edge that drifts changes
    what gets a phone call today versus this week, and nothing else in the
    suite reads the edges directly."""
    assert det.PCT_BANDS["LOW"][0] == pytest.approx(0.10)
    assert det.PCT_BANDS["MEDIUM"][0] == pytest.approx(0.20)
    assert det.PCT_BANDS["HIGH"][0] == pytest.approx(0.35)
    assert det.pct_severity(0.0999) == "NONE"
    assert det.pct_severity(0.10) == "LOW"
    assert det.pct_severity(0.1999) == "LOW"
    assert det.pct_severity(0.20) == "MEDIUM"
    assert det.pct_severity(0.3499) == "MEDIUM"
    assert det.pct_severity(0.35) == "HIGH"
    assert det.pct_severity(-0.40) == "HIGH"   # banded on magnitude


def test_variance_is_measured_against_the_budget_plus_approved_changes():
    """An approved change order raises the line it is measured against.
    Measuring spend against the baseline instead manufactures an overrun
    on work the owner has already agreed to pay for -- the single most
    common way a job-cost report loses its reader."""
    tx = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
           "date": pd.Timestamp("2025-03-01"), "amount": 600_000, "vendor": "V", "type": "Subcontractor"}]
    data = _tiny_dataset(line_pct=50, transactions=tx)
    data["change_orders"] = pd.DataFrame([{
        "co_id": "CO1", "project_id": "PX", "code": "06-01", "cost_amount": 200_000,
        "amount": 250_000, "submitted_date": pd.Timestamp("2025-02-01"),
        "approved_date": pd.Timestamp("2025-02-10"), "time_extension_days": 0,
        "reason": "Owner addition", "allowance_id": None,
    }])
    row = det.effective_budgets(data).iloc[0]
    assert row["current_budget"] == 1_200_000            # 1,000,000 + the CO's COST
    assert row["expected_spend_to_date"] == 600_000      # x 50% complete
    assert row["variance_amount"] == pytest.approx(0.0)
    assert [f for f in det.detect_cost_anomalies(data) if f.kind == "burn-rate"] == []


def test_earned_revenue_uses_the_revised_contract_not_the_original():
    """Approved change orders are revenue. Earning against the original
    contract under-reports what the job has earned and turns a correctly
    billed job into an over-billed one."""
    data = _tiny_dataset(line_pct=50)
    data["projects"].loc[0, "billed_to_date"] = 750_000
    data["change_orders"] = pd.DataFrame([{
        "co_id": "CO1", "project_id": "PX", "code": "06-01", "cost_amount": 200_000,
        "amount": 250_000, "submitted_date": pd.Timestamp("2025-02-01"),
        "approved_date": pd.Timestamp("2025-02-10"), "time_extension_days": 0,
        "reason": "Owner addition", "allowance_id": None,
    }])
    pos = det.compute_billing_position(data)[0]
    assert pos.revised_contract == 1_500_000                 # 1,250,000 + 250,000 sell
    assert pos.earned_revenue == pytest.approx(750_000)      # x 50%
    assert pos.under_billed == pytest.approx(0.0)
    assert pos.severity == "NONE"


def test_buyout_is_measured_against_the_original_budget():
    """Buyout compares the sub's contract to what the job was SOLD at. If
    it read the revised budget instead, editing the budget up to match the
    contract would erase the flag -- which is exactly the move the drift
    check exists to catch."""
    data = _tiny_dataset(line_pct=50)
    data["commitments"] = pd.DataFrame([{
        "commitment_id": "SC1", "project_id": "PX", "sub_id": "S1", "sub_name": "Sub",
        "trade": "Framing", "contract_amount": 1_500_000, "commitment_type": "Subcontract",
        "scope_lines": 1, "co_amount": 0.0, "signed_date": pd.Timestamp("2025-02-01"),
        "retention_pct": 0.1, "invoiced_to_date": 0.0, "retention_held": 0.0,
        "retention_released": 0.0, "paid_to_date": 0.0,
    }])
    data["commitment_lines"] = pd.DataFrame([{
        "commitment_id": "SC1", "project_id": "PX", "code": "06-01", "sub_id": "S1",
        "sub_name": "Sub", "line_amount": 1_500_000, "co_amount": 0.0, "invoiced_to_date": 0.0,
    }])
    data["change_orders"] = pd.DataFrame([{
        "co_id": "CO1", "project_id": "PX", "code": "06-01", "cost_amount": 500_000,
        "amount": 625_000, "submitted_date": pd.Timestamp("2025-01-15"),
        "approved_date": pd.Timestamp("2025-01-20"), "time_extension_days": 0,
        "reason": "Owner addition", "allowance_id": None,
    }])
    buyout = [f for f in det.detect_commitment_issues(data) if f.kind == "buyout"]
    assert buyout, "a contract 50% over the sold budget must flag even with a CO on the line"
    assert buyout[0].variance_amount == pytest.approx(500_000)   # 1,500,000 - 1,000,000
    assert buyout[0].variance_pct == pytest.approx(0.50)


def test_forecast_extrapolation_is_capped():
    """A line 30% complete that has burned three times its budget would
    extrapolate to ten times it. The cap keeps one early miscoded invoice
    from swamping the portfolio forecast."""
    tx = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
           "date": pd.Timestamp("2025-03-01"), "amount": 900_000, "vendor": "V", "type": "Subcontractor"}]
    data = _tiny_dataset(line_pct=30, transactions=tx)
    row = det.effective_budgets(data).iloc[0]
    # uncapped this would be 900,000 / 0.30 = 3,000,000
    assert row["forecast_at_completion"] == pytest.approx(det.FORECAST_EXTRAPOLATION_CAP * 1_000_000)
    assert det.FORECAST_EXTRAPOLATION_CAP == 2.0


def test_forecast_does_not_extrapolate_off_a_line_barely_started():
    """Below the minimum progress the run rate is noise: a mobilization
    deposit on a 5%-complete line says nothing about what the line will
    cost, and dividing by 0.05 says it very loudly."""
    assert det.FORECAST_MIN_PROGRESS == 0.25
    # $600K burned looks like $6.0M extrapolated at 10% complete and
    # $2.0M at 30%. While a line is open the forecast is the worst of
    # budget, commitment and run rate, so below the minimum progress the
    # run rate must not be in that comparison at all.
    tx = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
           "date": pd.Timestamp("2025-03-01"), "amount": 600_000, "vendor": "V", "type": "Subcontractor"}]
    early = det.effective_budgets(_tiny_dataset(line_pct=10, transactions=tx)).iloc[0]
    assert early["forecast_at_completion"] == pytest.approx(1_000_000)   # the budget, not 6,000,000
    later = det.effective_budgets(_tiny_dataset(line_pct=30, transactions=tx)).iloc[0]
    assert later["forecast_at_completion"] == pytest.approx(2_000_000)   # capped run rate


def test_reported_progress_above_one_hundred_percent_is_clipped():
    """A super who types 120 into the pay application must not create
    120% of earned value out of nothing."""
    data = _tiny_dataset(line_pct=50)
    data["project_budgets"].loc[0, "pct_complete"] = 120
    row = det.effective_budgets(data).iloc[0]
    assert row["progress"] == 1.0
    assert row["expected_spend_to_date"] == 1_000_000


def test_under_pace_needs_a_real_gap_and_real_progress():
    """The under-pace flag is deliberately hard to trip: on a real ledger
    it fires on invoices that have not landed far more often than on work
    that has not happened."""
    assert det.UNDER_PACE_PCT == -0.30 and det.UNDER_PACE_MIN_PROGRESS == 0.50
    def under(line_pct, spend):
        tx = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
               "date": pd.Timestamp("2025-03-01"), "amount": spend, "vendor": "V", "type": "Subcontractor"}]
        data = _tiny_dataset(line_pct=line_pct, transactions=tx)
        return [f for f in det.detect_cost_anomalies(data) if f.kind == "under-pace"]
    assert under(80, 800_000 * 0.75) == []      # -25%: inside tolerance
    assert under(80, 800_000 * 0.65) != []      # -35%: flagged
    assert under(40, 400_000 * 0.50) == []      # -50% but too early to read


def test_a_large_draw_is_named_but_never_moves_severity():
    """Supporting detail, not a severity input: the dollars already set
    the severity, and counting the same draw twice would double it."""
    assert det.LARGE_DRAW_SHARE == 0.25
    small = [{"transaction_id": f"T{i}", "project_id": "PX", "code": "06-01",
              "date": pd.Timestamp("2025-03-01"), "amount": 100_000, "vendor": f"V{i}",
              "type": "Subcontractor"} for i in range(6)]
    one_big = [{"transaction_id": "T1", "project_id": "PX", "code": "06-01",
                "date": pd.Timestamp("2025-03-01"), "amount": 600_000, "vendor": "V", "type": "Subcontractor"}]
    a = det.detect_cost_anomalies(_tiny_dataset(line_pct=50, transactions=small))
    b = det.detect_cost_anomalies(_tiny_dataset(line_pct=50, transactions=one_big))
    burn_a = [f for f in a if f.kind == "burn-rate"][0]
    burn_b = [f for f in b if f.kind == "burn-rate"][0]
    assert burn_a.severity == burn_b.severity
    assert burn_a.variance_amount == pytest.approx(burn_b.variance_amount)
    assert "over 25% of the line" in burn_b.explanation
    assert "over 25% of the line" not in burn_a.explanation


def test_dollars_promote_severity_but_never_demote_it():
    """The stated design: percentage and dollars together. A trivial
    percentage on a very large exposure still has to surface, and the
    floor still gates everything below materiality."""
    floor = 5_000.0
    assert det.combined_severity("LOW", floor - 1, floor) == "NONE"
    assert det.combined_severity("LOW", floor, floor) == "LOW"
    assert det.combined_severity("LOW", floor * det.DOLLAR_PROMOTION_TIERS["MEDIUM"], floor) == "MEDIUM"
    assert det.combined_severity("LOW", floor * det.DOLLAR_PROMOTION_TIERS["HIGH"], floor) == "HIGH"
    assert det.combined_severity("HIGH", floor, floor) == "HIGH"       # never demoted
    assert det.combined_severity("MEDIUM", floor * 1.5, floor) == "MEDIUM"


def test_margin_erosion_bands_sit_where_they_are_documented():
    """Points of margin, not percent of margin. A point is a point."""
    assert det.MARGIN_EROSION_BANDS["LOW"][0] == pytest.approx(0.02)
    assert det.MARGIN_EROSION_BANDS["MEDIUM"][0] == pytest.approx(0.04)
    assert det.MARGIN_EROSION_BANDS["HIGH"][0] == pytest.approx(0.08)


def test_a_suspected_duplicate_needs_the_same_code_as_well():
    """One sub billing the same round number against two different scopes
    is two invoices, not one invoice twice. Pairing those puts a false
    accusation in front of a subcontractor, which is a worse outcome than
    missing it."""
    same_code = [
        {"transaction_id": "T1", "project_id": "PX", "code": "06-01", "date": pd.Timestamp("2025-03-01"),
         "amount": 40_000, "vendor": "Framer", "type": "Subcontractor"},
        {"transaction_id": "T2", "project_id": "PX", "code": "06-01", "date": pd.Timestamp("2025-03-20"),
         "amount": 40_000, "vendor": "Framer", "type": "Subcontractor"},
    ]
    assert len(det.detect_duplicate_postings(_tiny_dataset(50, same_code))) == 1

    data = _tiny_dataset(50, same_code)
    data["cost_codes"] = pd.concat([data["cost_codes"], data["cost_codes"].assign(code="06-02")],
                                   ignore_index=True)
    data["project_budgets"] = pd.concat(
        [data["project_budgets"], data["project_budgets"].assign(code="06-02")], ignore_index=True)
    data["cost_transactions"].loc[1, "code"] = "06-02"
    assert det.detect_duplicate_postings(data) == []


def test_the_duplicate_window_has_an_edge():
    """Two identical draws a long way apart are two progress payments."""
    def pair(gap_days):
        tx = [
            {"transaction_id": "T1", "project_id": "PX", "code": "06-01",
             "date": pd.Timestamp("2025-03-01"), "amount": 40_000, "vendor": "F", "type": "Subcontractor"},
            {"transaction_id": "T2", "project_id": "PX", "code": "06-01",
             "date": pd.Timestamp("2025-03-01") + pd.Timedelta(days=gap_days),
             "amount": 40_000, "vendor": "F", "type": "Subcontractor"},
        ]
        return det.detect_duplicate_postings(_tiny_dataset(50, tx))
    assert len(pair(det.DUPLICATE_WINDOW_DAYS)) == 1
    assert pair(det.DUPLICATE_WINDOW_DAYS + 1) == []


def test_dollars_per_square_foot_are_believable(data):
    """The first number any builder computes, and the fastest way to
    catch a dataset that was never sanity-checked. A premium waterfront
    custom home does not get built for the same rate as a commercial
    shell, and neither of them gets built for $90 a foot."""
    p = data["projects"].set_index("project_id")
    psf = p["contract_value"] / p["square_feet"]
    bands = {
        "P01": (400, 620),    # high-tier custom
        "P02": (220, 400),    # remodel and addition, priced on affected area
        "P03": (550, 800),    # premium, difficult site
        "P04": (330, 480),    # standard cottage
        "P05": (170, 300),    # light commercial shell
    }
    for pid, (lo, hi) in bands.items():
        assert lo <= psf[pid] <= hi, f"{pid}: ${psf[pid]:,.0f}/sf"
    assert psf["P03"] > psf["P01"] > psf["P04"] > psf["P05"]


def test_a_named_delay_has_paperwork_behind_it(data):
    """A schedule that blames three weeks on unforeseen conditions and
    carries no change order for them is the first thing an owner's rep
    attacks. Where the delay has a cause the owner is paying for, the
    change order's time extension has to match the days the schedule
    shows."""
    ms = data["schedule_milestones"]
    named = ms[ms["other_delay_days"] > 0]
    assert not named.empty
    co = data["change_orders"]
    # Where the delay is work the owner is paying for, a change order has
    # to buy exactly the days the schedule shows. P01's delay is a late
    # owner selection, which a builder usually absorbs rather than papers,
    # so it is not in this list.
    for pid in ("P02", "P03", "P05"):
        days = int(named[named["project_id"] == pid]["other_delay_days"].sum())
        extensions = set(co[(co["project_id"] == pid)]["time_extension_days"])
        assert days in extensions, (
            f"{pid}: schedule shows {days} days, change orders buy {sorted(extensions)}"
        )


def test_slip_is_weather_plus_a_named_cause_less_float(data):
    """Slip is an output: what the weather actually cost plus the named
    delay, less whatever float the baseline carried. Treating total slip
    as the input and backing weather out of it silently deletes the named
    cause in a bad winter, and the job then has an unexplained delay."""
    ms = data["schedule_milestones"]
    for pid, g in ms.groupby("project_id"):
        cp = g[g["critical_path"]].sort_values("baseline_date")
        terminal = (cp.iloc[-1]["forecast_date"] - cp.iloc[-1]["baseline_date"]).days
        drawn = int(g["weather_delay_days"].sum() + g["other_delay_days"].sum())
        assert terminal <= drawn, f"{pid}: finish moved more than the delay drawn"
        assert terminal >= 0
    # The control job absorbs its weather rather than carrying none.
    p04 = ms[ms["project_id"] == "P04"]
    assert p04["weather_delay_days"].sum() > 0
    cp = p04[p04["critical_path"]].sort_values("baseline_date")
    assert (cp.iloc[-1]["forecast_date"] - cp.iloc[-1]["baseline_date"]).days == 0


def test_weather_reaches_the_exterior_finishes(data):
    """Flatwork, hardscape and landscaping all have to be in before the
    walkthrough, and in the Pacific Northwest that is the most
    weather-sensitive work on the job after the shell. A model where
    weather stops at dry-in has never watched a crew try to pour a
    driveway in January."""
    ms = data["schedule_milestones"]
    late = ms[ms["milestone"] == "Punch List & Final Walkthrough"]
    assert late["weather_delay_days"].sum() > 0


def test_a_supplier_is_not_also_the_sub_who_installs(data):
    """One legal entity appearing under two identities with no shared key
    means total exposure to that vendor cannot be rolled up at all."""
    suppliers = set(data["cost_codes"]["supplier"].dropna())
    subs = set(data["subcontractors"]["name"])
    assert not (suppliers & subs), sorted(suppliers & subs)


def test_budget_revisions_land_while_the_line_is_still_live(data):
    """Revising a framing budget up seven months after framing closed
    under budget is not a budget revision, it is a typo, and it makes a
    reviewer stop trusting every date in the file."""
    revs = data["budget_revisions"]
    last = data["cost_transactions"].groupby(["project_id", "code"])["date"].max()
    pct = data["project_budgets"].set_index(["project_id", "code"])["pct_complete"]
    joined = revs.join(last, on=["project_id", "code"], rsuffix="_last")
    joined["pct"] = joined.set_index(["project_id", "code"]).index.map(pct)
    # A line still in progress can legitimately be revised at any point.
    # The defect is a revision on a line that is finished and paid.
    closed = joined[(joined["date_last"].notna()) & (joined["pct"] >= 100)]
    assert not closed.empty
    late = closed[closed["date"] > closed["date_last"] + pd.Timedelta(days=30)]
    assert late.empty, late[["revision_id", "project_id", "code", "date", "date_last"]].to_dict("records")


def test_permits_are_priced_off_valuation_and_area_not_duration(data):
    """A permit fee is assessed on construction valuation and floor area.
    Pricing it as a share of a duration-driven general-conditions total
    means a job that SLIPS budgets more permit fee, and puts a bigger
    permit on a 4,000 sf house than on an 8,200 sf commercial building."""
    pb = data["project_budgets"]
    p = data["projects"].set_index("project_id")
    permits = pb[pb["code"] == "01-03"].set_index("project_id")["budgeted_amount"]
    assert permits["P05"] > permits["P01"], "commercial shell must out-permit a smaller house"
    assert permits["P03"] > permits["P01"] > permits["P04"]
    months = (p["end_date"] - p["start_date"]).dt.days / 30.44
    # Permit per month would be flat if duration were driving it.
    per_month = (permits / months).dropna()
    assert per_month.max() / per_month.min() > 2.0


def test_supervision_is_supervision_and_inspections_are_independent(data):
    """Two coding rules a controller checks straight away: field labor is
    not buried in the supervision variance, and a special inspection is
    not performed by the contractor being inspected."""
    tx = data["cost_transactions"]
    sup = tx[tx["code"] == "01-01"]
    assert not sup["vendor"].str.contains("field", case=False).any()
    testing = tx[tx["code"] == "01-05"]
    if not testing.empty:
        assert (testing["type"] == "Professional Services").all()
        assert not testing["vendor"].str.contains("Ridgeline", case=False).any()


def test_the_frame_is_not_cannibalised_to_pay_for_the_cabinets(data):
    """Tier moves the division; share adjustments move money within it.
    Reallocating inside a fixed division weight funds a premium cabinetry
    package out of the structure, and ends with the premium home framed
    cheaper per square foot than the cottage."""
    pb = data["project_budgets"]
    p = data["projects"].set_index("project_id")
    psf = {}
    for pid in ("P01", "P03", "P04"):
        framing = pb[(pb["project_id"] == pid) & (pb["code"] == "06-01")]["budgeted_amount"].sum()
        psf[pid] = framing / p.loc[pid, "square_feet"]
    for pid, v in psf.items():
        assert 20 <= v <= 60, f"{pid}: framing at ${v:,.0f}/sf"
    assert psf["P03"] > psf["P04"], "the premium home cannot frame cheaper than the cottage"
    # Cabinetry scales with tier, which is what a premium package means.
    def ratio(pid):
        g = pb[pb["project_id"] == pid].set_index("code")["budgeted_amount"]
        return g.get("06-02", 0) / g.get("06-01", 1)
    assert ratio("P03") > ratio("P01") > ratio("P04")


def test_no_scope_arrives_on_a_single_six_figure_invoice(data):
    """A sub bills monthly against a schedule of values. One invoice for
    a whole foundation, with no progress billing behind it, is not
    something a GC would pay or a lender would fund."""
    tx = data["cost_transactions"]
    by_code = tx.groupby(["project_id", "code"])["amount"].agg(["size", "max"])
    single_big = by_code[(by_code["size"] == 1) & (by_code["max"] > 50_000)]
    assert single_big.empty, single_big.to_dict("index")


def test_the_small_dollar_tail_is_drawn_not_clamped(data):
    """A ledger with ninety-three invoices for exactly $25.00 is the same
    tell as a ledger with nothing under $500: it says the numbers were
    generated to a floor rather than observed."""
    tx = data["cost_transactions"]
    counts = tx["amount"].value_counts()
    assert counts.iloc[0] / len(tx) < 0.01, (
        f"${counts.index[0]:,.2f} appears {counts.iloc[0]} times"
    )


def test_change_orders_are_written_while_the_work_is_live(data):
    """A change order dated six months after the frame closed is the same
    defect as a budget revision dated there, and it makes a reviewer stop
    trusting every date in the file."""
    co = data["change_orders"]
    cc = data["cost_codes"].set_index("code")
    p = data["projects"].set_index("project_id")
    inside = 0
    for _, r in co.iterrows():
        start, end = p.loc[r["project_id"], "start_date"], p.loc[r["project_id"], "end_date"]
        frac = (r["submitted_date"] - start).days / max(1, (end - start).days)
        if cc.loc[r["code"], "phase_start"] - 0.15 <= frac <= cc.loc[r["code"], "phase_end"] + 0.08:
            inside += 1
    assert inside / len(co) > 0.90, f"only {inside} of {len(co)} change orders land on live work"


def test_the_control_job_is_clean_by_construction_and_says_so(data):
    """P04 carries no planted problem and no random drift big enough to
    read as one. That is deliberate: a detector that cannot show a clean
    job is one nobody trusts on a dirty one. It is worth asserting
    explicitly so the reason is in the suite rather than in a comment."""
    rollup = det.portfolio_rollup(data).set_index("project_id")
    assert (rollup.loc["P04", det.ROLLUP_COLUMNS] == "NONE").all()
    co = data["change_orders"]
    assert co[co["project_id"] == "P04"]["approved_date"].notna().all()
    revs = data["budget_revisions"]
    assert revs[revs["project_id"] == "P04"].empty


def test_the_harborview_story_stays_internally_tied(data):
    """The budget revision, the re-issued sub contract and the spend that
    follows all have to agree with each other, because the whole point of
    the story is that the change order to the owner does NOT. If those
    three drift apart the cross-reference in the drift flag quietly stops
    firing and the flag loses the sentence that makes it useful."""
    rev = data["budget_revisions"]
    rev = rev[(rev["project_id"] == "P03") & (rev["code"] == "06-02")].iloc[-1]
    line = data["commitment_lines"]
    line = line[(line["project_id"] == "P03") & (line["code"] == "06-02")].iloc[0]
    assert abs(rev["revised_amount"] - line["line_amount"]) < 1.0
    drift = [f for f in det.detect_budget_drift(data)
             if f.project_id == "P03" and f.code == "06-02"][0]
    buyout = [f for f in det.detect_commitment_issues(data)
              if f.project_id == "P03" and f.code == "06-02" and f.kind == "buyout"][0]
    assert abs(drift.variance_amount - buyout.variance_amount) < 1.0
    assert "edited to match this contract" in buyout.explanation


# ---------------------------------------------------------------------------
# Calibration against published benchmarks.
#
# The cost shape of this dataset is not "what looked about right to me".
# It is calibrated to figures anyone can check, and these tests pin it
# there so a later tweak to a division weight cannot quietly drift the
# whole thing away from them. Sources are listed in the README.
# ---------------------------------------------------------------------------
NAHB_CATEGORIES = {
    "Site work": ["01-03", "02-01", "02-02", "31-01", "31-02", "33-01", "33-02"],
    "Foundations": ["03-01", "03-02"],
    "Framing": ["06-01", "05-01", "05-02"],
    "Exterior finishes": ["04-01", "04-02", "07-01", "07-03", "08-01", "08-02", "08-04"],
    "Major system rough-ins": ["21-01", "22-01", "23-01", "23-02", "23-03",
                               "26-01", "27-01", "28-01", "25-01"],
    "Interior finishes": ["06-02", "06-03", "07-02", "08-03", "09-01", "09-02", "09-03",
                          "09-04", "09-05", "10-01", "10-02", "10-03", "10-04", "11-01",
                          "12-01", "12-02", "22-02", "26-02"],
    "Final steps": ["03-03", "32-01", "32-02"],
}
GENERAL_CONDITIONS = ["01-01", "01-02", "01-04"]


def test_cost_breakdown_tracks_the_nahb_construction_cost_survey(data):
    """NAHB's Cost of Constructing a Home puts a single-family build at
    roughly 24% interior finishes, 19% rough-ins, 17% framing, 13%
    exterior, 10% foundations, 8% site work. Custom work legitimately
    runs heavier on interior finishes and lighter elsewhere, so the bands
    below are wide -- but a dataset where framing is 4% of the job, or
    interior finishes are 42%, is not a custom home, it is a spreadsheet
    someone filled in from memory.

    Checked on the three ground-up houses. The remodel is excluded (it
    reuses a foundation and is almost all finishes) and so is the
    commercial shell (a different building entirely)."""
    bands = {
        "Site work": (5.0, 11.0), "Foundations": (7.0, 14.0), "Framing": (12.0, 20.0),
        "Exterior finishes": (9.0, 16.0), "Major system rough-ins": (14.0, 22.0),
        "Interior finishes": (26.0, 36.0), "Final steps": (4.0, 9.0),
    }
    m = data["project_budgets"].merge(data["cost_codes"][["code"]], on="code")
    for pid in ("P01", "P03", "P04"):
        g = m[(m["project_id"] == pid) & (~m["code"].isin(GENERAL_CONDITIONS))]
        total = g["budgeted_amount"].sum()
        for cat, (lo, hi) in bands.items():
            share = g[g["code"].isin(NAHB_CATEGORIES[cat])]["budgeted_amount"].sum() / total * 100
            assert lo <= share <= hi, f"{pid} {cat}: {share:.1f}% (band {lo}-{hi}%)"


def test_general_conditions_land_in_the_published_range(data):
    """General conditions are commonly cited at 5-10% of project COST.
    This asserts the share of CONTRACT, which is the lower number of the
    two and the one the budget table makes directly checkable; the README
    reports both bases side by side so the comparison to the published
    figure is like for like. Small jobs sit above big ones either way,
    because a site costs what it costs whatever is being built on it."""
    pb = data["project_budgets"]
    for _, p in data["projects"].iterrows():
        gc = pb[(pb["project_id"] == p["project_id"])
                & (pb["code"].isin(GENERAL_CONDITIONS))]["budgeted_amount"].sum()
        share = gc / p["contract_value"]
        assert 0.05 <= share <= 0.12, f"{p['project_id']}: general conditions {share:.1%} of contract"
    # And the small jobs sit above the big ones, not below.
    def share(pid):
        row = data["projects"].set_index("project_id").loc[pid]
        gc = pb[(pb["project_id"] == pid) & (pb["code"].isin(GENERAL_CONDITIONS))]["budgeted_amount"].sum()
        return gc / row["contract_value"]
    assert share("P04") > share("P03")


def test_retainage_follows_washington_law(data):
    """Sub contracts carry 10% on the houses and 5% on the commercial
    job; the owner is billed 5% on all five, which is what this builder's
    contracts say. RCW 60.30.010 caps retainage on private construction at 5% of the
    contract price and exempts single-family residential of fewer than 12
    units. So the houses carry the 10% still common on private
    residential work and the commercial job carries the statutory cap.
    Carrying 10% on the commercial job would not be aggressive, it would
    be unenforceable."""
    p = data["projects"].set_index("project_id")
    assert p.loc["P05", "retainage_pct"] <= 0.05
    for pid in ("P01", "P02", "P03", "P04"):
        assert p.loc[pid, "retainage_pct"] <= 0.05      # what this builder bills at

    c = data["commitments"]
    commercial = c[c["project_id"] == "P05"]
    residential = c[c["project_id"] != "P05"]
    assert (commercial["retention_pct"] <= 0.05).all()
    assert residential["retention_pct"].max() == 0.10


def test_change_order_volume_matches_the_benchmark_database(data):
    """The AIA/Catina construction benchmark database, 18,229 projects and
    892,457 change orders, puts change orders at about 3.2% of contract on
    the smallest jobs rising to 5.0% on $1-5M work, with the middle 80% of
    projects spread far wider. These jobs sit in that spread."""
    co = data["change_orders"]
    p = data["projects"].set_index("project_id")
    for pid in p.index:
        adds = co[(co["project_id"] == pid) & (co["amount"] > 0)]["amount"].sum()
        share = adds / p.loc[pid, "contract_value"]
        assert 0.02 <= share <= 0.08, f"{pid}: change orders {share:.2%} of contract"


def test_thresholds_the_readme_quotes_are_pinned_to_their_values():
    """Two of these survived mutation testing: the duplicate window and
    the compliance look-ahead could both be moved without a single test
    noticing, because the seeded dataset happens to sit well clear of
    either edge. A threshold the README quotes by number needs a test
    that fails when the number changes, or the README is documenting an
    intention rather than the code."""
    assert det.DUPLICATE_WINDOW_DAYS == 45
    assert det.COMPLIANCE_LOOKAHEAD_DAYS == 30
    assert det.DOLLAR_FLOOR == 5_000
    assert det.MATERIALITY_PCT_OF_CONTRACT["default"] == 0.0025
    assert det.MATERIALITY_PCT_OF_CONTRACT["Light Commercial (ground-up)"] == 0.0035
    assert det.CO_AGING_THRESHOLD_DAYS == 30
    assert det.SELECTION_LATE_GRACE_DAYS == 7
    assert det.BUDGET_DRIFT_DOLLAR_FLOOR == 1_500
    assert det.ALLOWANCE_DOLLAR_FLOOR == 2_500


def test_thresholds_passed_in_actually_change_what_gets_flagged(data):
    """The dashboard's sidebar sliders are only honest if moving one
    changes what the page shows, not just what a constant equals. Raising
    the materiality floor past a real flag's exposure has to make that
    flag disappear -- and only for the caller holding that Thresholds."""
    baseline = det.detect_cost_anomalies(data)
    assert baseline, "fixture has no cost flags to test against"
    target = max(baseline, key=lambda f: abs(f.variance_amount))
    just_above = abs(target.variance_amount) + 1.0

    tight = det.DEFAULT_THRESHOLDS.with_overrides({"DOLLAR_FLOOR": just_above})
    tightened = det.detect_cost_anomalies(data, tight)
    assert not any(
        f.project_id == target.project_id and f.code == target.code and f.kind == target.kind
        for f in tightened
    ), "raising the floor past this flag's exposure should have cleared it"
    assert len(det.detect_cost_anomalies(data)) == len(baseline)
    assert len(det.detect_cost_anomalies(data, det.DEFAULT_THRESHOLDS.with_overrides({}))) == len(baseline)


def test_two_threshold_sets_do_not_interfere(data):
    """Streamlit runs every viewer as a thread in one process. Two viewers
    with two slider positions have to get two answers, at the same time,
    without either one moving the module's defaults: thresholds are
    values handed down, not state written up."""
    from concurrent.futures import ThreadPoolExecutor

    loose = det.DEFAULT_THRESHOLDS
    tight = loose.with_overrides({
        "DOLLAR_FLOOR": 20_000.0,
        "CO_AGING_DAYS_BANDS": {"LOW": (5, 10), "MEDIUM": (10, 20), "HIGH": (20, None)},
    })
    assert loose.dollar_floor == 5_000 and tight.dollar_floor == 20_000
    assert loose.co_aging_days_bands == det.CO_AGING_DAYS_BANDS      # untouched by the override
    assert tight.co_aging_threshold_days == 5
    # Frozen, and its tables are its own: nothing a holder does to one
    # instance can leak into another.
    with pytest.raises(AttributeError):
        tight.dollar_floor = 1
    tight.pct_bands["LOW"] = (0.0, 0.2)
    assert loose.pct_bands["LOW"] == (0.10, 0.20)

    loose_cost = det.detect_cost_anomalies(data, loose)
    tight_cost = det.detect_cost_anomalies(data, tight)
    assert len(tight_cost) < len(loose_cost)
    loose_co = det.detect_co_aging(data, thresholds=loose)
    tight_co = det.detect_co_aging(data, thresholds=tight)
    assert len(tight_co) > len(loose_co)

    def run(t):
        return (len(det.detect_cost_anomalies(data, t)), len(det.detect_co_aging(data, thresholds=t)),
                det.portfolio_rollup(data, thresholds=t)["overall_severity"].tolist())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, [loose, tight] * 4))
    assert all(r == results[0] for r in results[0::2])
    assert all(r == results[1] for r in results[1::2])
    assert results[0] != results[1]
    assert det.DEFAULT_THRESHOLDS == det.Thresholds() and det.DOLLAR_FLOOR == 5_000
