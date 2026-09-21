#!/usr/bin/env python
"""N4: can a simple trend rule beat buy-and-hold on the index itself?

Every other candidate in the register dies on cost. This one is different, and
the difference is the whole reason to test it: a rule that trades the index two
to four times a year pays roughly **10 bps a year** in total, against the 45 bps
*per trade* that kills stock-level strategies at EUR 10,000. An edge can be
largely decayed and still survive that.

The rule is deliberately the oldest and most-published one there is -- price
versus its own moving average -- because the point is not to discover it. The
point is to measure honestly whether it beats buy-and-hold **after costs, out
of sample, across parameters**, when the benchmark is a 10.85%/yr SPY and a
14.92%/yr QQQ.

What would make it a real finding:
  * beats buy-and-hold on return AND drawdown, not just drawdown
  * holds across a wide band of lookbacks, not at one tuned value
  * holds in the second half of the sample, not only through 2008
  * survives a realistic cost and a realistic execution lag

What would make it a mirage, and is the expected outcome:
  * all of the advantage comes from 2008, and the last fifteen years are a
    steady bleed of whipsaw against a rising market

Run:  .venv/bin/python scripts/study_index_timing.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

COST_BPS = 10.0
"""One-way cost of switching the whole book between the ETF and cash: spread
plus commission on a EUR 10k ticket. Deliberately not optimistic."""


def load(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path).sort_values("timestamp").reset_index(drop=True)
    frame["close"] = frame["close"].astype(float)
    return frame


def stats(equity: np.ndarray, days: float) -> dict:
    years = days / 365.25
    total = equity[-1] / equity[0]
    returns = np.diff(equity) / equity[:-1]
    vol = returns.std(ddof=1) * np.sqrt(252) if returns.size > 1 else 0.0
    peak = np.maximum.accumulate(equity)
    cagr = total ** (1 / years) - 1 if years > 0 else 0.0
    return {
        "cagr": cagr,
        "vol": vol,
        "sharpe": cagr / vol if vol > 0 else 0.0,
        "maxdd": float((equity / peak - 1).min()),
    }


def run_rule(frame: pd.DataFrame, lookback: int, *, lag: int = 1, cost_bps: float = COST_BPS):
    """Hold the ETF when its close is above its own N-day average, else cash.

    `lag=1` acts on the *next* session's close. Acting on the same close that
    generated the signal is the single most common way a timing backtest
    manufactures returns it could never have earned.
    """
    close = frame["close"].to_numpy(dtype=float)
    average = pd.Series(close).rolling(lookback).mean().to_numpy()
    signal = np.where(close > average, 1.0, 0.0)
    signal[: lookback - 1] = np.nan

    position = np.roll(signal, lag)
    position[:lag] = np.nan
    valid = ~np.isnan(position)
    position = position[valid]
    prices = close[valid]

    market = np.diff(prices) / prices[:-1]
    held = position[:-1]
    switches = np.abs(np.diff(position))
    strategy = held * market - switches * cost_bps / 10_000.0

    equity = np.concatenate([[1.0], np.cumprod(1.0 + strategy)])
    buy_hold = np.concatenate([[1.0], np.cumprod(1.0 + market)])
    stamps = frame["timestamp"].to_numpy()[valid]
    days = (pd.Timestamp(stamps[-1]) - pd.Timestamp(stamps[0])).days
    return equity, buy_hold, days, int(switches.sum()), float(held.mean())


def show(title: str, frame: pd.DataFrame, lookbacks: list[int]) -> pd.DataFrame:
    print(f"\n{title}")
    print(
        f"  {'lookback':>9}{'CAGR':>9}{'vol':>8}{'Sharpe':>8}{'maxDD':>9}"
        f"{'trades':>8}{'in mkt':>8}{'vs B&H':>9}"
    )
    _, buy_hold, days, _, _ = run_rule(frame, lookbacks[0])
    base = stats(buy_hold, days)
    rows = []
    for lookback in lookbacks:
        equity, buy_hold, days, switches, exposure = run_rule(frame, lookback)
        s = stats(equity, days)
        b = stats(buy_hold, days)
        delta = s["cagr"] - b["cagr"]
        rows.append({"lookback": lookback, **s, "delta": delta, "trades": switches})
        print(
            f"  {lookback:>9}{s['cagr']:>9.2%}{s['vol']:>8.1%}{s['sharpe']:>8.2f}"
            f"{s['maxdd']:>9.1%}{switches:>8}{exposure:>8.0%}{delta:>+9.2%}"
        )
    print(
        f"  {'BUY & HOLD':>9}{base['cagr']:>9.2%}{base['vol']:>8.1%}{base['sharpe']:>8.2f}"
        f"{base['maxdd']:>9.1%}{0:>8}{1.0:>8.0%}{0.0:>+9.2%}"
    )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("data/bars/benchmarks"))
    args = parser.parse_args()

    lookbacks = [50, 100, 150, 200, 250, 300]
    print("=" * 78)
    print("N4  INDEX TREND TIMING  --  does it beat simply holding the thing?")
    print("=" * 78)
    print(f"\nCost: {COST_BPS:.0f} bps per switch. Signal acted on the NEXT session's close.")

    for symbol in ("SPY", "QQQ"):
        frame = load(args.dir / f"{symbol}.parquet")
        table = show(
            f"{symbol}  full sample {frame['timestamp'].min():%Y-%m}"
            f" to {frame['timestamp'].max():%Y-%m}",
            frame,
            lookbacks,
        )

        # The question that decides it: is the advantage all from one crisis?
        midpoint = len(frame) // 2
        for label, part in (
            ("FIRST HALF", frame.iloc[:midpoint]),
            ("SECOND HALF", frame.iloc[midpoint:].reset_index(drop=True)),
        ):
            show(
                f"{symbol}  {label}  {part['timestamp'].min():%Y-%m}"
                f" to {part['timestamp'].max():%Y-%m}",
                part,
                lookbacks,
            )

        wins = int((table["delta"] > 0).sum())
        print(
            f"\n  {symbol}: beats buy-and-hold on return at {wins}/{len(table)} lookbacks; "
            f"median edge {table['delta'].median():+.2%}/yr"
        )

    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()
