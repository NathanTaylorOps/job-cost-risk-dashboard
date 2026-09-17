"""Charts, drawn as inline SVG.

Why SVG by hand rather than a charting library: these are four purpose-built
figures with a strict spec, and drawing them directly buys three things that
matter more here than a library's convenience. Severity has to be encoded as
an ordinal ladder that survives grayscale and color blindness, not as four
hues; every mark needs a text label beside it rather than a color in a
legend; and the output has to render identically on someone else's machine
two years from now, with no chart-library version in the middle. It also
keeps the dependency list at three packages.

Every function here is pure: data in, an SVG string out. That makes the
geometry unit-testable, which a rendered chart object is not.

Palette: validated for contrast and for deuteranopia/protanopia/tritanopia
separation (worst adjacent pair dE 17.5 deutan, 18.5 normal vision). Colour
is never the only encoding -- severity also carries a shape, a label, and
its position in the ordinal ladder.
"""

import html
import math
from datetime import date, timedelta

# --- design tokens ---------------------------------------------------------
INK = "#1A1A1A"          # primary text                      17.4:1
INK_2 = "#5A5A5A"        # secondary text                     6.9:1
INK_MUTED = "#6B7280"    # axis labels                        4.8:1
GRID = "#E4E7EC"         # recessive grid and rules
SURFACE = "#FFFFFF"
NEUTRAL = "#98A2B3"      # marks with no severity meaning

SEVERITY_COLOR = {
    "HIGH": "#9F1C15",     # 7.9:1 on white
    "MEDIUM": "#C07A0A",   # a real amber; labeled, never color-alone
    "LOW": "#1F5FA8",      # informational, not a warning
    "NONE": "#106B3F",     # clear is green, and it carries the check mark
}

WIDTH = 1000          # one chart width, matched to the page column

FONT = ("ui-sans-serif, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', "
        "Arial, sans-serif")


DOLLAR = "&#36;"  # SVG lands inside Streamlit markdown, where a bare "$" starts LaTeX


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


# A generous upper bound on the average advance width of the font stack
# above, as a fraction of the font size. Labels are fitted with it and the
# suite checks the result with the same number, so a chart that passes has
# real slack rather than a lucky guess.
CHAR_W = 0.62


def _fit(text, px, size):
    """Clip a label to the gutter it has to live in, in pixels.

    A fixed character count cannot do this: at 11px, "23-02 HVAC Ductwork
    & Controls" is thirty characters and 205px wide, which walks straight
    off the left edge of a 200px gutter."""
    return _clip(text, max(3, int(px / (size * CHAR_W))))


def _clip(text, limit):
    """Trim to fit the label gutter, on a word boundary and with an ellipsis.
    A hard slice gives "Custom Millwork & Cabinetr", which reads as a typo
    rather than as an abbreviation."""
    text = str(text)
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    if space >= limit * 0.6:            # only if it does not strand the label
        cut = cut[:space]
    return cut.rstrip(" &-,") + "\u2026"


def _nice_scale(raw_peak, target=5):
    """A round step and a round top, with about five gridlines.

    Halving the leading power of ten is not enough on its own: a $613K job
    lands on a $50K step and draws fourteen gridlines, which is a ruler
    rather than a reference. This walks the 1/2/2.5/5 ladder until the
    count comes back into range."""
    if raw_peak <= 0:
        return 1.0, 1.0
    magnitude = 10 ** math.floor(math.log10(raw_peak / target))
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * magnitude
        if raw_peak / step <= target + 2:
            break
    return step, math.ceil(raw_peak / step) * step


def _axis_units(peak):
    """One unit for the whole axis, chosen from its top. "$3,500k" on a
    $3.4M build is a number the reader has to convert first, and mixing
    "$500k" with "$1.5M" down one axis is worse than either."""
    if peak >= 1_000_000:
        scale, suffix, digits = 1e6, "M", 1
    elif peak >= 10_000:
        scale, suffix, digits = 1e3, "k", 0
    else:
        scale, suffix, digits = 1, "", 0

    def tick(value):
        if value == 0:
            return f"{DOLLAR}0"
        return f"{DOLLAR}{value / scale:,.{digits}f}{suffix}"

    return tick


def _money(value) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}{DOLLAR}{abs(value):,.0f}"


