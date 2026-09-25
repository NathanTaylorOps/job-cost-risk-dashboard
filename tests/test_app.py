"""The dashboard is the artifact anyone actually looks at, so it has to be
executed by the suite rather than assumed to work.

A health-check against a running Streamlit server does not do this: the
server answers before the script has run, so a NameError in the app ships
green. These tests run the real app file top to bottom against a stub and
assert on what it rendered.
"""
import os
import re
import runpy
import sys
import warnings

import pytest

HERE = os.path.dirname(__file__)
REPO_ROOT = os.path.join(HERE, "..")
APP = os.path.join(REPO_ROOT, "app", "streamlit_app.py")

sys.path.insert(0, HERE)
import streamlit_stub  # noqa: E402


def run_app(select_index=0, widget_overrides=None):
    """Execute the dashboard with the stub standing in for Streamlit.
    widget_overrides simulates a viewer having moved a control, keyed by
    its label. The app's globals from the run are left on
    streamlit_stub.LAST_RUN_GLOBALS for a test that needs to read what
    the page computed."""
    streamlit_stub.reset(select_index, widget_overrides)
    sys.modules["streamlit"] = streamlit_stub
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            streamlit_stub.LAST_RUN_GLOBALS = runpy.run_path(APP, run_name="__main__")
    finally:
        sys.modules.pop("streamlit", None)
    return list(streamlit_stub.LOG)


@pytest.mark.parametrize("index", range(5))
def test_the_dashboard_renders_every_project_without_error(data, index):
    log = run_app(index)
    kinds = [k for _, k, _ in log]
    assert "stop" not in kinds, "the app bailed out"
    assert "error" not in kinds, [p for _, k, p in log if k == "error"]
    assert kinds.count("tabs") == 1
    assert log[-1][1] == "caption", "the app did not reach its last line"


def test_the_dashboard_opens_on_the_job_that_needs_the_call(data):
    """Worst first is the whole premise of the front screen, so the
    drill-down has to open on the worst job rather than on whichever
    project happens to sit first in the file."""
    log = run_app(0)
    options = next(p[1] for _, k, p in log if k == "selectbox")
    worst = det_worst_project(data)
    assert options[0] == worst, f"opens on {options[0]}, worst job is {worst}"


def det_worst_project(data):
    import detection as det
    rollup = det.portfolio_rollup(data).merge(
        data["projects"][["project_id", "name", "contract_value"]], on="project_id"
    )
    highs = sum((rollup[c] == "HIGH").astype(int) for c in det.ROLLUP_COLUMNS)
    rollup = rollup.assign(_h=highs, _r=rollup["overall_severity"].map(det.SEVERITY_ORDER))
    return rollup.sort_values(["_r", "_h", "contract_value"], ascending=False).iloc[0]["name"]


def test_no_flag_text_reaches_the_page_with_an_unescaped_dollar_sign(data):
    """Streamlit runs markdown through a LaTeX pass, so "$30,030 of cost
    ($38,500 to the owner)" renders as math with the dollar signs eaten
    and the text in between italicized. Every explanation this tool
    writes is full of dollar amounts."""
    for index in range(5):
        for _, kind, payload in run_app(index):
            if kind == "markdown":
                text = payload[0]
                assert "$" not in text.replace("&#36;", ""), text[:160]


def test_html_blocks_carry_no_markdown_syntax(data):
    """CommonMark stops parsing markdown inside a block-level HTML tag, so
    anything this page wraps in a <div> is passed to the browser verbatim.
    A backslash escape is never consumed there and reaches the screen as
    "\\$59,970"; "**bold**" reaches it as four asterisks. Inside an HTML
    block the escape has to be an entity and the emphasis has to be a tag."""
    for index in range(5):
        for _, kind, payload in run_app(index):
            if kind != "markdown":
                continue
            text = payload[0]
            if not text.lstrip().startswith("<div"):
                continue
            assert "\\$" not in text, text[:160]
            assert "**" not in text, text[:160]
            assert not re.search(r"\*\(|\)\*", text), text[:160]


