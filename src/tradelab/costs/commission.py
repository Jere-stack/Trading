"""Commission models.

Every rate here is a **default that must be recalibrated against real broker
statements** before any result is trusted. The numbers below reflect Interactive
Brokers' published retail schedules as of 2026-09, but broker pricing changes
and exchange pass-through fees vary by venue and order flow. `CostCalibration`
in `tradelab.costs.calibration` exists to diff modelled cost against realised
statement cost; a model that has never been diffed against a statement is a
guess.

Why this matters more than the strategy: at EUR 10k with EUR 1.25-1.00 minimum
per order, a EUR 1,000 position pays ~12.5 bps one way in commission alone, so
~25 bps round trip before spread. A strategy needs a gross edge above roughly
40-50 bps per round trip merely to break even. Most published retail signals
have a gross edge well below that.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from tradelab.core.enums import LiquidityFlag, Side
from tradelab.core.money import ZERO, quantize_cash, to_decimal
from tradelab.core.types import Instrument


@dataclass(frozen=True, slots=True)
class CommissionBreakdown:
    """Itemised so that attribution can distinguish broker vs venue vs regulator."""

    broker: Decimal = ZERO
    exchange: Decimal = ZERO
    regulatory: Decimal = ZERO
    clearing: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return quantize_cash(self.broker + self.exchange + self.regulatory + self.clearing)

    def __add__(self, other: CommissionBreakdown) -> CommissionBreakdown:
        return CommissionBreakdown(
            broker=self.broker + other.broker,
            exchange=self.exchange + other.exchange,
            regulatory=self.regulatory + other.regulatory,
            clearing=self.clearing + other.clearing,
        )


class CommissionModel(ABC):
    """Maps an execution to its explicit cost."""

    @abstractmethod
    def calculate(
        self,
        instrument: Instrument,
        side: Side,
        quantity: Decimal,
        price: Decimal,
        liquidity: LiquidityFlag = LiquidityFlag.TAKER,
    ) -> CommissionBreakdown: ...

    def total(
        self,
        instrument: Instrument,
        side: Side,
        quantity: Decimal,
        price: Decimal,
        liquidity: LiquidityFlag = LiquidityFlag.TAKER,
    ) -> Decimal:
        return self.calculate(instrument, side, quantity, price, liquidity).total


@dataclass(frozen=True, slots=True)
class ZeroCommission(CommissionModel):
    """No commission. Only legitimate for isolating cost sensitivity in research.

    Never use this to produce a headline backtest result. Its purpose is to
    answer "how much of this strategy's return is eaten by commission?" by
    differencing against a real model.
    """

    def calculate(self, instrument, side, quantity, price, liquidity=LiquidityFlag.TAKER):
        return CommissionBreakdown()


@dataclass(frozen=True, slots=True)
class PerShareCommission(CommissionModel):
    """IBKR "Fixed" US equity shape: per-share rate, floor, and a value cap.

    Defaults: USD 0.005/share, min USD 1.00, capped at 1% of trade value.
    The cap is what makes sub-dollar stocks tradable at all; the floor is what
    makes small orders uneconomic.
    """

    rate_per_share: Decimal = Decimal("0.005")
    minimum_per_order: Decimal = Decimal("1.00")
    max_pct_of_value: Decimal | None = Decimal("0.01")

    def calculate(self, instrument, side, quantity, price, liquidity=LiquidityFlag.TAKER):
        quantity = abs(to_decimal(quantity))
        if quantity == 0:
            return CommissionBreakdown()
        value = quantity * to_decimal(price) * instrument.multiplier
        raw = quantity * self.rate_per_share
        charged = max(raw, self.minimum_per_order)
        if self.max_pct_of_value is not None:
            # The cap overrides the floor -- IBKR applies the max when the
            # computed maximum is below the minimum.
            charged = min(charged, value * self.max_pct_of_value)
        return CommissionBreakdown(broker=quantize_cash(charged))


@dataclass(frozen=True, slots=True)
class PercentOfValueCommission(CommissionModel):
    """IBKR European/Nordic shape: percent of trade value with a floor.

    Defaults are the Nasdaq Helsinki (EUR) retail schedule: 0.05% of trade
    value, minimum EUR 1.25 per order.
    """

    rate: Decimal = Decimal("0.0005")
    minimum_per_order: Decimal = Decimal("1.25")
    maximum_per_order: Decimal | None = None

    def calculate(self, instrument, side, quantity, price, liquidity=LiquidityFlag.TAKER):
        quantity = abs(to_decimal(quantity))
        if quantity == 0:
            return CommissionBreakdown()
        value = quantity * to_decimal(price) * instrument.multiplier
        charged = max(value * self.rate, self.minimum_per_order)
        if self.maximum_per_order is not None:
            charged = min(charged, self.maximum_per_order)
        return CommissionBreakdown(broker=quantize_cash(charged))


@dataclass(frozen=True, slots=True)
class IbkrTieredUsEquity(CommissionModel):
    """IBKR "Tiered" US equity: broker fee + venue fee/rebate + regulatory fees.

    Tiered is usually cheaper than Fixed for liquid names *if* you post
    liquidity, because the maker rebate can offset most of the broker fee. It is
    more expensive than Fixed when you always take liquidity at small size,
    because the USD 0.35 floor plus ~0.30 cents/share taker fee exceeds the flat
    USD 1.00 only above ~200 shares.

    Two regulatory fees apply to **sells only** (they fund the SEC and FINRA):
      - SEC Section 31 fee: a rate per dollar of principal, reset annually.
      - FINRA Trading Activity Fee: per share, capped per trade.

    Caveat carried from IBKR's own documentation: API orders that are *directed*
    to a specific venue cannot use Tiered pricing. Only SmartRouted API orders
    can. If the router directs, this model does not apply.
    """

    rate_per_share: Decimal = Decimal("0.0035")
    minimum_per_order: Decimal = Decimal("0.35")
    max_pct_of_value: Decimal = Decimal("0.005")
    taker_fee_per_share: Decimal = Decimal("0.0030")
    maker_rebate_per_share: Decimal = Decimal("0.0020")
    sec_fee_rate: Decimal = Decimal("0.0000278")
    finra_taf_per_share: Decimal = Decimal("0.000166")
    finra_taf_cap: Decimal = Decimal("8.30")
    clearing_per_share: Decimal = Decimal("0.00020")
    clearing_cap_pct: Decimal = Decimal("0.005")

    def calculate(self, instrument, side, quantity, price, liquidity=LiquidityFlag.TAKER):
        quantity = abs(to_decimal(quantity))
        if quantity == 0:
            return CommissionBreakdown()
        price = to_decimal(price)
        value = quantity * price * instrument.multiplier

        broker = max(quantity * self.rate_per_share, self.minimum_per_order)
        broker = min(broker, value * self.max_pct_of_value)

        if liquidity is LiquidityFlag.MAKER:
            exchange = -quantity * self.maker_rebate_per_share
        else:
            # UNKNOWN is treated as TAKER: assuming the unfavourable case keeps
            # the model conservative rather than flattering.
            exchange = quantity * self.taker_fee_per_share

        regulatory = ZERO
        if side is Side.SELL:
            regulatory += value * self.sec_fee_rate
            regulatory += min(quantity * self.finra_taf_per_share, self.finra_taf_cap)

        clearing = min(quantity * self.clearing_per_share, value * self.clearing_cap_pct)

        return CommissionBreakdown(
            broker=quantize_cash(broker),
            exchange=quantize_cash(exchange),
            regulatory=quantize_cash(regulatory),
            clearing=quantize_cash(clearing),
        )


@dataclass(frozen=True, slots=True)
class CurrencyCommissionRouter(CommissionModel):
    """Dispatch to a per-currency model, because venue schedules differ.

    A EUR 10k account trading both Helsinki and US names pays two different
    schedules; modelling one rate for both misstates cost in whichever venue is
    wrong, and typically in the flattering direction.
    """

    models: dict[str, CommissionModel]
    default: CommissionModel

    def calculate(self, instrument, side, quantity, price, liquidity=LiquidityFlag.TAKER):
        model = self.models.get(instrument.currency.upper(), self.default)
        return model.calculate(instrument, side, quantity, price, liquidity)


def ibkr_default_router() -> CurrencyCommissionRouter:
    """IBKR retail defaults for a Finnish resident trading EUR and USD stocks.

    USD uses **Tiered**, not Fixed. This is worth stating explicitly because the
    common advice is the reverse. Fixed charges a flat USD 1.00 floor; Tiered
    charges a USD 0.35 floor plus ~0.30 cents/share of taker fee. The crossover
    is near 130 shares:

        50 shares  -> Tiered ~USD 0.51 vs Fixed USD 1.00  (Tiered wins)
        200 shares -> Tiered ~USD 1.34 vs Fixed USD 1.00  (Fixed wins)

    A EUR 10k account taking EUR 500-1,500 positions holds well under 130 shares
    of any stock priced above ~USD 10, so Tiered is materially cheaper across
    this account's realistic order sizes. Accounts trading low-priced stocks in
    size should re-derive the crossover with `cheaper_us_schedule`.
    """
    return CurrencyCommissionRouter(
        models={
            "USD": IbkrTieredUsEquity(),
            "EUR": PercentOfValueCommission(
                rate=Decimal("0.0005"), minimum_per_order=Decimal("1.25")
            ),
            "SEK": PercentOfValueCommission(
                rate=Decimal("0.0005"), minimum_per_order=Decimal("49.00")
            ),
            "GBP": PercentOfValueCommission(
                rate=Decimal("0.0005"), minimum_per_order=Decimal("1.00")
            ),
        },
        default=PercentOfValueCommission(rate=Decimal("0.0010"), minimum_per_order=Decimal("4.00")),
    )


def ibkr_us_fixed() -> PerShareCommission:
    """IBKR "Fixed" US schedule, for comparison against Tiered."""
    return PerShareCommission(
        rate_per_share=Decimal("0.005"),
        minimum_per_order=Decimal("1.00"),
        max_pct_of_value=Decimal("0.01"),
    )


def cheaper_us_schedule(
    instrument: Instrument,
    side: Side,
    quantity: Decimal,
    price: Decimal,
    liquidity: LiquidityFlag = LiquidityFlag.TAKER,
) -> tuple[str, Decimal]:
    """Return the cheaper of IBKR Tiered/Fixed for one order, as (name, cost).

    Use this to verify the schedule choice against your own realised order size
    distribution rather than trusting the heuristic in `ibkr_default_router`.
    The schedule is an account-level setting at the broker, so the decision
    should be driven by the *distribution* of order sizes, not a single order.
    """
    tiered = IbkrTieredUsEquity().total(instrument, side, quantity, price, liquidity)
    fixed = ibkr_us_fixed().total(instrument, side, quantity, price, liquidity)
    return ("tiered", tiered) if tiered <= fixed else ("fixed", fixed)