def _open(width, height, title, subtitle=None):
    """Chart frame. The title is a real <title> so screen readers and
    hover both get it, and it is drawn as text so it survives as an image."""
    head = (
        # xMinYMin, not the default xMidYMid: with a fixed height and a
        # percentage width the browser centers the drawing in the leftover
        # space, so every chart floats off the text column's left edge.
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'preserveAspectRatio="xMinYMin meet" aria-label="{_esc(title)}" '
        f'style="font-family:{FONT}; max-width:100%; height:auto; display:block;">'
        f"<title>{_esc(title)}</title>"
        f'<rect width="{width}" height="{height}" fill="{SURFACE}"/>'
        f'<text x="0" y="16" font-size="13" font-weight="600" fill="{INK}">'
        f'{_esc(_fit(title, width, 13))}</text>'
    )
    if subtitle:
        head += (f'<text x="0" y="32" font-size="11" fill="{INK_2}">'
                 f'{_esc(_fit(subtitle, width, 11))}</text>')
    return head


def _empty(message, width=WIDTH):
    return (_open(width, 54, message)
            + f'<text x="0" y="40" font-size="11" fill="{INK_MUTED}">Nothing to plot.</text></svg>')


def _x_scale(lo, hi, left, right):
    span = (hi - lo) or 1.0
    return lambda v: left + (v - lo) / span * (right - left)


# ---------------------------------------------------------------------------
# 1. Cost variance by cost code, against the materiality floor
# ---------------------------------------------------------------------------
def cost_variance_chart(rows, floor, width=WIDTH, top_n=12):
    """Diverging bars: dollars over or under the spend each line's own
    reported progress supports.

    The dashed rules are the project's materiality floor. They are the
    point of the chart: everything between them is noise on this job by
    construction, and a reader can see immediately why a $6K miss is a
    flag on a cottage and nothing on a $3.4M build.

    rows: dicts with code, description, variance_amount, severity, flagged.
    """
    rows = sorted(rows, key=lambda r: -abs(r["variance_amount"]))[:top_n]
    if not rows:
        return _empty("Cost variance by cost code", width)

    row_h, top, bottom = 22, 56, 34
    height = top + row_h * len(rows) + bottom
    left, right = 230, width - 92
    # Headroom so the value label on the longest bar has somewhere to sit
    # without landing on top of the cost-code label.
    reach = max([abs(r["variance_amount"]) for r in rows] + [floor * 1.25]) * 1.28
    x = _x_scale(-reach, reach, left, right)
    zero = x(0)

    out = [_open(width, height, "Cost variance by cost code",
                 "Spend against what each line's reported progress supports")]

    # Materiality floor: recessive dashed rules, labeled once.
    for sign in (-1, 1):
        fx = x(sign * floor)
        out.append(f'<line x1="{fx:.1f}" y1="{top - 8}" x2="{fx:.1f}" y2="{height - bottom + 4}" '
                   f'stroke="{GRID}" stroke-width="1" stroke-dasharray="3 3"/>')
    # Centered on zero, because the caption describes BOTH rules: a label
    # sitting under the right-hand rule alone reads as though only overspend
    # has a floor.
    out.append(f'<text x="{zero:.1f}" y="{height - bottom + 18}" font-size="10" '
               f'fill="{INK_MUTED}" text-anchor="middle">'
               f'materiality floor &#177;{_money(floor)}</text>')

    for i, r in enumerate(rows):
        y = top + i * row_h
        v = r["variance_amount"]
        bar_x, bar_w = (zero, x(v) - zero) if v >= 0 else (x(v), zero - x(v))
        bar_w = max(bar_w, 1.5)
        color = SEVERITY_COLOR[r["severity"]] if r["flagged"] else NEUTRAL
        label = f'{r["code"]} {r["description"]}'
        tip = f'{label}: {_money(v)} {"over" if v >= 0 else "under"} pace'
        out.append(
            f'<g><title>{_esc(tip)}</title>'
            f'<text x="{left - 10}" y="{y + 12}" font-size="11" fill="{INK_2}" '
            f'text-anchor="end">{_esc(_fit(label, left - 10, 11))}</text>'
            f'<rect x="{bar_x:.1f}" y="{y + 3}" width="{bar_w:.1f}" height="12" rx="2" '
            f'fill="{color}"/>'
            f'<text x="{(x(v) + (7 if v >= 0 else -7)):.1f}" y="{y + 13}" font-size="10.5" '
            f'fill="{INK}" text-anchor="{"start" if v >= 0 else "end"}">{_money(v)}</text>'
            f"</g>"
        )

    out.append(f'<line x1="{zero:.1f}" y1="{top - 8}" x2="{zero:.1f}" y2="{height - bottom + 4}" '
               f'stroke="{INK_MUTED}" stroke-width="1"/>')
    out.append(f'<text x="{left - 10}" y="{height - bottom + 18}" font-size="10" '
               f'fill="{INK_MUTED}" text-anchor="end">under pace</text>')
    out.append(f'<text x="{right}" y="{height - bottom + 18}" font-size="10" '
               f'fill="{INK_MUTED}" text-anchor="end">over pace</text>')
    return "".join(out) + "</svg>"


