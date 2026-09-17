"""Reference strategy: reversal after an abnormal down-day.

**This is not a proposed strategy and is not validated.** It exists to exercise
the pipeline end to end against synthetic data with a *known* injected effect,
which is the only way to verify that a research pipeline detects real edges and
rejects absent ones. Validating a pipeline on real data confounds two unknowns:
whether the pipeline works, and whether the effect exists.

The rule is a deliberately simple stand-in for the forced-selling family of
hypotheses (H5, H8, H9 in `docs/06-strategy-hypotheses.md`): a large one-day
drop is used as a crude proxy for price-insensitive selling pressure, and the
position is held for a fixed window while that pressure unwinds.

Economically it is a weak proxy. Real forced selling is identified by its
*cause* (an index deletion, a redemption, a tax deadline), not by the price move
it produces, and "large down day" also selects for genuine bad news. That
confound is exactly why H5 requires a matched-sample control.
"""

from __future__ import annotations

import statistics
from decimal import Decimal

from tradelab.core.money import ZERO, round_to_lot
from tradelab.core.types import Instrument
from tradelab.strategy.base import Strategy, StrategyContext


class ForcedSellingReversal(Strategy):
    """Buy after an abnormal one-day drop; exit after a fixed holding period.

    Parameters are few and coarse on purpose. Every additional parameter is
    another trial that must be declared to the Deflated Sharpe Ratio, so a
    hypothesis-testing strategy should have as few as it can.
    """

    def __init__(
        self,
        universe: list[Instrument],
        strategy_id: str = "forced_selling_reversal",
        lookback: int = 60,
        entry_sigma: Decimal = Decimal("2.0"),
        holding_days: int = 20,
        target_weight: Decimal = Decimal("0.10"),
    ) -> None:
        super().__init__(strategy_id, warmup_bars=lookback + 1)
        if lookback < 20:
            raise ValueError("lookback must be >= 20 for a usable volatility estimate")
        if holding_days < 1:
            raise ValueError("holding_days must be >= 1")
        self._universe = list(universe)
        self.lookback = lookback
        self.entry_sigma = entry_sigma
        self.holding_days = holding_days
        self.target_weight = target_weight
        self._held_bars: dict[str, int] = {}

    @property
    def universe(self) -> list[Instrument]:
        return self._universe

    def on_bar(self, ctx: StrategyContext, bars) -> None:
        for instrument in self._universe:
            key = instrument.key
            if key not in bars:
                continue

            # --- exits first, so capital freed this bar can be redeployed
            if not ctx.is_flat(instrument):
                self._held_bars[key] = self._held_bars.get(key, 0) + 1
                if self._held_bars[key] >= self.holding_days:
                    ctx.close(instrument, reason=f"held {self._held_bars[key]} bars")
                    self._held_bars.pop(key, None)
                continue

            if ctx.has_open_order(instrument):
                continue

            # --- entry
            closes = ctx.closes(instrument, self.lookback + 1)
            if len(closes) < self.lookback + 1:
                continue
            returns = [
                float((closes[i] - closes[i - 1]) / closes[i - 1])
                for i in range(1, len(closes))
                if closes[i - 1] > 0
            ]
            if len(returns) < self.lookback // 2:
                continue
            sigma = statistics.stdev(returns[:-1])
            if sigma <= 0:
                continue
            today = returns[-1]
            if today > -float(self.entry_sigma) * sigma:
                continue

            price = ctx.last_price(instrument)
            if price is None or price <= 0:
                continue
            target_notional = ctx.equity * self.target_weight
            quantity = round_to_lot(target_notional / price, instrument.lot_size)
            if quantity <= ZERO:
                continue
            ctx.buy(
                instrument,
                quantity,
                reason=f"{today / sigma:.1f} sigma down-day",
            )
            self._held_bars[key] = 0

    def on_finish(self, ctx: StrategyContext) -> None:
        self._held_bars.clear()
