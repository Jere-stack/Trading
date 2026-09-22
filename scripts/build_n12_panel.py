#!/usr/bin/env python
"""Cache the OHLCV panel N12 is tested on, restricted to the concentrated universe.

M3 established that the alpha bar is a property of the universe: a signal
selecting from 5,000 names starts ~7 points behind SPY, the same signal
selecting within the top 30-50 by dollar volume starts 1-5 points behind. N12
is therefore tested inside the concentrated universe, which also makes the
panel small enough to iterate on.

THE RESTRICTION IS AN OPTIMISATION, NOT A FILTER ON OUTCOMES. A name is kept
if it was ever inside the top `keep_rank` by TRAILING dollar volume at any
month-end. Selection at test time takes the top 50-100 as known on that date,
so a name that never reached the top `keep_rank` could never have been picked
on any date. The kept set is a strict superset of everything selectable, and
the resulting selections are identical to running on the full panel.

What it is NOT is a claim that the restriction is free of survivorship: it is
not a forward-looking filter (trailing volume only), but it does mean the panel
cannot be used to study names outside the liquid core. It is cached for N12's
question, not as a general-purpose panel.

Run:  .venv/bin/python -m scripts.build_n12_panel
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.study_cross_section import load_panel, tradable_universe
from tradelab.research.cross_section import month_end_dates
from tradelab.research.screening import screen_price_series

LIQUIDITY_WINDOW = 60
LOOKBACK = 252
KEEP_RANK = 200
FIELDS = ("high", "low", "close", "dollar_volume")


def ever_top_ranked(closes: pd.DataFrame, volumes: pd.DataFrame, keep_rank: int) -> list[str]:
    """Names that reached the top `keep_rank` by trailing dollar volume, ever.

    Trailing only -- the rank at each date uses the 60 sessions ending there,
    so nothing about a later period decides membership at an earlier one.
    """
    dates = closes.index
    rebalances = [d for d in month_end_dates(dates) if d >= dates[LOOKBACK]]
    keep: set[str] = set()
    for date in rebalances:
        position = dates.get_loc(date)
        recent = volumes.iloc[max(0, position - LIQUIDITY_WINDOW) : position + 1]
        median_dv = recent.median(skipna=True).dropna()
        if median_dv.empty:
            continue
        keep.update(median_dv.nlargest(min(keep_rank, len(median_dv))).index)
    return sorted(keep)


def load_ohlc(bar_dir: Path, symbols: list[str], index: pd.DatetimeIndex):
    """High and low panels for `symbols`, aligned to `index`.

    Re-screens each series: the close-based screen that qualified a symbol in
    the first pass says nothing about whether its high/low are sane, and a
    corrupt range makes the close-location value meaningless rather than merely
    noisy.
    """
    highs, lows = {}, {}
    wanted = set(symbols)
    broken = 0
    for path in sorted(bar_dir.glob("*.parquet")):
        if path.stem not in wanted:
            continue
        frame = pd.read_parquet(path, columns=["timestamp", "high", "low", "close"])
        stamps = pd.DatetimeIndex(frame["timestamp"])
        if stamps.tz is not None:
            stamps = stamps.tz_convert("UTC").tz_localize(None)
        high = frame["high"].to_numpy(dtype=float)
        low = frame["low"].to_numpy(dtype=float)
        close = frame["close"].to_numpy(dtype=float)
        # A high below its own low, or a close outside the range, is corrupt
        # data rather than an unusual session.
        with np.errstate(invalid="ignore"):
            inconsistent = np.nansum((high < low) | (close > high * 1.001) | (close < low * 0.999))
        if inconsistent > len(high) * 0.01 or not screen_price_series(high).ok:
            broken += 1
            continue
        highs[path.stem] = pd.Series(high, index=stamps)
        lows[path.stem] = pd.Series(low, index=stamps)
    print(f"  loaded OHLC for {len(highs):,} symbols, rejected {broken:,} on range consistency")
    return (
        pd.DataFrame(highs).reindex(index).astype("float32"),
        pd.DataFrame(lows).reindex(index).astype("float32"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--out", type=Path, default=Path("data/signals/n12"))
    parser.add_argument("--keep-rank", type=int, default=KEEP_RANK)
    args = parser.parse_args()

    print("=" * 80)
    print("BUILDING THE N12 PANEL")
    print("=" * 80)

    tradable_universe()
    closes, volumes = load_panel(args.bars)
    print(f"  full panel {closes.shape[0]:,} x {closes.shape[1]:,}")

    keep = ever_top_ranked(closes, volumes, args.keep_rank)
    print(f"  ever inside top {args.keep_rank} by trailing DV: {len(keep):,} symbols")

    high, low = load_ohlc(args.bars, keep, closes.index)
    common = sorted(set(high.columns) & set(closes.columns))
    panels = {
        "high": high[common],
        "low": low[common],
        "close": closes[common],
        "dollar_volume": volumes[common],
    }

    args.out.mkdir(parents=True, exist_ok=True)
    for name, frame in panels.items():
        frame.to_parquet(args.out / f"{name}.parquet")
        print(f"  wrote {name:<14} {frame.shape[0]:,} x {frame.shape[1]:,}")

    coverage = panels["close"].notna().mean().mean()
    print(f"\n  mean per-symbol coverage {coverage:.1%}")
    print(f"  cached to {args.out}/")
    print("=" * 80)


if __name__ == "__main__":
    main()
