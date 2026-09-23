#!/usr/bin/env python
"""Render the N13 results page: the strategy against simply holding the index.

Reads what scripts/run_n13.py wrote to data/signals/clean/ and emits one
self-contained HTML page (no libraries; charts are drawn in SVG at the
viewer's actual width so text stays legible on a phone).

The output embeds curves DERIVED from licensed vendor prices. It is written to
a path outside the repository by default and is not to be committed: the
licence covers private use, and a git remote is redistribution.

Run:  .venv/bin/python -m scripts.make_n13_report --out /path/outside/repo.html
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

CLEAN = Path(os.environ.get("N13_CLEAN_DIR", "data/signals/clean"))
SHOW = {
    "N13 (top 20 by composite)": "N13 strategy",
    "S&P 500 (SPY)": "S&P 500",
    "Nasdaq-100 (QQQ)": "Nasdaq-100",
}


SPLIT = pd.Timestamp("2019-07-01")  # as in scripts/run_n13.py


def _cagr(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def _gate_details(gates: dict, curves: pd.DataFrame, summary: pd.DataFrame) -> None:
    """Spell out two details that runs before this fix stored in shorthand.

    Gate 5 was logged as "see halves above"; the halves are recomputed here
    from the saved curves exactly as run_n13 computes them. Gate 9 was logged
    at two decimals, which hides that it passed by 0.001.
    """
    for g in gates["gates"]:
        if g["gate"] == 5 and g["detail"] == "see halves above":
            n13 = curves["N13 (top 20 by composite)"].dropna()
            base = curves["Size-matched baseline (20 largest eligible)"].reindex(n13.index).ffill()
            parts = []
            for label, m in (("2012-19", n13.index < SPLIT), ("2019-26", n13.index >= SPLIT)):
                parts.append(f"{label}: strategy {_cagr(n13[m]):.1%} a year vs {_cagr(base[m]):.1%}")
            g["detail"] = "; ".join(parts)
        if g["gate"] == 9:
            sharpe = float(summary.loc[summary["book"] == "N13 (top 20 by composite)", "sharpe"].iloc[0])
            ir = gates["active_vs_spy"]["ir"]
            g["detail"] = (f"Sharpe {sharpe:.3f} vs bar {gates['deflation_bar']:.3f}: passes by "
                           f"{sharpe - gates['deflation_bar']:.3f}. The stricter reading, Sharpe of returns "
                           f"in excess of the S&P 500, is {ir:+.2f} and fails.")


def payload(run1_dir: Path | None = None) -> dict:
    curves = pd.read_parquet(CLEAN / "n13_curves.parquet")
    weekly = curves.resample("W-FRI").last().dropna(how="all")
    weekly.iloc[0] = curves.iloc[0]
    weekly = weekly / weekly.iloc[0]
    dd = curves / curves.cummax() - 1.0
    dd_weekly = dd.resample("W-FRI").min().dropna(how="all")

    summary = pd.read_csv(CLEAN / "n13_summary.csv")
    gates = json.loads((CLEAN / "n13_gates.json").read_text())
    ident = pd.read_csv(CLEAN / "identification.csv")

    yearly = {}
    for col in SHOW:
        s = curves[col]
        ends = s.resample("YE").last()
        prev = ends.shift(1)
        prev.iloc[0] = s.iloc[0]
        yearly[col] = (ends / prev - 1.0)

    _gate_details(gates, curves, summary)
    audit = ident[ident["method"] != "contaminant"]
    live, dead = audit[~audit["delisted"]], audit[audit["delisted"]]
    run1 = None
    if run1_dir is not None:
        g1 = json.loads((run1_dir / "n13_gates.json").read_text())
        s1 = pd.read_csv(run1_dir / "n13_summary.csv").set_index("book")
        run1 = {"verdict": g1["verdict"], "cagr": float(s1.loc["N13 (top 20 by composite)", "cagr"])}
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in weekly.index],
        "growth": {SHOW[c]: [round(float(v), 4) for v in weekly[c]] for c in SHOW},
        "drawdown": {SHOW[c]: [round(float(v), 4) for v in dd_weekly[c].reindex(weekly.index).ffill().fillna(0)]
                     for c in ("N13 (top 20 by composite)", "S&P 500 (SPY)")},
        "years": [str(y.year) for y in yearly["S&P 500 (SPY)"].index],
        "yearly": {SHOW[c]: [round(float(v), 4) for v in yearly[c]] for c in SHOW},
        "summary": summary.replace({np.nan: None}).to_dict(orient="records"),
        "gates": gates,
        "audit": {
            "contaminants": int((ident["method"] == "contaminant").sum()),
            "identified": int(ident["cik"].notna().sum()),
            "candidates": len(ident),
            "live_rate": float(live["cik"].notna().mean()),
            "dead_rate": float(dead["cik"].notna().mean()),
        },
        "run1": run1,
    }


PAGE = r"""<title>Did Quality Beat the S&amp;P 500?</title>
<style>
:root {
  --page: #f4f4f1; --surface: #fcfcfb; --border: #e2e1dc; --grid: #e8e7e2; --axis: #c3c2b7;
  --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #7a7873;
  --s-strategy: #2a78d6; --s-index: #eb6834; --s-context: #898781;
  --good: #006300; --good-bg: #e3f1e3; --bad: #b3261e; --bad-bg: #f8e3e1; --muted-bg: #ecebe6;
  --wash: rgba(42,120,214,.08);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #111110; --surface: #1a1a19; --border: #34342f; --grid: #2b2b28; --axis: #383835;
    --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #93918a;
    --s-strategy: #3987e5; --s-index: #d95926; --s-context: #898781;
    --good: #0ca30c; --good-bg: #16301a; --bad: #e66767; --bad-bg: #3a1c1b; --muted-bg: #262624;
    --wash: rgba(57,135,229,.12);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #111110; --surface: #1a1a19; --border: #34342f; --grid: #2b2b28; --axis: #383835;
  --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #93918a;
  --s-strategy: #3987e5; --s-index: #d95926; --s-context: #898781;
  --good: #0ca30c; --good-bg: #16301a; --bad: #e66767; --bad-bg: #3a1c1b; --muted-bg: #262624;
  --wash: rgba(57,135,229,.12);
}
body { background: var(--page); color: var(--ink); font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 920px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 56px; display: grid; gap: 28px; }
h1 { font-size: clamp(1.6rem, 4.4vw, 2.3rem); line-height: 1.15; margin: 0; text-wrap: balance; letter-spacing: -.01em; }
h2 { font-size: 1.12rem; margin: 0 0 4px; text-wrap: balance; }
p { margin: 0; max-width: 68ch; color: var(--ink-2); }
.eyebrow { font-size: .74rem; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }
header { display: grid; gap: 12px; }
.verdict { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; font-size: .92rem;
  padding: 6px 12px; border-radius: 999px; width: fit-content; }
