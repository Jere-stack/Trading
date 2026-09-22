#!/usr/bin/env python
"""The definitive test on N12: an empirical null, not a parametric t-statistic.

The post-mortem in scripts/run_n12.py produced a result that looked like a
survivor and a warning that it was not. The mean close-location value over 21
sessions scored IC +0.0309 at t +2.74 -- and two of three shuffled placebos
scored t +2.01 and t +2.61.

That is not a near miss. It says the NULL DISTRIBUTION OF THIS STATISTIC IS
MUCH WIDER THAN ITS t-STATISTIC CLAIMS, so no threshold applied to that t can
separate signal from noise. The parametric standard error -- even corrected to
the effective sample size -- assumes away the structure that makes this panel
what it is.

WHY THE PLACEBO BEHAVES THIS WAY, which is the real finding:

  Shuffling a stock's close-location values in time preserves that stock's
  LONG-RUN MEAN close-location. A name that habitually settles near its highs
  still does after shuffling. A 21-session rolling mean of a shuffled series
  therefore converges on a STOCK-LEVEL CHARACTERISTIC rather than being
  destroyed -- so the placebo removes the timing and leaves the level, and
  whatever the level is worth survives it.

  That makes the shuffle a test of TIMING information only. It was designed as
  a test of the whole construct, and for the footprint -- which is dominated by
  when elevation occurs -- it is. For a rolling mean it is not. The design flaw
  is recorded rather than patched over, because the same flaw would silently
  flatter any future signal built as a trailing average.

So the statistic is positioned inside its own empirical null: run the shuffle
many times, collect the distribution of ICs it produces, and ask what fraction
of them beat the real one. That is a permutation test, it assumes nothing about
standard errors, and it is the number that decides.

Run:  .venv/bin/python -m scripts.n12_permutation_test
"""

from __future__ import annotations

import argparse

import numpy as np

from scripts.run_n12 import LOOKBACK, eligible_mask, load_panels, restrict
from tradelab.research.accumulation import (
    FootprintParams,
    accumulation_footprint,
    close_location_value,
    shuffle_clv,
    shuffle_volume,
)
from tradelab.research.cross_section import month_end_dates
from tradelab.research.ic import forward_returns, information_coefficient, summarise_ic


