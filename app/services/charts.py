"""Server-side SVG-grafieken voor de analyses (licht thema, huisstijl).

Kleuren komen uit het gevalideerde categorische referentiepalet van de
dataviz-richtlijnen (vaste volgorde, kleurenblind-veilig gecontroleerd):
blauw #2a78d6, oranje #eb6834, aqua #1baf7a. Tekst draagt inktkleuren,
nooit de serieskleur. Eén y-as per grafiek; raster onopvallend.
"""

import math
import re

SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e7e5e1"

W, H = 640, 300
ML, MR, MT, MB = 64, 14, 52, 30   # marges; MT bevat titel + legende


def _fmt_compact(v: float) -> str:
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}".replace(".", ",") + "M"
    if a >= 1_000:
        return f"{v / 1_000:.0f}k"
    return f"{v:.0f}"


def _fmt_full(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _ticks(lo: float, hi: float, target: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 0.001:
        if t >= lo - step * 0.001:
            ticks.append(round(t, 6))
        t += step
    return ticks


def _frame(title: str, series_names: list[str]) -> list[str]:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'role="img" style="max-width:100%;height:auto;background:#fff;'
        f'font-family:system-ui,sans-serif">',
        f'<text x="{ML}" y="18" font-size="13" font-weight="600" '
        f'fill="{INK}">{title}</text>',
    ]
    if len(series_names) >= 2:
        x = ML
        for i, name in enumerate(series_names):
            parts.append(f'<circle cx="{x + 5}" cy="34" r="5" '
                         f'fill="{SERIES_COLORS[i % len(SERIES_COLORS)]}"/>')
            parts.append(f'<text x="{x + 14}" y="38" font-size="11" '
                         f'fill="{MUTED}">{name}</text>')
            x += 14 + 7 * len(name) + 22
    return parts


def _scale(lo: float, hi: float):
    plot_h = H - MT - MB

    def y(v: float) -> float:
        return MT + plot_h * (1 - (v - lo) / (hi - lo))

    return y


def _grid_and_axis(parts: list[str], ticks: list[float], y) -> None:
    for t in ticks:
        yy = y(t)
        parts.append(f'<line x1="{ML}" y1="{yy:.1f}" x2="{W - MR}" y2="{yy:.1f}" '
                     f'stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{ML - 8}" y="{yy + 4:.1f}" font-size="11" '
                     f'fill="{MUTED}" text-anchor="end">{_fmt_compact(t)}</text>')


def _rounded_bar(x: float, w: float, y_val: float, y_base: float, color: str) -> str:
    """Balk met 4px afgeronde hoeken aan het data-uiteinde, vlak op de basis."""
    top, bottom = min(y_val, y_base), max(y_val, y_base)
    h = bottom - top
    r = min(4.0, w / 2, h)
    if h <= 0.5:
        return ""
    if y_val <= y_base:  # positieve waarde: ronding boven
        return (f'M{x:.1f},{bottom:.1f} L{x:.1f},{top + r:.1f} '
                f'Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} '
                f'L{x + w - r:.1f},{top:.1f} Q{x + w:.1f},{top:.1f} '
                f'{x + w:.1f},{top + r:.1f} L{x + w:.1f},{bottom:.1f} Z')
    return (f'M{x:.1f},{top:.1f} L{x:.1f},{bottom - r:.1f} '
            f'Q{x:.1f},{bottom:.1f} {x + r:.1f},{bottom:.1f} '
            f'L{x + w - r:.1f},{bottom:.1f} Q{x + w:.1f},{bottom:.1f} '
            f'{x + w:.1f},{bottom - r:.1f} L{x + w:.1f},{top:.1f} Z')


def grouped_bar_chart(labels: list[str], series: list[tuple[str, list]],
                      title: str) -> str:
    """Gegroepeerde staafgrafiek (max 3 reeksen); None-waarden worden
    overgeslagen. Retourneert één regel SVG."""
    values = [v for _, vals in series for v in vals if v is not None]
    if not values or not labels:
        return ""
    lo = min(0.0, min(values))
    hi = max(0.0, max(values))
    ticks = _ticks(lo, hi)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1] * 1.02 if ticks[-1] > 0 else hi)
    y = _scale(lo, hi)
    parts = _frame(title, [name for name, _ in series])
    _grid_and_axis(parts, ticks, y)
    y0 = y(0)

    plot_w = W - ML - MR
    group_w = plot_w / len(labels)
    bar_gap = 2.0
    bar_w = min(34.0, (group_w * 0.7 - bar_gap * (len(series) - 1)) / len(series))
    for gi, label in enumerate(labels):
        total_w = bar_w * len(series) + bar_gap * (len(series) - 1)
        x0 = ML + group_w * gi + (group_w - total_w) / 2
        for si, (name, vals) in enumerate(series):
            v = vals[gi] if gi < len(vals) else None
            if v is None:
                continue
            x = x0 + si * (bar_w + bar_gap)
            path = _rounded_bar(x, bar_w, y(v), y0, SERIES_COLORS[si])
            if path:
                parts.append(
                    f'<path d="{path}" fill="{SERIES_COLORS[si % len(SERIES_COLORS)]}">'
                    f'<title>{label} · {name}: {_fmt_full(v)}</title></path>'
                )
        parts.append(f'<text x="{ML + group_w * gi + group_w / 2:.1f}" '
                     f'y="{H - 8}" font-size="11" fill="{MUTED}" '
                     f'text-anchor="middle">{label}</text>')
    if lo < 0:
        parts.append(f'<line x1="{ML}" y1="{y0:.1f}" x2="{W - MR}" y2="{y0:.1f}" '
                     f'stroke="{MUTED}" stroke-width="1"/>')
    parts.append("</svg>")
    return "".join(parts)


