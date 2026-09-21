#!/usr/bin/env python
"""N5-N8: the best price-only published strategies, tested against the index.

Selected from the replication literature rather than from a backtest list,
because the base rate for "strategies from papers" is dismal: Hou, Xue and
Zhang find 65% of 452 published anomalies fail a t>1.96 hurdle and 82% fail at
t>2.78, and paperswithbacktest's own corpus of 4,843 backtested papers has a
MEDIAN annualised return of 1.5%.

The four tested here are the price-only survivors of that filter:

  N5  low idiosyncratic volatility  -- strongest member of the low-risk family
                                       in 2025 replication work
  N6  low total volatility          -- the simpler cousin, as a control
  N7  cross-sectional momentum 12-1 -- the most-replicated return effect
  N8  52-week high proximity        -- George & Hwang anchoring

Every one is monthly, price-only, and long-only, so all four fit the mandate.
The bar is absolute return against SPY and QQQ, not Sharpe.

Run:  .venv/bin/python scripts/study_cross_section.py
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.research.cross_section import (
    run_backtest,
    signal_52_week_high,
    signal_low_idiosyncratic_vol,
    signal_low_volatility,
    signal_momentum_12_1,
)
from tradelab.research.screening import screen_price_series

COST_BPS = 45.0


def load_panel(bar_dir: Path, min_ever_liquid: float = 1_000_000):
    """Wide close and dollar-volume matrices for every plausibly tradable name.

    The `min_ever_liquid` pre-filter is only to keep the matrix in memory and
    is deliberately looser than the backtest's own point-in-time threshold, so
    it cannot decide which names are eligible on any given date.
    """
    closes, volumes = {}, {}
    rejected: dict[str, int] = {}
    scanned = kept = broken = 0
    for path in sorted(bar_dir.glob("*.parquet")):
        scanned += 1
        try:
            frame = pd.read_parquet(path, columns=["timestamp", "close", "volume"])
        except Exception:
            continue
        if len(frame) < 300:
            continue
        close = frame["close"].to_numpy(dtype=float)
        verdict = screen_price_series(close)
        if not verdict.ok:
            broken += 1
            rejected[verdict.reason.split("(")[0].strip()] = (
                rejected.get(verdict.reason.split("(")[0].strip(), 0) + 1
            )
            continue
        dollar = close * frame["volume"].to_numpy(dtype=float)
        if float(np.nanmedian(dollar)) < min_ever_liquid:
            continue
        stamps = pd.DatetimeIndex(frame["timestamp"])
        if stamps.tz is not None:
            stamps = stamps.tz_convert("UTC").tz_localize(None)
        symbol = path.stem
        closes[symbol] = pd.Series(close, index=stamps)
        volumes[symbol] = pd.Series(dollar, index=stamps)
        kept += 1
    print(f"  scanned {scanned:,} symbols, kept {kept:,}, rejected {broken:,}")
    for reason, count in sorted(rejected.items(), key=lambda kv: -kv[1]):
        print(f"    {count:>6,}  {reason}")
    close_panel = pd.DataFrame(closes).sort_index()
    volume_panel = pd.DataFrame(volumes).reindex(close_panel.index)
    return close_panel.astype("float32"), volume_panel.astype("float32")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--benchmarks", type=Path, default=Path("data/bars/benchmarks"))
    parser.add_argument("--hold", type=int, default=20)
    args = parser.parse_args()

    print("=" * 84)
    print("CROSS-SECTIONAL STRATEGIES  --  monthly, price-only, long-only, vs the index")
    print("=" * 84)
    print(
        f"\nCost: {COST_BPS:.0f} bps on the traded fraction each rebalance. Hold {args.hold} names."
    )
    print("Universe: point-in-time liquid, survivorship-free, prices screened.\n")

    closes, volumes = load_panel(args.bars)
    print(
        f"  panel {closes.shape[0]:,} dates x {closes.shape[1]:,} symbols "
        f"({closes.index.min():%Y-%m} to {closes.index.max():%Y-%m})"
    )

    benchmarks = {}
    for symbol in ("SPY", "QQQ"):
        frame = pd.read_parquet(args.benchmarks / f"{symbol}.parquet").sort_values("timestamp")
        stamps = pd.DatetimeIndex(frame["timestamp"])
        if stamps.tz is not None:
            stamps = stamps.tz_convert("UTC").tz_localize(None)
        benchmarks[symbol] = pd.Series(frame["close"].to_numpy(dtype=float), index=stamps)

    strategies = [
        ("N5 low idiosyncratic vol", signal_low_idiosyncratic_vol, True),
        ("N6 low total vol", signal_low_volatility, True),
        ("N7 momentum 12-1", signal_momentum_12_1, True),
        ("N8 52-week high", signal_52_week_high, True),
        ("   (control) high vol", signal_low_volatility, False),
        ("   (control) losers 12-1", signal_momentum_12_1, False),
    ]

    print(
        f"\n{'strategy':<26}{'CAGR':>8}{'vol':>7}{'Sharpe':>8}{'maxDD':>8}"
        f"{'turnover':>10}{'cost':>7}{'vs SPY':>9}{'vs QQQ':>9}"
    )
    results = []
    for name, fn, top in strategies:
        try:
            result = run_backtest(
                closes,
                volumes,
                benchmarks["SPY"],
                fn,
                name=name,
                n_hold=args.hold,
                cost_bps=COST_BPS,
                top=top,
            )
        except ValueError as exc:
            print(f"{name:<26}  skipped: {exc}")
            continue
        s = result.stats
        spy = result.benchmark_stats["cagr"]
        # QQQ over the identical dates, for a like-for-like comparison.
        q = benchmarks["QQQ"]
        first, last = result.equity.index[0], result.equity.index[-1]
        years = (last - first).days / 365.25
        qqq = (float(q.asof(last)) / float(q.asof(first))) ** (1 / years) - 1
        results.append((result, spy, qqq))
        print(
            f"{name:<26}{s['cagr']:>8.2%}{s['vol']:>7.1%}{s['sharpe']:>8.2f}"
            f"{s['maxdd']:>8.1%}{result.turnover:>10.0%}{result.cost_drag:>7.2%}"
            f"{s['cagr'] - spy:>+9.2%}{s['cagr'] - qqq:>+9.2%}"
        )

    if results:
        result, spy, qqq = results[0]
        first, last = result.equity.index[0], result.equity.index[-1]
        print(f"\n{'BUY & HOLD SPY':<26}{spy:>8.2%}")
        print(f"{'BUY & HOLD QQQ':<26}{qqq:>8.2%}")
        print(f"\n  {result.n_rebalances} monthly rebalances, {first:%Y-%m} to {last:%Y-%m}")

    print("\n" + "=" * 84)
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
