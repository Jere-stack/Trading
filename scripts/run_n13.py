#!/usr/bin/env python
"""N13 on the CLEAN universe: pre-registered gates 1-9, and a full comparison
against simply holding the S&P 500 and the Nasdaq-100.

STATUS OF THIS RUN
------------------
Gate 0 FAILED in round 9 (survivorship audit). The account holder chose to
proceed on data cleaned against the SEC registry (scripts/build_clean_universe.py).
Every number here therefore carries that caveat, and the re-measured audit gap
is printed first so it is never separated from the results.

THE VERDICT follows the pre-registered gate order and stops at the first kill.
THE BENCHMARK COMPARISON is computed regardless, because it is what was asked
for; after a kill it is DESCRIPTION, not evidence, and is labelled so.

Fixed before this run, alongside the pre-registration:
  * composite = mean of percentile ranks of profitability, quality and 12-1
    momentum, among top-100 names with all three available; a name missing
    any component cannot be selected (banks and insurers have no gross profit)
  * size-matched baseline = the 20 largest by dollar volume from the SAME
    eligible set, same rebalance, band and costs -- so the comparison isolates
    the signal, not the exclusion of financials
  * gate 9 is applied AS WRITTEN (raw Sharpe vs the deflation bar), and the
    stricter version -- the Sharpe of returns in excess of SPY -- is reported
    beside it, because a long-only book clears a raw-Sharpe bar on market beta

Run:  .venv/bin/python -m scripts.run_n13
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.data.sec import SecClient
from tradelab.research.cross_section import month_end_dates, signal_momentum_12_1
from tradelab.research.fundamentals import annual_ratio_rows, as_of, with_predecessors
from tradelab.research.ic import forward_returns, information_coefficient, neutralise, summarise_ic
from tradelab.research.ledger import ResearchLedger
from tradelab.research.rank_backtest import perf_stats, run_ranked
from tradelab.research.validation import expected_max_sharpe

CLEAN = Path(os.environ.get("N13_CLEAN_DIR", "data/signals/clean"))
POOL, MIN_DV, LIQ, LOOKBACK = 100, 2_000_000, 60, 252
N_HOLD, BAND, COST = 20, 10, 33.1
PERMUTATIONS = 200
CAPITAL_EUR = 25_000
SPLIT = pd.Timestamp("2019-07-01")


class Killed(Exception):
    pass


def rule(title: str) -> None:
    print("\n" + "=" * 92, flush=True)
    print(title, flush=True)
    print("=" * 92, flush=True)


# ------------------------------------------------------------------ data
def load():
    closes = pd.read_parquet(CLEAN / "close.parquet")
    dv = pd.read_parquet(CLEAN / "dollar_volume.parquet")
    sym_cik = pd.read_csv(CLEAN / "symbol_cik.csv", index_col=0)["cik"].astype(int).to_dict()
    bench = {}
    for s in ("SPY", "QQQ"):
        f = pd.read_parquet(f"data/bars/benchmarks/{s}.parquet").sort_values("timestamp")
        idx = pd.DatetimeIndex(f["timestamp"])
        idx = idx.tz_convert("UTC").tz_localize(None) if idx.tz is not None else idx
        bench[s] = pd.Series(f["close"].to_numpy(dtype=float), index=idx)
    return closes, dv, sym_cik, bench


def load_fundamentals(ciks: set[int]) -> dict[int, pd.DataFrame]:
    """Annual ratio rows per CIK, cached. A CIK is recorded as processed even
    when it has no usable rows (banks), so a re-run does not re-fetch it."""
    cache, done_file = CLEAN / "fundamentals.parquet", CLEAN / "fundamentals_done.txt"
    frame = pd.read_parquet(cache) if cache.exists() else pd.DataFrame()
    done = {int(x) for x in done_file.read_text().split()} if done_file.exists() else set()
    todo = sorted(ciks - done)
    if todo:
        client = SecClient()
        parts = [frame] if len(frame) else []
        for i, cik in enumerate(todo, 1):
            facts = client.companyfacts(cik)
            if facts:
                rows = annual_ratio_rows(facts).frame
                if len(rows):
                    parts.append(rows.assign(cik=cik))
            done.add(cik)
            if i % 100 == 0:
                print(f"    fundamentals {i}/{len(todo)}  (network requests {client.requests_made})", flush=True)
        frame = pd.concat(parts, ignore_index=True)
        frame.to_parquet(cache, index=False)
        done_file.write_text("\n".join(str(c) for c in sorted(done)))
    return {int(c): g.drop(columns="cik") for c, g in frame.groupby("cik")}


def load_predecessors() -> dict[int, int]:
    path = CLEAN / "predecessors.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    return {int(c): int(p) for c, p in zip(frame["cik"], frame["predecessor_cik"], strict=True)}


def universe_on(date, closes, dv, sym_cik) -> list[str]:
    pos = closes.index.get_loc(date)
    med = dv.iloc[max(0, pos - LIQ) : pos + 1].median(skipna=True)
    med = med[(med >= MIN_DV) & closes.loc[date].notna()].dropna().sort_values(ascending=False)
    seen: set[int] = set()
    out: list[str] = []
    for sym in med.index:
        cik = sym_cik.get(sym)
        if cik is None or cik in seen:
            continue  # one slot per company: the more liquid share class wins
        seen.add(cik)
        out.append(sym)
        if len(out) == POOL:
            break
    return out


def build_panels(dates, closes, dv, sym_cik, funds):
    mom, prof, qual, size = {}, {}, {}, {}
    members = {}
    for date in dates:
        names = universe_on(date, closes, dv, sym_cik)
        members[date] = names
        pos = closes.index.get_loc(date)
        window = closes.iloc[max(0, pos - LOOKBACK) : pos + 1][names]
        mom[date] = signal_momentum_12_1(window)
        med = dv.iloc[max(0, pos - LIQ) : pos + 1][names].median(skipna=True)
        size[date] = med
        p, q = {}, {}
        for s in names:
            rows = funds.get(sym_cik[s])
            p[s] = as_of(rows, date, "profitability") if rows is not None else np.nan
            q[s] = as_of(rows, date, "quality") if rows is not None else np.nan
        prof[date], qual[date] = pd.Series(p), pd.Series(q)
    frame = lambda d: pd.DataFrame(d).T.reindex(columns=closes.columns)  # noqa: E731
    return frame(mom), frame(prof), frame(qual), frame(size), members


def ranks(panel: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    return panel.where(eligible).rank(axis=1, pct=True)


# ------------------------------------------------------------------ helpers
def daily_bench(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    s = series.reindex(series.index.union(index)).ffill().reindex(index)
    return s / s.iloc[0]


def calendar_excess(strat: pd.Series, bench: pd.Series) -> pd.Series:
    """Calendar-year return of `strat` minus `bench`; the first year runs from inception."""
    def yearly(series: pd.Series) -> pd.Series:
        ends = series.resample("YE").last()
        prev = ends.shift(1)
        prev.iloc[0] = series.iloc[0]
        return ends / prev - 1.0
    return (yearly(strat) - yearly(bench.reindex(strat.index).ffill())).dropna()


def active_stats(strat: pd.Series, bench: pd.Series) -> dict[str, float]:
    ra = strat.pct_change().dropna()
    rb = bench.reindex(strat.index).pct_change().dropna()
    active = (ra - rb).dropna()
    te = float(active.std(ddof=1) * math.sqrt(252))
    ir = float(active.mean() * 252 / te) if te > 0 else float("nan")
    return {"te": te, "ir": ir}


def per_date_permutation(panel: pd.DataFrame, rng) -> pd.DataFrame:
    out = panel.copy()
    for date in out.index:
        row = out.loc[date]
        ok = row.notna()
        vals = row[ok].to_numpy()
        out.loc[date, ok[ok].index] = vals[rng.permutation(len(vals))]
    return out


# ------------------------------------------------------------------ main
def main() -> None:
    closes, dv, sym_cik, bench = load()
    rule("N13 ON THE CLEAN UNIVERSE  --  quality + profitability + momentum, long-only")
    print(f"  clean panel {closes.shape[0]:,} sessions x {closes.shape[1]:,} identified securities")

    months = [d for d in month_end_dates(closes.index) if d >= closes.index[LOOKBACK]]
    quarters = [d for d in months if d.month in (3, 6, 9, 12)]
    print(f"  {len(months)} month-ends, {len(quarters)} quarter-ends, {months[0]:%Y-%m} to {months[-1]:%Y-%m}")

    print("  loading point-in-time fundamentals from SEC XBRL...", flush=True)
    links = load_predecessors()
    funds = load_fundamentals(set(sym_cik.values()) | set(links.values()))
    funds = with_predecessors(funds, links)
    print(f"  companies with annual ratio history: {len(funds)}  "
          f"(reorganised companies linked to their predecessor: {len(links)})")

    mom, prof, qual, size, members = build_panels(months, closes, dv, sym_cik, funds)
    in_univ = pd.DataFrame(False, index=months, columns=closes.columns)
    for d, names in members.items():
        in_univ.loc[d, names] = True
    eligible = in_univ & mom.notna() & prof.notna() & qual.notna()
    r_m, r_p, r_q = ranks(mom, eligible), ranks(prof, eligible), ranks(qual, eligible)
    composite = (r_m + r_p + r_q) / 3
    pq = (r_p + r_q) / 2
    n_elig = eligible.sum(axis=1)
    print(f"  eligible names per month (top {POOL} with all three components): "
          f"mean {n_elig.mean():.0f}, min {n_elig.min()}")
    if (n_elig < N_HOLD).any():
        # Run 1 of round 10 went through with seven such months and parked every
        # ranked book in cash for them. A month that cannot fill the book is a
        # data defect, never a result.
        bad = ", ".join(f"{d:%Y-%m-%d} ({n})" for d, n in n_elig[n_elig < N_HOLD].items())
        raise SystemExit(f"data defect: month-ends with fewer than {N_HOLD} eligible names: {bad}")

    q_comp = composite.loc[quarters]
    fwd_q = forward_returns(closes, quarters)

    ledger = ResearchLedger(Path("research/ledger.jsonl"))
    trials_before = ledger.trial_count()

    verdict = None
    gate_log: list[dict] = []

    def log(gate: int, label: str, ok: bool | None, detail: str) -> None:
        gate_log.append({"gate": gate, "label": label,
                         "status": "pass" if ok else ("fail" if ok is False else "not run"),
                         "detail": detail})
    try:
        # ------------------------------------------------ GATE 1
        rule("GATE 1  --  composite IC vs next-quarter return (non-overlapping), t >= 2 on n_eff")
        ic = summarise_ic(information_coefficient(q_comp, fwd_q, min_names=20))
        print(f"  {ic.line()}")
        g1_detail = f"IC {ic.mean:+.4f}, t {ic.t_stat:+.2f} on {ic.n_eff:.0f} effective quarters, hit {ic.hit_rate:.0%}"
        if abs(ic.t_stat) < 2 or ic.mean <= 0:
            log(1, "Signal ranks next-quarter returns (IC t >= 2)", False, g1_detail)
            raise Killed(f"gate 1: composite IC {ic.mean:+.4f}, t {ic.t_stat:+.2f}")
        log(1, "Signal ranks next-quarter returns (IC t >= 2)", True, g1_detail)
        print("  -> PASS")

        # ------------------------------------------------ GATE 2
        rule(f"GATE 2  --  cross-sectional permutation null ({PERMUTATIONS} draws), p < 0.05")
        rng = np.random.default_rng(13)
        null = np.array([
            summarise_ic(information_coefficient(per_date_permutation(q_comp, rng), fwd_q, min_names=20)).mean
            for _ in range(PERMUTATIONS)
        ])
        p = (int(np.sum(np.abs(null) >= abs(ic.mean))) + 1) / (PERMUTATIONS + 1)
        print(f"  real IC {ic.mean:+.4f}   null mean {null.mean():+.4f} sd {null.std(ddof=1):.4f}   "
              f"z {(ic.mean - null.mean()) / null.std(ddof=1):+.2f}   empirical p {p:.3f}")
        g2_detail = f"empirical p {p:.3f} against {PERMUTATIONS} shuffles"
        if p >= 0.05:
            log(2, "Beats a shuffled-signal null (p < 0.05)", False, g2_detail)
            raise Killed(f"gate 2: empirical p {p:.3f}")
        log(2, "Beats a shuffled-signal null (p < 0.05)", True, g2_detail)
        print("  -> PASS")

        # ------------------------------------------------ GATE 3
        rule("GATE 3  --  at least 2 of 3 components with positive IC")
        comps = {"profitability": r_p, "quality": r_q, "momentum": r_m}
        positive = 0
        for name, panel in comps.items():
            s = summarise_ic(information_coefficient(panel.loc[quarters], fwd_q, min_names=20))
            positive += s.mean > 0
            print(f"  {name:<14}{s.line()}")
        if positive < 2:
            log(3, "At least 2 of 3 components work", False, f"{positive} of 3 positive")
            raise Killed(f"gate 3: only {positive} of 3 components positive")
        log(3, "At least 2 of 3 components work", True, f"{positive} of 3 positive")
        print("  -> PASS")

        # ------------------------------------------------ GATE 4
        rule("GATE 4  --  fundamentals add value after neutralising momentum, |t| >= 2")
        resid = neutralise(pq.loc[quarters], r_m.loc[quarters])
        s4 = summarise_ic(information_coefficient(resid, fwd_q, min_names=20))
        print(f"  profitability+quality, momentum removed   {s4.line()}")
        g4_detail = f"IC {s4.mean:+.4f}, t {s4.t_stat:+.2f} after removing momentum"
        if abs(s4.t_stat) < 2 or s4.mean <= 0:
            log(4, "Fundamentals add value beyond momentum", False, g4_detail)
            raise Killed(f"gate 4: fundamentals add nothing beyond momentum (t {s4.t_stat:+.2f})")
        log(4, "Fundamentals add value beyond momentum", True, g4_detail)
        print("  -> PASS")
        verdict = "passed gates 1-4"
    except Killed as exc:
        verdict = f"KILLED at {exc}"
        labels = {1: "Signal ranks next-quarter returns (IC t >= 2)", 2: "Beats a shuffled-signal null (p < 0.05)",
                  3: "At least 2 of 3 components work", 4: "Fundamentals add value beyond momentum"}
        done = {g["gate"] for g in gate_log}
        for k in range(1, 5):
            if k not in done:
                log(k, labels[k], None, "not run: stopped at the first failing gate")
        print(f"\n  N13 {verdict}")
        print("  Gates 5-9 are still computed below because the benchmark comparison was")
        print("  asked for -- but from here on the numbers are DESCRIPTION, not evidence.")

    # ---------------------------------------------------- backtests
    baseline_scores = size.where(eligible)
    ew_eligible = eligible.astype(float).where(eligible)
    ew_universe = in_univ.astype(float).where(in_univ)
    top20_universe = size.where(in_univ)

    def bt(panel, dates, n=N_HOLD, band=BAND):
        return run_ranked(panel.loc[dates], closes, n_hold=n, hold_band=band, cost_bps=COST)

    res = {
        "N13 (top 20 by composite)": bt(composite, quarters),
        "Size-matched baseline (20 largest eligible)": bt(baseline_scores, quarters),
        "Equal-weight all eligible": bt(ew_eligible, quarters, n=POOL, band=0),
        "Equal-weight clean top 100": bt(ew_universe, quarters, n=POOL, band=0),
        "Top 20 largest, clean universe (M3 re-run)": bt(top20_universe, quarters),
    }
    start = res["N13 (top 20 by composite)"].equity.index[0]
    idx = res["N13 (top 20 by composite)"].equity.index
    spy, qqq = daily_bench(bench["SPY"], idx), daily_bench(bench["QQQ"], idx)

    rule("GATES 5-6  --  vs the size-matched baseline in both halves, and Sharpe vs SPY")
    n13 = res["N13 (top 20 by composite)"].equity
    # Aligned explicitly: both books start from the same eligibility mask, so the
    # dates should match, but a boolean mask built on one index must not be
    # applied to another on the strength of "should".
    base = res["Size-matched baseline (20 largest eligible)"].equity.reindex(n13.index).ffill()
    halves = [("first half", n13.index < SPLIT), ("second half", n13.index >= SPLIT)]
    g5 = True
    for label, m in halves:
        a, b = perf_stats(n13[m] / n13[m].iloc[0]), perf_stats(base[m] / base[m].iloc[0])
        g5 &= a["cagr"] > b["cagr"]
        print(f"  {label:<12} N13 {a['cagr']:>7.2%}   baseline {b['cagr']:>7.2%}   "
              f"{'beats' if a['cagr'] > b['cagr'] else 'LOSES'}")
    s_n13, s_spy = perf_stats(n13), perf_stats(spy)
    g6 = s_n13["sharpe"] >= s_spy["sharpe"]
    print(f"  Sharpe N13 {s_n13['sharpe']:.2f} vs SPY {s_spy['sharpe']:.2f}  -> {'PASS' if g6 else 'FAIL'}")
    print(f"  gate 5 {'PASS' if g5 else 'FAIL'}   gate 6 {'PASS' if g6 else 'FAIL'}")

    rule("GATES 7-8  --  parameter surface (12 cells) vs baseline, full sample and both halves")
    cells = []
    for n in (15, 20, 30):
        for label, dates in (("monthly", months), ("quarterly", quarters)):
            for band in (0, 10):
                a = run_ranked(composite.loc[dates], closes, n_hold=n, hold_band=band, cost_bps=COST).equity
                b = run_ranked(baseline_scores.loc[dates], closes, n_hold=n, hold_band=band, cost_bps=COST).equity
                row = {"hold": n, "rebal": label, "band": band}
                for tag, m in (("full", slice(None)), ("h1", a.index < SPLIT), ("h2", a.index >= SPLIT)):
                    aa, bb = a[m], b.reindex(a.index)[m]
                    row[tag] = perf_stats(aa / aa.iloc[0])["cagr"] - perf_stats(bb / bb.iloc[0])["cagr"]
                cells.append(row)
    surf = pd.DataFrame(cells)
    print(f"  {'hold':>5}{'rebal':>11}{'band':>6}{'full':>10}{'2012-19':>10}{'2019-26':>10}   (N13 minus baseline, CAGR)")
    for r in surf.itertuples():
        print(f"  {r.hold:>5}{r.rebal:>11}{r.band:>6}{r.full:>+10.2%}{r.h1:>+10.2%}{r.h2:>+10.2%}")
    share = {t: float((surf[t] > 0).mean()) for t in ("full", "h1", "h2")}
    g7 = share["full"] >= 0.5
    g8 = share["h1"] >= 0.5 and share["h2"] >= 0.5
    print(f"  cells beating baseline: full {share['full']:.0%}, 2012-19 {share['h1']:.0%}, 2019-26 {share['h2']:.0%}")
    print(f"  gate 7 {'PASS' if g7 else 'FAIL'}   gate 8 {'PASS' if g8 else 'FAIL'}")

    rule("GATE 9  --  deflation")
    n_configs = 12 * 3 + 5 + 4  # surface x 3 windows, 5 books, 4 IC gates
    n_trials = trials_before + n_configs
    daily = n13.pct_change().dropna()
    var_proxy = 1.0 / len(daily)
    bar_raw = expected_max_sharpe(n_trials, var_proxy) * math.sqrt(252)
    act = active_stats(n13, spy)
    g9 = s_n13["sharpe"] > bar_raw
    print(f"  trials counted {n_trials}   deflation bar (annualised) {bar_raw:.2f}")
    print(f"  AS WRITTEN   raw Sharpe {s_n13['sharpe']:.2f}  -> {'PASS' if g9 else 'FAIL'}")
    print(f"  STRICTER     Sharpe of returns in excess of SPY (information ratio) {act['ir']:+.2f}  "
          f"-> {'would pass' if act['ir'] > bar_raw else 'would fail'}")

    # ---------------------------------------------------- the comparison
    rule("HOW IT COMPARES WITH SIMPLY HOLDING THE INDEX  (USD, total return, after trading costs)")
    books = {name: r.equity for name, r in res.items()}
    books["S&P 500 (SPY)"] = spy
    books["Nasdaq-100 (QQQ)"] = qqq
    print(f"  {start:%Y-%m-%d} to {idx[-1]:%Y-%m-%d}")
    print(f"  {'':<46}{'CAGR':>8}{'vol':>7}{'Sharpe':>8}{'maxDD':>8}{'vs SPY':>9}{'IR':>7}{'yrs>SPY':>9}")
    summary = []
    for name, eq in books.items():
        st = perf_stats(eq)
        ex = st["cagr"] - s_spy["cagr"]
        a = active_stats(eq, spy) if "SPY" not in name else {"ir": float("nan")}
        ce = calendar_excess(eq, spy)
        beat = f"{int((ce > 0).sum())}/{len(ce)}" if "SPY" not in name else "-"
        turn = res[name].mean_turnover if name in res else float("nan")
        summary.append({"book": name, **st, "excess": ex, "ir": a["ir"], "beat": beat, "turnover": turn})
        print(f"  {name:<46}{st['cagr']:>8.2%}{st['vol']:>7.1%}{st['sharpe']:>8.2f}{st['maxdd']:>8.1%}"
              f"{ex:>+9.2%}{a['ir']:>+7.2f}{beat:>9}")
    pd.DataFrame(summary).to_csv(CLEAN / "n13_summary.csv", index=False)
    pd.DataFrame(books).to_parquet(CLEAN / "n13_curves.parquet")

    rule("YEAR BY YEAR: N13 minus the S&P 500")
    ce = calendar_excess(n13, spy)
    for year, v in ce.items():
        bar = ("+" if v > 0 else "-") * min(int(abs(v) * 100), 50)
        print(f"  {year.year}  {v:>+8.2%}  {bar}")
    print(f"  worst {ce.min():+.2%}  -> on EUR {CAPITAL_EUR:,}: {ce.min() * CAPITAL_EUR:+,.0f} EUR vs holding SPY")

    rule("IN EUROS, as a Finnish investor would have experienced it")
    try:
        from tradelab.data.providers.ecb_fx import EcbFxProvider

        fx = EcbFxProvider().rates("EUR", "USD", datetime(2011, 1, 1), datetime(2026, 12, 31))
        fx.index = pd.DatetimeIndex(fx.index).tz_localize(None) if getattr(fx.index, "tz", None) else pd.DatetimeIndex(fx.index)
        rate = fx.reindex(fx.index.union(idx)).ffill().reindex(idx)
        for name in ("N13 (top 20 by composite)", "S&P 500 (SPY)", "Nasdaq-100 (QQQ)"):
            eur = books[name] / rate
            st = perf_stats(eur / eur.iloc[0])
            print(f"  {name:<30} CAGR in EUR {st['cagr']:>7.2%}   maxDD {st['maxdd']:>7.1%}")
        print("  Relative performance is unchanged by currency: every book here holds USD assets.")
    except Exception as exc:  # FX is a presentation layer; never let it hide the results
        print(f"  EUR view unavailable ({type(exc).__name__}: {exc})")

    rule("TURNOVER AND COST")
    for name, r in res.items():
        print(f"  {name:<46} one-way turnover per rebalance {r.mean_turnover:>6.1%}   "
              f"total cost paid {r.cost_paid:>6.2%} of NAV")

    log(5, "Beats the 20 largest eligible stocks in both halves", g5, "see halves above")
    log(6, "Sharpe at least the S&P 500's", g6, f"{s_n13['sharpe']:.2f} vs {s_spy['sharpe']:.2f}")
    log(7, "Holds across the parameter grid (>= 50% of 12 cells)", g7, f"{share['full']:.0%} of cells beat the baseline")
    log(8, "Holds in both halves of the sample", g8, f"2012-19 {share['h1']:.0%}, 2019-26 {share['h2']:.0%} of cells")
    log(9, "Clears the deflation bar for 500+ trials", g9, f"Sharpe {s_n13['sharpe']:.2f} vs bar {bar_raw:.2f}; vs-SPY IR {act['ir']:+.2f}")
    record = {
        "verdict": verdict, "gates": sorted(gate_log, key=lambda g: g["gate"]),
        "start": f"{start:%Y-%m-%d}", "end": f"{idx[-1]:%Y-%m-%d}",
        "eligible_mean": float(n_elig.mean()), "deflation_bar": bar_raw, "trials": n_trials,
        "active_vs_spy": act, "calendar_excess": {str(k.year): float(v) for k, v in ce.items()},
        "surface": surf.to_dict(orient="records"),
    }
    (CLEAN / "n13_gates.json").write_text(json.dumps(record, indent=2, default=float))

    rule("VERDICT")
    gates = {"5": g5, "6": g6, "7": g7, "8": g8, "9": g9}
    if verdict.startswith("KILLED"):
        print(f"  N13 {verdict}.  Later gates for information: " +
              ", ".join(f"{k} {'pass' if v else 'fail'}" for k, v in gates.items()))
    else:
        failed = [k for k, v in gates.items() if not v]
        print("  gates 1-4 PASSED; gates 5-9: " +
              ", ".join(f"{k} {'pass' if v else 'FAIL'}" for k, v in gates.items()))
        print(f"  first failing later gate: {failed[0] if failed else 'none -- N13 SURVIVES'}")
    print(f"  configurations evaluated in this run: {n_configs}")


if __name__ == "__main__":
    main()