.verdict.fail { background: var(--bad-bg); color: var(--bad); }
.verdict.pass { background: var(--good-bg); color: var(--good); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px;
  background: var(--border); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.kpi { background: var(--surface); padding: 14px 16px; display: grid; gap: 2px; }
.kpi .label { font-size: .8rem; color: var(--ink-3); }
.kpi .value { font-size: 1.6rem; font-weight: 600; }
.kpi .sub { font-size: .8rem; color: var(--ink-2); }
section.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 18px 18px 14px; display: grid; gap: 12px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: .84rem; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.key { width: 16px; height: 2px; border-radius: 2px; display: inline-block; }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.chart { position: relative; width: 100%; }
.chart svg { display: block; width: 100%; overflow: visible; }
.chart svg:focus-visible { outline: 2px solid var(--s-strategy); outline-offset: 4px; border-radius: 4px; }
.tip { position: absolute; top: 0; pointer-events: none; background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px; font-size: .8rem; box-shadow: 0 4px 16px rgba(0,0,0,.08);
  display: grid; gap: 3px; min-width: 150px; }
.tip[hidden] { display: none; }
.tip .when { color: var(--ink-3); font-size: .74rem; }
.tip .row { display: flex; align-items: center; gap: 8px; }
.tip .row b { font-variant-numeric: tabular-nums; min-width: 58px; }
.tip .row em { font-style: normal; color: var(--ink-2); }
button.toggle { justify-self: start; background: none; border: 1px solid var(--border); color: var(--ink-2);
  border-radius: 6px; padding: 4px 10px; font: inherit; font-size: .8rem; cursor: pointer; }
button.toggle:hover { background: var(--muted-bg); }
button.toggle:focus-visible { outline: 2px solid var(--s-strategy); outline-offset: 2px; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .84rem; }
th, td { padding: 7px 10px; text-align: right; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { font-weight: 600; color: var(--ink-3); font-size: .74rem; text-transform: uppercase; letter-spacing: .05em; }
td { font-variant-numeric: tabular-nums; }
tr.hl td { font-weight: 600; }
tr.ref td:first-child { color: var(--ink); }
.neg { color: var(--bad); } .pos { color: var(--good); }
.gates { display: grid; gap: 0; }
.gate { display: grid; grid-template-columns: 28px 1fr auto; gap: 4px 12px; align-items: start;
  padding: 10px 0; border-bottom: 1px solid var(--grid); }
.gate:last-child { border-bottom: 0; }
.gate .n { color: var(--ink-3); font-variant-numeric: tabular-nums; font-size: .84rem; padding-top: 1px; }
.gate .t { font-weight: 500; }
.gate .d { grid-column: 2 / 4; font-size: .82rem; color: var(--ink-2); }
.chip { font-size: .74rem; font-weight: 600; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.chip.pass { background: var(--good-bg); color: var(--good); }
.chip.fail { background: var(--bad-bg); color: var(--bad); }
.chip.notrun { background: var(--muted-bg); color: var(--ink-3); }
.notes { display: grid; gap: 10px; }
.notes p { font-size: .88rem; }
footer p { font-size: .8rem; color: var(--ink-3); }
@media (prefers-reduced-motion: no-preference) { .tip { transition: opacity .08s; } }
</style>

<div class="wrap">
  <header>
    <div class="eyebrow">Backtest · US large caps · point-in-time SEC fundamentals</div>
    <h1>Did quality, profitability and momentum beat simply holding the S&amp;P 500?</h1>
    <div id="verdict" class="verdict"></div>
    <p id="lede"></p>
    <p id="lede2"></p>
  </header>

  <div class="kpis" id="kpis"></div>

  <section class="card" aria-labelledby="g-title">
    <div><h2 id="g-title">Growth of $1</h2><p>Total return in US dollars after trading costs, log scale. Hover or use the arrow keys to read any week.</p></div>
    <div class="legend" id="g-legend"></div>
    <div class="chart" id="growth"></div>
    <button class="toggle" id="g-toggle" aria-expanded="false" aria-controls="g-table">Show table</button>
    <div class="tablewrap" id="g-table" hidden></div>
  </section>

  <section class="card" aria-labelledby="y-title">
    <div><h2 id="y-title">Each calendar year: strategy minus the S&amp;P 500</h2><p>Blue years the strategy came out ahead; orange years the index did. The first year runs from inception.</p></div>
    <div class="chart" id="yearly"></div>
    <button class="toggle" id="y-toggle" aria-expanded="false" aria-controls="y-table">Show table</button>
    <div class="tablewrap" id="y-table" hidden></div>
  </section>

  <section class="card" aria-labelledby="d-title">
    <div><h2 id="d-title">How far below its previous peak</h2><p>Drawdown, weekly worst point. The risk you would have lived through.</p></div>
    <div class="legend" id="d-legend"></div>
    <div class="chart" id="drawdown"></div>
  </section>

  <section class="card" aria-labelledby="t-title">
    <div><h2 id="t-title">Every book side by side</h2><p>Same dates, same costs. "Years ahead" counts calendar years beating the S&amp;P 500.</p></div>
    <div class="tablewrap" id="books"></div>
  </section>

  <section class="card" aria-labelledby="k-title">
    <div><h2 id="k-title">The tests it had to pass</h2><p>Fixed in the research ledger before any result existed. The verdict follows the first failure; later tests are still shown for information.</p></div>
    <div class="gates" id="gates"></div>
  </section>

  <section class="card notes" aria-labelledby="n-title">
    <h2 id="n-title">What to keep in mind</h2>
    <div id="notes" class="notes"></div>
  </section>

  <footer><p id="foot"></p></footer>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
(() => {
  const D = JSON.parse(document.getElementById("data").textContent);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const pct = (x, d = 1) => (x == null || isNaN(x)) ? "\u2013" : (x < 0 ? "\u2212" : "") + Math.abs(x * 100).toFixed(d) + "%";
  const spct = (x, d = 1) => (x == null || isNaN(x)) ? "\u2013" : (x > 0 ? "+" : x < 0 ? "\u2212" : "") + Math.abs(x * 100).toFixed(d) + "%";
  const el = (tag, attrs = {}, text) => { const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text != null) n.textContent = text; return n; };
  const svgEl = (tag, attrs = {}) => { const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
  const COLORS = { "N13 strategy": "--s-strategy", "S&P 500": "--s-index", "Nasdaq-100": "--s-context" };

  const book = (name) => D.summary.find((r) => r.book === name) || {};
  const n13 = book("N13 (top 20 by composite)"), spy = book("S&P 500 (SPY)"), qqq = book("Nasdaq-100 (QQQ)");
  const base = book("Size-matched baseline (20 largest eligible)");
  const killed = String(D.gates.verdict).startsWith("KILLED");
  const allPass = !killed && D.gates.gates.every((g) => g.status === "pass");

  // ---------------------------------------------------------------- header
  const v = document.getElementById("verdict");
  v.className = "verdict " + (allPass ? "pass" : "fail");
  v.textContent = allPass ? "Passed every pre-registered test" :
    (killed ? "Rejected: failed test " + D.gates.gates.find((g) => g.status === "fail").gate + " of 9"
            : "Rejected: failed a later test");
  const ahead = n13.cagr - spy.cagr;
  document.getElementById("lede").textContent =
    `From ${D.gates.start} to ${D.gates.end}, the strategy compounded at ${pct(n13.cagr)} a year against ` +
    `${pct(spy.cagr)} for the S&P 500 (${spct(ahead)}), with ${pct(n13.vol, 0)} volatility against ${pct(spy.vol, 0)}. ` +
    `${allPass ? "It cleared every test fixed in advance." : "It did not clear the tests fixed in advance, so this is a description of history, not evidence of an edge."}`;
  const top20 = book("Top 20 largest, clean universe (M3 re-run)");
  document.getElementById("lede2").textContent =
    `Two simpler things did better. The Nasdaq-100 fund compounded at ${pct(qqq.cagr)} a year with a worst fall of ` +
    `${pct(qqq.maxdd, 0)}. Holding the 20 largest eligible stocks, with no signal at all, compounded at ${pct(base.cagr)} ` +
    `(worst fall ${pct(base.maxdd, 0)}) \u2014 the quality and momentum ranking subtracted value from that starting point in every variant tested.`;

  const kpis = [
    ["Strategy, per year", pct(n13.cagr), "compound annual return"],
    ["S&P 500, per year", pct(spy.cagr), "SPY, dividends reinvested"],
    ["Difference", spct(ahead), `${n13.beat || "\u2013"} years ahead`],
    ["Worst fall", pct(n13.maxdd, 0), `S&P 500: ${pct(spy.maxdd, 0)}`],
  ];
  const K = document.getElementById("kpis");
  for (const [l, val, s] of kpis) { const k = el("div", { class: "kpi" });
    k.append(el("span", { class: "label" }, l), el("span", { class: "value" }, val), el("span", { class: "sub" }, s)); K.append(k); }

  // ---------------------------------------------------------------- legend
  const legend = (id, names) => { const L = document.getElementById(id);
    for (const n of names) { const s = el("span"); const k = el("span", { class: "key" });
      k.style.background = css(COLORS[n]); s.append(k, document.createTextNode(n)); L.append(s); } };
  legend("g-legend", Object.keys(D.growth));
  legend("d-legend", Object.keys(D.drawdown));

  // ---------------------------------------------------------------- line chart
  function lineChart(host, series, opts) {
    const dates = D.dates.map((d) => new Date(d + "T00:00:00Z"));
    const names = Object.keys(series);
    let idx = dates.length - 1;
    const tip = el("div", { class: "tip", "aria-hidden": "true" }); tip.hidden = true;
    function draw() {
      host.replaceChildren();
      // On a phone the full end labels would take a third of the plot; the
      // legend names the lines there and the labels carry the value alone.
      const W = host.clientWidth, H = opts.height, narrow = W < 560;
      const m = { t: 10, r: opts.endLabels ? (narrow ? 56 : 96) : 16, b: 26, l: 48 };
      const iw = W - m.l - m.r, ih = H - m.t - m.b;
      const all = names.flatMap((n) => series[n]).filter((x) => x != null);
      const y0 = opts.log ? Math.log(Math.min(...all)) : Math.min(0, ...all);
      const y1 = opts.log ? Math.log(Math.max(...all)) : Math.max(0, ...all);
      const X = (i) => m.l + (i / (dates.length - 1)) * iw;
      const Y = (v) => m.t + ih - ((opts.log ? Math.log(v) : v) - y0) / (y1 - y0) * ih;
      const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, height: H, tabindex: "0", role: "img",
        "aria-label": opts.label });
      for (const t of opts.ticks(y0, y1)) {
        const y = Y(t.v); if (y < m.t - 1 || y > m.t + ih + 1) continue;
        svg.append(svgEl("line", { x1: m.l, x2: m.l + iw, y1: y, y2: y, stroke: css(t.base ? "--axis" : "--grid"), "stroke-width": 1 }));
        const tx = svgEl("text", { x: m.l - 8, y: y + 4, "text-anchor": "end", fill: css("--ink-3"), "font-size": 11 });
        tx.textContent = t.label; svg.append(tx);
      }
      let lastYear = null;
      dates.forEach((d, i) => { const yr = d.getUTCFullYear();
        if (yr !== lastYear && d.getUTCMonth() === 0 && (yr % (iw < 520 ? 3 : 2) === 0)) {
          const tx = svgEl("text", { x: X(i), y: H - 6, "text-anchor": "middle", fill: css("--ink-3"), "font-size": 11 });
          tx.textContent = yr; svg.append(tx); lastYear = yr; } });
      for (const n of names) {
        const pts = series[n].map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
        svg.append(svgEl("polyline", { points: pts, fill: "none", stroke: css(COLORS[n]),
          "stroke-width": n === "Nasdaq-100" ? 1.5 : 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      }
      if (opts.endLabels) {
        const last = names.map((n) => ({ n, y: Y(series[n].at(-1)) })).sort((a, b) => a.y - b.y);
        for (let k = 1; k < last.length; k++) if (last[k].y - last[k - 1].y < 14) last[k].y = last[k - 1].y + 14;
        for (const { n, y } of last) {
          svg.append(svgEl("circle", { cx: X(dates.length - 1), cy: Y(series[n].at(-1)), r: 4, fill: css(COLORS[n]),
            stroke: css("--surface"), "stroke-width": 2 }));
          const tx = svgEl("text", { x: X(dates.length - 1) + 10, y: y + 4, fill: css("--ink-2"), "font-size": 11 });
          tx.textContent = narrow ? opts.fmt(series[n].at(-1)) : `${n} ${opts.fmt(series[n].at(-1))}`; svg.append(tx);
        }
      }
      const cross = svgEl("line", { y1: m.t, y2: m.t + ih, stroke: css("--ink-3"), "stroke-width": 1 });
      cross.setAttribute("visibility", "hidden"); svg.append(cross);
      const dots = names.map((n) => { const c = svgEl("circle", { r: 4, fill: css(COLORS[n]), stroke: css("--surface"), "stroke-width": 2 });
        c.setAttribute("visibility", "hidden"); svg.append(c); return c; });
      function show(i) {
        idx = Math.max(0, Math.min(dates.length - 1, i));
        const x = X(idx); cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
        names.forEach((n, k) => { dots[k].setAttribute("cx", x); dots[k].setAttribute("cy", Y(series[n][idx])); dots[k].setAttribute("visibility", "visible"); });
        tip.replaceChildren(el("span", { class: "when" }, "Week of " + D.dates[idx]));
        for (const n of [...names].sort((a, b) => series[b][idx] - series[a][idx])) {
          const r = el("div", { class: "row" }); const k = el("span", { class: "key" }); k.style.background = css(COLORS[n]);
          r.append(k, el("b", {}, opts.fmt(series[n][idx])), el("em", {}, n)); tip.append(r);
        }
        tip.hidden = false;
        const tw = tip.offsetWidth; tip.style.left = Math.min(Math.max(x + 12, 0), W - tw) + "px";
        if (x + 12 + tw > W) tip.style.left = Math.max(0, x - tw - 12) + "px";
      }
      function hide() { cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); tip.hidden = true; }
      svg.addEventListener("pointermove", (e) => { const r = svg.getBoundingClientRect();
        show(Math.round((e.clientX - r.left - m.l) / iw * (dates.length - 1))); });
      svg.addEventListener("pointerleave", hide);
      svg.addEventListener("focus", () => show(idx));
      svg.addEventListener("blur", hide);
      svg.addEventListener("keydown", (e) => {
        const step = e.shiftKey ? 52 : 4;
        if (e.key === "ArrowRight") { show(idx + step); e.preventDefault(); }
        if (e.key === "ArrowLeft") { show(idx - step); e.preventDefault(); }
      });
      host.append(svg, tip);
    }
    draw();
    return draw;
  }

  const growthTicks = (y0, y1) => { const out = []; for (let v = 1; Math.log(v) <= y1 + 1e-9; v *= 2) out.push({ v, label: "$" + v, base: v === 1 }); return out; };
  const ddTicks = (y0) => { const out = []; for (let v = 0; v >= y0 - 0.1; v -= 0.1) out.push({ v, label: v === 0 ? "0%" : "\u2212" + Math.round(-v * 100) + "%", base: v === 0 }); return out; };
  const redrawG = lineChart(document.getElementById("growth"), D.growth,
    { height: 320, log: true, endLabels: true, fmt: (v) => "$" + v.toFixed(2), ticks: growthTicks,
      label: "Growth of one dollar for the strategy, the S&P 500 and the Nasdaq-100" });
  const redrawD = lineChart(document.getElementById("drawdown"), D.drawdown,
    { height: 200, log: false, endLabels: false, fmt: (v) => pct(v, 1), ticks: ddTicks,
      label: "Drawdown from previous peak for the strategy and the S&P 500" });

  // ---------------------------------------------------------------- yearly bars
  const yrHost = document.getElementById("yearly");
  const diff = D.years.map((_, i) => D.yearly["N13 strategy"][i] - D.yearly["S&P 500"][i]);
  function drawYears() {
    yrHost.replaceChildren();
    const W = yrHost.clientWidth, H = 240, m = { t: 22, r: 10, b: 26, l: 44 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const lim = Math.max(0.05, ...diff.map(Math.abs)) * 1.12;
    const Y = (v) => m.t + ih / 2 - (v / lim) * (ih / 2);
    const band = iw / diff.length, bw = Math.min(24, band - 6);
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img",
      "aria-label": "Yearly return of the strategy minus the S&P 500" });
    const step = lim > 0.3 ? 0.2 : 0.1;
    for (let t = -Math.floor(lim / step) * step; t <= lim + 1e-9; t += step) {
      const y = Y(t);
      svg.append(svgEl("line", { x1: m.l, x2: m.l + iw, y1: y, y2: y, stroke: css(Math.abs(t) < 1e-9 ? "--axis" : "--grid"), "stroke-width": 1 }));
      const tx = svgEl("text", { x: m.l - 8, y: y + 4, "text-anchor": "end", fill: css("--ink-3"), "font-size": 11 });
      tx.textContent = spct(t, 0); svg.append(tx);
    }
    diff.forEach((v, i) => {
      const cx = m.l + band * i + band / 2, y = Y(v), y0 = Y(0), h = Math.abs(y - y0);
      const up = v >= 0, r = Math.min(4, h);
      const top = up ? y : y0, bottom = up ? y0 : y;
      const x = cx - bw / 2;
      const d = up
        ? `M${x},${bottom} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${bottom} Z`
        : `M${x},${top} V${bottom - r} Q${x},${bottom} ${x + r},${bottom} H${x + bw - r} Q${x + bw},${bottom} ${x + bw},${bottom - r} V${top} Z`;
      const bar = svgEl("path", { d, fill: css(up ? "--s-strategy" : "--s-index"), tabindex: "0" });
      const t = svgEl("title"); t.textContent = `${D.years[i]}: strategy ${spct(D.yearly["N13 strategy"][i])}, S&P 500 ${spct(D.yearly["S&P 500"][i])}, difference ${spct(v)}`;
      bar.append(t); svg.append(bar);
      if (band >= 30) { const lab = svgEl("text", { x: cx, y: up ? y - 6 : y + 14, "text-anchor": "middle", fill: css("--ink-2"), "font-size": 10.5 });
        lab.textContent = spct(v, 0); svg.append(lab); }
      if (i % (band < 34 ? 2 : 1) === 0) { const yl = svgEl("text", { x: cx, y: H - 6, "text-anchor": "middle", fill: css("--ink-3"), "font-size": 11 });
        yl.textContent = "\u2019" + D.years[i].slice(2); svg.append(yl); }
    });
    yrHost.append(svg);
  }
  drawYears();

  // ---------------------------------------------------------------- tables
  function table(host, head, rows, cls = () => "") {
    const t = el("table"), tr = el("tr");
    head.forEach((h) => tr.append(el("th", { scope: "col" }, h))); const th = el("thead"); th.append(tr); t.append(th);
    const tb = el("tbody");
    for (const r of rows) { const row = el("tr", { class: cls(r) });
      r.cells.forEach((c) => { const td = el("td", {}, c.text); if (c.cls) td.className = c.cls; row.append(td); }); tb.append(row); }
    t.append(tb); host.replaceChildren(t);
  }
  const signCls = (x) => x > 0 ? "pos" : x < 0 ? "neg" : "";
  const yearEnd = D.years.map((y) => { const i = D.dates.map((d) => d.slice(0, 4)).lastIndexOf(y); return i; });
  table(document.getElementById("g-table"), ["Year end", ...Object.keys(D.growth)],
    D.years.map((y, k) => ({ cells: [{ text: y }, ...Object.keys(D.growth).map((n) => ({ text: "$" + D.growth[n][yearEnd[k]].toFixed(2) }))] })));
  table(document.getElementById("y-table"), ["Year", "Strategy", "S&P 500", "Nasdaq-100", "Strategy \u2212 S&P 500"],
    D.years.map((y, i) => ({ cells: [{ text: y }, { text: spct(D.yearly["N13 strategy"][i]) }, { text: spct(D.yearly["S&P 500"][i]) },
      { text: spct(D.yearly["Nasdaq-100"][i]) }, { text: spct(diff[i]), cls: signCls(diff[i]) }] })));

  const order = ["N13 (top 20 by composite)", "S&P 500 (SPY)", "Nasdaq-100 (QQQ)", "Size-matched baseline (20 largest eligible)",
    "Top 20 largest, clean universe (M3 re-run)", "Equal-weight clean top 100", "Equal-weight all eligible"];
  const nice = { "N13 (top 20 by composite)": "N13 strategy (20 stocks)", "S&P 500 (SPY)": "S&P 500 (SPY)", "Nasdaq-100 (QQQ)": "Nasdaq-100 (QQQ)",
    "Size-matched baseline (20 largest eligible)": "20 largest eligible stocks", "Top 20 largest, clean universe (M3 re-run)": "20 largest, any sector",
    "Equal-weight clean top 100": "Equal-weight top 100", "Equal-weight all eligible": "Equal-weight all eligible" };
  table(document.getElementById("books"), ["Book", "Per year", "Volatility", "Sharpe", "Worst fall", "vs S&P 500", "Years ahead"],
    order.map((b) => book(b)).filter((r) => r.book).map((r) => ({ book: r.book, cells: [
      { text: nice[r.book] || r.book }, { text: pct(r.cagr) }, { text: pct(r.vol, 0) }, { text: r.sharpe == null ? "\u2013" : r.sharpe.toFixed(2) },
      { text: pct(r.maxdd, 0) }, { text: r.book === "S&P 500 (SPY)" ? "\u2013" : spct(r.excess), cls: r.book === "S&P 500 (SPY)" ? "" : signCls(r.excess) },
      { text: r.beat && r.beat !== "-" ? r.beat : "\u2013" }] })),
    (r) => r.book === "N13 (top 20 by composite)" ? "hl" : (r.book === "S&P 500 (SPY)" ? "ref" : ""));

  const wire = (btn, pane) => { const b = document.getElementById(btn), p = document.getElementById(pane);
    b.addEventListener("click", () => { p.hidden = !p.hidden; b.setAttribute("aria-expanded", String(!p.hidden));
      b.textContent = p.hidden ? "Show table" : "Hide table"; }); };
  wire("g-toggle", "g-table"); wire("y-toggle", "y-table");

  // ---------------------------------------------------------------- gates
  const G = document.getElementById("gates");
  const g0 = { gate: 0, label: "Survivorship audit of the data", status: "fail",
    detail: `Failed in round 9. Run anyway at your request on data cleaned against the SEC registry, where it still fails: ` +
      `${pct(D.audit.live_rate, 1)} of surviving stocks identified vs ${pct(D.audit.dead_rate, 1)} of delisted ones, a gap of ` +
      `${((D.audit.live_rate - D.audit.dead_rate) * 100).toFixed(1)} points against a limit of 6.` };
  for (const g of [g0, ...D.gates.gates]) {
    const row = el("div", { class: "gate" });
    const chip = el("span", { class: "chip " + (g.status === "pass" ? "pass" : g.status === "fail" ? "fail" : "notrun") },
      g.status === "pass" ? "Pass" : g.status === "fail" ? "Fail" : "Not run");
    row.append(el("span", { class: "n" }, String(g.gate)), el("span", { class: "t" }, g.label), chip, el("span", { class: "d" }, g.detail));
    G.append(row);
  }

  // ---------------------------------------------------------------- notes
  const notes = [
    `The data was cleaned against the SEC's own registry: ${D.audit.contaminants} contaminated codes removed (foreign listings mislabelled as US, codes with no company behind them) and prices outside each company's filing history blanked. Delisted companies are still under-identified (${pct(D.audit.dead_rate, 0)} vs ${pct(D.audit.live_rate, 0)}), which tends to flatter every stock-picking book here slightly, including the strategy.`,
    `Costs: 0.33% per round trip on the value traded (IBKR commissions plus spread, sized for a €25,000 account). Not included: taxes, the price-data subscription, or a server. For a Finnish investor, an equity savings account (osakesäästötili) would defer tax on this strategy's trades; an S&P 500 index fund already defers it by not trading.`,
    `Returns are in US dollars. In euros both the strategy and the index move with EUR/USD together, so the gap between them is essentially unchanged.`,
    `The strategy holds 20 stocks, so it can drift far from the index in any single year, as the yearly chart shows. That tracking risk is the price of trying to beat it.`,
    `The \u201c20 largest\u201d books beat the S&P 500 by a wide margin, but they were comparison yardsticks here, not a tested strategy. Over 2012\u20132026 they amount to a bet on the mega-caps that led this particular era, with a worst fall of about ${pct(top20.maxdd, 0)}. The Nasdaq-100 fund captured much of the same move with a shallower fall and no trading.`,
  ];
  if (D.run1) notes.push(
    `This is the second run. The first (${D.run1.verdict.replace("KILLED", "killed").split(":")[0]}, strategy ${pct(D.run1.cagr)} a year) had a data defect: ` +
    `removing foreign listings left 105 US market holidays in the price table, which broke seven month-ends and left the ranked books in cash for those months. ` +
    `The defect and the first result were recorded before the fix, and the verdict did not change.`);
  const N = document.getElementById("notes"); notes.forEach((t) => N.append(el("p", {}, t)));
  document.getElementById("foot").textContent =
    `Ledger trials at test time: ${D.gates.trials}. Deflation bar ${D.gates.deflation_bar.toFixed(2)}. Universe: top 100 US-listed stocks by trading value each month, one share class per company; fundamentals from SEC XBRL filings, usable from the day after filing.`;

  let t; window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { redrawG(); redrawD(); drawYears(); }, 120); });
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener?.("change", () => { redrawG(); redrawD(); drawYears(); });
  new MutationObserver(() => { redrawG(); redrawD(); drawYears(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
</script>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run1", type=Path, default=None,
                        help="directory holding run 1's n13_gates.json and n13_summary.csv, to cite beside run 2")
    args = parser.parse_args()
    data = json.dumps(payload(args.run1), separators=(",", ":")).replace("</", "<\\/")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(PAGE.replace("__DATA__", data), encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
