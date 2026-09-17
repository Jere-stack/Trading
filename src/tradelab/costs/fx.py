"""Currency conversion cost.

Usually ignored in retail backtests, and at EUR 10k that is a material error.

IBKR charges 0.20 bp of notional with a **USD 2.00 minimum** per conversion.
On a EUR 1,000 conversion that minimum is 20 bps -- comparable to the entire
commission budget for a round trip. Converting per-trade would roughly double
total transaction costs on a EUR 10k account trading US stocks.

The architectural consequence is a rule, not just a number: **hold a standing
balance in each traded currency and convert in infrequent, large blocks.** This
module prices both policies so the difference is visible rather than assumed.

Note also that FX conversion is not the only currency cost. An unhedged USD
balance carries EUR/USD exposure, which for a Finnish investor is an
uncompensated risk that can easily swamp a 30 bps/trade edge. The system tracks
it explicitly in the portfolio's currency exposure report rather than pretending
base-currency returns are the same as local-currency returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from tradelab.core.money import ZERO, quantize_cash, to_decimal


@dataclass(frozen=True, slots=True)
class FxConversion:
    """One conversion, with its cost itemised."""

    from_currency: str
    to_currency: str
    from_amount: Decimal
    to_amount: Decimal
    rate: Decimal
    commission: Decimal
    commission_currency: str

    @property
    def cost_bps(self) -> Decimal:
        if self.from_amount == 0:
            return ZERO
        return (self.commission / self.from_amount) * Decimal("10000")


@dataclass(frozen=True, slots=True)
class FxCostModel:
    """IBKR IDEALPRO spot conversion cost.

    Defaults: 0.20 bp of converted notional, minimum USD 2.00 equivalent.
    """

    rate_bps: Decimal = Decimal("0.20")
    minimum: Decimal = Decimal("2.00")
    minimum_currency: str = "USD"

    def commission_for(self, notional: Decimal) -> Decimal:
        """Commission on a conversion of `notional`, in `minimum_currency`."""
        notional = abs(to_decimal(notional))
        if notional == 0:
            return ZERO
        return quantize_cash(max(notional * self.rate_bps / Decimal("10000"), self.minimum))

    def convert(
        self, from_currency: str, to_currency: str, amount: Decimal, rate: Decimal
    ) -> FxConversion:
        """Convert `amount` of `from_currency` at `rate` (to per from)."""
        amount = to_decimal(amount)
        rate = to_decimal(rate)
        if rate <= 0:
            raise ValueError("fx rate must be positive")
        commission = self.commission_for(amount)
        return FxConversion(
            from_currency=from_currency.upper(),
            to_currency=to_currency.upper(),
            from_amount=quantize_cash(amount),
            to_amount=quantize_cash(amount * rate),
            rate=rate,
            commission=commission,
            commission_currency=self.minimum_currency,
        )


@dataclass
class FxPolicyComparison:
    """Compare per-trade conversion against block conversion over a trade set.

    `block_conversions` is the number of large conversions the block policy
    needs (e.g. 1 per month). The output is the annual cost difference, which is
    what makes the case for a standing foreign-currency balance concrete.
    """

    model: FxCostModel = field(default_factory=FxCostModel)

    def compare(
        self, trade_notionals: list[Decimal], block_conversions: int = 12
    ) -> dict[str, Decimal]:
        notionals = [abs(to_decimal(n)) for n in trade_notionals if to_decimal(n) != 0]
        if not notionals:
            return {
                "per_trade_cost": ZERO,
                "block_cost": ZERO,
                "saving": ZERO,
                "per_trade_bps": ZERO,
                "block_bps": ZERO,
            }
        total = sum(notionals, ZERO)
        # Per-trade: a conversion on entry and exit of every trade.
        per_trade = sum((self.model.commission_for(n) * 2 for n in notionals), ZERO)
        # Block: convert the aggregate in `block_conversions` equal tranches.
        tranche = total / Decimal(block_conversions)
        block = self.model.commission_for(tranche) * Decimal(block_conversions)
        return {
            "per_trade_cost": quantize_cash(per_trade),
            "block_cost": quantize_cash(block),
            "saving": quantize_cash(per_trade - block),
            "per_trade_bps": quantize_cash((per_trade / total) * Decimal("10000")),
            "block_bps": quantize_cash((block / total) * Decimal("10000")),
        }