def line_chart(labels: list[str], series: list[tuple[str, list]],
               title: str) -> str:
    """Lijngrafiek (2px lijnen, 8px markers); kan negatieve waarden aan
    (nul-lijn wordt getoond). Retourneert één regel SVG."""
    values = [v for _, vals in series for v in vals if v is not None]
    if not values or not labels:
        return ""
    lo, hi = min(values), max(values)
    if lo > 0:
        lo = 0.0
    if hi < 0:
        hi = 0.0
    pad = (hi - lo) * 0.06 or 1
    ticks = _ticks(lo, hi + pad)
    lo, hi = min(lo, ticks[0]), max(hi + pad, ticks[-1])
    y = _scale(lo, hi)
    parts = _frame(title, [name for name, _ in series])
    _grid_and_axis(parts, ticks, y)
    if lo < 0 < hi:
        y0 = y(0)
        parts.append(f'<line x1="{ML}" y1="{y0:.1f}" x2="{W - MR}" y2="{y0:.1f}" '
                     f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="4 3"/>')

    plot_w = W - ML - MR
    step_x = plot_w / max(len(labels) - 1, 1)

    def x(i: int) -> float:
        return ML + step_x * i if len(labels) > 1 else ML + plot_w / 2

    for si, (name, vals) in enumerate(series):
        color = SERIES_COLORS[si % len(SERIES_COLORS)]
        points = [(x(i), y(v)) for i, v in enumerate(vals) if v is not None]
        if len(points) >= 2:
            d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in points)
            parts.append(f'<path d="{d}" fill="none" stroke="{color}" '
                         f'stroke-width="2"/>')
        for i, v in enumerate(vals):
            if v is None:
                continue
            parts.append(
                f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" fill="{color}" '
                f'stroke="#fff" stroke-width="2">'
                f'<title>{labels[i]} · {name}: {_fmt_full(v)}</title></circle>'
            )
    for i, label in enumerate(labels):
        parts.append(f'<text x="{x(i):.1f}" y="{H - 8}" font-size="11" '
                     f'fill="{MUTED}" text-anchor="middle">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ── chartdata-blokken uit de AI-interpretatie omzetten naar grafieken ────────

CHARTDATA_RE = re.compile(r"```chartdata\s*\n(.*?)```", re.S)


def _parse_number(cell: str):
    cell = cell.strip().replace(".", "").replace(",", ".")
    if not cell or cell in ("—", "-", "None"):
        return None
    try:
        return float(cell)
    except ValueError:
        return None


def expand_chartdata(text: str) -> str:
    """Vervang ```chartdata```-blokken (jaar;kolom;kolom;… als CSV) door
    SVG-grafieken: eerste twee kolommen als staven, volgende twee als lijnen.
    Onleesbare blokken verdwijnen stil — nooit een kapot blok in het rapport."""

    def replace(match: re.Match) -> str:
        try:
            lines = [ln.strip() for ln in match.group(1).strip().splitlines()
                     if ln.strip()]
            header = [h.strip() for h in lines[0].split(";")]
            if len(header) < 2 or len(lines) < 3:
                return ""
            labels: list[str] = []
            columns: list[list] = [[] for _ in header[1:]]
            for line in lines[1:]:
                cells = [c.strip() for c in line.split(";")]
                labels.append(cells[0])
                for i in range(len(columns)):
                    columns[i].append(
                        _parse_number(cells[i + 1]) if i + 1 < len(cells) else None
                    )
            names = [h.replace("_", " ").capitalize() for h in header[1:]]
            out: list[str] = []
            bar_series = [(names[i], columns[i]) for i in range(min(2, len(columns)))
                          if any(v is not None for v in columns[i])]
            if bar_series:
                out.append(grouped_bar_chart(
                    labels, bar_series, " en ".join(n for n, _ in bar_series)))
            line_series = [(names[i], columns[i])
                           for i in range(2, min(4, len(columns)))
                           if any(v is not None for v in columns[i])]
            if line_series:
                out.append(line_chart(
                    labels, line_series, " en ".join(n for n, _ in line_series)))
            if not out:
                return ""
            return "\n\n## Grafieken\n\n" + "\n\n".join(out) + "\n"
        except Exception:
            return ""

    return CHARTDATA_RE.sub(replace, text)
