"""Self-contained HTML report of the paper-trading track record.

One file, no network, no build step, no charting library. It opens from a phone
over Tailscale, from a laptop, or by copying it off the server -- and it still
opens in five years, which a page depending on a CDN does not.

**What it is honest about, by construction:**

* The header states the run mode and that fills are simulated. A dashboard that
  looks like a brokerage statement invites being read as one.
* A Sharpe ratio is shown only past `MIN_SESSIONS_FOR_SHARPE` sessions, and
  always beside the track-record length needed to distinguish it from zero.
* Per-strategy results are grouped by currency and never summed across them.
* The strategy's own description is printed. When the strategy is a plumbing
  vehicle with no expected edge, the report says so on its face, next to the
  return -- the moment a number like +23% appears without that sentence beside
  it is the moment it starts being believed.

Charts are inline SVG generated here. The palette is validated for
colour-vision deficiency in both light and dark mode (worst adjacent pair
deltaE 9.2 light / 9.4 dark against a >=8 target), and every series is direct-labelled
as well as coloured, so identity never rests on hue alone.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tradelab.reporting.metrics import (
    MIN_SESSIONS_FOR_SHARPE,
    EquityPoint,
    PerformanceSummary,
    StrategyResult,
    load_equity_curve,
    max_drawdown,
    strategy_results,
    summarise,
)

_CHART_W = 960
_CHART_H = 260
_PAD_L = 62
_PAD_R = 18
_PAD_T = 16
_PAD_B = 28


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    """Round tick values, so the axis reads in human numbers."""
    if high <= low:
        return [low]
    import math

    span = high - low
    raw = span / max(count - 1, 1)
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for multiple in (1, 2, 2.5, 5, 10):
        step = magnitude * multiple
        if step >= raw:
            break
    start = math.floor(low / step) * step
    ticks = []
    value = start
    while value <= high + step * 0.5:
        if value >= low - step * 0.5:
            ticks.append(value)
        value += step
    return ticks


class _Scale:
    def __init__(self, n: int, low: float, high: float) -> None:
        self.n = max(n - 1, 1)
        self.low = low
        self.high = high if high > low else low + 1.0

    def x(self, index: int) -> float:
        inner = _CHART_W - _PAD_L - _PAD_R
        return _PAD_L + inner * index / self.n

    def y(self, value: float) -> float:
        inner = _CHART_H - _PAD_T - _PAD_B
        fraction = (value - self.low) / (self.high - self.low)
        return _PAD_T + inner * (1.0 - fraction)


def _axis(scale: _Scale, fmt: str = "{:,.0f}") -> str:
    parts = []
    for tick in _nice_ticks(scale.low, scale.high):
        y = scale.y(tick)
        if not (_PAD_T - 1 <= y <= _CHART_H - _PAD_B + 1):
            continue
        parts.append(
            f'<line class="grid" x1="{_PAD_L}" y1="{y:.1f}" '
            f'x2="{_CHART_W - _PAD_R}" y2="{y:.1f}"/>'
            f'<text class="tick" x="{_PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{_esc(fmt.format(tick))}</text>"
        )
    return "".join(parts)


def _path(scale: _Scale, values: list[float]) -> str:
    return "M" + " L".join(f"{scale.x(i):.1f},{scale.y(v):.1f}" for i, v in enumerate(values))


def _area(scale: _Scale, values: list[float], baseline: float) -> str:
    if not values:
        return ""
    base_y = scale.y(baseline)
    points = " L".join(f"{scale.x(i):.1f},{scale.y(v):.1f}" for i, v in enumerate(values))
    return (
        f"M{scale.x(0):.1f},{base_y:.1f} L{points} L{scale.x(len(values) - 1):.1f},{base_y:.1f} Z"
    )


def _date_labels(curve: list[EquityPoint], scale: _Scale, count: int = 6) -> str:
    if not curve:
        return ""
    step = max(len(curve) // max(count - 1, 1), 1)
    indices = list(range(0, len(curve), step))
    last = len(curve) - 1
    if indices[-1] != last:
        # Appending the final index can place it a few pixels from the previous
        # tick, which renders the two labels on top of each other. Drop the
        # neighbour rather than let them overlap.
        if len(indices) > 1 and last - indices[-1] < step * 0.6:
            indices.pop()
        indices.append(last)
    y = _CHART_H - _PAD_B + 18
    parts = []
    for position, i in enumerate(indices):
        # The end labels are centred on the plot edge, so half of each would
        # hang outside the viewBox and collide with its neighbour. Anchor them
        # inward instead.
        anchor = "start" if position == 0 else "end" if position == len(indices) - 1 else "middle"
        parts.append(
            f'<text class="tick" x="{scale.x(i):.1f}" y="{y}" text-anchor="{anchor}">'
            f"{curve[i].timestamp:%b %y}</text>"
        )
    return "".join(parts)


def _equity_chart(curve: list[EquityPoint], currency: str) -> str:
    equity = [p.equity for p in curve]
    exposure = [p.gross_exposure for p in curve]
    low = min(min(equity), min(exposure))
    high = max(max(equity), max(exposure))
    margin = (high - low) * 0.08 or 1.0
    scale = _Scale(len(curve), low - margin, high + margin)

    last_e, last_x = equity[-1], scale.x(len(equity) - 1)
    return f"""
