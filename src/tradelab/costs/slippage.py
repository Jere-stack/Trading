"""Slippage and market-impact models.

Commission is the easy cost -- it is deterministic and the broker publishes it.
Slippage is where backtests lie, for three reasons:

1. **Spread is paid on every round trip and is invisible in OHLCV data.** A
   backtest that fills at the close pays zero spread; live, a taker pays the
   full half-spread on entry and again on exit.
2. **Impact grows with size**, so a strategy validated on unlimited capital may
   be untradable at the size it needs to matter.
3. **Signals correlate with wide spreads.** Anything triggered by a large move,
   a gap, or a volume spike fires precisely when the book is thinnest. Modelling
   an average spread systematically understates cost for exactly those signals.

The impact model is the square-root law:

    impact_bps = coefficient * sigma_daily_bps * sqrt(participation)

where participation = order quantity / average daily volume. The square-root
form is well established empirically (Almgren et al., Torre/BARRA, and the
broader market-microstructure literature converge on an exponent near 0.5 and a
coefficient of order 0.5-1.0 for equities).

At EUR 10k in liquid names, participation is tiny (a EUR 1,000 order in a stock
with EUR 50m daily volume is 0.002%), so impact is negligible and **spread
dominates**. The impact term exists to enforce the capacity ceiling and to catch
the illiquid-small-cap case, where it bites hard and fast.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from tradelab.core.enums import OrderType, Side
from tradelab.core.money import ZERO, quantize_price, safe_div, to_decimal
from tradelab.core.types import Bar, Instrument, Quote


@dataclass(frozen=True, slots=True)
class SlippageEstimate:
    """Decomposed execution price relative to a reference (decision) price."""

    fill_price: Decimal
    spread_bps: Decimal
    impact_bps: Decimal
    reference_price: Decimal

    @property
    def total_bps(self) -> Decimal:
        return self.spread_bps + self.impact_bps

    @property
    def spread_cost_per_share(self) -> Decimal:
        return quantize_price(self.reference_price * self.spread_bps / Decimal("10000"))

    @property
    def impact_cost_per_share(self) -> Decimal:
        return quantize_price(self.reference_price * self.impact_bps / Decimal("10000"))


class SlippageModel(ABC):
    @abstractmethod
    def estimate(
        self,
        instrument: Instrument,
        side: Side,
        quantity: Decimal,
        reference_price: Decimal,
        order_type: OrderType = OrderType.MARKET,
        quote: Quote | None = None,
        bar: Bar | None = None,
    ) -> SlippageEstimate: ...


@dataclass(frozen=True, slots=True)
class NoSlippage(SlippageModel):
    """Zero slippage. Research-only, for cost sensitivity differencing."""

    def estimate(
        self,
        instrument,
        side,
        quantity,
        reference_price,
        order_type=OrderType.MARKET,
        quote=None,
        bar=None,
    ):
        ref = to_decimal(reference_price)
        return SlippageEstimate(ref, ZERO, ZERO, ref)


@dataclass(frozen=True, slots=True)
class SpreadImpactSlippage(SlippageModel):
    """Half-spread plus square-root impact. The default model.

    Spread is sourced in strict order of preference:
      1. A live/recorded `Quote` -- authoritative.
      2. `instrument.spread_bps` -- the per-instrument calibrated median.
      3. The Corwin-Schultz high-low estimator from a `Bar`, if enabled.
      4. `max(default_spread_bps, one tick)` -- a last-resort global assumption.

    Reaching step 4 for an illiquid name is a red flag, not a fallback: it means
    cost is being guessed for precisely the instrument where the guess matters
    most. `require_calibrated_spread` turns that into a hard error so it cannot
    pass silently into a headline result.

    `spread_multiplier` inflates the spread to account for the correlation
    between signals and wide books described in the module docstring. It
    defaults above 1.0 deliberately: being wrong in the conservative direction
    discards good strategies, while being wrong in the flattering direction
    deploys bad ones.

    Limit orders pay no spread when they rest and get filled, but they incur
    adverse selection -- they fill preferentially when the price is moving
    against you. `limit_adverse_selection_bps` prices that, and it is not
    optional: a model where limit orders are free manufactures edge out of thin
    air, which is one of the most common ways a market-making-flavoured
    backtest fools its author.
    """

    default_spread_bps: Decimal = Decimal("5")
    spread_multiplier: Decimal = Decimal("1.25")
    impact_coefficient: Decimal = Decimal("0.75")
    impact_exponent: float = 0.5
    default_sigma_daily_bps: Decimal = Decimal("180")
    limit_adverse_selection_bps: Decimal = Decimal("2")
    use_corwin_schultz: bool = False
    max_spread_bps: Decimal = Decimal("500")
    require_calibrated_spread: bool = False

    def estimate(
        self,
        instrument,
        side,
        quantity,
        reference_price,
        order_type=OrderType.MARKET,
        quote=None,
        bar=None,
    ):
        ref = to_decimal(reference_price)
        if ref <= 0:
            raise ValueError(f"{instrument.symbol}: reference_price must be positive")
        quantity = abs(to_decimal(quantity))

        spread_bps = self._spread_bps(instrument, quote, bar)
        is_passive = order_type in (OrderType.LIMIT, OrderType.LIMIT_ON_CLOSE)
        if is_passive:
            cost_bps = self.limit_adverse_selection_bps
        else:
            cost_bps = (spread_bps / Decimal("2")) * self.spread_multiplier

        impact_bps = self._impact_bps(instrument, quantity)
        total_bps = cost_bps + impact_bps
        fill = ref * (Decimal("1") + side.sign * total_bps / Decimal("10000"))
        return SlippageEstimate(
            fill_price=quantize_price(fill),
            spread_bps=cost_bps,
            impact_bps=impact_bps,
            reference_price=ref,
        )

    def _spread_bps(
        self, instrument: Instrument, quote: Quote | None, bar: Bar | None
    ) -> Decimal:
        if quote is not None and not quote.is_crossed:
            observed = quote.spread_bps
            if observed > 0:
                return min(observed, self.max_spread_bps)
        if instrument.spread_bps is not None and instrument.spread_bps > 0:
            return min(instrument.spread_bps, self.max_spread_bps)
        if self.use_corwin_schultz and bar is not None:
            estimated = corwin_schultz_spread_bps(bar)
            if estimated is not None and estimated > 0:
                return min(estimated, self.max_spread_bps)
        if self.require_calibrated_spread:
            raise ValueError(
                f"{instrument.symbol}: no quote and no calibrated instrument.spread_bps, "
                "but require_calibrated_spread=True. Populate spread_bps from measured "
                "quote data before trusting cost estimates for this instrument."
            )
        if bar is not None and bar.close > 0:
            # A spread can never be narrower than one tick; for low-priced
            # stocks the tick floor is the binding constraint, not the average.
            tick_bps = safe_div(instrument.tick_size, bar.close) * Decimal("10000")
            return min(max(self.default_spread_bps, tick_bps), self.max_spread_bps)
        return self.default_spread_bps

    def _impact_bps(self, instrument: Instrument, quantity: Decimal) -> Decimal:
        if instrument.adv is None or instrument.adv <= 0:
            return ZERO
        sigma_bps = (
            instrument.sigma_daily * Decimal("10000")
            if instrument.sigma_daily is not None
            else self.default_sigma_daily_bps
        )
        participation = safe_div(quantity, instrument.adv)
        if participation <= 0:
            return ZERO
        scaled = Decimal(str(math.pow(float(participation), self.impact_exponent)))
        return self.impact_coefficient * sigma_bps * scaled


def corwin_schultz_spread_bps(bar: Bar) -> Decimal | None:
    """Single-bar degenerate case of the Corwin-Schultz (2012) estimator.

    The published estimator needs two consecutive bars to separate the spread
    from volatility. This single-bar variant is a crude floor and is why
    `use_corwin_schultz` is off by default: prefer real quotes, and prefer a
    measured universe-level spread assumption over a noisy per-bar estimate.

    Returns None when the bar has no range (a limit-up/halt day), since the
    estimator is undefined there.
    """
    if bar.high <= 0 or bar.low <= 0 or bar.high == bar.low:
        return None
    ratio = float(bar.high / bar.low)
    if ratio <= 1.0:
        return None
    beta = math.log(ratio) ** 2
    gamma = beta
    denom = 3 - 2 * math.sqrt(2)
    alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / denom - math.sqrt(gamma / denom)
    spread = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
    if spread <= 0 or not math.isfinite(spread):
        return None
    return Decimal(str(spread * 10000))