def test_no_markdown_payload_is_an_indented_code_block(data):
    """Four leading spaces make a CommonMark indented code block. The page's
    stylesheet lives in a triple-quoted literal inside a function call, so
    indenting it to match the surrounding code prints the CSS on the page
    instead of applying it -- and the whole type scale silently dies."""
    import textwrap
    for index in range(5):
        for _, kind, payload in run_app(index):
            if kind != "markdown":
                continue
            for line in textwrap.dedent(payload[0]).strip().splitlines():
                assert not (line.startswith("    ") and line.lstrip().startswith("<")), line


def test_no_table_shows_a_raw_missing_value(data):
    """A pandas frame with gaps renders "nan", "NaT" and "None" straight
    onto the page unless every Styler carries na_rep. An unsigned change
    order has no approval date by definition, so these columns are not an
    edge case -- they are most of the interesting rows."""
    for index in range(5):
        for _, kind, payload in run_app(index):
            if kind not in ("dataframe", "table"):
                continue
            obj = payload[0]
            if not hasattr(obj, "to_html"):
                continue
            html_out = obj.to_html()
            for token in ("nan", "NaT", "NaN", "None", "inf"):
                assert not re.search(r">\s*" + token + r"\s*<", html_out), (
                    f"{token} reached a table on project index {index}")


def test_the_page_is_built_from_bordered_cards(data):
    """The layout is a border around every distinct unit of content -- a
    chart, a list of flags, a group of calls. A regression here means the
    page went back to being a loose scroll of unboxed lines."""
    for index in range(5):
        markdown_payloads = [payload[0] for _, kind, payload in run_app(index) if kind == "markdown"]
        assert any('class="rg-card"' in t or 'class="rg-card rg-chart-card"' in t
                  for t in markdown_payloads), f"no card rendered on project index {index}"
        assert any('class="rg-card rg-chart-card"' in t
                  for t in markdown_payloads), f"no chart card rendered on project index {index}"


def test_the_headline_numbers_sit_in_a_bordered_container(data):
    """The four metrics are native Streamlit widgets, so they cannot be
    wrapped in our own HTML card the way a chart or a flag list can; this
    checks the one place the page relies on Streamlit's own container
    border instead of ours."""
    for index in range(5):
        opens = [payload[0] for _, kind, payload in run_app(index) if kind == "container_open"]
        assert True in opens, f"no bordered container on project index {index}"


def test_every_bordered_container_is_closed(data):
    for index in range(5):
        kinds = [kind for _, kind, _ in run_app(index)]
        assert kinds.count("container_open") == kinds.count("container_close"), (
            f"unbalanced container open/close on project index {index}")


def test_flag_rows_inside_a_card_carry_no_loose_top_level_flag_class(data):
    """A flags_card() call nests every row inside a single .rg-flags
    wrapper -- one wrapper, however many rows -- so the CSS divider between
    rows applies. A row escaping that wrapper as a second, sibling
    .rg-flags block would silently lose its divider against the row before
    it and read as an orphan line again."""
    for index in range(5):
        for _, kind, payload in run_app(index):
            if kind != "markdown":
                continue
            text = payload[0]
            if 'class="rg-card"' in text:
                assert text.count('class="rg-flags"') <= 1, text[:160]


def test_the_sidebar_carries_all_four_threshold_controls(data):
    """The live threshold panel is the dashboard's argument that it
    understands these numbers need calibrating per business, not just a
    claim in the README -- so the four controls actually have to be on
    the page, in the sidebar, every time."""
    expected = {
        "Materiality floor ($)",
        "Materiality floor (% of contract, residential)",
        "Cost & buyout variance bands (MEDIUM / HIGH start)",
        "Change-order aging (MEDIUM / HIGH start, days unsigned)",
    }
    for index in range(5):
        log = run_app(index)
        sidebar_controls = {
            payload[0] for ctx, kind, payload in log
            if ctx == "sidebar" and kind in ("slider", "number_input")
        }
        assert expected <= sidebar_controls, f"missing controls on index {index}: {expected - sidebar_controls}"


