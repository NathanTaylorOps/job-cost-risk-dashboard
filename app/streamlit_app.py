"""
Ridgeline Custom Homes -- Job-Cost & Schedule-Risk Dashboard

Portfolio-level executive rollup plus per-project drill-down, built on the
detection logic in src/detection.py (the single source of truth for every
threshold used here) and the figures in app/charts.py.

The screen is organized around the question the tool exists to answer --
which job needs a phone call this morning, and who is that call to -- so
the drill-down opens with the calls, not with a row of calm metrics.

Run:
    streamlit run app/streamlit_app.py
"""

import bisect
import csv
import hashlib
import html
import io
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st

import charts
import detection as det

st.set_page_config(
    page_title="Job-Cost & Schedule-Risk Dashboard | Ridgeline Custom Homes",
    page_icon="\U0001F3D7️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Severity as an ORDINAL LADDER, not four hues.
#
# HIGH is a solid chip, MEDIUM a tinted one, LOW an outline, CLEAR bare text.
# That ramp survives a grayscale printout and all three types of color
# blindness, which four hues at one weight do not: a deuteranope can tell
# the labels apart but cannot see that HIGH outranks MEDIUM, and ranking is
# the whole job of the front screen.
#
# Hues are validated for contrast (all text >= 4.5:1 on its own background)
# and for CVD separation (worst adjacent pair dE 17.5 deutan, 18.5 normal).
# Green is CLEAR and carries the check; LOW is an informational blue, because
# a green "LOW" on a job forecast 19 days late reads as good news.
# ---------------------------------------------------------------------------
SEVERITY_STYLE = {
    "HIGH":   {"symbol": "▲", "label": "HIGH",   "color": "#9F1C15",
               "bg": "#9F1C15", "fg": "#FFFFFF", "border": "#9F1C15"},
    "MEDIUM": {"symbol": "◆", "label": "MEDIUM", "color": "#C07A0A",
               "bg": "#FDF0DC", "fg": "#7A4405", "border": "#E8C48A"},
    "LOW":    {"symbol": "●", "label": "LOW",    "color": "#1F5FA8",
               "bg": "transparent", "fg": "#1F5FA8", "border": "#BCD0E8"},
    "NONE":   {"symbol": "✓", "label": "CLEAR",  "color": "#106B3F",
               "bg": "transparent", "fg": "#106B3F", "border": "transparent"},
}

ROLLUP_ROWS = [
    ("Cost", "cost_severity"),
    ("Schedule", "schedule_severity"),
    ("Change orders", "co_aging_severity"),
    ("Allowances", "allowance_severity"),
    ("Billing", "billing_severity"),
    ("Margin", "forecast_severity"),
    ("Sub compliance", "compliance_severity"),
]

DATE = "{:%Y-%m-%d}"
REPO_URL = "https://github.com/NathanTaylorOps/job-cost-risk-dashboard"


def money(value) -> str:
    """The sign goes outside the symbol. A deleted scope is -$11,560, not
    "$-11,560", which is how a bare "${:,.0f}" prints it and how nobody
    writes it on a change order."""
    return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def money_html(value) -> str:
    """As money(), for markdown and raw HTML: a bare "$" there opens a LaTeX
    span and Streamlit eats the text up to the next one."""
    return money(value).replace("$", "&#36;")

# A small design-token system, in the spirit of a native macOS/Windows
# settings app: a soft neutral canvas, white grouped-card surfaces with a
# generous continuous-looking radius, a system font stack instead of the
# web-default sans, and one accent color reused everywhere (it is also
# .streamlit/config.toml's primaryColor, so it recolors Streamlit's own
# tab indicator and focus rings the same way).
#
# The literal starts hard against the left margin on purpose: CommonMark
# reads four leading spaces as an indented code block, which would print
# this stylesheet on the page instead of applying it.
st.markdown(
    """
<style>
  :root {
    --rg-accent: #1F5FA8;
    --rg-page-bg: #F5F5F7;
    --rg-card-bg: #FFFFFF;
    --rg-ink: #1A1A1A;
    --rg-ink-2: #5A5A5A;
    --rg-border: #D7DBE2;
    --rg-border-soft: #E9EBEF;
    --rg-radius-lg: 16px;
    --rg-radius-md: 12px;
    --rg-radius-sm: 8px;
    --rg-shadow: 0 1px 2px rgba(16,24,40,.04), 0 6px 16px rgba(16,24,40,.05);
    --rg-font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI",
               "Segoe UI Variable", Roboto, "Helvetica Neue", Arial, sans-serif;
  }

  html, body, .stApp, [class*="css"] { font-family: var(--rg-font) !important; }
  body, .stApp { background: var(--rg-page-bg) !important; }

  .block-container { padding: 2.4rem 2.2rem 4rem; max-width: 1180px; }
  /* Tables want the whole column; running text does not. A flag set in
     14px across 1180px is a 150-character line, which is roughly twice
     what anyone reads comfortably. */
  .rg-card, [data-testid="stCaptionContainer"] { max-width: 88ch; }

  h1 { font-size: 34px !important; font-weight: 700 !important; letter-spacing: -.02em;
       color: var(--rg-ink) !important; }
  h2 { font-size: 13px !important; font-weight: 700 !important;
       text-transform: uppercase; letter-spacing: .07em; color: var(--rg-ink-2) !important;
       margin-top: 2.2rem !important; }
  h3 { font-size: 19px !important; font-weight: 650 !important; letter-spacing: -.01em;
       color: var(--rg-ink) !important; }

  /* Figures line up in columns, which matters on every table here. */
  [data-testid="stMetricValue"], .stDataFrame td, .rg-chip { font-variant-numeric: tabular-nums; }
  [data-testid="stMetricValue"] { font-size: 27px !important; font-weight: 650 !important; }
  [data-testid="stMetricLabel"] p { font-size: 12px !important; color: var(--rg-ink-2) !important; }

  /* Pill-shaped, the way a status tag reads on a settings screen rather
     than a spreadsheet -- full round ends, not a 4px rectangle. */
  .rg-chip { display:inline-block; padding:3px 11px; border-radius:999px;
             font-size:12px; font-weight:700; line-height:1.5; white-space:nowrap; }

  hr { border: none !important; border-top: 1px solid var(--rg-border-soft) !important;
       margin: 2rem 0 !important; }

  /* Every distinct unit of content -- a chart, a list of flags, a group of
     calls -- gets its own bordered, elevated card. That is the whole
     layout system: a border (and a hair of shadow to lift it off the
     gray canvas) means "this is one thing", so a reader's eye can chunk
     the page into pieces instead of parsing one long scroll of lines. */
  .rg-card { border: 1.5px solid var(--rg-border); border-radius: var(--rg-radius-lg);
             background: var(--rg-card-bg); box-shadow: var(--rg-shadow);
             padding: 20px 24px; margin: 0 0 20px; }
  .rg-card-title { font-size: 15px; font-weight: 650; color: var(--rg-ink); }
  .rg-card-sub { font-size: 12px; color: var(--rg-ink-2); margin-top: 2px; margin-bottom: 10px; }
  .rg-card-more { font-size: 12px; color: var(--rg-ink-2); margin-top: 8px; }

  /* The chart is already its own SVG frame with a title baked in, so the
     card here only needs to add air around it -- no title of its own. */
  .rg-chart-card { padding: 22px 24px 12px; }

  /* Flags inside a card read as one list: a hairline between rows instead
     of each line floating in its own margin. */
  .rg-flags .rg-flag { margin: 0; padding: 10px 0; font-size: 14px; line-height: 1.5;
                        border-top: 1px solid var(--rg-border-soft); }
  .rg-flags .rg-flag:first-child { border-top: none; padding-top: 0; }
  .rg-flags .rg-flag:last-child { padding-bottom: 0; }
  /* Outside a card (the expander's "current on insurance" list, which
     already sits inside its own bordered widget) a flag stays a plain row. */
  .rg-flag { margin: 2px 0 8px 0; font-size: 14px; line-height: 1.5; }
  .rg-call-who { font-size: 15px; font-weight: 650; color: var(--rg-ink); }
  .rg-call-why { font-size: 12px; color: var(--rg-ink-2); margin-bottom: 10px; }

  /* Native Streamlit widgets, brought to the same radius/border/shadow
     scale as the hand-built cards above, via the handful of data-testid
     selectors Streamlit's own docs cite as the stable way to theme them
     (unlike its internal, version-churning emotion-css class names). */
  [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: var(--rg-radius-lg) !important; border: 1.5px solid var(--rg-border) !important;
    background: var(--rg-card-bg) !important; box-shadow: var(--rg-shadow) !important;
  }
  [data-testid="stExpander"] {
    border-radius: var(--rg-radius-md) !important; border: 1.5px solid var(--rg-border) !important;
    background: var(--rg-card-bg) !important; overflow: hidden;
  }
  [data-testid="stDataFrame"] {
    border-radius: var(--rg-radius-sm) !important; overflow: hidden;
    border: 1px solid var(--rg-border-soft) !important;
  }
  [data-testid="stSelectbox"] > div > div {
    border-radius: var(--rg-radius-sm) !important; border: 1.5px solid var(--rg-border) !important;
  }
  [data-testid="stAlert"] { border-radius: var(--rg-radius-md) !important; }

  /* Phone and small-tablet widths. Streamlit already stacks st.columns and
     collapses the sidebar into a drawer below its own breakpoint; what is
     left to fix here is spacing and type that was sized for a 1180px
     column, plus the two places dense figures (a metric row, a data table)
     need to admit they are on a phone rather than just shrinking in place. */
  @media (max-width: 768px) {
    .block-container { padding: 1.1rem .9rem 3rem !important; }
    h1 { font-size: 26px !important; }
    h2 { margin-top: 1.6rem !important; }
    h3 { font-size: 17px !important; }
    .rg-card { padding: 14px 16px; margin-bottom: 14px; }
    [data-testid="stMetricValue"] { font-size: 21px !important; }
    /* Every st.columns() call on the page -- the five-metric row, the PM
       cards, the header controls -- lays its children out at a percentage
       width each. At 768px that is a metric every 74px with its label
       wrapped onto three lines. Stack them full-width instead, in the
       order they were created. */
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    [data-testid="stHorizontalBlock"] > div { min-width: 100% !important; }
    [data-testid="stHorizontalBlock"] [data-testid="stMetric"] {
      padding-bottom: 10px; border-bottom: 1px solid var(--rg-border-soft); margin-bottom: 10px;
    }
  }
</style>
""",
    unsafe_allow_html=True,
)


def esc(value) -> str:
    """Every data field that reaches raw HTML goes through here. Vendor
    names and change-order reasons come from the ledger; on real data
    they are user input."""
    return html.escape(str(value), quote=True)


def esc_md(value) -> str:
    """As esc(), plus the dollar signs. Streamlit runs its markdown through
    a LaTeX pass, so a flag reading "$30,030 of cost ($38,500 to the owner)"
    has its two dollar signs treated as inline math: the signs disappear and
    everything between them goes italic. Every explanation this tool writes
    is full of dollar amounts, so this is not an edge case.

    The escape is the HTML entity, not a backslash. Every flag on this page
    is wrapped in a <div>, and CommonMark treats a line opening with a block
    tag as raw HTML: nothing inside it is parsed as markdown, so a backslash
    escape is never consumed and "\\$59,970" reaches the screen with the
    backslash still on it. The entity is inert to the LaTeX pass -- it is
    not a dollar character when the math rule scans the line -- and the
    browser decodes it in both contexts."""
    return esc(value).replace("$", "&#36;")


def flag_key(project_id: str, flag) -> str:
    """A stable identity for a flag across reruns, for the call-list
    checkboxes below: there is no natural id on most flag types, so this
    hashes what actually identifies one -- the project, the kind of check,
    and its exact explanation, which is unique per flag because it is
    generated from the specific numbers behind it. Stable across reruns of
    the same session (thresholds moving can change a flag's severity or
    make it disappear, but a flag that still exists keeps its key), not
    across sessions -- the point is a checkbox that stays checked while you
    work through this morning's calls, not a database."""
    ident = f"{project_id}|{type(flag).__name__}|{getattr(flag, 'explanation', '')}"
    return "call_" + hashlib.md5(ident.encode()).hexdigest()[:12]


def severity_badge(sev: str) -> str:
    """The ordinal ladder. The glyph is aria-hidden because a screen reader
    announcing "black up-pointing triangle" before every one of thirty-five
    badges is noise, and the word beside it already says HIGH."""
    s = SEVERITY_STYLE.get(sev, SEVERITY_STYLE["NONE"])
    if sev == "NONE":
        return (f'<span class="rg-chip" style="color:{s["fg"]}; font-weight:600;">'
                f'<span aria-hidden="true">{s["symbol"]}</span> {s["label"]}</span>')
    return (f'<span class="rg-chip" style="background:{s["bg"]}; color:{s["fg"]}; '
            f'border:1px solid {s["border"]};">'
            f'<span aria-hidden="true">{s["symbol"]}</span> {s["label"]}</span>')


def flag_line(sev: str, text: str, prefix: str = "") -> None:
    """A single flag row on its own -- used only where a card would be one
    box holding one line, which is no box at all (the expander's "current
    on insurance" list, already inside its own bordered widget)."""
    st.markdown(f'<div class="rg-flag">{severity_badge(sev)} &nbsp;{prefix}{esc_md(text)}</div>',
                unsafe_allow_html=True)


def flag_rows(items) -> str:
    """The inner rows for a card: items is (severity, text, prefix) triples.
    Returns HTML, unrendered, so a caller can wrap it in whatever card
    frame -- a bare list card or one with its own header -- owns it."""
    return "".join(
        f'<div class="rg-flag">{severity_badge(sev)} &nbsp;{prefix}{esc_md(text)}</div>'
        for sev, text, prefix in items
    )


def flags_card(items, title: str = "", subtitle: str = "") -> None:
    """One bordered card holding every flag in `items`, so a run of loose
    lines reads as a single list rather than N independent paragraphs.
    Renders nothing for an empty list -- the caller's own success message
    covers that case, and an empty card is a border around nothing."""
    if not items:
        return
    head = ""
    if title:
        head += f'<div class="rg-card-title">{title}</div>'
    if subtitle:
        head += f'<div class="rg-card-sub">{subtitle}</div>'
    st.markdown(
        f'<div class="rg-card">{head}<div class="rg-flags">{flag_rows(items)}</div></div>',
        unsafe_allow_html=True,
    )


def chart_card(svg: str) -> None:
    """A chart is already a self-contained SVG frame with its own title;
    the card here only adds the air and the border that make it read as
    one block of the page rather than an image floating in the margin."""
    st.markdown(f'<div class="rg-card rg-chart-card">{svg}</div>', unsafe_allow_html=True)


def tab_label(name: str, items) -> str:
    """Put the state in the tab, so a reader does not have to click six tabs
    on five jobs to find out which ones have anything in them."""
    if not items:
        return name
    worst = max((f.severity for f in items), key=lambda s: det.SEVERITY_ORDER[s])
    return f"{name} {SEVERITY_STYLE[worst]['symbol']} {len(items)}"


@st.cache_data
def load_dataset():
    """The eleven CSVs, cached: nothing about the ledger itself changes
    between reruns of the page. What thresholds make of it does, which is
    why detection is a separate, uncached step below -- dragging a
    threshold slider must not need the dataset regenerated or re-read."""
    return det.load_data()


try:
    data = load_dataset()
except subprocess.CalledProcessError as exc:  # the generator itself failed
    detail = (exc.stderr or b"").decode(errors="replace").strip() or str(exc)
    st.error("The dataset could not be generated.")
    st.code(detail)
    st.stop()
except Exception as exc:  # noqa: BLE001 -- surface any load failure in the page
    st.error("Could not load the dataset.")
    st.code(f"{type(exc).__name__}: {exc}")
    st.stop()

projects = data["projects"].set_index("project_id")

# ---------------------------------------------------------------------------
# SIDEBAR -- thresholds and the portfolio filter live here, above the fold,
# so the page reads as an app shell with working controls rather than a
# single static column. The call list further down adds to this same
# sidebar in the order the script reaches it; Streamlit stacks sidebar
# calls in call order regardless of where in the script they happen.
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Ridgeline Custom Homes")
    st.caption("Job-cost & schedule-risk dashboard")
    st.divider()

    st.markdown("#### Thresholds")
    st.caption(
        "These four move the needle most on a five-job portfolio. Fine "
        "control over every other threshold lives in "
        "`config/thresholds.json`."
    )
    with st.expander("Where these starting numbers came from"):
        st.markdown(
            "- **Materiality floor:** the larger of a flat dollar amount and a "
            "share of contract value, so a &#36;5K miss is a call on a &#36;780K "
            "remodel and noise on a &#36;3.4M house.\n"
            "- **Variance bands:** LOW/MEDIUM/HIGH on a cost or buyout variance "
            "are informational, worth a look, and worth a call -- the shipped "
            "defaults put that line at 20% and 35% over pace.\n"
            "- **Change-order aging:** a change order is first flagged at 30 "
            "days unsigned, whatever its size; MEDIUM and HIGH move it up from "
            "there as it keeps sitting.\n\n"
            "None of these are universal. A GC running tighter margins on "
            "production work would set the floor lower; a custom builder with "
            "slower owner-decision cycles might push change-order aging out. "
            "That is the point of putting them on sliders instead of leaving "
            "them buried in a config file -- see [how the sample dataset itself "
            f"was calibrated]({REPO_URL}#how-the-numbers-were-calibrated) for "
            "the benchmarks the *numbers on this page* are checked against."
        )
    # The deployment's configured baseline (code defaults plus
    # config/thresholds.json). Every slider starts here and every rerun
    # layers the current slider values back on top of it, so a slider
    # dragged back to its start lands exactly on the shipped number.
    base = det.DEFAULT_THRESHOLDS
    dollar_floor_input = st.number_input(
        "Materiality floor ($)", min_value=1_000, max_value=25_000,
        value=int(base.dollar_floor), step=500,
        help="Below this, nothing gets flagged however bad the percentage looks.",
    )
    default_share = float(base.materiality_pct_of_contract["default"])
    materiality_pct_input = st.slider(
        "Materiality floor (% of contract, residential)", min_value=0.05, max_value=1.00,
        value=round(default_share * 100, 2),
        step=0.05, format="%.2f%%",
        help=(
            "The floor is the larger of the dollar amount above and this share of contract "
            "value. Commercial work carries a proportionally higher share (0.35% against the "
            "shipped 0.25%); moving this moves every project type by the same ratio."
        ),
    ) / 100
    # Scale every project type by the same ratio, so the commercial job's
    # higher share moves with the slider instead of sitting at its
    # shipped value while the houses' floor climbs past it.
    share_ratio = materiality_pct_input / default_share if default_share > 0 else 1.0
    materiality_shares = {k: v * share_ratio for k, v in base.materiality_pct_of_contract.items()}
    variance_low = base.pct_bands["LOW"][0]
    variance_medium, variance_high = st.slider(
        "Cost & buyout variance bands (MEDIUM / HIGH start)",
        # The LOW band opens at 10%, so MEDIUM cannot start there too: a
        # band has to be wider than nothing.
        min_value=int(round(variance_low * 100)) + 1, max_value=80,
        value=(int(round(base.pct_bands["MEDIUM"][0] * 100)), int(round(base.pct_bands["HIGH"][0] * 100))),
        step=1, format="%d%%",
        help="Where a burn-rate or buyout variance moves from LOW to MEDIUM to HIGH.",
    )
    variance_high = max(variance_high, variance_medium + 1)
    co_low = int(base.co_aging_days_bands["LOW"][0])
    co_medium, co_high = st.slider(
        "Change-order aging (MEDIUM / HIGH start, days unsigned)",
        min_value=co_low + 1, max_value=150,
        value=(int(base.co_aging_days_bands["MEDIUM"][0]), int(base.co_aging_days_bands["HIGH"][0])),
        step=1,
        help=(f"A change order is first flagged (LOW) at {co_low} days unsigned; "
              "these move it up from there."),
    )
    co_high = max(co_high, co_medium + 1)

    threshold_overrides = {
        "DOLLAR_FLOOR": float(dollar_floor_input),
        "MATERIALITY_PCT_OF_CONTRACT": materiality_shares,
        "PCT_BANDS": {
            # LOW keeps its floor unless MEDIUM has been dragged down onto
            # it, in which case it gives way by a point rather than
            # collapsing to an empty band.
            "LOW": (min(variance_low, variance_medium / 100 - 0.01), variance_medium / 100),
            "MEDIUM": (variance_medium / 100, variance_high / 100),
            "HIGH": (variance_high / 100, float("inf")),
        },
        "CO_AGING_DAYS_BANDS": {
            "LOW": (float(co_low), float(co_medium)),
            "MEDIUM": (float(co_medium), float(co_high)),
            "HIGH": (float(co_high), float("inf")),
        },
    }

# This session's thresholds: a private, immutable object rather than a
# change to the detection module. Streamlit runs every open browser tab as
# a thread in the same process, so anything written to a module global
# here would be read by every other viewer mid-rerun.
thresholds = det.DEFAULT_THRESHOLDS.with_overrides(threshold_overrides)


def compute_results(data: dict, thresholds: det.Thresholds) -> dict:
    """Every detector's output at the given thresholds. Deliberately not
    cached: the dataset is a few hundred rows across five jobs, this runs
    in well under a second, and caching it would mean keying the cache on
    every slider in the sidebar above -- more risk for a saving nobody
    will feel."""
    return {
        "cost_flags": det.detect_cost_anomalies(data, thresholds),
        "drift_flags": det.detect_budget_drift(data, thresholds),
        "commitment_flags": det.detect_commitment_issues(data, thresholds),
        "schedule_risk": det.compute_schedule_risk(data, thresholds=thresholds),
        "co_aging": det.detect_co_aging(data, thresholds=thresholds),
        "allowances": det.detect_allowance_overages(data, thresholds=thresholds),
        "billing": det.compute_billing_position(data, thresholds),
        "forecast": det.compute_cost_forecast(data, thresholds),
        "compliance": det.detect_compliance_flags(data, thresholds=thresholds),
        "cross_project": det.detect_cross_project_patterns(data, thresholds=thresholds),
        "rollup": det.portfolio_rollup(data, thresholds=thresholds),
        "budgets": det.effective_budgets(data, thresholds),
    }


results = compute_results(data, thresholds)


def short_name(name: str) -> str:
    return name.replace("The ", "").split()[0]


# ---------------------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------------------
st.title("Ridgeline Custom Homes")
st.caption(
    f"Job-cost & schedule-risk dashboard &nbsp;·&nbsp; five live jobs, as of "
    f"{det.DATASET_AS_OF:%d %b %Y} &nbsp;·&nbsp; **synthetic data**: every project, "
    f"subcontractor and figure on this page is fictional."
)

# ---------------------------------------------------------------------------
# PORTFOLIO -- one matrix, not five small tables. Position carries the
# signal so the eye compares along a row instead of re-finding a label on
# five separate cards.
# ---------------------------------------------------------------------------
rollup = results["rollup"].merge(
    projects[["name", "type", "contract_value", "pct_complete", "project_manager",
              "retainage_held"]],
    left_on="project_id", right_index=True,
)

# Worst first, and worst means more than the single worst signal: a job
# rated HIGH on four of seven is a different morning from a job rated HIGH
# on one. Ties break on how many signals are lit, then on contract value,
# so the ordering is deterministic rather than file order.
for level in ("HIGH", "MEDIUM", "LOW"):
    rollup[f"_n_{level}"] = sum((rollup[c] == level).astype(int) for c in det.ROLLUP_COLUMNS)
rollup["_rank"] = rollup["overall_severity"].map(det.SEVERITY_ORDER)
rollup = rollup.sort_values(
    by=["_rank", "_n_HIGH", "_n_MEDIUM", "_n_LOW", "contract_value"],
    ascending=False, kind="stable",
)

if rollup.empty:
    st.warning("No projects in the dataset.")
    st.stop()

# Cash not yet in hand: retainage the owner is holding, plus anything earned
# but not yet invoiced (billed ahead of the work is not cash outstanding --
# it is the opposite problem, and stays out of this number so an over-billed
# job does not net against an under-billed one and hide both).
_billing_by_pid = {b.project_id: b.under_billed for b in results["billing"]}
rollup["cash_outstanding"] = (
    rollup["project_id"].map(_billing_by_pid).clip(lower=0) + rollup["retainage_held"]
)


def flags_for(pid):
    """Every flag on a project, from every detector, worst first."""
    out = []
    for key in ("cost_flags", "drift_flags", "commitment_flags", "co_aging", "allowances"):
        out += [f for f in results[key] if f.project_id == pid]
    for key in ("schedule_risk", "billing", "forecast"):
        out += [f for f in results[key] if f.project_id == pid and f.severity != "NONE"]
    active = set(det.subs_on_project(data, pid)["sub_id"]) if len(data["subcontractors"]) else set()
    out += [f for f in results["compliance"] if f.sub_id in active]
    return sorted(out, key=lambda f: -det.SEVERITY_ORDER[f.severity])


h1, h2 = st.columns([3, 2])
with h1:
    st.header("Five jobs, worst first")
with h2:
    st.write("")
    attention_only = st.checkbox(
        "Only show jobs needing attention", value=False,
        help="Hides any job that reads CLEAR on all seven signals from the grid below.",
    )

grid_rollup = rollup[rollup["overall_severity"] != "NONE"] if attention_only else rollup
if grid_rollup.empty:
    st.success("Every job on the portfolio is clear on all seven signals.")
else:
    matrix_projects = [
        {"name": r["name"], "short": short_name(r["name"]),
         "pct_complete": r["pct_complete"], "contract_value": r["contract_value"],
         **{c: r[c] for c in det.ROLLUP_COLUMNS}}
        for _, r in grid_rollup.iterrows()
    ]
    chart_card(charts.signal_matrix(matrix_projects, ROLLUP_ROWS, lambda p, k: p[k]))

worst = rollup.iloc[0]
total_flags = sum(len(flags_for(p)) for p in rollup["project_id"])
total_cash_out = float(rollup["cash_outstanding"].sum())
shown_note = f" ({len(grid_rollup)} of {len(rollup)} jobs shown above)" if attention_only else ""
st.caption(
    f"{total_flags} open flags across the five jobs, {len(flags_for(worst['project_id']))} of them "
    f"on {esc(worst['name'])}.{shown_note} {money_html(total_cash_out)} sits in retainage or "
    "unbilled earned work across the portfolio right now -- real cash, just not collected yet. "
    "Severity is a fill ladder, not four colors: solid needs a call today, tinted this week, "
    "outlined is worth knowing, a bare check means the signal found nothing. It reads the same "
    "in grayscale and to a colorblind viewer."
)

# ---------------------------------------------------------------------------
# BY PROJECT MANAGER -- worst-first on the job tells you which house to
# call. It does not tell you whose desk that call sits on. A GM reading the
# grid above still has to do this regrouping by hand every morning; this
# does it once, so "how's Priya's week going" has an answer without paging
# through five jobs to find her three.
# ---------------------------------------------------------------------------
st.markdown("### By project manager")
pm_groups = []
for pm, grp in rollup.groupby("project_manager", sort=False):
    worst_pm_sev = max(grp["overall_severity"], key=lambda s: det.SEVERITY_ORDER[s])
    pm_flag_total = sum(len(flags_for(pid)) for pid in grp["project_id"])
    job_names = grp.sort_values("_rank", ascending=False)["name"].tolist()
    pm_groups.append((pm, worst_pm_sev, len(grp), pm_flag_total, job_names))
pm_groups.sort(key=lambda r: (-det.SEVERITY_ORDER[r[1]], -r[3]))

pm_cols = st.columns(len(pm_groups))
for col, (pm, worst_pm_sev, n_jobs, pm_flag_total, job_names) in zip(pm_cols, pm_groups, strict=True):
    with col:
        st.markdown(
            f'<div class="rg-card">'
            f'<div class="rg-call-who">{severity_badge(worst_pm_sev)} &nbsp;{esc(pm)}</div>'
            f'<div class="rg-call-why">{n_jobs} job{"s" if n_jobs != 1 else ""} '
            f'&nbsp;&middot;&nbsp; {pm_flag_total} open flag{"s" if pm_flag_total != 1 else ""}</div>'
            f'<div class="rg-flag">{esc(", ".join(short_name(n) for n in job_names))}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

if results["cross_project"]:
    flags_card([
        (f.severity, f.explanation, "<strong>Across the portfolio:</strong> ")
        for f in results["cross_project"]
    ])
else:
    st.caption(
        "No cost code is running over on three or more jobs at once -- the check that "
        "separates one bad job from a pricing problem in the estimate."
    )

# ---------------------------------------------------------------------------
# PORTFOLIO TREND -- every dollar here is real: each project's own posted
# transactions and milestone baselines, summed at every date any job posted
# one. There is still no per-line percent-complete history in this dataset
# (see the per-project chart in Billing & forecast below), so this is spend
# against plan across all five jobs at once, not five earned-value curves
# stapled together.
# ---------------------------------------------------------------------------
def _value_at(points, on_date):
    """Step-function read of a sorted [(date, cumulative)] series: the last
    posted value on or before on_date, or 0 before the series starts."""
    if not points:
        return 0.0
    idx = bisect.bisect_right([p[0] for p in points], on_date) - 1
    return points[idx][1] if idx >= 0 else 0.0


_budgets_all = results["budgets"]
_actual_curves, _planned_curves = {}, {}
for pid in rollup["project_id"]:
    tx = data["cost_transactions"]
    tx = tx[tx["project_id"] == pid].sort_values("date")
    running, pts = 0.0, []
    for _, t in tx.iterrows():
        running += float(t["amount"])
        pts.append((t["date"].date(), running))
    _actual_curves[pid] = pts

    bac_p = float(_budgets_all[_budgets_all["project_id"] == pid]["current_budget"].sum())
    ms = data["schedule_milestones"]
    ms = ms[ms["project_id"] == pid].sort_values("baseline_date")
    _planned_curves[pid] = sorted(
        (r["baseline_date"].date(), float(r["planned_pct_complete"]) * bac_p)
        for _, r in ms.iterrows() if r["baseline_date"] == r["baseline_date"]
    )

_all_dates = sorted({d for pts in _actual_curves.values() for d, _ in pts}
                     | {d for pts in _planned_curves.values() for d, _ in pts})
if _all_dates:
    portfolio_actual = [(d, sum(_value_at(_actual_curves[pid], d) for pid in rollup["project_id"]))
                        for d in _all_dates]
    portfolio_planned = [(d, sum(_value_at(_planned_curves[pid], d) for pid in rollup["project_id"]))
                         for d in _all_dates]
    portfolio_forecast = sum(f.forecast_at_completion for f in results["forecast"])
    portfolio_earned = float(_budgets_all["expected_spend_to_date"].sum())
    _finish_dates = [data["schedule_milestones"][
        data["schedule_milestones"]["project_id"] == pid]["forecast_date"].max()
        for pid in rollup["project_id"]]
    _finish_dates = [d.date() for d in _finish_dates if d == d]
    portfolio_finish = max(_finish_dates) if _finish_dates else det.DATASET_AS_OF

    st.markdown("### Portfolio trend")
    chart_card(charts.cost_curve_chart(
        portfolio_planned, portfolio_actual, det.DATASET_AS_OF, portfolio_finish,
        portfolio_forecast, earned=portfolio_earned,
        title="All five jobs, cost to date against plan",
        subtitle="Every posted transaction across the portfolio, summed by date",
    ))
    st.caption(
        "All five jobs' posted cost transactions, summed at every date any job posted one, "
        "against all five schedules' planned value. This is the one place the tool shows "
        "the portfolio moving through time instead of a single as-of snapshot."
    )

st.divider()

# ---------------------------------------------------------------------------
# DRILL-DOWN
# ---------------------------------------------------------------------------
selected_name = st.selectbox("Project (worst first)", rollup["name"].tolist(), index=0)
selected_pid = rollup[rollup["name"] == selected_name]["project_id"].iloc[0]
proj = projects.loc[selected_pid]

sched = next(r for r in results["schedule_risk"] if r.project_id == selected_pid)
bill = next(b for b in results["billing"] if b.project_id == selected_pid)
fcst = next(f for f in results["forecast"] if f.project_id == selected_pid)
project_flags = flags_for(selected_pid)

st.subheader(
    f"{selected_name} -- {len(project_flags)} open flag{'s' if len(project_flags) != 1 else ''}"
    if project_flags else f"{selected_name} -- clear on all seven signals"
)

# --- This morning's calls --------------------------------------------------
# The tool's output is a phone call, not a report. Thirteen flags on one job
# are not thirteen conversations; grouping them by who you have to ring is
# the difference between a list and a plan for the morning.
subs_by_code = {}
for _, line in data["commitment_lines"].iterrows():
    if line["project_id"] == selected_pid:
        subs_by_code[line["code"]] = line["sub_name"]


def counterparty(flag):
    kind = getattr(flag, "kind", "")
    if hasattr(flag, "trade") and hasattr(flag, "sub_id"):     # compliance
        return flag.name
    if kind in ("duplicate", "unbudgeted", "unstarted", "drift"):
        return "Your own books"
    if kind in ("buyout", "over-invoiced", "burn-rate", "under-pace"):
        return subs_by_code.get(getattr(flag, "code", None), "Your own books")
    return "The owner"                                  # COs, allowances, billing, margin


if project_flags:
    calls = {}
    for f in project_flags:
        calls.setdefault(counterparty(f), []).append(f)
    ordered = sorted(calls.items(),
                     key=lambda kv: -max(det.SEVERITY_ORDER[f.severity] for f in kv[1]))
    st.markdown("### This morning's calls")
    for who, items in ordered:
        worst_sev = max((f.severity for f in items), key=lambda s: det.SEVERITY_ORDER[s])
        exposure = sum(abs(getattr(f, "variance_amount", 0) or 0) for f in items)
        called = sum(1 for f in items if st.session_state.get(flag_key(selected_pid, f)))
        why = f"{len(items)} flag{'s' if len(items) != 1 else ''}"
        if called:
            why += f" &nbsp;·&nbsp; {called}/{len(items)} called"
        if exposure:
            why += f" &nbsp;·&nbsp; {money_html(exposure)} of exposure named"
        shown, rest = items[:4], items[4:]
        more = (f'<div class="rg-card-more">…and {len(rest)} more, in the tabs below.</div>'
                if rest else "")
        st.markdown(
            f'<div class="rg-card">'
            f'<div class="rg-call-who">{severity_badge(worst_sev)} &nbsp;{esc(who)}</div>'
            f'<div class="rg-call-why">{why}</div>'
            f'<div class="rg-flags">{flag_rows([(f.severity, f.explanation, "") for f in shown])}</div>'
            f'{more}'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.caption(
        "The same decision often raises several flags on purpose -- a verbal "
        "instruction shows up as a burn-rate miss, a quiet budget edit, a "
        "re-issued sub contract and an unsigned change order. They are four "
        "different risks from one conversation, which is why the exposure "
        "figures above are not summed across groups."
    )
else:
    st.success(
        "Nothing on this job needs a call. It is in the dataset deliberately: a "
        "detector that cannot show you a clean job is one you stop believing on a dirty one."
    )

# --- Call list: the working version of the section above -------------------
# "This morning's calls" is read-only, because its job is to be scanned and
# understood at a glance. This is the same list with a checkbox on every
# line, so the visit that starts as reading ends as a worked call sheet --
# and an export of what is left once you have made some of the calls.
def _note_key(project_id: str, flag) -> str:
    return flag_key(project_id, flag) + "_note"


def build_call_list_pdf(rows: list, project_name: str) -> bytes:
    """rows: (who, severity, flag_text, note) tuples. A one-page-per-job
    print sheet -- the CSV goes into Excel, this goes in a shirt pocket or
    an inbox that expects an attachment, not a spreadsheet."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=11)
    buf = io.BytesIO()
    # A4, not US Letter: this is a call sheet meant to print cleanly
    # wherever it's opened, and A4 is the sheet size most of the world
    # (including where this builder's own crews would print it) actually
    # loads.
    doc = SimpleDocTemplate(
        buf, pagesize=A4, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    )
    story = [
        Paragraph(f"Call list &mdash; {html.escape(project_name)}", styles["Title"]),
        Paragraph(
            f"Ridgeline Custom Homes &middot; generated {det.DATASET_AS_OF:%d %b %Y} from the "
            "job-cost &amp; schedule-risk dashboard", styles["Normal"],
        ),
        Spacer(1, 14),
    ]
    header = ["Who to call", "Severity", "Flag", "Note"]
    body = [[html.escape(who), sev, Paragraph(html.escape(text), cell), Paragraph(html.escape(note), cell)]
            for who, sev, text, note in rows]
    table = Table([header] + body, colWidths=[1.15 * inch, 0.7 * inch, 3.45 * inch, 1.6 * inch],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5F5F7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1A1A1A")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7DBE2")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFB")]),
    ]))
    story.append(table)
    doc.build(story)
    return buf.getvalue()


with st.sidebar:
    st.divider()
    st.markdown("#### Call list")
    st.caption(
        f"{selected_name}, worst first. Checked off and noted here, session-only -- export "
        "before you close the tab."
    )
    if not project_flags:
        st.success("Nothing to call on this job.")
    else:
        for f in project_flags:
            key = flag_key(selected_pid, f)
            st.checkbox(
                f"{SEVERITY_STYLE[f.severity]['symbol']} {f.explanation[:64].rstrip()}"
                + ("…" if len(f.explanation) > 64 else ""),
                key=key,
                help=f.explanation,
            )
            st.text_input(
                "Note", key=_note_key(selected_pid, f),
                placeholder="Note -- what they said, what's still open",
                label_visibility="collapsed",
            )
        called_n = sum(1 for f in project_flags if st.session_state.get(flag_key(selected_pid, f)))
        st.progress(called_n / len(project_flags))
        st.caption(f"{called_n} of {len(project_flags)} called")

        remaining = [f for f in project_flags if not st.session_state.get(flag_key(selected_pid, f))]
        export_rows = [
            (counterparty(f), f.severity, f.explanation, st.session_state.get(_note_key(selected_pid, f), ""))
            for f in remaining
        ]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Project", "Who to call", "Severity", "Flag", "Note"])
        for who, sev, text, note in export_rows:
            writer.writerow([selected_name, who, sev, text, note])
        list_label = "remaining" if called_n else "today's"
        st.download_button(
            f"Download {list_label} call list (CSV)",
            data=buf.getvalue(),
            file_name=f"{selected_pid}_call_list.csv",
            mime="text/csv",
            disabled=not remaining,
        )
        st.download_button(
            f"Download {list_label} call list (PDF)",
            data=build_call_list_pdf(export_rows, selected_name) if remaining else b"",
            file_name=f"{selected_pid}_call_list.pdf",
            mime="application/pdf",
            disabled=not remaining,
        )

st.divider()

# The four headline numbers as one bordered unit, native to Streamlit rather
# than hand-built: st.metric and st.columns cannot be assembled into a raw
# HTML string the way a chart or a flag list can, so this is the one card
# on the page that uses Streamlit's own container border instead of ours.
with st.container(border=True):
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Revised contract", money(bill.revised_contract))
    c2.metric(
        f"Projected margin · {SEVERITY_STYLE[fcst.severity]['label']}",
        f"{fcst.projected_margin_pct:.1%}",
        delta=f"{(fcst.projected_margin_pct - fcst.target_margin_pct) * 100:+.1f} pts vs. priced",
        delta_color="off",   # one severity language on this page, not Streamlit's as well
    )
    c3.metric(
        f"Forecast finish · {SEVERITY_STYLE[sched.severity]['label']}",
        f"{sched.critical_path_slip_days} days late" if sched.critical_path_slip_days else "On baseline",
    )
    c4.metric(
        f"Billing position · {SEVERITY_STYLE[bill.severity]['label']}",
        ("+" if bill.under_billed >= 0 else "−") + money(abs(bill.under_billed)),
    )
    _cash_out = max(bill.under_billed, 0) + bill.retainage_held
    c5.metric(
        "Cash not yet collected",
        money(_cash_out),
    )
    st.caption(
        "Billing position is positive when the job has earned more than it has "
        "invoiced. Margin is against the priced margin, in points. Cash not yet "
        f"collected is retainage held (${bill.retainage_held:,.0f}) plus any earned, "
        "unbilled work -- real money owed to the job, just not in the bank."
    )

cost_items = [f for f in results["cost_flags"] if f.project_id == selected_pid]
drift_items = [f for f in results["drift_flags"] if f.project_id == selected_pid]
commit_items = [f for f in results["commitment_flags"] if f.project_id == selected_pid]
co_items = [f for f in results["co_aging"] if f.project_id == selected_pid]
al_items = [a for a in results["allowances"] if a.project_id == selected_pid]
_active_subs = set(det.subs_on_project(data, selected_pid)["sub_id"]) \
    if len(data["subcontractors"]) else set()
comp_items = [f for f in results["compliance"] if f.sub_id in _active_subs]

tab_cost, tab_schedule, tab_co, tab_allow, tab_fin, tab_compliance = st.tabs([
    tab_label("Cost", cost_items + drift_items + commit_items),
    tab_label("Schedule", [sched] if sched.severity != "NONE" else []),
    tab_label("Change orders", co_items),
    tab_label("Allowances", al_items),
    tab_label("Billing & forecast",
              [f for f in (bill, fcst) if f.severity != "NONE"]),
    tab_label("Sub compliance", comp_items),
])

with tab_cost:
    budgets = results["budgets"]
    pb = budgets[budgets["project_id"] == selected_pid].merge(
        data["cost_codes"][["code", "description"]], on="code", how="left"
    )
    flagged = {f.code: f.severity for f in cost_items}
    chart_rows = [
        {"code": r["code"], "description": str(r["description"] or ""),
         "variance_amount": float(r["variance_amount"]),
         "severity": flagged.get(r["code"], "LOW"),
         "flagged": r["code"] in flagged}
        for _, r in pb.iterrows()
    ]
    if chart_rows:
        chart_card(charts.cost_variance_chart(chart_rows, float(pb["dollar_floor"].iloc[0])))

    if not cost_items and not drift_items and not commit_items:
        st.success("No cost anomalies on this project.")
    flags_card(
        [(f.severity, f.explanation, "") for f in cost_items]
        + [(f.severity, f.explanation, "<em>(budget revision)</em> ") for f in drift_items]
        + [(f.severity, f.explanation, "<em>(sub contract)</em> ") for f in commit_items]
    )

    with st.expander("Every cost code: budget, progress, spend and forecast"):
        b = pb[["code", "description", "current_budget", "progress",
                "actual_spend", "variance_pct", "forecast_at_completion"]]
        b = b.sort_values("variance_pct", ascending=False, kind="stable").rename(columns={
            "code": "Code", "description": "Description", "current_budget": "Budget",
            "progress": "% complete", "actual_spend": "Spent",
            "variance_pct": "Variance", "forecast_at_completion": "Forecast",
        })
        st.dataframe(
            b.style.format({"Budget": money, "% complete": "{:.0%}", "Spent": money,
                            "Variance": "{:+.1%}", "Forecast": money}),
            use_container_width=True, hide_index=True,
        )

    with st.expander("Sub contracts: one per trade, with the scopes each one covers"):
        sc = data["commitments"]
        sc = sc[sc["project_id"] == selected_pid][
            ["sub_name", "trade", "commitment_type", "scope_lines", "contract_amount",
             "co_amount", "invoiced_to_date", "retention_held", "retention_released",
             "paid_to_date", "signed_date"]
        ].rename(columns={
            "sub_name": "Subcontractor", "trade": "Trade", "commitment_type": "Document",
            "scope_lines": "Scopes", "contract_amount": "Contract", "co_amount": "Change orders",
            "invoiced_to_date": "Invoiced", "retention_held": "Retention held",
            "retention_released": "Retention released", "paid_to_date": "Paid",
            "signed_date": "Signed",
        })
        st.dataframe(
            sc.style.format({
                "Contract": money, "Change orders": money, "Invoiced": money,
                "Retention held": money, "Retention released": money, "Paid": money, "Signed": DATE,
            }),
            use_container_width=True, hide_index=True,
        )
        st.caption(
            "A trade signs one contract per job covering every scope it holds, so this "
            "is the sub's whole exposure on the job in one row. Retention releases "
            "scope by scope as each one closes out."
        )

with tab_schedule:
    flags_card([(sched.severity, sched.explanation, "")])
    ms = data["schedule_milestones"]
    ms = ms[ms["project_id"] == selected_pid].sort_values("baseline_date")
    slip_rows = [
        {"milestone": r["milestone"],
         "baseline": r["baseline_date"].date() if r["baseline_date"] == r["baseline_date"] else None,
         "forecast": r["forecast_date"].date() if r["forecast_date"] == r["forecast_date"] else None,
         "actual": r["actual_date"].date() if r["actual_date"] == r["actual_date"] else None,
         "critical_path": bool(r["critical_path"])}
        for _, r in ms.iterrows()
    ]
    chart_card(charts.milestone_slip_chart(slip_rows, det.DATASET_AS_OF))
    st.caption(
        "Slip is read off the terminal critical-path milestone, not summed across the "
        "schedule: one delay that pushes framing pushes everything behind it, and adding "
        "those up counts the same delay several times. Weather comes off the job's float "
        "before it moves the finish date."
    )
    with st.expander("Milestone dates and delay reasons"):
        table = ms[["milestone", "baseline_date", "forecast_date", "actual_date",
                    "critical_path", "weather_delay_days", "other_delay_days", "delay_reason"]]
        st.dataframe(
            table.rename(columns={
                "milestone": "Milestone", "baseline_date": "Baseline",
                "forecast_date": "Forecast", "actual_date": "Actual",
                "critical_path": "Critical path", "weather_delay_days": "Weather days",
                "other_delay_days": "Other days", "delay_reason": "Reason",
            }).style.format({"Baseline": DATE, "Forecast": DATE, "Actual": DATE}, na_rep="—"),
            use_container_width=True, hide_index=True,
        )

with tab_co:
    if co_items:
        chart_card(charts.co_aging_chart(
            [{"co_id": f.co_id, "days": int(f.days_unapproved),
              "amount": float(f.amount), "severity": f.severity} for f in co_items],
            thresholds.co_aging_days_bands,
        ))
        st.caption(
            "Bar color is the flag's severity, not its age: a change order worth "
            "less than the job's materiality floor stays LOW however long it sits, "
            "because chasing a signature costs more than the exposure is worth."
        )
    else:
        st.success(f"No change orders sitting unsigned past "
                   f"{thresholds.co_aging_threshold_days:.0f} days.")
    flags_card([(f.severity, f.explanation, "") for f in co_items])
    all_co = data["change_orders"]
    co_tbl = all_co[all_co["project_id"] == selected_pid][
        ["co_id", "code", "reason", "cost_amount", "amount", "time_extension_days",
         "submitted_date", "approved_date"]
    ].rename(columns={
        "co_id": "CO", "code": "Code", "reason": "Reason", "cost_amount": "Cost",
        "amount": "To owner", "time_extension_days": "Days", "submitted_date": "Submitted",
        "approved_date": "Approved",
    })
    st.dataframe(
        co_tbl.style.format({"Cost": money, "To owner": money, "Submitted": DATE,
                             "Approved": DATE}, na_rep="unsigned"),
        use_container_width=True, hide_index=True,
    )
    st.caption(
        "Cost is what the change adds to the budget; To owner is what it adds to the "
        "contract at the job's priced margin. Keeping the two apart is the difference "
        "between a change order that protects margin and one that quietly gives it away."
    )

with tab_allow:
    if not al_items:
        st.success("Owner selections are inside allowance and on time.")
    flags_card([(a.severity, a.explanation, "") for a in al_items])
    al = data["allowances"]
    al = al[al["project_id"] == selected_pid].copy()
    cos = data["change_orders"]
    covered = {}
    if "allowance_id" in cos.columns:
        for _, c in cos[cos["approved_date"].notna()].iterrows():
            if c["allowance_id"] and str(c["allowance_id"]) != "nan":
                covered[c["allowance_id"]] = c["co_id"]
    al["Covered by"] = [covered.get(a_id, "—") for a_id in al["allowance_id"]]
    al = al[["allowance_id", "code", "description", "allowance_amount", "selected_amount",
             "selection_due", "selection_date", "Covered by"]].rename(columns={
        "allowance_id": "ID", "code": "Code", "description": "Description",
        "allowance_amount": "Allowance", "selected_amount": "Selected",
        "selection_due": "Due", "selection_date": "Selected on",
    })
    st.dataframe(
        al.style.format({"Allowance": money, "Selected": money, "Due": DATE,
                         "Selected on": DATE}, na_rep="not selected"),
        use_container_width=True, hide_index=True,
    )
    st.caption(
        "An allowance is a contract number, so it carries the job's margin like every "
        "other contract dollar. Setting one at the builder's cost means the owner spends "
        "the whole allowance and the builder earns nothing on that scope."
    )

with tab_fin:
    flags_card([(bill.severity, bill.explanation, ""), (fcst.severity, fcst.explanation, "")])

    pb_all = results["budgets"]
    pb_p = pb_all[pb_all["project_id"] == selected_pid]
    bac = float(pb_p["current_budget"].sum())
    tx = data["cost_transactions"]
    tx = tx[tx["project_id"] == selected_pid].sort_values("date")
    ms = data["schedule_milestones"]
    ms = ms[ms["project_id"] == selected_pid].sort_values("baseline_date")
    if len(tx) and len(ms):
        running, actual_points = 0.0, []
        step = max(1, len(tx) // 70)
        for i, (_, t) in enumerate(tx.iterrows()):
            running += float(t["amount"])
            if i % step == 0 or i == len(tx) - 1:
                actual_points.append((t["date"].date(), running))
        planned_points = [(r["baseline_date"].date(), float(r["planned_pct_complete"]) * bac)
                          for _, r in ms.iterrows() if r["baseline_date"] == r["baseline_date"]]
        finish = ms.iloc[-1]["forecast_date"]
        chart_card(charts.cost_curve_chart(
            planned_points, actual_points, det.DATASET_AS_OF,
            finish.date() if finish == finish else det.DATASET_AS_OF,
            fcst.forecast_at_completion,
            earned=float(pb_p["expected_spend_to_date"].sum()),
            title=f"{short_name(selected_name)}, cost to date against plan",
        ))
        st.caption(
            "Earned value is one point at today, not a curve. This dataset holds the "
            "superintendent's per-line percent complete as it stands now, with no history "
            "of it, so an earned-value curve would be invented. Drawing the one honest "
            "point is worth more than a smooth line nobody can source."
        )
    st.caption(
        "Earned revenue is revised contract (contract plus approved change orders "
        "at their sell price) × reported % complete. Forecast at completion is the "
        "sum of every line's forecast: while a line is open, the larger of its "
        "budget, its sub contract and its spend extrapolated over progress; once "
        "done, what it cost plus what the sub has not yet invoiced."
    )

with tab_compliance:
    on_project = det.subs_on_project(data, selected_pid)
    flags_by_sub = {}
    for f in results["compliance"]:
        flags_by_sub.setdefault(f.sub_id, []).append(f)
    if on_project.empty:
        st.write("No subcontractors recorded on this project.")
    else:
        flagged_subs = [f for _, s in on_project.iterrows()
                        for f in flags_by_sub.get(s["sub_id"], [])]
        current = [s for _, s in on_project.iterrows() if not flags_by_sub.get(s["sub_id"])]
        if flagged_subs:
            flags_card([
                (f.severity, f.explanation, "")
                for f in sorted(flagged_subs, key=lambda f: -det.SEVERITY_ORDER[f.severity])
            ])
        else:
            st.success("Every subcontractor on this job has current insurance and licensing.")
        if current:
            with st.expander(f"{len(current)} subcontractors current on insurance and licensing"):
                for s in current:
                    flag_line(
                        "NONE",
                        f"{s['name']} ({s['trade']}): insurance to "
                        f"{s['insurance_expiry']:%Y-%m-%d}, license to {s['license_expiry']:%Y-%m-%d}",
                    )

# ---------------------------------------------------------------------------
# FOOTER -- a deployed link is the first thing most readers see, and until
# now it said nothing about what this is or who wrote it.
# ---------------------------------------------------------------------------
st.divider()
f1, f2 = st.columns([3, 2])
with f1:
    st.markdown(
        "**Job-cost & schedule-risk dashboard** &nbsp;&middot;&nbsp; "
        "built by Nathan Taylor",
        unsafe_allow_html=True,
    )
    st.caption(
        "Seven checks over a synthetic but internally consistent builder's ledger: "
        "eleven tables, five jobs, one seeded generator. Every threshold is a named "
        "constant in `src/detection.py` with the reasoning written next to it, and "
        "every one is covered by a test that fails if the number moves."
    )
with f2:
    st.caption(
        f"[Source and method on GitHub]({REPO_URL}) &nbsp;&middot;&nbsp; "
        f"[How the numbers were calibrated]({REPO_URL}#how-the-numbers-were-calibrated)"
    )
    st.caption(
        "Ridgeline Custom Homes is fictional. No client data, real or "
        "anonymized, appears anywhere in this repository."
    )
