#!/usr/bin/env python
"""Screen candidate strategy hypotheses on cost and statistical power.

Run:  .venv/bin/python scripts/hypothesis_screen.py

This runs BEFORE any data is acquired. Its purpose is to reject candidates
using arithmetic alone, so that data engineering effort goes only to hypotheses
that could survive even if the effect is real.

Every `expected_gross_edge_bps` below is a **prior stated in advance**, sourced
from published literature and discounted for decay -- not fitted from a
backtest. Stating priors before testing is what makes the later validation
meaningful; a prior chosen after seeing results is not a prior.

Noise is set to 150 bps x sqrt(holding_days), i.e. roughly 1.5% daily
market-adjusted idiosyncratic volatility. This is the term that makes event
studies hard: over a 45-day horizon, per-event noise is ~1,000 bps against an
effect of perhaps 100 bps.
"""

from __future__ import annotations

import math

from tradelab.research.feasibility import Feasibility, screen

# Round-trip cost at EUR 10k with EUR 800-1,200 positions in liquid names.
# Derived in docs/01-broker-selection.md via scripts/cost_report.py.
COST_BPS = 30.0


def noise_for(holding_days: float) -> float:
    """Per-event market-adjusted return stdev, in bps."""
    return 150.0 * math.sqrt(holding_days)


# (id, name, holding_days, prior_gross_edge_bps, events_per_year, note)
HYPOTHESES = [
    (
        "H1",
        "Overnight risk premium (buy close, sell open)",
        1,
        3.0,
        252,
        "Prior: the overnight/intraday return split is well documented, but the "
        "per-day magnitude is a few bps. Requires trading every single day.",
    ),
    (
        "H2",
        "Short-term reversal, 1-week horizon",
        5,
        35.0,
        250,
        "Prior: historically 40-80 bps in liquid names, discounted for decay. "
        "This is now a market-making activity conducted at near-zero cost.",
    ),
    (
        "H3",
        "Turn-of-month flow (pension contribution timing)",
        4,
        25.0,
        12,
        "Prior: genuine flow mechanism (regular contributions), but small and "
        "widely known. Only 12 observations per year.",
    ),
    (
        "H4",
        "Post-earnings announcement drift (PEAD), liquid US large caps",
        45,
        90.0,
        200,
        "Prior: 2-4% historically for extreme deciles (Bernard & Thomas 1989), "
        "heavily discounted -- this is the textbook anomaly, so crowding in "
        "liquid names is near-certain.",
    ),
    (
        "H5",
        "Index deletion forced selling (non-S&P-500 indices)",
        20,
        140.0,
        30,
        "Prior: mandated price-insensitive selling by index funds. Discounted "
        "but not to zero: the deletion side is less attractive to arbitrageurs "
        "than the addition side, and smaller indices are less crowded.",
    ),
    (
        "H6",
        "Index addition premium, S&P 500",
        10,
        15.0,
        25,
        "Prior: near zero. Greenwood & Sammon document the index effect "
        "disappearing for large, well-telegraphed indices.",
    ),
    (
        "H7",
        "Spin-off indiscriminate selling",
        126,
        400.0,
        15,
        "Prior: strong mechanism (parent holders receive an unwanted asset, "
        "index funds must sell, no analyst coverage yet). Highest economic "
        "plausibility of any candidate here.",
    ),
    (
        "H8",
        "December tax-loss selling reversal",
        30,
        120.0,
        20,
        "Prior: real flow mechanism with a calendar deadline, which limits how "
        "far arbitrageurs will front-run it.",
    ),
    (
        "H9",
        "Mutual fund fire-sale pressure (Coval & Stafford)",
        60,
        200.0,
        25,
        "Prior: strongest academic support of the forced-flow family. Requires "
        "quarterly fund holdings data, which is available but lagged 45 days -- "
        "the lag may consume the effect.",
    ),
    (
        "H10",
        "Cross-sectional momentum, 12-1, monthly rebalance",
        21,
        45.0,
        250,
        "Prior: the most crowded factor in existence, with severe crash risk "
        "(2009, 2016, 2020). Included only to demonstrate it fails on cost.",
    ),
]