def empirical_p(real: float, null: np.ndarray) -> float:
    """Two-sided empirical p: share of null draws at least as extreme as real.

    The +1 in numerator and denominator is the standard finite-sample
    correction. Without it a permutation test can report p = 0, which claims
    more certainty than the number of permutations can support.
    """
    extreme = int(np.sum(np.abs(null) >= abs(real)))
    return (extreme + 1) / (len(null) + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=50)
    args = parser.parse_args()

    print("=" * 86, flush=True)
    print("N12  PERMUTATION TEST  --  positioning the statistic in its own null", flush=True)
    print("=" * 86, flush=True)

    panels = load_panels()
    closes = panels["close"]
    dates = [d for d in month_end_dates(closes.index) if d >= closes.index[LOOKBACK]]
    mask = eligible_mask(closes, panels["dollar_volume"], dates)
    fwd = forward_returns(closes, dates)
    base = FootprintParams()
    clv = close_location_value(panels["high"], panels["low"], closes)

    print(f"\n  {len(dates)} rebalances, {mask.sum(axis=1).mean():.0f} names per date")
    print(f"  {args.draws} permutations per statistic\n")

    # ---------------------------------------------------- the two statistics
    footprint = restrict(
        accumulation_footprint(
            panels["high"], panels["low"], closes, panels["dollar_volume"], base
        ),
        mask,
    )
    direction = restrict(clv.rolling(base.window).mean(), mask)

    real_fp = summarise_ic(information_coefficient(footprint, fwd))
    real_dir = summarise_ic(information_coefficient(direction, fwd))

    print("-" * 86)
    print("1. THE FULL FOOTPRINT  (volume shuffled -- an appropriate placebo here)")
    print("-" * 86)
    null_fp = []
    for seed in range(args.draws):
        panel = accumulation_footprint(
            panels["high"], panels["low"], closes, panels["dollar_volume"], base,
            volume_override=shuffle_volume(panels["dollar_volume"], 1000 + seed),
        )
        null_fp.append(summarise_ic(information_coefficient(restrict(panel, mask), fwd)).mean)
    null_fp = np.asarray(null_fp)
    p_fp = empirical_p(real_fp.mean, null_fp)
    print(f"  real IC                    {real_fp.mean:+.4f}   (parametric t {real_fp.t_stat:+.2f})")
    print(f"  null mean                  {null_fp.mean():+.4f}")
    print(f"  null sd                    {null_fp.std(ddof=1):.4f}")
    print(f"  null 5th / 95th pctile     {np.percentile(null_fp, 5):+.4f} / "
          f"{np.percentile(null_fp, 95):+.4f}")
    print(f"  real in null z-units       {(real_fp.mean - null_fp.mean()) / null_fp.std(ddof=1):+.2f}")
    print(f"  EMPIRICAL p                {p_fp:.3f}")

    print("\n" + "-" * 86)
    print("2. MEAN CLOSE-LOCATION  (direction shuffled -- the flawed placebo, shown anyway)")
    print("-" * 86)
    null_dir = []
    for seed in range(args.draws):
        panel = shuffle_clv(clv, 2000 + seed).rolling(base.window).mean()
        null_dir.append(summarise_ic(information_coefficient(restrict(panel, mask), fwd)).mean)
    null_dir = np.asarray(null_dir)
    p_dir = empirical_p(real_dir.mean, null_dir)
    print(f"  real IC                    {real_dir.mean:+.4f}   (parametric t {real_dir.t_stat:+.2f})")
    print(f"  null mean                  {null_dir.mean():+.4f}")
    print(f"  null sd                    {null_dir.std(ddof=1):.4f}")
    print(f"  null 5th / 95th pctile     {np.percentile(null_dir, 5):+.4f} / "
          f"{np.percentile(null_dir, 95):+.4f}")
    print(f"  EMPIRICAL p                {p_dir:.3f}")
    print(f"\n  Null mean {null_dir.mean():+.4f} against a real {real_dir.mean:+.4f}: a SHUFFLED")
    print("  series reproduces most of the effect, because shuffling preserves each")
    print("  stock's long-run mean close-location. The statistic is measuring a")
    print("  stock-level characteristic, not a timing signal.")

    print("\n" + "-" * 86)
    print("3. THE SAME STATISTIC AGAINST A PROPER NULL")
    print("-" * 86)
    print("  The right placebo for a trailing average is not a within-stock shuffle.")
    print("  It is a CROSS-SECTIONAL one: keep every stock's series intact and permute")
    print("  which stock's signal is matched to which stock's forward return. That")
    print("  destroys the mapping without touching any stock's own level.\n")
    rng = np.random.default_rng(7)
    null_cross = []
    for _ in range(args.draws):
        shuffled = direction.copy()
        cols = np.asarray(shuffled.columns)
        shuffled.columns = cols[rng.permutation(len(cols))]
        null_cross.append(
            summarise_ic(information_coefficient(shuffled.reindex(columns=cols), fwd)).mean
        )
    null_cross = np.asarray(null_cross)
    p_cross = empirical_p(real_dir.mean, null_cross)
    print(f"  real IC                    {real_dir.mean:+.4f}")
    print(f"  cross-sectional null mean  {null_cross.mean():+.4f}")
    print(f"  null sd                    {null_cross.std(ddof=1):.4f}")
    print(f"  real in null z-units       "
          f"{(real_dir.mean - null_cross.mean()) / null_cross.std(ddof=1):+.2f}")
    print(f"  EMPIRICAL p                {p_cross:.3f}")

    print("\n" + "=" * 86)
    print("VERDICT")
    print("=" * 86)
    print(f"  footprint (the hypothesis)      empirical p {p_fp:.3f}")
    print(f"  mean close-location             empirical p {p_dir:.3f} (within-stock null)")
    print(f"                                  empirical p {p_cross:.3f} (cross-sectional null)")
    print("=" * 86)


if __name__ == "__main__":
    main()
