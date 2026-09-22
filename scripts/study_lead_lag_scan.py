#!/usr/bin/env python
"""N11: does ANYTHING in daily OHLCV lead the market? Scan first, build later.

The order is the point. N10 built a signal, validated it, tuned it, and only
then asked whether it led the market -- it followed by six months, and one
correlation chart on day one would have said so. This asks that question of
twelve pre-declared candidates before a single line of strategy is written.

DISCOVERY runs on 2011-2018 only. Survivors are tested once on 2019-2026,
which is examined for nothing else. Every candidate's profile is printed,
survivors and failures alike, because a scan that reports only its winners is
not a scan.

Run:  .venv/bin/python -m scripts.study_lead_lag_scan
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.research.board_hesitation import lead_lag_profile
from tradelab.research.market_state import (
    average_correlation,
    clv_breadth,
    gap_breadth,
    illiquidity,
    listing_churn,
    new_low_breadth,
    range_expansion,
    return_dispersion,
    return_skew,
    reversal_breadth,
    volume_concentration,
)
from tradelab.research.screening import screen_price_series
from tradelab.research.universe import survivorship_check, us_common_stocks

DISCOVERY_END = "2018-12-31"
MIN_PEAK = 0.15
"""A candidate advances only if its peak |correlation| clears this at a
positive lag. Set before looking at anything."""


def load_ohlcv(bar_dir: Path, reference: Path):
    """Wide open/high/low/close/dollar-volume panels for the clean universe."""
    symbols = pd.read_parquet(reference)
    universe = us_common_stocks(symbols)
    check = survivorship_check(symbols)
    gap = check.loc["live", "keep_rate"] - check.loc["delisted", "keep_rate"]
    print(f"  universe: {len(universe):,} US common stocks, survivorship gap {gap:+.1%}")
    if abs(gap) >= 0.06:
        raise SystemExit(f"universe filter is survival-biased ({gap:+.1%}); refusing to run")

    fields = {k: {} for k in ("open", "high", "low", "close", "dollar")}
    kept = rejected = 0
    for path in sorted(bar_dir.glob("*.parquet")):
        if path.stem.upper() not in universe:
            continue
        try:
            frame = pd.read_parquet(
                path,
                columns=["timestamp", "open", "high", "low", "close", "volume", "unadjusted_close"],
            )
        except Exception:
            continue
        close = frame["close"].to_numpy(dtype=float)
        if not screen_price_series(close).ok:
            rejected += 1
            continue
        stamps = pd.DatetimeIndex(frame["timestamp"])
        if stamps.tz is not None:
            stamps = stamps.tz_convert("UTC").tz_localize(None)
        volume = frame["volume"].to_numpy(dtype=float)
        symbol = path.stem
        for name in ("open", "high", "low", "close"):
            fields[name][symbol] = pd.Series(frame[name].to_numpy(dtype=float), index=stamps)
        # Conservative dollar volume: close is split-adjusted and volume is not
        # consistently so, and the two products err in opposite directions.
        fields["dollar"][symbol] = pd.Series(
            np.minimum(close * volume, frame["unadjusted_close"].to_numpy(dtype=float) * volume),
            index=stamps,
        )
        kept += 1
    print(f"  kept {kept:,} symbols, rejected {rejected:,} on price screening")
    panels = {k: pd.DataFrame(v).sort_index().astype("float32") for k, v in fields.items()}
    index = panels["close"].index
    return {k: v.reindex(index) for k, v in panels.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--reference", type=Path, default=Path("data/reference/us_symbols.parquet"))
    parser.add_argument("--benchmark", type=Path, default=Path("data/bars/benchmarks/SPY.parquet"))
    args = parser.parse_args()

    print("=" * 84)
    print("N11  LEAD-LAG SCAN  --  does anything here arrive BEFORE the market moves?")
    print("=" * 84)

    panels = load_ohlcv(args.bars, args.reference)
    o, h, low_, c, dollar = (panels[k] for k in ("open", "high", "low", "close", "dollar"))
    print(
        f"  panel {c.shape[0]:,} dates x {c.shape[1]:,} symbols "
        f"({c.index.min():%Y-%m} to {c.index.max():%Y-%m})"
    )

    spy = pd.read_parquet(args.benchmark).sort_values("timestamp")
    stamps = pd.DatetimeIndex(spy["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(spy["close"].to_numpy(dtype=float), index=stamps)

    print("\n  computing 12 pre-declared candidates...")
    listing_rate, delisting_rate = listing_churn(c)
    candidates = {
        "clv_breadth": clv_breadth(h, low_, c),
        "return_dispersion": return_dispersion(c),
        "return_skew": return_skew(c),
        "range_expansion": range_expansion(h, low_, c),
        "gap_breadth": gap_breadth(o, h, low_),
        "volume_concentration": volume_concentration(dollar),
        "new_low_breadth  [control]": new_low_breadth(c),
        "delisting_rate": delisting_rate,
        "listing_rate": listing_rate,
        "reversal_breadth": reversal_breadth(o, c),
        "avg_correlation  [control]": average_correlation(c),
        "illiquidity      [control]": illiquidity(c, dollar),
    }

    # ---------------------------------------------------------------- discovery
    print("\n" + "-" * 84)
    print(f"DISCOVERY  --  {c.index.min():%Y-%m} to {DISCOVERY_END[:7]} ONLY")
    print("-" * 84)
    print(
        f"  {'candidate':<28}{'peak lag':>10}{'peak corr':>11}{'best +lag':>11}"
        f"{'corr at +':>11}{'advance?':>10}"
    )

    survivors = []
    for name, series in candidates.items():
        window = series.loc[:DISCOVERY_END].dropna()
        if len(window) < 500:
            print(f"  {name:<28}{'too short':>10}")
            continue
        profile = lead_lag_profile(window, spy.loc[:DISCOVERY_END], months=12)
        if profile.empty:
            print(f"  {name:<28}{'no overlap':>10}")
            continue
        peak = profile.loc[profile["correlation"].abs().idxmax()]
        positive = profile[profile["lag_months"] > 0]
        best_positive = positive.loc[positive["correlation"].abs().idxmax()]
        advances = peak["lag_months"] > 0 and abs(peak["correlation"]) >= MIN_PEAK
        if advances:
            survivors.append((name, series, float(peak["lag_months"])))
        print(
            f"  {name:<28}{int(peak['lag_months']):>+10}{peak['correlation']:>+11.3f}"
            f"{int(best_positive['lag_months']):>+11}{best_positive['correlation']:>+11.3f}"
            f"{'YES' if advances else '':>10}"
        )

    print(
        f"\n  {len(survivors)} of {len(candidates)} advance "
        f"(peak at a POSITIVE lag with |corr| >= {MIN_PEAK})"
    )

    if not survivors:
        print("\n  *** KILL CRITERION MET ***")
        print("  Nothing peaks at a positive lag in the discovery half. Every candidate")
        print("  either tracks the market contemporaneously or follows it. The")
        print("  out-of-sample half is left untouched, so it stays clean for future work.")
    else:
        # ------------------------------------------------------- out of sample
        print("\n" + "-" * 84)
        print(f"OUT OF SAMPLE  --  {DISCOVERY_END[:7]} onward, examined for nothing else")
        print("-" * 84)
        print(f"  {'candidate':<28}{'lag':>6}{'in-sample':>12}{'out-sample':>12}{'holds?':>9}")
        for name, series, lag in survivors:
            window = series.loc[DISCOVERY_END:].dropna()
            profile = lead_lag_profile(window, spy.loc[DISCOVERY_END:], months=12)
            if profile.empty:
                continue
            row = profile[profile["lag_months"] == lag]
            if row.empty:
                continue
            out_corr = float(row.iloc[0]["correlation"])
            in_profile = lead_lag_profile(
                series.loc[:DISCOVERY_END].dropna(), spy.loc[:DISCOVERY_END], months=12
            )
            in_corr = float(in_profile[in_profile["lag_months"] == lag].iloc[0]["correlation"])
            holds = np.sign(out_corr) == np.sign(in_corr) and abs(out_corr) >= 0.10
            print(
                f"  {name:<28}{int(lag):>+6}{in_corr:>+12.3f}{out_corr:>+12.3f}"
                f"{'YES' if holds else 'no':>9}"
            )

    print("\n" + "=" * 84)
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
