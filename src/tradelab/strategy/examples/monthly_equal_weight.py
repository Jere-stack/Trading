"""Monthly equal-weight rebalance -- a plumbing test vehicle, not a strategy.

**This has no edge and is not supposed to.** It exists to exercise the live
runner end to end: signal -> risk gate -> order -> fill -> ledger -> persisted
state -> session rollover. It will roughly track the market, which is the
point: any large deviation from a hand-computed equal-weight basket means the
system is doing something wrong.

Chosen properties, all deliberate:

* **Verifiable by hand.** Target weight is 1/N. Given the equity and prices you
  can compute the intended position yourself and compare. A cleverer strategy
  would make it impossible to tell a plumbing bug from a signal quirk.
* **Trades rarely.** Monthly, with a drift band, so it generates a handful of
  orders rather than a stream. Enough to exercise execution, few enough that
  every order can be inspected individually.
* **Long-only, liquid names.** Stays inside the mandate and inside the cost
  budget, so risk-gate rejections indicate a real problem rather than the
  strategy asking for something absurd.

The drift band matters for a reason worth stating: rebalancing to exact weights
every month generates orders for trivial deviations, and at a EUR 1.25
commission floor those cost more than the tracking error they remove. The band
is the same economic logic the register applies to real strategies.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tradelab.core.money import ZERO, round_to_lot
from tradelab.core.types import Instrument
from tradelab.strategy.base import Strategy, StrategyContext


class MonthlyEqualWeight(Strategy):
    """Hold `universe` at equal weight, rebalanced monthly within a drift band."""

    def __init__(
        self,
        universe: list[Instrument],
        strategy_id: str = "monthly_equal_weight",
        drift_band: Decimal = Decimal("0.25"),
        target_gross: Decimal = Decimal("0.90"),
        min_order_value: Decimal = Decimal("400"),
        warmup_bars: int = 2,
    ) -> None:
        super().__init__(strategy_id, warmup_bars=warmup_bars)
        if not universe:
            raise ValueError("universe must be non-empty")
        if not 0 < target_gross <= 1:
            raise ValueError("target_gross must be in (0, 1]")
        self._universe = list(universe)
        self.drift_band = drift_band
        # Below 1.0 so commission and price drift between decision and fill do
        # not push the book through the gross exposure ceiling.
        self.target_gross = target_gross
        # Base currency. The risk gate enforces its own minimum and remains the
        # authority; this is the strategy declining to ask. Without it the
        # monthly rebalance re-submits the same uneconomic one-share top-up
        # every month forever -- 39 identical rejections in a two-year run --
        # because a rejected order never changes the position that produced it.
        self.min_order_value = min_order_value
        self._last_rebalance: date | None = None

    @property
    def universe(self) -> list[Instrument]:
        return self._universe

    def on_bar(self, ctx: StrategyContext, bars) -> None:
        today = ctx.now.date()
        if not self._is_rebalance_day(today):
            return

        priced = {
            inst.key: price
            for inst in self._universe
            if (price := ctx.last_price(inst)) is not None and price > 0
        }
        if len(priced) < len(self._universe):
            # Rebalancing on a partial universe would silently overweight
            # whatever happened to have a price today.
            return

        self._last_rebalance = today
        # Base currency. Prices are in the instrument's currency, so this has to
        # be converted per name before it can be divided by a price -- see
        # `ctx.budget_in`. Dividing EUR equity by a USD price sizes every US
        # name short by the EUR/USD rate (13% at 1.148), which looks like a
        # risk limit refusing to fill the book rather than an arithmetic error.
        target_base = ctx.equity * self.target_gross / Decimal(len(self._universe))

        for inst in self._universe:
            price = priced[inst.key]
            if ctx.has_open_order(inst):
                continue
            held = ctx.quantity(inst)
            target_value = ctx.budget_in(inst, target_base)
            target_qty = round_to_lot(target_value / price, inst.lot_size)
            delta = target_qty - held

            if held > 0:
                drift = abs(delta) / held
                if drift < self.drift_band:
                    continue
            if delta == ZERO:
                continue
            # Let the drift accumulate until the trade is worth its commission.
            # Skipping here leaves the position underweight, which is the
            # cheaper of the two errors: a EUR 276 order paying a EUR 1.25
            # floor is 45 bps, more than the tracking error it removes.
            if ctx.to_base(abs(delta) * price, inst.currency) < self.min_order_value:
                continue

            reason = f"rebalance to 1/{len(self._universe)} ({today})"
            if delta > 0:
                ctx.buy(inst, delta, reason=reason)
            else:
                ctx.sell(inst, -delta, reason=reason)

    def _is_rebalance_day(self, today: date) -> bool:
        """First observed session of a new calendar month."""
        if self._last_rebalance is None:
            return True
        return (today.year, today.month) != (
            self._last_rebalance.year,
            self._last_rebalance.month,
        )