def test_the_call_list_has_exactly_one_checkbox_per_open_flag(data):
    """The call list is a working copy of "This morning's calls": every
    flag shown there needs a checkbox in the sidebar, no more and no
    fewer, or ticking one off would not correspond to anything on screen."""
    for index in range(5):
        log = run_app(index)
        subheader = next(p[0] for _, k, p in log if k == "subheader")
        m = re.search(r"-- (\d+) open flag", subheader)
        expected_n = int(m.group(1)) if m else 0
        sidebar_checkboxes = [p for ctx, kind, p in log if ctx == "sidebar" and kind == "checkbox"]
        assert len(sidebar_checkboxes) == expected_n, (
            f"index {index}: subheader says {expected_n} open flags, "
            f"sidebar call list has {len(sidebar_checkboxes)} checkboxes"
        )
        sidebar_kinds = [kind for ctx, kind, _ in log if ctx == "sidebar"]
        if expected_n:
            assert "download_button" in sidebar_kinds
        else:
            assert "success" in sidebar_kinds, "a clear job should tell you there is nothing to call"


def test_the_call_list_has_one_note_field_per_open_flag(data):
    """A checkbox alone can say a call happened; it cannot say what the sub
    told you or why the item is still open. Every flag with a checkbox
    needs the matching note field, one-to-one."""
    for index in range(5):
        log = run_app(index)
        subheader = next(p[0] for _, k, p in log if k == "subheader")
        m = re.search(r"-- (\d+) open flag", subheader)
        expected_n = int(m.group(1)) if m else 0
        sidebar_notes = [p for ctx, kind, p in log if ctx == "sidebar" and kind == "text_input"]
        assert len(sidebar_notes) == expected_n, (
            f"index {index}: {expected_n} open flags but {len(sidebar_notes)} note fields")


def test_call_list_exports_both_csv_and_pdf_when_flags_are_open(data):
    """The CSV goes into a spreadsheet; the PDF goes in an inbox or a
    pocket. A working call sheet needs both, and the PDF has to actually
    be a PDF, not just a button that claims to make one."""
    for index in range(5):
        log = run_app(index)
        subheader = next(p[0] for _, k, p in log if k == "subheader")
        if "open flag" not in subheader:
            continue
        downloads = [p for ctx, kind, p in log if ctx == "sidebar" and kind == "download_button"]
        files = {fname for _, fname, _ in downloads}
        assert any(f.endswith(".csv") for f in files), f"index {index}: no CSV export button"
        assert any(f.endswith(".pdf") for f in files), f"index {index}: no PDF export button"
        pdf_bytes = next(d for _, fname, d in downloads if fname and fname.endswith(".pdf"))
        assert isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes[:4] == b"%PDF", (
            f"index {index}: PDF export is not a real PDF")
        csv_text = next(d for _, fname, d in downloads if fname and fname.endswith(".csv"))
        assert "Note" in csv_text.splitlines()[0], f"index {index}: CSV export has no Note column"


def test_every_project_manager_shows_up_in_the_pm_rollup(data):
    """Five jobs, two PMs, not an even split -- the rollup only earns its
    keep if it actually reflects that imbalance rather than just listing
    names."""
    for index in range(5):
        log = run_app(index)
        pms = set(data["projects"]["project_manager"])
        assert len(pms) >= 2, "fixture should carry more than one PM to make this test meaningful"
        who_cards = "".join(p[0] for _, k, p in log if k == "markdown" and 'rg-call-who' in p[0])
        for pm in pms:
            assert pm in who_cards, f"index {index}: {pm} missing from the PM rollup"


def test_headline_metrics_include_cash_not_yet_collected(data):
    """Retainage held plus earned-but-unbilled work is real money the job
    does not have yet. It needs its own number, not a mental sum of two
    other metrics on the same card."""
    for index in range(5):
        log = run_app(index)
        metric_labels = [p[0] for _, kind, p in log if kind == "metric"]
        assert any("Cash not yet collected" in lbl for lbl in metric_labels), (
            f"index {index}: no cash-not-yet-collected metric")