<figure class="chart">
  <figcaption>
    <h3>Equity and gross exposure</h3>
    <p>Account value against the capital actually deployed, in {_esc(currency)}.
       The gap between them is cash sitting idle.</p>
  </figcaption>
  <div class="legend">
    <span><i style="background:var(--series-1)"></i>Equity</span>
    <span><i style="background:var(--series-2)"></i>Gross exposure</span>
  </div>
  <div class="plot-scroll">
  <svg viewBox="0 0 {_CHART_W} {_CHART_H}" role="img"
       aria-label="Equity and gross exposure over {len(curve)} sessions"
       class="plot" data-chart="equity">
    {_axis(scale)}
    <path class="series-2" d="{_path(scale, exposure)}"/>
    <path class="series-1" d="{_path(scale, equity)}"/>
    <circle class="end-dot series-1-fill" cx="{last_x:.1f}"
            cy="{scale.y(last_e):.1f}" r="4"/>
    {_date_labels(curve, scale)}
    <g class="crosshair" hidden>
      <line y1="{_PAD_T}" y2="{_CHART_H - _PAD_B}"/>
    </g>
  </svg>
  </div>
  <div class="tip" hidden></div>
</figure>"""


def _drawdown_chart(curve: list[EquityPoint], summary: PerformanceSummary) -> str:
    points = max_drawdown(curve)
    values = [p.drawdown * 100 for p in points]
    low = min(min(values), -1.0)
    scale = _Scale(len(values), low * 1.15, 0.0)
    worst_index = values.index(min(values))
    return f"""
<figure class="chart">
  <figcaption>
    <h3>Drawdown from peak</h3>
    <p>How far below its best the account has been. Measured on equity, not on
       closed trades: an open position that has halved has already cost you the
       money whether or not it has been sold.</p>
  </figcaption>
  <div class="plot-scroll">
  <svg viewBox="0 0 {_CHART_W} {_CHART_H}" role="img"
       aria-label="Drawdown, worst {summary.max_drawdown:.1%}"
       class="plot" data-chart="drawdown">
    {_axis(scale, "{:,.1f}%")}
    <path class="dd-fill" d="{_area(scale, values, 0.0)}"/>
    <path class="dd-line" d="{_path(scale, values)}"/>
    <circle class="dd-worst" cx="{scale.x(worst_index):.1f}"
            cy="{scale.y(values[worst_index]):.1f}" r="4"/>
    <text class="dd-label" x="{scale.x(worst_index) + 10:.1f}"
          y="{scale.y(values[worst_index]) + 4:.1f}">
      worst {summary.max_drawdown:.1%}
    </text>
    {_date_labels(curve, scale)}
    <g class="crosshair" hidden>
      <line y1="{_PAD_T}" y2="{_CHART_H - _PAD_B}"/>
    </g>
  </svg>
  </div>
  <div class="tip" hidden></div>
