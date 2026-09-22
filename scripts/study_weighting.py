#!/usr/bin/env python
"""Is the universe handicap a weighting choice rather than a law of nature?

Round 3's most consequential finding was the UNIVERSE HANDICAP: a random 20
names, equal-weighted and rebalanced monthly, returned 6.21%/yr against SPY's
14.87% -- a deficit of 8.66 points before any signal is applied. And it was not
a size effect: a random 20 drawn from large caps only still lost 8.68%.

That framing left one thing untested. SPY is not merely a basket of large caps;
it is a CAP-WEIGHTED basket. In 2012-2026 the return was concentrated in a
handful of mega-caps, and equal-weighting systematically underweights exactly
those names. If the handicap is a property of the WEIGHTING rather than of the
selection, then every strategy this project has rejected was carrying an
8-point penalty that had nothing to do with its signal -- and the bar for a
future strategy falls from roughly 12% gross alpha to roughly 3%.

That is worth more than another signal, so it is tested first.

Market capitalisation is not in the reference data, so trailing dollar volume
is the size proxy. It is imperfect -- a high-turnover mid-cap can out-trade a
sleepy mega-cap -- which biases this test AGAINST the hypothesis: a noisier
proxy closes less of the gap than true cap weighting would. Any gap it does
close is therefore a lower bound.

Run:  .venv/bin/python -m scripts.study_weighting
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.study_cross_section import load_panel
from tradelab.research.cross_section import month_end_dates

N_HOLD = 20
MIN_DOLLAR_VOLUME = 2_000_000
LIQUIDITY_WINDOW = 60
LOOKBACK = 252
COST_BPS = 33.1
LARGE_CAP_N = 500

SCHEMES = ("equal", "sqrt(dv)", "dv capped 25%", "dollar-volume")


@dataclass(frozen=True, slots=True)
class Rebalance:
    """Everything about one rebalance that does not depend on which names are drawn.

    Precomputing this is the whole performance story: the eligibility median is
    a 60-session pass over 5,000 symbols, it is identical for every random draw
    and every weighting scheme, and computing it inside the draw loop made the
    study 300x slower than it needed to be.
    """

    stamp: pd.Timestamp
    symbols: np.ndarray
    size: np.ndarray
    """Trailing median dollar volume, the size proxy."""
    holding_return: np.ndarray
    """Return from this rebalance close to the next, per symbol."""
    large_cap_mask: np.ndarray


def precompute(closes: pd.DataFrame, volumes: pd.DataFrame) -> list[Rebalance]:
    dates = closes.index
    rebalances = [d for d in month_end_dates(dates) if d >= dates[LOOKBACK]]
    out: list[Rebalance] = []

    for i, date in enumerate(rebalances[:-1]):
        nxt = rebalances[i + 1]
        position = dates.get_loc(date)
        end = dates.get_loc(nxt)

        recent = volumes.iloc[max(0, position - LIQUIDITY_WINDOW) : position + 1]
        median_dv = recent.median(skipna=True)
        liquid = median_dv >= MIN_DOLLAR_VOLUME
        priced_now = closes.iloc[position].notna()
        priced_then = closes.iloc[position + 1 : end + 1].notna().any()
        eligible = closes.columns[liquid & priced_now & priced_then]
        if len(eligible) < N_HOLD * 2:
            continue

        entry = closes.iloc[position][eligible]
        # A name that stops trading inside the window is liquidated at its last
        # observed price, never dropped -- dropping it would delete exactly the
        # failures survivorship bias is about.
        exit_price = closes.iloc[position : end + 1][eligible].ffill().iloc[-1]
        ret = (exit_price / entry - 1.0).to_numpy(dtype=float)
        size = median_dv[eligible].to_numpy(dtype=float)

        ok = np.isfinite(ret) & np.isfinite(size) & (size > 0)
        if ok.sum() < N_HOLD * 2:
            continue
        symbols = np.asarray(eligible)[ok]
        size, ret = size[ok], ret[ok]

        cutoff = LARGE_CAP_N if len(size) > LARGE_CAP_N else len(size)
        threshold = np.partition(size, len(size) - cutoff)[len(size) - cutoff]
        out.append(
            Rebalance(
                stamp=nxt,
                symbols=symbols,
                size=size,
                holding_return=ret,
                large_cap_mask=size >= threshold,
            )
        )
    return out


def weights_for(scheme: str, size: np.ndarray) -> np.ndarray:
    """Portfolio weights from a size proxy, normalised to sum to one."""
    if scheme == "equal":
        return np.full(len(size), 1.0 / len(size))
    if scheme == "dollar-volume":
        return size / size.sum()
    if scheme == "sqrt(dv)":
        w = np.sqrt(size)
        return w / w.sum()
    if scheme == "dv capped 25%":
        w = size / size.sum()
        for _ in range(64):
            over = w > 0.25
            if not over.any():
                break
            excess = float((w[over] - 0.25).sum())
            w = np.where(over, 0.25, w)
            room = ~over
            if not room.any() or w[room].sum() <= 0:
                break
            w = np.where(room, w + excess * w / w[room].sum(), w)
        return w / w.sum()
    raise ValueError(scheme)


def run_draws(
    book: list[Rebalance],
    *,
    scheme: str,
    large_cap_only: bool,
    seeds: int,
    pool: int | None = None,
) -> np.ndarray:
    """CAGR of `seeds` randomly selected, `scheme`-weighted monthly books.

    `pool` restricts each draw to the largest `pool` eligible names by the size
    proxy. At `pool == N_HOLD` the draw is deterministic -- the book simply IS
    the largest twenty names -- which is what makes the concentration sweep
    interpretable at its left edge.
    """
    results = np.empty(seeds, dtype=float)
    years = (book[-1].stamp - book[0].stamp).days / 365.25

    for s in range(seeds):
        rng = np.random.default_rng(s)
        equity = 1.0
        for reb in book:
            idx = np.flatnonzero(reb.large_cap_mask) if large_cap_only else np.arange(len(reb.size))
            if pool is not None and idx.size > pool:
                order = np.argsort(reb.size[idx])[::-1]
                idx = idx[order[:pool]]
            if idx.size < N_HOLD:
                continue
            pick = rng.choice(idx, size=N_HOLD, replace=False)
            w = weights_for(scheme, reb.size[pick])
            gross = float((w * reb.holding_return[pick]).sum())
            # A random book replaces itself every month: turnover ~100%.
            equity *= 1.0 + gross - COST_BPS / 10_000.0
            if equity <= 0:
                equity = 1e-9
                break
        results[s] = equity ** (1 / years) - 1
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--benchmarks", type=Path, default=Path("data/bars/benchmarks"))
    parser.add_argument("--seeds", type=int, default=200)
    args = parser.parse_args()

    print("=" * 86, flush=True)
    print("IS THE UNIVERSE HANDICAP A WEIGHTING CHOICE?", flush=True)
    print("=" * 86, flush=True)

    closes, volumes = load_panel(args.bars)
    print(f"  panel {closes.shape[0]:,} x {closes.shape[1]:,}", flush=True)
    print("  precomputing rebalances...", flush=True)
    book = precompute(closes, volumes)
    print(f"  {len(book)} rebalances, {book[0].stamp:%Y-%m} to {book[-1].stamp:%Y-%m}", flush=True)

    frame = pd.read_parquet(args.benchmarks / "SPY.parquet").sort_values("timestamp")
    stamps = pd.DatetimeIndex(frame["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(frame["close"].to_numpy(dtype=float), index=stamps)
    years = (book[-1].stamp - book[0].stamp).days / 365.25
    spy_cagr = (float(spy.asof(book[-1].stamp)) / float(spy.asof(book[0].stamp))) ** (
        1 / years
    ) - 1

    print(f"\n  SPY over the identical window: {spy_cagr:.2%}/yr", flush=True)
    print(f"  {args.seeds} random draws per cell, {N_HOLD} names, {COST_BPS:.0f} bps\n", flush=True)

    print(
        f"  {'universe':<16}{'weighting':<16}{'mean':>9}{'median':>9}"
        f"{'vs SPY':>10}{'p5':>9}{'p95':>9}{'beat SPY':>10}",
        flush=True,
    )
    print("  " + "-" * 82, flush=True)

    for large_only in (False, True):
        label = f"top {LARGE_CAP_N} by DV" if large_only else "all liquid"
        for scheme in SCHEMES:
            r = run_draws(book, scheme=scheme, large_cap_only=large_only, seeds=args.seeds)
            print(
                f"  {label:<16}{scheme:<16}{r.mean():>9.2%}{np.median(r):>9.2%}"
                f"{r.mean() - spy_cagr:>+10.2%}{np.percentile(r, 5):>9.2%}"
                f"{np.percentile(r, 95):>9.2%}{(r > spy_cagr).mean():>10.0%}",
                flush=True,
            )
        print(flush=True)

    # ------------------------------------------------ concentration sweep
    # Weighting did not close the gap, which points at a different explanation:
    # SPY's return came from a handful of mega-caps, and a 20-name draw from a
    # wide universe almost never holds them. If that is right, the deficit
    # should shrink monotonically as the draw pool narrows toward the top --
    # and at pool = 20 the "draw" is deterministic, so the row below is simply
    # the return of owning the twenty largest names.
    print("=" * 86, flush=True)
    print("CONCENTRATION SWEEP -- how much of the gap is about missing the mega-caps?", flush=True)
    print("=" * 86, flush=True)
    print(
        f"\n  {'draw pool':<18}{'weighting':<16}{'mean':>9}{'vs SPY':>10}"
        f"{'p5':>9}{'p95':>9}{'beat SPY':>10}",
        flush=True,
    )
    print("  " + "-" * 72, flush=True)

    for pool in (20, 30, 50, 100, 200, 500):
        for scheme in ("equal", "dv capped 25%"):
            r = run_draws(
                book, scheme=scheme, large_cap_only=False, seeds=args.seeds, pool=pool
            )
            print(
                f"  {f'top {pool} by DV':<18}{scheme:<16}{r.mean():>9.2%}"
                f"{r.mean() - spy_cagr:>+10.2%}{np.percentile(r, 5):>9.2%}"
                f"{np.percentile(r, 95):>9.2%}{(r > spy_cagr).mean():>10.0%}",
                flush=True,
            )
        print(flush=True)

    print("=" * 86, flush=True)
    print("A deficit that shrinks as the pool narrows is a CONCENTRATION effect:", flush=True)
    print("the index's return lives in names a small draw usually misses. A", flush=True)
    print("deficit that stays flat is something else, and worse -- it would mean", flush=True)
    print("small-N portfolios lose for reasons no selection rule can fix.", flush=True)
    print("=" * 86, flush=True)


if __name__ == "__main__":
    main()
