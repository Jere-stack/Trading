#!/usr/bin/env python
"""Reproduce every cost figure quoted in docs/01-broker-selection.md.

Run:  .venv/bin/python scripts/cost_report.py

The point of this script is that the broker analysis is checkable rather than
asserted. If IBKR changes its schedules, edit the models in tradelab.costs and
re-run; the document's numbers should be regenerated from here.
"""

from __future__ import annotations

from decimal import Decimal as D

from tradelab.core.enums import Side, Venue
from tradelab.core.types import Instrument
from tradelab.costs.commission import (
    cheaper_us_schedule,
    ibkr_default_router,
    ibkr_us_fixed,
    IbkrTieredUsEquity,
)
from tradelab.costs.fx import FxCostModel, FxPolicyComparison
from tradelab.costs.slippage import SpreadImpactSlippage


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def round_trip_commission_table() -> None:
    rule("1. Round-trip commission as bps of notional (IBKR retail)")
    router = ibkr_default_router()
    us = Instrument("USSTOCK", currency="USD", adv=D("20000000"), spread_bps=D("3"))
    fi = Instrument(
        "FISTOCK", venue=Venue.HELSINKI, currency="EUR", adv=D("5000000"), spread_bps=D("8")
    )
    print(f"{'Position':>10} | {'US (Tiered)':>14} | {'Helsinki (0.05%)':>18}")
    print("-" * 50)
    for notional in (D("250"), D("500"), D("1000"), D("2500"), D("5000")):
        row = [f"{notional:>9.0f}"]
        for inst, price in ((us, D("100")), (fi, D("20"))):
            qty = (notional / price).to_integral_value()
            if qty <= 0:
                row.append(f"{'n/a':>14}")
                continue
            buy = router.total(inst, Side.BUY, qty, price)
            sell = router.total(inst, Side.SELL, qty, price)
            bps = (buy + sell) / (qty * price) * D("10000")
            row.append(f"{bps:>13.1f}b")
        print(" | ".join(row))
    print("\nAt EUR 10k with 8-12 positions (EUR 800-1,200 each), round-trip")
    print("commission is 20-30 bps. Add spread: ~25-40 bps of gross edge is")
    print("needed per round trip merely to break even.")


def tiered_vs_fixed() -> None:
    rule("2. IBKR Tiered vs Fixed crossover (US equities)")
    us = Instrument("USSTOCK", currency="USD", adv=D("20000000"))
    price = D("100")
    print(f"{'Shares':>8} | {'Tiered':>9} | {'Fixed':>9} | Cheaper")
    print("-" * 44)
    tiered_model, fixed_model = IbkrTieredUsEquity(), ibkr_us_fixed()
    for qty in (10, 50, 100, 130, 140, 150, 200, 500):
        q = D(qty)
        t = tiered_model.total(us, Side.BUY, q, price)
        f = fixed_model.total(us, Side.BUY, q, price)
        winner, _ = cheaper_us_schedule(us, Side.BUY, q, price)
        print(f"{qty:>8} | {t:>9.2f} | {f:>9.2f} | {winner}")
    print("\nCrossover near 140 shares. A EUR 10k account holding EUR 500-1,500")
    print("positions stays well below that for any stock above ~USD 10, so")
    print("Tiered is cheaper across this account's realistic order sizes.")


def fx_policy() -> None:
    rule("3. FX conversion cost and the case for block conversion")
    model = FxCostModel()
    print(f"{'Converted':>12} | {'Cost (USD)':>11} | {'bps':>7}")
    print("-" * 36)
    for amount in (D("500"), D("1000"), D("5000"), D("10000"), D("100000")):
        cost = model.commission_for(amount)
        print(f"{amount:>12,.0f} | {cost:>11.2f} | {cost / amount * 10000:>6.1f}b")

    print("\n100 round trips of EUR 1,000 in US stocks over a year:")
    comparison = FxPolicyComparison().compare([D("1000")] * 100, block_conversions=12)
    print(f"  per-trade conversion : EUR {comparison['per_trade_cost']:>8,.2f} "
          f"({comparison['per_trade_bps']:.1f} bps)")
    print(f"  monthly block        : EUR {comparison['block_cost']:>8,.2f} "
          f"({comparison['block_bps']:.1f} bps)")
    print(f"  annual saving        : EUR {comparison['saving']:>8,.2f} "
          f"= {comparison['saving'] / 10000:.2%} of a EUR 10k account")
    print("\nRule: hold a standing USD balance, convert in infrequent blocks.")


def microcap_trap() -> None:
    rule("4. Why illiquid small caps are a trap, not a frontier")
    router = ibkr_default_router()
    slippage = SpreadImpactSlippage()
    micro = Instrument(
        "MICRO",
        venue=Venue.HELSINKI,
        currency="EUR",
        adv=D("15000"),
        sigma_daily=D("0.045"),
        spread_bps=D("180"),
    )
    price, notional = D("6.00"), D("600")
    qty = int(notional / price)
    est = slippage.estimate(micro, Side.BUY, D(qty), price)
    commission = router.total(micro, Side.BUY, D(qty), price)
    commission_bps = commission / (D(qty) * price) * D("10000")
    one_way = est.spread_bps + est.impact_bps + commission_bps

    print(f"Helsinki micro-cap: 180 bps quoted spread, EUR 15k daily volume")
    print(f"Buying EUR {notional:,.0f} ({qty} shares at EUR {price}):\n")
    print(f"  spread (half, x1.25 signal-conditional) : {est.spread_bps:>7.1f} bps")
    print(f"  market impact (square-root law)         : {est.impact_bps:>7.1f} bps")
    print(f"  commission                              : {commission_bps:>7.1f} bps")
    print(f"  {'-' * 50}")
    print(f"  one way                                 : {one_way:>7.1f} bps")
    print(f"  round trip                              : {one_way * 2:>7.1f} bps "
          f"({one_way * 2 / 100:.2f}%)")
    print("\nA ~3% gross edge per trade would be required to break even.")
    print("Institutions are absent because the spread makes it uneconomic for")
    print("everyone -- not because retail size confers an advantage there.")


def break_even_summary() -> None:
    rule("5. Break-even gross edge by holding period (EUR 10k, 10 positions)")
    print("Assumes EUR 1,000 positions, ~25 bps round-trip cost.\n")
    print(f"{'Holding period':>16} | {'Round trips/yr':>15} | {'Break-even gross/yr':>20}")
    print("-" * 58)
    cost_bps = D("25")
    for label, days in (("1 day", 1), ("1 week", 5), ("1 month", 21), ("1 quarter", 63)):
        trips = D(252) / D(days)
        print(f"{label:>16} | {trips:>15.0f} | {trips * cost_bps / 100:>19.1f}%")
    print("\nDaily trading requires a 63% gross annual return just to cover")
    print("costs. This is why holding periods must be weeks to months.")


if __name__ == "__main__":
    print("IBKR cost model report -- regenerates the figures in")
    print("docs/01-broker-selection.md. Schedules as of 2026-09; recalibrate")
    print("against your own statements before trusting any of it.")
    round_trip_commission_table()
    tiered_vs_fixed()
    fx_policy()
    microcap_trap()
    break_even_summary()
    print()
