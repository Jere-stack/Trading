#!/usr/bin/env python
"""N2: does a stock underperform the index after cutting its dividend?

Pre-registered in `research/ledger.jsonl` with kill criteria fixed before any
result was seen. This script is the test.

Everything is measured as abnormal return against SPY -- what the stock did
minus what the index did over the identical window -- because a strategy that
returns 8% while the index returns 11% has lost.

Run:  .venv/bin/python scripts/study_dividend_cuts.py
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.research.dividends import detect_dividend_cuts, detect_omissions
from tradelab.research.event_study import (
    Event,
    abnormal_returns,
    split_half_agreement,
    summarise_car,
)

HORIZONS = [5, 21, 63, 126, 252]
ROUND_TRIP_BPS = 45.0
"""Realistic round trip at EUR 10k: ~8 bps spread each way on a liquid name,
plus the IBKR commission floor on a ~EUR 1,000 position, plus slippage. The
cost report in docs/01 derives it; it is deliberately not optimistic."""


def load_bars(bar_dir: Path, symbols: set[str], min_dollar_volume: float) -> pd.DataFrame:
    """Load bars, keeping only names liquid enough to actually trade.

    The liquidity filter is not tidiness, it is what makes the measurement
    mean anything. A placebo of random dates across the full survivorship-free
    universe drifts -3.1% at 126 days with no event at all: the median small
    stock underperforms a cap-weighted index, so SPY is simply the wrong
    yardstick for a microcap. Split by liquidity, the same placebo runs +0.95%
    at 63 days in the top third and -3.8% in the middle third.

    Restricting to liquid names fixes the benchmark mismatch AND matches
    reality: at EUR 10k with a 45 bps cost budget, the illiquid two thirds were
    never tradable. A result measured where you cannot trade is not a result.
    """
    frames = []
    dropped = 0
    broken = 0
    for symbol in sorted(symbols):
        path = bar_dir / f"{symbol}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path, columns=["timestamp", "close", "volume"])
        if len(frame) < 250:
            dropped += 1
            continue
        dollar_volume = float((frame["close"] * frame["volume"]).median())
        if dollar_volume < min_dollar_volume:
            dropped += 1
            continue

        # Reject broken price series. 183 liquid symbols in this universe carry
        # a single-session move above +500% -- POW_OLD "goes" from $0.005 to
        # $15,600, a 312-million-percent jump -- which is a failed split
        # adjustment, not a price. One such series put +562% into a placebo
        # whose median was -0.67%.
        closes = frame["close"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            moves = np.abs(np.diff(closes) / closes[:-1])
        if not np.isfinite(closes).all() or (np.isfinite(moves) & (moves > 5.0)).any():
            broken += 1
            continue
        frame = frame[["timestamp", "close"]].copy()
        frame["symbol"] = symbol
        frames.append(frame)
    if not frames:
        raise SystemExit("no bars matched the event symbols")
    print(
        f"  {dropped:,} symbols dropped as untradable below "
        f"${min_dollar_volume:,.0f}/day median dollar volume"
    )
    return pd.concat(frames, ignore_index=True)


def show(title: str, results: list) -> None:
    print(f"\n{title}")
    print(
        f"  {'days':>5}{'n':>7}{'mean':>9}{'median':>9}{'t':>7}{'clustered t':>13}"
        f"{'months':>8}{'hit':>7}{'net@45bps':>11}"
    )
    for r in results:
        if r.n == 0:
            continue
        flag = "  <--" if r.is_significant else ""
        print(
            f"  {r.horizon:>5}{r.n:>7,}{r.mean:>9.2%}{r.trimmed_mean:>9.2%}"
            f"{r.median:>9.2%}{r.t_stat:>7.2f}{r.monthly_t:>13.2f}"
            f"{r.monthly_n:>8}{r.hit_rate:>7.0%}{flag}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument(
        "--dividends", type=Path, default=Path("data/dividends/us-universe/dividends.parquet")
    )
    parser.add_argument("--benchmark", type=Path, default=Path("data/bars/benchmarks/SPY.parquet"))
    parser.add_argument("--min-cut", type=float, default=0.25)
    parser.add_argument(
        "--min-dollar-volume",
        type=float,
        default=2_000_000,
        help="Median daily dollar volume required. Below this the name is not "
        "tradable at EUR 10k within the cost budget, and SPY is the wrong benchmark.",
    )
    args = parser.parse_args()

    print("=" * 78)
    print("N2  DIVIDEND CUT DRIFT  --  abnormal return vs SPY")
    print("=" * 78)

    divs = pd.read_parquet(args.dividends)
    bench = pd.read_parquet(args.benchmark)
    print(f"\n{len(divs):,} dividend records, {divs['symbol'].nunique():,} paying symbols")

    cuts = detect_dividend_cuts(divs, min_cut=args.min_cut, require_declaration=True)
    universe_end = divs["ex_date"].max()
    omissions = detect_omissions(divs, universe_end=universe_end)
    print(
        f"{len(cuts):,} declared cuts >= {args.min_cut:.0%}   {len(omissions):,} inferred omissions"
    )

    symbols = {c.symbol for c in cuts} | {o.symbol for o in omissions}
    bars = load_bars(args.bars, symbols, args.min_dollar_volume)
    print(
        f"{len(bars):,} bars loaded for {bars['symbol'].nunique():,} of "
        f"{len(symbols):,} event symbols"
    )

    # ---------------------------------------------------------------- placebo
    # Random dates through the identical pipeline. If alignment or the
    # benchmark join is wrong, drift shows up here where none can exist.
    rng = np.random.default_rng(11)
    pool = bars.groupby("symbol", observed=True)["timestamp"].agg(["min", "max"])
    placebo = []
    for symbol in rng.choice(pool.index.to_numpy(), size=min(2000, len(pool)), replace=True):
        low, high = pool.loc[symbol, "min"], pool.loc[symbol, "max"]
        span = (high - low).days
        if span < 500:
            continue
        placebo.append(
            Event(str(symbol), low + pd.Timedelta(days=int(rng.integers(0, span - 300))), "placebo")
        )
    placebo_panel = abnormal_returns(bars, placebo, bench, HORIZONS)
    show(
        f"PLACEBO  ({len(placebo):,} random dates -- must be flat)",
        [summarise_car(placebo_panel, h) for h in HORIZONS],
    )

    # ------------------------------------------------------------ declared cuts
    cut_events = [
        Event(c.symbol, c.event_date, "declared", c.cut_fraction)
        for c in cuts
        if not c.date_is_inferred
    ]
    panel = abnormal_returns(bars, cut_events, bench, HORIZONS)
    results = [summarise_car(panel, h) for h in HORIZONS]
    show(f"DECLARED CUTS  ({len(cut_events):,} events, declaration-dated)", results)

    # --------------------------------------------------------------- omissions
    omission_events = [Event(o.symbol, o.event_date, "omission") for o in omissions]
    omission_panel = abnormal_returns(bars, omission_events, bench, HORIZONS)
    show(
        f"INFERRED OMISSIONS  ({len(omission_events):,} events -- DATE IS INFERRED, kept separate)",
        [summarise_car(omission_panel, h) for h in HORIZONS],
    )

    # -------------------------------------------------------------- split half
    print("\nSPLIT-HALF AGREEMENT (declared cuts) -- a sign flip is a kill criterion")
    print(f"  {'days':>5}{'early n':>9}{'early':>9}{'late n':>9}{'late':>9}{'agree':>8}")
    for h in HORIZONS:
        early, late = split_half_agreement(panel, h)
        if early.n == 0 or late.n == 0:
            continue
        agree = "yes" if np.sign(early.mean) == np.sign(late.mean) else "NO"
        print(f"  {h:>5}{early.n:>9,}{early.mean:>9.2%}{late.n:>9,}{late.mean:>9.2%}{agree:>8}")

    # ------------------------------------------------------- cut-size gradient
    # A real mechanism should scale: a 90% cut ought to hurt more than a 30%
    # one. No gradient is evidence the signal is noise that happened to sort.
    print("\nCUT-SIZE GRADIENT at 63 days -- a real mechanism should scale")
    sixty_three = panel[panel["horizon"] == 63]
    if not sixty_three.empty:
        buckets = pd.cut(
            sixty_three["magnitude"],
            [0, 0.4, 0.6, 0.8, 1.01],
            labels=["25-40%", "40-60%", "60-80%", "80-100%"],
        )
        grouped = sixty_three.groupby(buckets, observed=True)["abnormal"]
        print(f"  {'cut size':>10}{'n':>7}{'mean CAR':>11}")
        for label, values in grouped:
            print(f"  {label!s:>10}{len(values):>7,}{values.mean():>11.2%}")

    print("\n" + "=" * 78)
    print("Benchmark to beat: SPY 10.85%/yr, QQQ 14.92%/yr (2004-2026)")
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