</figure>"""


def _strategy_table(results: list[StrategyResult]) -> str:
    if not results:
        return '<p class="empty">No fills recorded yet.</p>'
    rows = "".join(
        f"""<tr>
      <td class="name">{_esc(r.strategy_id)}</td>
      <td>{_esc(r.currency)}</td>
      <td class="num {"pos" if r.realised >= 0 else "neg"}">{r.realised:+,.2f}</td>
      <td class="num">{r.commission:,.2f}</td>
      <td class="num {"pos" if r.net >= 0 else "neg"}">{r.net:+,.2f}</td>
      <td class="num">{r.fills:,}</td>
      <td class="num">{r.symbols}</td>
      <td class="num">{r.cost_bps:.1f}</td>
    </tr>"""
        for r in results
    )
    return f"""
<div class="scroll"><table class="grid-table">
  <thead><tr>
    <th>Strategy</th><th>Ccy</th><th class="num">Realised</th>
    <th class="num">Commission</th><th class="num">Net</th>
    <th class="num">Fills</th><th class="num">Names</th><th class="num">Cost bps</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table></div>
<p class="table-note">Realised P&amp;L per strategy, reconstructed by replaying every
fill on an average-cost basis. Grouped by currency and never summed across them:
fills carry no FX rate, so a combined total would be computed at an exchange rate
nobody chose. Unrealised P&amp;L on open positions is not included here.</p>"""


def _events_table(path: Path, limit: int = 12) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    with connection:
        rows = connection.execute(
            "SELECT timestamp, kind, severity, message FROM events "
            "WHERE kind IN ('risk_reject','halt','reconcile','fx_convert') "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return '<p class="empty">No rejections, halts or conversions recorded.</p>'
    body = "".join(
        f'<tr><td class="mono">{_esc(row["timestamp"][:19])}</td>'
        f'<td><span class="chip chip-{_esc(row["kind"])}">{_esc(row["kind"])}</span></td>'
        f"<td>{_esc(row['message'][:160])}</td></tr>"
        for row in rows
    )
    return f"""
<div class="scroll"><table class="grid-table wide">
  <thead><tr><th>When</th><th>Kind</th><th>Detail</th></tr></thead>
  <tbody>{body}</tbody>
</table></div>
<p class="table-note">Risk rejections, halts, reconciliation breaks and currency
conversions. An empty list here is the goal: it means every order the strategy
asked for was one the risk gate agreed to.</p>"""


def _tile(label: str, value: str, note: str = "", tone: str = "") -> str:
    return f"""<div class="tile{" " + tone if tone else ""}">
  <span class="tile-label">{_esc(label)}</span>
  <span class="tile-value">{_esc(value)}</span>
  {f'<span class="tile-note">{_esc(note)}</span>' if note else ""}
