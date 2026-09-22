#!/usr/bin/env python
"""N12 under its pre-registered kill criteria, run in order, stopping at the first kill.

The criteria and their order were fixed in the ledger (entry 86) before any
return was computed. This script runs them; it does not choose them.

GATE ORDER -- cheapest and most likely fatal first:

  A  SIGNAL INTEGRITY   coverage, distribution, no look-ahead
  B  INFORMATION COEFFICIENT + EFFECTIVE SAMPLE   (criterion 6)
        does the signal rank next month's returns at all, and does the
        t-statistic survive the autocorrelation correction that killed N11
  C  PLACEBOS            (criteria 1 and 2)
        shuffle volume in time; shuffle direction. Either one reproducing the
        result means the construct carries nothing
  D  MOMENTUM ORTHOGONALITY   (criterion 4)
        N7 momentum is already rejected; a signal that IS momentum inherits it
  E  EARNINGS CONTAMINATION   (criterion 5)
        post-earnings drift is published and decayed
  F  PARAMETER SURFACE   (criterion 3)
        the N11 lesson: a parameter never varied is a parameter never tested
  G  OUT OF SAMPLE       (criterion 8)
  H  PORTFOLIO AND RISK  (criterion 7)

Stopping at the first kill is the discipline, not an optimisation. Running the
remaining gates after a fatal one and reporting the ones that passed is how a
dead hypothesis gets resurrected in a slide deck.

Run:  .venv/bin/python -m scripts.run_n12
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.research.accumulation import (
    FootprintParams,
    accumulation_footprint,
    close_location_value,
    shuffle_clv,
    shuffle_volume,
)
from tradelab.research.cross_section import month_end_dates, signal_momentum_12_1
from tradelab.research.ic import (
    forward_returns,
    information_coefficient,
    neutralise,
    summarise_ic,
)

PANEL = Path("data/signals/n12")
LOOKBACK = 252
POOL = 100
"""Per M3: select within the top 100 by trailing dollar volume, where the
baseline sits 1-5 points from SPY rather than 7."""
MIN_DOLLAR_VOLUME = 2_000_000
LIQUIDITY_WINDOW = 60
PLACEBO_SEEDS = (11, 22, 33)
KILL_PLACEBO_RATIO = 0.80
KILL_T_STAT = 2.0
KILL_SURFACE_SHARE = 0.50


class Killed(Exception):
    """Raised when a gate fails. The run stops; later gates are not consulted."""


def load_panels() -> dict[str, pd.DataFrame]:
    if not PANEL.exists():
        raise SystemExit("run scripts/build_n12_panel.py first")
    return {name: pd.read_parquet(PANEL / f"{name}.parquet") for name in
            ("high", "low", "close", "dollar_volume")}


def eligible_mask(closes: pd.DataFrame, volumes: pd.DataFrame, dates: list[pd.Timestamp]):
    """Point-in-time top-POOL membership, as known on each rebalance date."""
    index = closes.index
    mask = pd.DataFrame(False, index=pd.DatetimeIndex(dates), columns=closes.columns)
    for date in dates:
        position = index.get_loc(date)
        recent = volumes.iloc[max(0, position - LIQUIDITY_WINDOW) : position + 1]
        median_dv = recent.median(skipna=True)
        liquid = median_dv[median_dv >= MIN_DOLLAR_VOLUME].dropna()
        if liquid.empty:
            continue
        top = liquid.nlargest(min(POOL, len(liquid))).index
        priced = closes.loc[date, top].notna()
        mask.loc[date, top[priced.to_numpy()]] = True
    return mask


def restrict(panel: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """Signal values only where the name was eligible on that date."""
    aligned = panel.reindex(index=mask.index, columns=mask.columns)
    return aligned.where(mask)


def rule(title: str) -> None:
    print("\n" + "=" * 86, flush=True)
    print(title, flush=True)
    print("=" * 86, flush=True)


def main(context: dict | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    del args
    context = context if context is not None else {}

    print("=" * 86, flush=True)
    print("N12  INSTITUTIONAL ACCUMULATION FOOTPRINT", flush=True)
    print("running pre-registered kill criteria in order; stops at the first kill", flush=True)
    print("=" * 86, flush=True)

    panels = load_panels()
    closes = panels["close"]
    dates = [d for d in month_end_dates(closes.index) if d >= closes.index[LOOKBACK]]
    print(f"\n  panel {closes.shape[0]:,} x {closes.shape[1]:,}")
    print(f"  {len(dates)} monthly rebalances, {dates[0]:%Y-%m} to {dates[-1]:%Y-%m}")

    mask = eligible_mask(closes, panels["dollar_volume"], dates)
    print(f"  eligible names per date: mean {mask.sum(axis=1).mean():.0f}")

    fwd = forward_returns(closes, dates)
    base = FootprintParams()
    # Published into the caller so the post-mortem can run on the same
    # panels after a kill, without recomputing or -- worse -- recomputing
    # them slightly differently.
    context.update(panels=panels, mask=mask, fwd=fwd, dates=dates)

    # ------------------------------------------------------------- GATE A
    rule("GATE A  --  SIGNAL INTEGRITY")
    raw = accumulation_footprint(
        panels["high"], panels["low"], closes, panels["dollar_volume"], base
    )
    signal = restrict(raw, mask)
    live = signal.notna().sum(axis=1)
    values = signal.to_numpy(dtype=float)
    finite = values[np.isfinite(values)]
    print(f"  params                {base.label()}")
    print(f"  names scored per date mean {live.mean():.0f}  min {live.min()}")
    print(f"  signal distribution   mean {finite.mean():+.4f}  sd {finite.std():.4f}")
    print(f"                        p05 {np.percentile(finite, 5):+.4f}  "
          f"p50 {np.percentile(finite, 50):+.4f}  p95 {np.percentile(finite, 95):+.4f}")
    nonzero = float((np.abs(finite) > 1e-9).mean())
    print(f"  non-zero share        {nonzero:.1%}")
    if live.mean() < 20:
        raise Killed("fewer than 20 names scored per date; no cross-section to rank")
    print("  -> PASS")

    # ------------------------------------------------------------- GATE B
    rule("GATE B  --  INFORMATION COEFFICIENT AND EFFECTIVE SAMPLE  (criterion 6)")
    ic = information_coefficient(signal, fwd)
    summary = summarise_ic(ic)
    print(f"  {summary.line()}")
    print(f"  lag-1 autocorrelation of the IC series {summary.autocorr:+.3f}")
    print(f"\n  kill rule: |t| on n_eff must be >= {KILL_T_STAT}")
    if abs(summary.t_stat) < KILL_T_STAT:
        print("  -> KILL")
        raise Killed(
            f"IC t-statistic {summary.t_stat:+.2f} on {summary.n_eff:.1f} effective "
            f"observations, below the pre-registered {KILL_T_STAT}"
        )
    print("  -> PASS")

    # ------------------------------------------------------------- GATE C
    rule("GATE C  --  PLACEBOS  (criteria 1 and 2)")
    clv = close_location_value(panels["high"], panels["low"], closes)
    print(f"  real                          IC {summary.mean:+.4f}  t {summary.t_stat:+.2f}")

    vol_ics, clv_ics = [], []
    for seed in PLACEBO_SEEDS:
        shuffled_vol = accumulation_footprint(
            panels["high"], panels["low"], closes, panels["dollar_volume"], base,
            volume_override=shuffle_volume(panels["dollar_volume"], seed),
        )
        s = summarise_ic(information_coefficient(restrict(shuffled_vol, mask), fwd))
        vol_ics.append(s.mean)
        print(f"  placebo volume   seed {seed:<4}  IC {s.mean:+.4f}  t {s.t_stat:+.2f}")

        shuffled_clv = accumulation_footprint(
            panels["high"], panels["low"], closes, panels["dollar_volume"], base,
            clv_override=shuffle_clv(clv, seed),
        )
        s2 = summarise_ic(information_coefficient(restrict(shuffled_clv, mask), fwd))
        clv_ics.append(s2.mean)
        print(f"  placebo direction seed {seed:<4}  IC {s2.mean:+.4f}  t {s2.t_stat:+.2f}")

    scale = abs(summary.mean) if summary.mean != 0 else 1e-9
    vol_ratio = float(np.mean(np.abs(vol_ics)) / scale)
    clv_ratio = float(np.mean(np.abs(clv_ics)) / scale)
    print(f"\n  mean |placebo IC| / |real IC|   volume {vol_ratio:.2f}   direction {clv_ratio:.2f}")
    print(f"  kill rule: either ratio >= {KILL_PLACEBO_RATIO}")
    if vol_ratio >= KILL_PLACEBO_RATIO or clv_ratio >= KILL_PLACEBO_RATIO:
        print("  -> KILL")
        raise Killed(
            f"placebo reproduces the signal (volume {vol_ratio:.2f}, "
            f"direction {clv_ratio:.2f}); the construct carries nothing"
        )
    print("  -> PASS")

    # ------------------------------------------------------------- GATE D
    rule("GATE D  --  MOMENTUM ORTHOGONALITY  (criterion 4)")
    mom_rows = {}
    for date in dates:
        position = closes.index.get_loc(date)
        window = closes.iloc[max(0, position - LOOKBACK) : position + 1]
        mom_rows[date] = signal_momentum_12_1(window)
    momentum = restrict(pd.DataFrame(mom_rows).T, mask)

    mom_ic = summarise_ic(information_coefficient(momentum, fwd))
    resid = neutralise(signal, momentum)
    resid_ic = summarise_ic(information_coefficient(resid, fwd))
    print(f"  momentum alone                {mom_ic.line()}")
    print(f"  footprint alone               {summary.line()}")
    print(f"  footprint NEUTRALISED to mom  {resid_ic.line()}")
    print(f"\n  kill rule: neutralised |t| must stay >= {KILL_T_STAT}")
    if abs(resid_ic.t_stat) < KILL_T_STAT:
        print("  -> KILL")
        raise Killed(
            f"neutralising momentum removes the signal (t {resid_ic.t_stat:+.2f}); "
            "it is momentum, which N7 already rejected"
        )
    print("  -> PASS")

    print("\n  gates E-H not reached in this run; see the script header for order.")


def postmortem(panels: dict[str, pd.DataFrame], mask, fwd, dates) -> None:
    """Autopsy of a killed hypothesis. NOT a rescue attempt, and the difference
    is that the null expectation is stated before the numbers are produced.

    A single parameterisation is a single observation, so a kill at the default
    settings leaves one question genuinely open: is the IDEA dead, or only my
    guess at k=1.25/w21/b120/l21? Criterion 3 answers it -- but only if the
    surface is read against what chance produces, which is written down here
    first:

      48 cells are evaluated. Under the null, 4.6% of independent cells clear
      |t| >= 2, so 2.2 cells would be expected if they were independent. They
      are NOT independent -- overlapping windows and adjacent thresholds make
      perhaps 5-10 of them effectively distinct -- and the expected MAXIMUM |t|
      across ~10 independent draws is roughly 2.0-2.3.

      READ THE SURFACE THIS WAY: one to three cells above |t| = 2 IS the null,
      not a finding. Reviving this hypothesis would need a COHERENT REGION of
      adjacent cells at |t| >= 3.5, not scattered hits.

    Two further diagnostics follow, to establish whether the failure is in the
    combination or in the ingredients: persistence alone, direction alone, and
    a horizon sweep in case a monthly holding period is simply the wrong clock.
    """
    closes = panels["close"]
    rule("POST-MORTEM 1  --  PARAMETER SURFACE  (criterion 3, read against the null above)")
    print(
        f"  {'k':>5}{'win':>6}{'base':>7}{'lag':>6}{'IC':>10}{'t':>8}{'hit':>7}",
        flush=True,
    )
    cells = []
    for k in (1.10, 1.25, 1.50, 2.00):
        for window in (10, 21, 42, 63):
            for baseline in (60, 120, 250):
                params = FootprintParams(
                    k=k, window=window, baseline_window=baseline, baseline_lag=21
                )
                panel = accumulation_footprint(
                    panels["high"], panels["low"], closes, panels["dollar_volume"], params
                )
                s = summarise_ic(information_coefficient(restrict(panel, mask), fwd))
                cells.append((params, s))
                flag = "  <-" if abs(s.t_stat) >= 2.0 else ""
                print(
                    f"  {k:>5.2f}{window:>6}{baseline:>7}{21:>6}"
                    f"{s.mean:>+10.4f}{s.t_stat:>+8.2f}{s.hit_rate:>7.0%}{flag}",
                    flush=True,
                )

    hits = [c for c in cells if abs(c[1].t_stat) >= 2.0]
    positive = sum(1 for c in cells if c[1].mean > 0)
    best = max(cells, key=lambda c: abs(c[1].t_stat))
    print(f"\n  cells evaluated {len(cells)}   positive IC {positive} "
          f"({positive / len(cells):.0%})   |t| >= 2 in {len(hits)}")
    print(f"  strongest cell {best[0].label()}  t {best[1].t_stat:+.2f}")
    print("  expected by chance: ~2.2 cells if independent; max |t| ~2.0-2.3")
    print(f"  kill rule (criterion 3) also required >= {KILL_SURFACE_SHARE:.0%} positive cells")

    rule("POST-MORTEM 2  --  DO THE INGREDIENTS WORK SEPARATELY?")
    print("  If the combination is dead but a component is not, the failure is in")
    print("  the multiplication. If both components are dead too, the premise is.\n")
    clv = close_location_value(panels["high"], panels["low"], closes)
    base = FootprintParams()
    baseline_vol = (
        panels["dollar_volume"]
        .rolling(base.baseline_window, min_periods=base.baseline_window // 2)
        .median()
        .shift(base.baseline_lag)
    )
    elevated = panels["dollar_volume"] > (base.k * baseline_vol)

    persistence = elevated.astype(float).rolling(base.window).sum() / base.window
    direction = clv.rolling(base.window).mean()
    for label, panel in (
        ("persistence only (count of elevated days)", persistence),
        ("direction only (mean close-location)", direction),
        # min_periods=1 is required: `.where(elevated)` leaves NaN on every
        # quiet session, and a default rolling sum needs the whole window
        # non-null, so without it this panel is NaN everywhere and the
        # diagnostic silently reports nothing rather than failing.
        ("elevated-day volume share", (
            panels["dollar_volume"].where(elevated).rolling(base.window, min_periods=1).sum()
            / panels["dollar_volume"].rolling(base.window, min_periods=1).sum())),
    ):
        s = summarise_ic(information_coefficient(restrict(panel, mask), fwd))
        print(f"  {label:<44}{s.line()}")

    # The decomposition above can hand back a live component, and a live
    # component from an autopsy is the most dangerous object in quantitative
    # research: it was found by searching, so it carries the whole search's
    # multiple-testing burden and none of the pre-registration. It gets the
    # same two attacks the parent hypothesis would have faced at gates C and D.
    rule("POST-MORTEM 2b  --  ATTACKING WHATEVER SURVIVED THE DECOMPOSITION")
    direction_ic = summarise_ic(information_coefficient(restrict(direction, mask), fwd))
    if abs(direction_ic.t_stat) < 2.0:
        print("  Nothing survived the decomposition; no attack needed.")
    else:
        print(f"  surviving component: mean close-location over {base.window} sessions")
        print(f"  as found                      {direction_ic.line()}\n")
        for seed in PLACEBO_SEEDS:
            placebo = shuffle_clv(clv, seed).rolling(base.window).mean()
            s = summarise_ic(information_coefficient(restrict(placebo, mask), fwd))
            print(f"  placebo (shuffled) seed {seed:<4}  {s.line()}")
        mom_rows_pm = {}
        for date in dates:
            position = closes.index.get_loc(date)
            window_c = closes.iloc[max(0, position - LOOKBACK) : position + 1]
            mom_rows_pm[date] = signal_momentum_12_1(window_c)
        momentum_pm = restrict(pd.DataFrame(mom_rows_pm).T, mask)
        mom_only = summarise_ic(information_coefficient(momentum_pm, fwd))
        resid = neutralise(restrict(direction, mask), momentum_pm)
        resid_ic = summarise_ic(information_coefficient(resid, fwd))
        print(f"\n  momentum alone                {mom_only.line()}")
        print(f"  component NEUTRALISED to mom  {resid_ic.line()}")
        print("\n  A component that dies here is momentum, which N7 already rejected.")

    rule("POST-MORTEM 3  --  IS A MONTH THE WRONG CLOCK?")
    print("  Order splitting runs days-to-weeks. If the footprint predicts anything")
    print("  it may decay well inside a monthly holding period.\n")
    all_dates = list(closes.index)
    raw = accumulation_footprint(
        panels["high"], panels["low"], closes, panels["dollar_volume"], base
    )
    for horizon, label in ((5, "1 week"), (10, "2 weeks"), (21, "1 month"), (63, "1 quarter")):
        stamps = [d for d in dates if d in closes.index]
        fwd_h = {}
        for date in stamps:
            i = all_dates.index(date)
            if i + horizon >= len(all_dates):
                continue
            entry = closes.iloc[i]
            exit_price = closes.iloc[i : i + horizon + 1].ffill().iloc[-1]
            fwd_h[date] = (exit_price / entry - 1.0).replace([np.inf, -np.inf], np.nan)
        s = summarise_ic(information_coefficient(restrict(raw, mask), pd.DataFrame(fwd_h).T))
        print(f"  {label:<12}{s.line()}")


if __name__ == "__main__":
    context: dict = {}
    try:
        main(context)
    except Killed as exc:
        print("\n" + "=" * 86)
        print(f"N12 KILLED: {exc}")
        print("=" * 86)
        print("Later gates deliberately not run. A hypothesis that fails a")
        print("pre-registered criterion is dead at that criterion; continuing to")
        print("test it and reporting what passed is how dead ideas get revived.")
        print("=" * 86)
        if context:
            postmortem(context["panels"], context["mask"], context["fwd"], context["dates"])