# ---------------------------------------------------------------------------
# 2. Milestone slip
# ---------------------------------------------------------------------------
def milestone_slip_chart(rows, as_of, width=WIDTH):
    """Baseline against forecast or actual, one row per milestone.

    Slip is read off the LAST critical-path milestone, never summed down
    the schedule, and this is the picture of why: one delay early pushes
    everything behind it, so the bars stack up and the finish moves once.

    rows: dicts with milestone, baseline, forecast, actual, critical_path.
    """
    rows = [r for r in rows if r["baseline"] is not None]
    if not rows:
        return _empty("Milestone slip", width)

    row_h, top, bottom = 20, 56, 30
    height = top + row_h * len(rows) + bottom
    left, right = 210, width - 58

    def end_of(r):
        return r["actual"] or r["forecast"] or r["baseline"]

    lo = min([r["baseline"] for r in rows] + [as_of])
    hi = max([end_of(r) for r in rows] + [as_of])
    pad = timedelta(days=max(6, (hi - lo).days // 22))
    lo, hi = lo - pad, hi + pad
    x = _x_scale(0, (hi - lo).days, left, right)

    def px(d):
        return x((d - lo).days)

    out = [_open(width, height, "Milestone slip: baseline against forecast",
                 "Filled marker = reached. Hollow = forecast. Bold rows are the critical path.")]

    # Year gridlines, recessive.
    for year in range(lo.year, hi.year + 1):
        for marker in (date(year, 1, 1), date(year, 7, 1)):
            if lo <= marker <= hi:
                out.append(f'<line x1="{px(marker):.1f}" y1="{top - 8}" x2="{px(marker):.1f}" '
                           f'y2="{height - bottom}" stroke="{GRID}" stroke-width="1"/>')
                out.append(f'<text x="{px(marker):.1f}" y="{height - bottom + 14}" font-size="9.5" '
                           f'fill="{INK_MUTED}" text-anchor="middle">{marker:%b %y}</text>')

    for i, r in enumerate(rows):
        y = top + i * row_h + 9
        base, end = r["baseline"], end_of(r)
        slipped = (end - base).days
        critical = bool(r["critical_path"])
        weight, opacity = (3, 1.0) if critical else (1.5, 0.45)
        color = SEVERITY_COLOR["HIGH"] if slipped > 0 else SEVERITY_COLOR["NONE"]
        tip = (f'{r["milestone"]}: baseline {base:%d %b %Y}, '
               f'{"actual" if r["actual"] else "forecast"} {end:%d %b %Y}'
               + (f", {slipped} days late" if slipped > 0 else ", on baseline"))
        out.append(f'<g><title>{_esc(tip)}</title>')
        out.append(f'<text x="{left - 10}" y="{y + 4}" font-size="10.5" '
                   f'fill="{INK if critical else INK_MUTED}" '
                   f'font-weight="{"600" if critical else "400"}" '
                   f'text-anchor="end">{_esc(_fit(r["milestone"], left - 10, 11))}</text>')
        if slipped > 0:
            out.append(f'<line x1="{px(base):.1f}" y1="{y}" x2="{px(end):.1f}" y2="{y}" '
                       f'stroke="{color}" stroke-width="{weight}" stroke-linecap="round" '
                       f'opacity="{opacity}"/>')
        out.append(f'<line x1="{px(base):.1f}" y1="{y - 5}" x2="{px(base):.1f}" y2="{y + 5}" '
                   f'stroke="{INK_MUTED}" stroke-width="1.5"/>')
        if r["actual"]:
            out.append(f'<circle cx="{px(end):.1f}" cy="{y}" r="4.5" fill="{color}" '
                       f'stroke="{SURFACE}" stroke-width="2" opacity="{opacity}"/>')
        else:
            out.append(f'<circle cx="{px(end):.1f}" cy="{y}" r="4" fill="{SURFACE}" '
                       f'stroke="{color}" stroke-width="2" opacity="{opacity}"/>')
        if slipped > 0 and critical:
            out.append(f'<text x="{px(end) + 8:.1f}" y="{y + 4}" font-size="10" '
                       f'fill="{INK}">+{slipped}d</text>')
        out.append("</g>")

    out.append(f'<line x1="{px(as_of):.1f}" y1="{top - 10}" x2="{px(as_of):.1f}" '
               f'y2="{height - bottom}" stroke="{INK}" stroke-width="1" stroke-dasharray="2 3"/>')
    out.append(f'<text x="{px(as_of):.1f}" y="{top - 14}" font-size="10" fill="{INK}" '
               f'text-anchor="middle">today</text>')
    return "".join(out) + "</svg>"


# ---------------------------------------------------------------------------
# 3. Cost curve: planned, actual, and the forecast to finish
# ---------------------------------------------------------------------------
def cost_curve_chart(planned, actual, as_of, finish, forecast_at_completion, earned=None,
                     width=WIDTH, height=260, title="Cost to date against plan",
                     subtitle="Planned value, actual cost, and the forecast to completion"):
    """Planned value against actual cost, with the forecast to completion.

    Earned value is plotted as a single point at today, not as a curve.
    There is no history of per-line percent complete in this dataset --
    only today's -- so an earned-value curve would be invented. Drawing
    the one honest point and saying so is worth more than a smooth line
    nobody can source.

    planned/actual: [(date, cumulative dollars)], both sorted. title/subtitle
    are overridable because this same shape of chart draws a single
    project's curve and the whole portfolio's -- two frames with identical
    labels on one page would read as a bug, not a feature.
    """
    if not planned or not actual:
        return _empty(title, width)

    top, bottom, left, right = 56, 34, 66, width - 150
    xs = [d for d, _ in planned] + [d for d, _ in actual] + [as_of, finish]
    lo, hi = min(xs), max(xs)
    raw_peak = max([v for _, v in planned] + [v for _, v in actual] + [forecast_at_completion]) * 1.08
    step, peak = _nice_scale(raw_peak)
    x = _x_scale(0, (hi - lo).days or 1, left, right)
    y = _x_scale(0, peak, height - bottom, top)

    def px(d):
        return x((d - lo).days)

    def path(points):
        return " ".join(("M" if i == 0 else "L") + f"{px(d):.1f},{y(v):.1f}"
                        for i, (d, v) in enumerate(points))

    out = [_open(width, height, title, subtitle)]

    ticks = int(peak / step) or 1
    tick = _axis_units(peak)
    for t in range(ticks + 1):
        frac = t / ticks
        gy = y(peak * frac)
        out.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{right}" y2="{gy:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{left - 8}" y="{gy + 3.5:.1f}" font-size="9.5" fill="{INK_MUTED}" '
                   f'text-anchor="end">{tick(peak * frac)}</text>')

    out.append(f'<path d="{path(planned)}" fill="none" stroke="{NEUTRAL}" stroke-width="2"/>')
    out.append(f'<path d="{path(actual)}" fill="none" stroke="{INK}" stroke-width="2"/>')
    last_d, last_v = actual[-1]
    out.append(f'<path d="M{px(last_d):.1f},{y(last_v):.1f} '
               f'L{px(finish):.1f},{y(forecast_at_completion):.1f}" fill="none" '
               f'stroke="{SEVERITY_COLOR["HIGH"]}" stroke-width="2" stroke-dasharray="5 4"/>')
    out.append(f'<circle cx="{px(finish):.1f}" cy="{y(forecast_at_completion):.1f}" r="4" '
               f'fill="{SEVERITY_COLOR["HIGH"]}"/>')
    out.append(f'<text x="{px(finish) + 8:.1f}" y="{y(forecast_at_completion) + 4:.1f}" '
               f'font-size="10.5" fill="{INK}">forecast {_money(forecast_at_completion)}</text>')

    if earned is not None:
        out.append(f'<g><title>Earned value today: {_money(earned)}</title>'
                   f'<circle cx="{px(as_of):.1f}" cy="{y(earned):.1f}" r="5" fill="{SURFACE}" '
                   f'stroke="{SEVERITY_COLOR["LOW"]}" stroke-width="2.5"/></g>')

    out.append(f'<line x1="{px(as_of):.1f}" y1="{top - 10}" x2="{px(as_of):.1f}" '
               f'y2="{height - bottom}" stroke="{INK}" stroke-width="1" stroke-dasharray="2 3"/>')
    out.append(f'<text x="{px(as_of):.1f}" y="{top - 14}" font-size="10" fill="{INK}" '
               f'text-anchor="middle">today</text>')

    legend = [("Planned value", NEUTRAL, "line"), ("Actual cost", INK, "line"),
              ("Forecast", SEVERITY_COLOR["HIGH"], "dash")]
    if earned is not None:
        legend.append(("Earned today", SEVERITY_COLOR["LOW"], "dot"))
    lx = left
    for label, color, kind in legend:
        if kind == "dot":
            out.append(f'<circle cx="{lx + 6}" cy="{height - 10}" r="4" fill="{SURFACE}" '
                       f'stroke="{color}" stroke-width="2"/>')
        else:
            dash = ' stroke-dasharray="5 4"' if kind == "dash" else ""
            out.append(f'<line x1="{lx}" y1="{height - 10}" x2="{lx + 13}" y2="{height - 10}" '
                       f'stroke="{color}" stroke-width="2"{dash}/>')
        out.append(f'<text x="{lx + 19}" y="{height - 6}" font-size="10" '
                   f'fill="{INK_2}">{_esc(label)}</text>')
        lx += 22 + len(label) * 5.9
    return "".join(out) + "</svg>"


# ---------------------------------------------------------------------------
# 4. Change orders waiting for a signature
# ---------------------------------------------------------------------------
def co_aging_chart(rows, bands, width=WIDTH):
    """Days unsigned, longest first, with the severity bands drawn in.

    The table says the same thing; the chart shows the shape of the tail,
    which is the part that tells you whether this is one slow owner or a
    process that has stopped working.

    rows: dicts with co_id, days, amount, severity.
    """
    rows = sorted(rows, key=lambda r: -r["days"])
    if not rows:
        return _empty("Change orders waiting for signature", width)

    row_h, top, bottom = 22, 56, 30
    height = top + row_h * len(rows) + bottom
    left, right = 92, width - 132
    reach = max([r["days"] for r in rows] + [bands["HIGH"][0] * 1.2])
    x = _x_scale(0, reach, left, right)

    edges = " / ".join(f"{bands[k][0]:.0f}" for k in ("LOW", "MEDIUM", "HIGH"))
    out = [_open(width, height, "Change orders waiting for signature",
                 f"Days since submitted, work usually under way throughout. "
                 f"Dashed rules: {edges} days.")]

    # The rules are severity thresholds, not an axis, so each is named for the
    # band it opens. One change order sitting for a year compresses the three
    # thresholds into the first fifth of the axis, so a label is drawn only
    # where it clears the one before it; the subtitle carries the numbers
    # either way.
    last_right = float("-inf")
    for level, short in (("LOW", "LOW"), ("MEDIUM", "MED"), ("HIGH", "HIGH")):
        edge = bands[level][0]
        if edge > reach:
            continue
        ex = x(edge)
        out.append(f'<line x1="{ex:.1f}" y1="{top - 8}" x2="{ex:.1f}" '
                   f'y2="{height - bottom}" stroke="{GRID}" stroke-width="1" '
                   f'stroke-dasharray="3 3"/>')
        label = f"{short} {edge:.0f}d"
        half = len(label) * 9.5 * CHAR_W / 2
        if ex - half < last_right + 4:
            continue
        last_right = ex + half
        out.append(f'<text x="{ex:.1f}" y="{height - bottom + 14}" font-size="9.5" '
                   f'fill="{INK_MUTED}" text-anchor="middle">{label}</text>')

    for i, r in enumerate(rows):
        y = top + i * row_h
        color = SEVERITY_COLOR[r["severity"]]
        tip = f'{r["co_id"]}: {_money(r["amount"])}, unsigned {r["days"]} days'
        out.append(
            f'<g><title>{_esc(tip)}</title>'
            f'<text x="{left - 10}" y="{y + 12}" font-size="11" fill="{INK_2}" '
            f'text-anchor="end">{_esc(r["co_id"])}</text>'
            f'<rect x="{left}" y="{y + 3}" width="{max(x(r["days"]) - left, 2):.1f}" height="12" '
            f'rx="2" fill="{color}"/>'
            f'<text x="{x(r["days"]) + 7:.1f}" y="{y + 13}" font-size="10.5" fill="{INK}">'
            f'{r["days"]}d &#183; {_money(r["amount"])}</text>'
            f"</g>"
        )
    return "".join(out) + "</svg>"


# ---------------------------------------------------------------------------
# 5. Portfolio signal matrix
# ---------------------------------------------------------------------------
def signal_matrix(projects, signals, severity_of, width=WIDTH):
    """Seven signals down, the jobs across, worst job on the left.

    This replaces one small table per job. Position carries the signal,
    so the eye compares along a row instead of re-finding a label on five
    separate cards, and severity is a fill ladder -- solid, tint, outline,
    plain -- which still reads in grayscale and under color blindness.
    """
    if not projects:
        return _empty("Portfolio signals", width)

    label_w, col_w, row_h, top = 150, min(160, (width - 150) // len(projects)), 30, 74
    height = top + row_h * len(signals) + 16
    out = [_open(width, height, "Portfolio signals", "Worst job first. Rows are the seven checks.")]

    for j, p in enumerate(projects):
        cx = label_w + j * col_w + col_w / 2
        name = p.get("short") or p["name"].split()[0]
        out.append(f'<text x="{cx:.1f}" y="{top - 30}" font-size="11" font-weight="600" '
                   f'fill="{INK}" text-anchor="middle">{_esc(name)}</text>')
        out.append(f'<text x="{cx:.1f}" y="{top - 17}" font-size="9.5" fill="{INK_MUTED}" '
                   f'text-anchor="middle">{p["pct_complete"]}% &#183; '
                   f'{DOLLAR}{p["contract_value"] / 1e6:.1f}M</text>')

    marks = {"HIGH": "▲", "MEDIUM": "◆", "LOW": "●", "NONE": "✓"}
    for i, (label, key) in enumerate(signals):
        y = top + i * row_h
        if i % 2 == 0:
            out.append(f'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="#FAFBFC"/>')
        out.append(f'<text x="{label_w - 12}" y="{y + 19}" font-size="11" fill="{INK_2}" '
                   f'text-anchor="end">{_esc(label)}</text>')
        for j, p in enumerate(projects):
            sev = severity_of(p, key)
            cx = label_w + j * col_w + col_w / 2
            color = SEVERITY_COLOR[sev]
            tip = f'{p["name"]} - {label}: {"CLEAR" if sev == "NONE" else sev}'
            out.append(f'<g><title>{_esc(tip)}</title>')
            if sev == "HIGH":       # solid chip
                out.append(f'<rect x="{cx - 30:.1f}" y="{y + 5}" width="60" height="19" rx="4" '
                           f'fill="{color}"/>'
                           f'<text x="{cx:.1f}" y="{y + 18}" font-size="10" font-weight="700" '
                           f'fill="{SURFACE}" text-anchor="middle">{marks[sev]} HIGH</text>')
            elif sev == "MEDIUM":   # tinted chip
                out.append(f'<rect x="{cx - 30:.1f}" y="{y + 5}" width="60" height="19" rx="4" '
                           f'fill="#FDF0DC" stroke="#E8C48A"/>'
                           f'<text x="{cx:.1f}" y="{y + 18}" font-size="10" font-weight="600" '
                           f'fill="#7A4405" text-anchor="middle">{marks[sev]} MED</text>')
            elif sev == "LOW":      # outline only
                out.append(f'<rect x="{cx - 30:.1f}" y="{y + 5}" width="60" height="19" rx="4" '
                           f'fill="none" stroke="#BCD0E8"/>'
                           f'<text x="{cx:.1f}" y="{y + 18}" font-size="10" fill="{color}" '
                           f'text-anchor="middle">{marks[sev]} LOW</text>')
            else:                   # no chip at all
                out.append(f'<text x="{cx:.1f}" y="{y + 18}" font-size="10" fill="{color}" '
                           f'text-anchor="middle">{marks[sev]}</text>')
            out.append("</g>")
    return "".join(out) + "</svg>"