</div>"""


def build_report(
    state_path: Path | str,
    *,
    base_currency: str = "EUR",
    strategy_note: str = "",
    mode: str = "PAPER",
) -> str:
    """Render the whole report as one HTML string."""
    state_path = Path(state_path)
    curve = load_equity_curve(state_path)
    summary = summarise(state_path)
    results = strategy_results(state_path)

    if summary.sharpe is None:
        sharpe_value = "--"
        sharpe_note = f"needs {MIN_SESSIONS_FOR_SHARPE}+ sessions; have {summary.sessions}"
    else:
        sharpe_value = f"{summary.sharpe:.2f}"
        sharpe_note = "~2.7 years needed to tell 1.0 from zero"

    period = (
        f"{summary.start:%d %b %Y} to {summary.end:%d %b %Y}"
        if summary.start and summary.end
        else "no sessions recorded"
    )

    tiles = "".join(
        [
            _tile(
                "Equity",
                f"{summary.equity:,.2f}",
                f"{base_currency}, from {summary.starting_equity:,.0f}",
            ),
            _tile(
                "Total return",
                f"{summary.total_return:+.2%}",
                "not an edge -- see below",
                "pos" if summary.total_return >= 0 else "neg",
            ),
            _tile(
                "Max drawdown",
                f"{summary.max_drawdown:.2%}",
                f"{summary.max_drawdown_date:%b %Y}" if summary.max_drawdown_date else "",
                "neg",
            ),
            _tile("Sharpe (ann.)", sharpe_value, sharpe_note),
            _tile(
                "Deployed",
                f"{summary.leverage:.2f}x",
                f"{summary.open_positions} positions",
            ),
            _tile(
                "Sessions",
                f"{summary.sessions:,}",
                f"{summary.fills:,} fills, {summary.rejections:,} rejected",
            ),
        ]
    )

    charts = ""
    if curve:
        charts = _equity_chart(curve, base_currency) + _drawdown_chart(curve, summary)

    equity_json = ",".join(f"{p.equity:.2f}" for p in curve)
    exposure_json = ",".join(f"{p.gross_exposure:.2f}" for p in curve)
    dd_json = ",".join(f"{p.drawdown * 100:.3f}" for p in max_drawdown(curve))
    dates_json = ",".join(f'"{p.timestamp:%Y-%m-%d}"' for p in curve)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper Trading Record</title>
<style>
:root {{
  color-scheme: light;
  --surface-0: #f4f4f1;
  --surface-1: #fcfcfb;
  --border:    #e2e1dc;
  --text-primary:   #0b0b0b;
  --text-secondary: #52514e;
  --text-muted:     #7a7873;
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --critical: #d03b3b;
  --good:     #0ca30c;
  --grid:     #e8e7e2;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --surface-0: #111110;
    --surface-1: #1a1a19;
    --border:    #34342f;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #93918a;
    --series-1: #3987e5;
    --series-2: #d95926;
    --critical: #e66767;
    --good:     #2fb82f;
    --grid:     #2b2b28;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --surface-0: #111110; --surface-1: #1a1a19; --border: #34342f;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #93918a;
  --series-1: #3987e5; --series-2: #d95926; --critical: #e66767;
  --good: #2fb82f; --grid: #2b2b28;
}}
* {{ box-sizing: border-box; }}
/* The `hidden` attribute has no effect on SVG elements -- without this the
   crosshair renders at x=0 as a stray dashed line down the left of every
   chart. Browsers only honour `hidden` on HTML, via the UA stylesheet. */
[hidden] {{ display: none !important; }}
body {{
  margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  -webkit-text-size-adjust: 100%;
}}
.wrap {{ max-width: 1040px; margin: 0 auto; padding: 24px 16px 64px; }}
header h1 {{ font-size: 1.5rem; margin: 0 0 4px; letter-spacing: -0.01em; }}
.sub {{ color: var(--text-secondary); margin: 0 0 16px; font-size: 0.92rem; }}
.banner {{
  background: var(--surface-1); border: 1px solid var(--border);
  border-left: 3px solid var(--series-2);
  border-radius: 8px; padding: 12px 14px; margin: 0 0 24px;
  color: var(--text-secondary); font-size: 0.9rem;
}}
.banner strong {{ color: var(--text-primary); }}
.tiles {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px; margin-bottom: 28px;
}}
.tile {{
  background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 2px;
}}
.tile-label {{ font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.04em; }}
.tile-value {{ font-size: 1.32rem; font-weight: 600; font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em; }}
.tile-note {{ font-size: 0.74rem; color: var(--text-muted); }}
.tile.pos .tile-value {{ color: var(--good); }}
.tile.neg .tile-value {{ color: var(--critical); }}
.chart {{
  background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 16px; margin: 0 0 20px; position: relative;
}}
figcaption h3 {{ margin: 0 0 3px; font-size: 1rem; }}
figcaption p {{ margin: 0 0 10px; color: var(--text-secondary); font-size: 0.85rem;
  max-width: 68ch; }}
.legend {{ display: flex; gap: 16px; margin-bottom: 6px; font-size: 0.82rem;
  color: var(--text-secondary); }}
.legend i {{ display: inline-block; width: 12px; height: 3px; border-radius: 2px;
  margin-right: 6px; vertical-align: middle; }}
.plot-scroll {{ overflow-x: auto; overflow-y: hidden; margin: 0 -4px; padding: 0 4px; }}
/* A phone renders the 960-unit viewBox at ~380px, which shrinks an 11px tick
   to about 4px. Holding a minimum width and letting the chart scroll keeps the
   axis readable instead of decorative. */
.plot {{ width: 100%; min-width: 660px; height: auto; display: block; overflow: visible; }}
.grid {{ stroke: var(--grid); stroke-width: 1; }}
.tick {{ fill: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
path.series-1 {{ fill: none; stroke: var(--series-1); stroke-width: 2;
  stroke-linejoin: round; stroke-linecap: round; }}
path.series-2 {{ fill: none; stroke: var(--series-2); stroke-width: 2;
  stroke-linejoin: round; stroke-linecap: round; }}
.end-dot {{ stroke: var(--surface-1); stroke-width: 2; }}
.series-1-fill {{ fill: var(--series-1); }}
.dd-fill {{ fill: var(--critical); opacity: 0.16; }}
.dd-line {{ fill: none; stroke: var(--critical); stroke-width: 2; stroke-linejoin: round; }}
.dd-worst {{ fill: var(--critical); stroke: var(--surface-1); stroke-width: 2; }}
.dd-label {{ fill: var(--text-secondary); font-size: 11px; }}
.crosshair line {{ stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 3 3; }}
.tip {{
  position: absolute; pointer-events: none; z-index: 5;
  background: var(--surface-0); border: 1px solid var(--border); border-radius: 7px;
  padding: 7px 10px; font-size: 0.8rem; line-height: 1.45; white-space: nowrap;
  box-shadow: 0 4px 14px rgb(0 0 0 / 0.14); font-variant-numeric: tabular-nums;
}}
.tip b {{ display: block; color: var(--text-muted); font-weight: 500; margin-bottom: 2px; }}
h2 {{ font-size: 1.05rem; margin: 30px 0 8px; }}
.grid-table {{
  width: 100%; border-collapse: collapse; background: var(--surface-1);
  border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
  font-size: 0.87rem;
}}
.grid-table caption {{ caption-side: bottom; text-align: left; padding: 10px 2px 0;
  color: var(--text-muted); font-size: 0.78rem; max-width: 76ch; }}
.grid-table th, .grid-table td {{ padding: 8px 10px; text-align: left;
  border-bottom: 1px solid var(--border); }}
.grid-table thead th {{ color: var(--text-muted); font-weight: 600; font-size: 0.74rem;
  text-transform: uppercase; letter-spacing: 0.04em; }}
.grid-table tbody tr:last-child td {{ border-bottom: none; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.name {{ font-weight: 600; }}
.pos {{ color: var(--good); }}
.neg {{ color: var(--critical); }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem;
  color: var(--text-secondary); }}
.chip {{ display: inline-block; padding: 1px 7px; border-radius: 99px; font-size: 0.72rem;
  background: var(--surface-0); border: 1px solid var(--border); color: var(--text-secondary); }}
.empty {{ color: var(--text-muted); font-size: 0.9rem; }}
.table-note {{ color: var(--text-muted); font-size: 0.78rem; max-width: 76ch;
  margin: 10px 2px 0; }}
.scroll {{ overflow-x: auto; }}
footer {{ margin-top: 36px; padding-top: 14px; border-top: 1px solid var(--border);
  color: var(--text-muted); font-size: 0.78rem; }}
@media (max-width: 640px) {{
  .tile-value {{ font-size: 1.15rem; }}
  figcaption p {{ font-size: 0.8rem; }}
}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Paper trading record</h1>
  <p class="sub">{_esc(period)} &middot; {summary.sessions:,} sessions &middot;
     base currency {_esc(base_currency)}</p>
</header>

<div class="banner">
  <strong>{_esc(mode)} &mdash; fills are simulated, not executed.</strong>
  {_esc(strategy_note)}
</div>

<div class="tiles">{tiles}</div>

{charts}

<h2>Per-strategy results</h2>
{_strategy_table(results)}

<h2>Risk events</h2>
{_events_table(state_path)}

<footer>
  Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from
  <span class="mono">{_esc(state_path)}</span>.
  Every figure is recomputed from the persisted order and fill history; nothing
  here is stored or editable, so a number cannot be revised after the fact.
</footer>
</div>
<script>
const DATA = {{
  dates: [{dates_json}],
  equity: [{equity_json}],
  exposure: [{exposure_json}],
  drawdown: [{dd_json}]
}};
const PAD_L = {_PAD_L}, PAD_R = {_PAD_R}, W = {_CHART_W};
const money = new Intl.NumberFormat(undefined, {{minimumFractionDigits: 2,
  maximumFractionDigits: 2}});

document.querySelectorAll('.chart').forEach(fig => {{
  const svg = fig.querySelector('.plot');
  const tip = fig.querySelector('.tip');
  const cross = fig.querySelector('.crosshair');
  if (!svg || !DATA.dates.length) return;
  const kind = svg.dataset.chart;

  function indexFor(clientX) {{
    const box = svg.getBoundingClientRect();
    const xUser = (clientX - box.left) / box.width * W;
    const frac = (xUser - PAD_L) / (W - PAD_L - PAD_R);
    return Math.max(0, Math.min(DATA.dates.length - 1,
      Math.round(frac * (DATA.dates.length - 1))));
  }}

  function show(clientX) {{
    const i = indexFor(clientX);
    const box = svg.getBoundingClientRect();
    const figBox = fig.getBoundingClientRect();
    const xUser = PAD_L + (W - PAD_L - PAD_R) * i / (DATA.dates.length - 1);
    const xPx = xUser / W * box.width;

    cross.hidden = false;
    cross.querySelector('line').setAttribute('x1', xUser);
    cross.querySelector('line').setAttribute('x2', xUser);

    tip.hidden = false;
    tip.innerHTML = kind === 'drawdown'
      ? `<b>${{DATA.dates[i]}}</b>Drawdown ${{DATA.drawdown[i].toFixed(2)}}%`
      : `<b>${{DATA.dates[i]}}</b>Equity ${{money.format(DATA.equity[i])}}<br>` +
        `Exposure ${{money.format(DATA.exposure[i])}}`;

    const left = box.left - figBox.left + xPx;
    const w = tip.offsetWidth;
    tip.style.left = Math.max(4, Math.min(figBox.width - w - 4, left - w / 2)) + 'px';
    tip.style.top = (box.top - figBox.top + 8) + 'px';
  }}

  function hide() {{ tip.hidden = true; cross.hidden = true; }}

  svg.addEventListener('pointermove', e => show(e.clientX));
  svg.addEventListener('pointerdown', e => show(e.clientX));
  svg.addEventListener('pointerleave', hide);
  svg.addEventListener('pointercancel', hide);
}});
</script>
</body>
</html>"""


def write_report(
    state_path: Path | str,
    out_path: Path | str,
    *,
    base_currency: str = "EUR",
    strategy_note: str = "",
    mode: str = "PAPER",
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        build_report(
            state_path, base_currency=base_currency, strategy_note=strategy_note, mode=mode
        ),
        encoding="utf-8",
    )
    return out_path