def test_the_portfolio_trend_chart_has_its_own_title(data):
    """cost_curve_chart draws both a per-project curve and the portfolio
    curve; if both used the default title, two identically-labeled charts
    would sit on the same page for no reason a reader could find."""
    log = run_app(0)
    combined = "".join(p[0] for _, k, p in log if k == "markdown")
    assert "All five jobs, cost to date against plan" in combined
    assert "Cost to date against plan" not in combined, (
        "the portfolio chart should not carry the old generic per-project title")


def test_threshold_methodology_expander_is_in_the_sidebar(data):
    """The four sliders say what the numbers are; this says where they came
    from and that they are a starting point, not a standard."""
    log = run_app(0)
    sidebar_expanders = [p[0] for ctx, kind, p in log if ctx == "sidebar" and kind == "expander"]
    assert any("Where these starting numbers came from" in e for e in sidebar_expanders)


VARIANCE_SLIDER = "Cost & buyout variance bands (MEDIUM / HIGH start)"
MATERIALITY_SLIDER = "Materiality floor (% of contract, residential)"


def test_the_variance_slider_at_its_minimum_does_not_crash_the_page(data):
    """The LOW band opens at 10%. Dragging MEDIUM down to the bottom of the
    slider used to produce LOW = (0.10, 0.10), an empty band, and the
    threshold validation then raised on every rerun until the viewer
    found the slider again. MEDIUM cannot start below 11% now, and if a
    config ever puts LOW's floor above the slider's bottom the LOW band
    gives way by a point rather than collapsing."""
    import detection as det
    floor = det.DEFAULT_THRESHOLDS.pct_bands["LOW"][0]
    minimum = int(round(floor * 100)) + 1
    log = run_app(0, {VARIANCE_SLIDER: (minimum, minimum + 1)})
    assert "error" not in [k for _, k, _ in log]
    thresholds = streamlit_stub.LAST_RUN_GLOBALS["thresholds"]
    low, medium, high = (thresholds.pct_bands[b] for b in ("LOW", "MEDIUM", "HIGH"))
    assert low[0] < low[1] == medium[0] < medium[1] == high[0]
    assert low == (pytest.approx(floor), pytest.approx(minimum / 100))
    # And the app cannot even ask for a value below that minimum: the stub
    # enforces the widget range the way the real widget does.
    with pytest.raises(ValueError):
        run_app(0, {VARIANCE_SLIDER: (minimum - 1, minimum)})


def test_the_materiality_slider_moves_every_project_type(data):
    """The commercial job carries a higher share than the houses. A slider
    that only rewrote the "default" key left the commercial floor at its
    shipped 0.35% while the residential one was dragged past it, so the
    control silently stopped applying to one job in five."""
    import detection as det
    base = det.DEFAULT_THRESHOLDS.materiality_pct_of_contract
    run_app(0, {MATERIALITY_SLIDER: 0.75})
    shares = streamlit_stub.LAST_RUN_GLOBALS["thresholds"].materiality_pct_of_contract
    assert shares["default"] == pytest.approx(0.0075)
    for key, value in base.items():
        assert shares[key] == pytest.approx(value * 0.0075 / base["default"]), key
    assert shares["Light Commercial (ground-up)"] > shares["default"]


def test_a_viewer_moving_a_slider_does_not_touch_the_module_defaults(data):
    """Streamlit runs every open tab as a thread in one process. The page
    builds its thresholds as a private object and never writes them back
    to the detection module, so one viewer's slider cannot move another
    viewer's flags."""
    import detection as det
    before = det.DEFAULT_THRESHOLDS.as_dict()
    run_app(0, {"Materiality floor ($)": 25_000, VARIANCE_SLIDER: (50, 60)})
    session = streamlit_stub.LAST_RUN_GLOBALS["thresholds"]
    assert session.dollar_floor == 25_000 and session.pct_bands["MEDIUM"] == (0.50, 0.60)
    assert det.DEFAULT_THRESHOLDS.as_dict() == before
    assert det.DOLLAR_FLOOR == 5_000 and det.PCT_BANDS["MEDIUM"] == (0.20, 0.35)
