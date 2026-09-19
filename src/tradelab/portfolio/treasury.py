"""Currency funding policy.

A EUR-based account buying USD stocks needs USD. Left alone, the USD balance
simply goes negative -- which at a real broker is a margin loan accruing
interest, and is the implicit leverage a cash-equity mandate forbids.

**The policy is block conversion, and the reason is arithmetic.** IBKR charges
0.20 bp with a USD 2.00 minimum, so on a EUR 500 conversion the minimum alone
is 40 bps. Measured on the first paper run, which needed 7,717 USD across 19
fills:

    converting per trade   USD 76.00
    converting in blocks   USD  2.00

A 38x difference, and the reason `tradelab.costs.fx` exists. Converting on
demand for each order is the single most expensive way to run a
multi-currency account at this size.

So the policy converts in **chunks sized well beyond immediate need**, holding
a standing foreign balance and topping it up rarely. That trades a small,
deliberate FX exposure for a large, certain saving on conversion cost.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal

from tradelab.core.money import ZERO, quantize_cash, to_decimal
from tradelab.costs.fx import FxCostModel
from tradelab.portfolio.portfolio import Portfolio


@dataclass(frozen=True)
class FundingAction:
    """A conversion the policy decided to make."""

    from_currency: str
    to_currency: str
    amount_from: Decimal
    amount_to: Decimal
    rate: Decimal
    cost: Decimal
    reason: str

    def __str__(self) -> str:
        return (
            f"convert {self.amount_from:,.2f} {self.from_currency} -> "
            f"{self.amount_to:,.2f} {self.to_currency} "
            f"(cost {self.cost:,.2f}, {self.reason})"
        )


class FxPolicy(ABC):
    """Decides whether and how much currency to convert before trading."""

    @abstractmethod
    def fund(self, portfolio: Portfolio, currency: str, required: Decimal) -> FundingAction | None:
        """Ensure `required` units of `currency` are available, or return None."""


@dataclass
class NoFxPolicy(FxPolicy):
    """Never converts. Foreign balances are allowed to go negative.

    Research-only, and misleading if used for a headline result: it silently
    grants an interest-free margin loan in the foreign currency.
    """

    def fund(self, portfolio, currency, required):
        return None


@dataclass
class BlockFxPolicy(FxPolicy):
    """Convert in large, infrequent blocks rather than per trade."""

    cost_model: FxCostModel = field(default_factory=FxCostModel)
    min_block: Decimal = Decimal("2500")
    """Smallest conversion to make. Below this the USD 2.00 minimum dominates:
    at EUR 2,500 it is 0.8 bp, at EUR 500 it is 40 bp."""

    buffer_multiple: Decimal = Decimal("1.5")
    """Convert this multiple of the immediate need, so the next few orders do
    not each trigger another conversion."""

    max_foreign_share: Decimal = Decimal("0.70")
    """Ceiling on how much of the account may sit in one foreign currency.
    Unhedged FX is uncompensated risk -- measured EUR/USD volatility is 6.70%
    a year, which dwarfs any per-trade edge this account can earn."""

    def fund(self, portfolio: Portfolio, currency: str, required: Decimal) -> FundingAction | None:
        currency = currency.upper()
        base = portfolio.base_currency
        if currency == base:
            return None

        required = to_decimal(required)
        held = portfolio.cash.get(currency, ZERO)
        shortfall = required - held
        if shortfall <= 0:
            return None

        target = max(shortfall * self.buffer_multiple, self.min_block)

        # Respect the foreign-exposure ceiling, measured in base currency.
        equity = portfolio.equity
        rate_to_base = portfolio.fx_rate(currency)
        if equity > 0:
            current_base = (held + target) * rate_to_base
            ceiling_base = equity * self.max_foreign_share
            if current_base > ceiling_base:
                allowed = ceiling_base / rate_to_base - held
                target = min(target, allowed)

        if target < shortfall:
            # Cannot fund the order without breaching the FX ceiling. Convert
            # nothing rather than partially: a half-funded order would still
            # drive the balance negative.
            return None

        # base -> currency, so the rate is the inverse of currency -> base.
        base_needed = quantize_cash(target * rate_to_base)
        available_base = portfolio.cash.get(base, ZERO)
        if base_needed > available_base:
            return None

        cost = self.cost_model.commission_for(base_needed)
        rate = to_decimal(1) / rate_to_base
        credited = portfolio.convert(
            base, currency, base_needed, rate, cost=cost, cost_currency=currency
        )
        return FundingAction(
            from_currency=base,
            to_currency=currency,
            amount_from=base_needed,
            amount_to=credited,
            rate=rate,
            cost=cost,
            reason=(f"shortfall {shortfall:,.2f} {currency}, converted a {target:,.2f} block"),
        )