def main() -> None:
    print(__doc__)
    print(f"Assumed round-trip cost: {COST_BPS:.0f} bps (EUR 10k, liquid names)\n")

    results = [
        screen(
            name=f"{hid}  {name}",
            holding_days=hold,
            expected_gross_edge_bps=edge,
            events_per_year=events,
            noise_bps=noise_for(hold),
            round_trip_cost_bps=COST_BPS,
            positions_held=10,
            available_years=10.0,
            note=note,
        )
        for hid, name, hold, edge, events, note in HYPOTHESES
    ]

    print("=" * 78)
    print("SCREENING RESULTS")
    print("=" * 78)
    for r in results:
        print()
        print(r.report().rstrip())

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    buckets: dict[Feasibility, list[str]] = {}
    for (hid, *_), r in zip(HYPOTHESES, results, strict=True):
        buckets.setdefault(r.verdict, []).append(hid)
    for verdict in Feasibility:
        names = buckets.get(verdict, [])
        if names:
            print(f"  {verdict.value:<18} {', '.join(names)}")

    survivors = [r for r in results if r.verdict in (Feasibility.VIABLE, Feasibility.MARGINAL)]
    print()
    print(f"  {len(survivors)} of {len(results)} candidates survive pre-data screening.")
    print()
    print("  Surviving a screen is NOT evidence of an edge. It only means the")
    print("  hypothesis is not arithmetically impossible, and therefore that")
    print("  spending data-engineering effort on it is defensible.")
    print()
    print("  Note the central bind: the candidates with the strongest economic")
    print("  mechanism (H7 spin-offs, H9 fire sales) are rare, so they are the")
    print("  hardest to validate. Uncrowded effects are uncrowded partly")
    print("  because they are rare, and rare effects cannot be proven with")
    print("  retail-accessible data.")

    _data_cost_section(survivors)


def _data_cost_section(survivors: list) -> None:
    """Show what a data subscription costs relative to the expected edge.

    Survivorship-bias-free history is not free, and a subscription is charged
    per year regardless of whether the strategy trades. On a small account that
    behaves like a large fixed drag, which per-trade cost models miss entirely.
    """
    from tradelab.research.feasibility import annual_economics, breakeven_account_size

    by_id = {h[0]: h for h in HYPOTHESES}
    print()
    print("=" * 78)
    print("DATA SUBSCRIPTION ECONOMICS (EUR 10,000 account)")
    print("=" * 78)
    print("""
  Survivorship-bias-free history costs money. EODHD end-of-day, all-world,
  including delisted tickers and Nasdaq Helsinki, is EUR 199/year -- which is
  1.99% of a EUR 10,000 account, paid whether or not the strategy trades.
""")
    print(
        f"  {'':>4} {'gross/yr':>9} {'trading':>9} {'data':>7} {'net/yr':>9} "
        f"{'net EUR':>9} {'break-even acct':>16}"
    )
    print("  " + "-" * 72)

    for result in survivors:
        hid = result.name.split()[0]
        spec = by_id.get(hid)
        if spec is None:
            continue
        _, _, hold, edge, events, _ = spec
        economics = annual_economics(
            events_per_year=events,
            gross_edge_bps=edge,
            holding_days=hold,
            positions_held=10,
            round_trip_cost_bps=COST_BPS,
            fixed_annual_cost=199.0,
            account_equity=10_000.0,
        )
        threshold = breakeven_account_size(
            events, edge, hold, round_trip_cost_bps=COST_BPS, fixed_annual_cost=199.0
        )
        threshold_text = "never" if threshold == float("inf") else f"EUR {threshold:>9,.0f}"
        print(
            f"  {hid:>4} {economics.gross_annual_return_pct:>8.2f}% "
            f"{economics.trading_cost_drag_pct:>8.2f}% "
            f"{economics.fixed_cost_drag_pct:>6.2f}% "
            f"{economics.net_annual_return_pct:>8.2f}% "
            f"{economics.net_annual_currency:>9,.0f} {threshold_text:>16}"
        )

    print("""
  Read this carefully, because it cuts both ways.

  The break-even account sizes sit BELOW EUR 10,000, so paying for data is
  justified at this account size -- but only if the edges are real, and none
  of them is validated. The subscription is a bet on the research succeeding,
  not on a known return.

  The net figures are the sobering part. The best candidate nets on the order
  of a hundred euros a year on EUR 10,000 after data costs, with the
  subscription consuming roughly half the gross profit. That is the honest
  scale of what a EUR 10k systematic equity account can expect even when a
  real edge is found.

  Both improve sharply with account size: the edge scales with equity while
  the subscription does not.""")


if __name__ == "__main__":
    main()
