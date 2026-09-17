"""The charts are hand-rolled SVG, so nothing between this module and the
browser will tell you that a string is malformed or that a label has run
off the right-hand edge. A broken chart does not raise -- it renders as
blank space, and the page around it looks fine.

These tests parse every chart the app draws, on every project, and check
the geometry that a screenshot would otherwise have to catch.
"""
import math
import re
import xml.etree.ElementTree as ET
from datetime import date

import pytest

import charts
from test_app import run_app

SVG_NS = "{http://www.w3.org/2000/svg}"
# The same bound the chart module fits labels with, so this checks its
# arithmetic rather than re-guessing the font metrics.
CHAR_W = charts.CHAR_W


def svgs_on_page(index):
    out = []
    for _, kind, payload in run_app(index):
        if kind == "markdown":
            out.extend(re.findall(r"<svg\b.*?</svg>", payload[0], re.S))
    return out


@pytest.mark.parametrize("index", range(5))
def test_every_chart_is_well_formed_xml(data, index):
    found = svgs_on_page(index)
    assert found, f"project index {index} drew no charts at all"
    for svg in found:
        ET.fromstring(svg)          # raises on malformed markup


@pytest.mark.parametrize("index", range(5))
def test_nothing_is_drawn_outside_the_viewbox(data, index):
    for svg in svgs_on_page(index):
        root = ET.fromstring(svg)
        _, _, vw, vh = (float(v) for v in root.get("viewBox").split())
        for rect in root.iter(f"{SVG_NS}rect"):
            x, w = float(rect.get("x", 0)), float(rect.get("width", 0))
            assert -0.5 <= x and x + w <= vw + 0.5, f"rect {x}+{w} past {vw}"
        for text in root.iter(f"{SVG_NS}text"):
            label = "".join(text.itertext())
            width = len(label) * float(text.get("font-size", 11)) * CHAR_W
            anchor = text.get("text-anchor", "start")
            x = float(text.get("x", 0))
            left = {"start": x, "middle": x - width / 2, "end": x - width}[anchor]
            assert left >= -1, f"{label!r} starts at {left:.0f}, left of the frame"
            assert left + width <= vw + 1, f"{label!r} ends at {left + width:.0f} past {vw}"
            assert 0 <= float(text.get("y", 0)) <= vh, f"{label!r} outside {vh}"


@pytest.mark.parametrize("index", range(5))
def test_no_chart_label_carries_a_bare_dollar_sign(data, index):
    """The SVG goes into a markdown block, where "$" opens a LaTeX span."""
    for svg in svgs_on_page(index):
        assert "$" not in svg.replace("&#36;", "")


@pytest.mark.parametrize("raw", [1, 950, 9_500, 42_000, 613_000, 850_000,
                                 1_100_000, 2_050_000, 3_283_000, 47_000_000])
def test_axis_scales_are_round_and_readable(raw):
    step, peak = charts._nice_scale(raw)
    assert peak >= raw, "the top of the axis has to clear the data"
    assert 3 <= round(peak / step) <= 7, f"{round(peak / step)} gridlines for {raw}"
    assert math.isclose(peak / step, round(peak / step)), "top is not a whole step"
    mantissa = step / 10 ** math.floor(math.log10(step))
    assert mantissa in (1, 2, 2.5, 5), f"step {step} is not a round number"


def test_axis_units_do_not_mix_down_one_axis():
    tick = charts._axis_units(3_500_000)
    assert [tick(v) for v in (0, 1_000_000, 3_500_000)] == ["&#36;0", "&#36;1.0M", "&#36;3.5M"]
    tick = charts._axis_units(700_000)
    assert tick(700_000) == "&#36;700k"


@pytest.mark.parametrize("text,limit,expect", [
    ("Custom Millwork & Cabinetry", 20, "Custom Millwork…"),
    ("Foundations", 20, "Foundations"),
    ("Supercalifragilistic", 20, "Supercalifragilistic"),
    ("Supercalifragilisticexpialidocious", 12, "Supercalifr…"),
])
def test_labels_are_clipped_on_a_word_boundary(text, limit, expect):
    got = charts._clip(text, limit)
    assert got == expect
    assert len(got) <= limit


def test_every_chart_survives_empty_input():
    """A brand-new job has no change orders and no spend. The page still
    has to render, and an exception here takes the whole tab down."""
    assert "<svg" in charts.cost_variance_chart([], 5000)
    assert "<svg" in charts.milestone_slip_chart([], date(2026, 9, 30))
    assert "<svg" in charts.co_aging_chart([], {"LOW": (30, 46), "MEDIUM": (46, 61),
                                                "HIGH": (61, math.inf)})
    assert "<svg" in charts.signal_matrix([], [("Cost", "cost_severity")], lambda p, k: "NONE")
