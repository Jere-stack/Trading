#!/usr/bin/env python
"""N3: do stocks outperform the index after initiating their first dividend?

The mirror of N2 and the better fit for a long-only mandate. A cut is a
forced-*seller* story you can act on only by not holding. An initiation is a
forced-*buyer* story: funds with an income mandate may hold only dividend
payers, so a company becoming one is mechanically bought by a class of
investor previously barred from owning it -- and they buy on the calendar, not
on the price.

Same measurement discipline as N2: abnormal return against SPY, entry at the
close of the first session after the declaration, a clustered t-statistic, a
placebo, and a liquidity screen without which the benchmark is simply wrong
for the names being measured.

Run:  .venv/bin/python scripts/study_dividend_initiations.py
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.research.dividends import detect_initiations
from tradelab.research.event_study import (
    Event,
    abnormal_returns,
    split_half_agreement,
    summarise_car,
)

HORIZONS = [5, 21, 63, 126, 252]
ROUND_TRIP_BPS = 45.0


def load_bars(bar_dir: Path, symbols: set[str], min_dollar_volume: float):
    """Returns (bars, first_bar_date_by_symbol). Screens liquidity and bad prices."""
    frames, first_bar = [], {}
    dropped = broken = 0
    for symbol in sorted(symbols):
        path = bar_dir / f"{symbol}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path, columns=["timestamp", "close", "volume"])
        if len(frame) < 250:
            dropped += 1
            continue
        if float((frame["close"] * frame["volume"]).median()) < min_dollar_volume:
            dropped += 1
            continue
        closes = frame["close"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            moves = np.abs(np.diff(closes) / closes[:-1])
        if not np.isfinite(closes).all() or (np.isfinite(moves) & (moves > 5.0)).any():
            broken += 1
            continue
        first_bar[symbol] = frame["timestamp"].min()
        frame = frame[["timestamp", "close"]].copy()
        frame["symbol"] = symbol
        frames.append(frame)
    if not frames:
        raise SystemExit("no tradable bars matched")
    print(f"  {dropped:,} dropped as untradable; {broken:,} dropped for impossible prices")
    return pd.concat(frames, ignore_index=True), first_bar


def show(title: str, results: list) -> None:
    print(f"\n{title}")
    print(
        f"  {'days':>5}{'n':>7}{'mean':>9}{'trimmed':>9}{'median':>9}{'t':>7}"
        f"{'clustered t':>13}{'months':>8}{'hit':>7}"
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
    parser.add_argument("--min-dollar-volume", type=float, default=2_000_000)
    args = parser.parse_args()

    print("=" * 78)
    print("N3  DIVIDEND INITIATION DRIFT  --  abnormal return vs SPY")
    print("=" * 78)

    divs = pd.read_parquet(args.dividends)
    bench = pd.read_parquet(args.benchmark)
    symbols = set(divs["symbol"].unique())
    bars, first_bar = load_bars(args.bars, symbols, args.min_dollar_volume)
    print(f"  {bars['symbol'].nunique():,} tradable symbols, {len(bars):,} bars")

    # Without first_bar, a company whose data begins mid-stream is counted as
    # initiating when it was already paying. Reporting both makes the size of
    # that contamination visible instead of assumed.
    naive = detect_initiations(divs, first_bar=None)
    real = detect_initiations(divs, first_bar=first_bar, min_history_days=730)
    print(
        f"  {len(naive):,} first-observed dividends, of which {len(real):,} are genuine "
        f"initiations\n    ({len(naive) - len(real):,} discarded: the company may have "
        "been paying before its data begins)"
    )

    rng = np.random.default_rng(23)
    pool = bars.groupby("symbol", observed=True)["timestamp"].agg(["min", "max"])
    placebo = []
    for symbol in rng.choice(pool.index.to_numpy(), size=min(2500, len(pool)), replace=True):
        low, high = pool.loc[symbol, "min"], pool.loc[symbol, "max"]
        span = (high - low).days
        if span < 600:
            continue
        placebo.append(
            Event(str(symbol), low + pd.Timedelta(days=int(rng.integers(0, span - 400))))
        )
    placebo_panel = abnormal_returns(bars, placebo, bench, HORIZONS)
    show(
        f"PLACEBO  ({len(placebo):,} random dates -- the baseline everything is read against)",
        [summarise_car(placebo_panel, h) for h in HORIZONS],
    )

    events = [Event(i.symbol, i.event_date, "initiation") for i in real]
    panel = abnormal_returns(bars, events, bench, HORIZONS)
    results = [summarise_car(panel, h) for h in HORIZONS]
    show(f"DIVIDEND INITIATIONS  ({len(events):,} events, declaration-dated)", results)

    print("\nEXCESS OVER THE PLACEBO BASELINE -- the only comparison that means anything")
    print(f"  {'days':>5}{'initiation':>12}{'placebo':>10}{'excess':>9}{'net@45bps':>11}")
    for h in HORIZONS:
        init = summarise_car(panel, h)
        base = summarise_car(placebo_panel, h)
        if init.n == 0:
            continue
        excess = init.median - base.median
        print(
            f"  {h:>5}{init.median:>12.2%}{base.median:>10.2%}{excess:>9.2%}"
            f"{excess - ROUND_TRIP_BPS / 10_000:>11.2%}"
        )

    print("\nSPLIT-HALF AGREEMENT -- a sign flip is a kill criterion")
    print(f"  {'days':>5}{'early n':>9}{'early':>9}{'late n':>9}{'late':>9}{'agree':>8}")
    for h in HORIZONS:
        early, late = split_half_agreement(panel, h)
        if early.n == 0 or late.n == 0:
            continue
        agree = "yes" if np.sign(early.median) == np.sign(late.median) else "NO"
        print(f"  {h:>5}{early.n:>9,}{early.median:>9.2%}{late.n:>9,}{late.median:>9.2%}{agree:>8}")

    print("\n" + "=" * 78)
    print("Benchmark to beat: SPY 10.85%/yr, QQQ 14.92%/yr (2004-2026)")
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
