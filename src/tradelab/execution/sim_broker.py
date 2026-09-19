"""Simulated broker: realistic fills for backtest and paper trading.

The fill model is where backtests are won and lost. The rules below are chosen
to be *pessimistic where reality is uncertain*, because the asymmetry of errors
is brutal: an optimistic simulator deploys strategies that lose real money,
while a pessimistic one merely discards some that might have worked.

Specific anti-lookahead and anti-optimism rules:

1. **A market order never fills at the price that triggered it.** A signal
   computed from a bar's close fills at the *next* bar, because the close is
   only knowable once the bar is over. Filling at the signal bar's close is the
   single most common source of fake backtest edge.

2. **Limit orders require the price to trade strictly through the limit**, not
   merely touch it. Touching means your order was somewhere in a queue at that
   price; assuming a fill assumes queue priority you did not have. This is the
   second most common source of fake edge, and it is what makes naive
   mean-reversion backtests look extraordinary.

3. **Volume participation caps fills.** An order for 20% of a bar's volume does
   not fill in that bar. Partial fills are the realistic outcome.

4. **Spread is always paid by takers**, sourced from real quotes where
   available, and inflated by a configurable multiplier to reflect that signals
   cluster in wide-spread conditions.

5. **Gaps fill at the open, not the limit.** If a stock gaps through a buy
   limit, the fill is at the open, which is worse. Filling at the limit price
   manufactures a profit from an adverse gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tradelab.core.clock import Clock
from tradelab.core.enums import (
    LiquidityFlag,
    OrderStatus,
    OrderType,
    RunMode,
    Side,
    TimeInForce,
)
from tradelab.core.ids import IdGenerator
from tradelab.core.money import ZERO, quantize_price, round_to_lot
from tradelab.core.types import Bar, Fill, Instrument, Order, OrderRequest, Position, Quote
from tradelab.costs.commission import CommissionModel, ibkr_default_router
from tradelab.costs.slippage import SlippageModel, SpreadImpactSlippage
from tradelab.execution.broker import Broker, BrokerAccount, OrderBook
from tradelab.portfolio.portfolio import Portfolio


@dataclass
class SimulationConfig:
    """Fill-realism knobs. Defaults are deliberately conservative."""

    max_volume_participation: Decimal = Decimal("0.05")
    """Max fraction of a bar's volume one order may consume."""

    require_limit_penetration: bool = True
    """Require price to trade *through* a limit, not merely touch it."""

    fill_on_next_bar: bool = True
    """Market orders fill on the bar after the signal. Turning this off
    introduces lookahead bias and is only for diagnostic comparison."""

    allow_partial_fills: bool = True

    reject_if_no_volume: bool = True
    """A bar with zero volume is a halt or a data gap. No fill either way."""

    limit_fill_probability: Decimal = Decimal("1.0")
    """Optional extra haircut on limit fills to model queue position. 1.0 fills
    whenever penetration occurs; lower values model losing the queue race."""


@dataclass
class SimulatedBroker(Broker):
    """Deterministic fill simulator driven by bars and (optionally) quotes."""

    clock: Clock
    portfolio: Portfolio
    mode: RunMode = RunMode.BACKTEST
    commission_model: CommissionModel = field(default_factory=ibkr_default_router)
    slippage_model: SlippageModel = field(default_factory=SpreadImpactSlippage)
    config: SimulationConfig = field(default_factory=SimulationConfig)
    book: OrderBook = field(default_factory=OrderBook)
    account_id: str = "SIM"
    owns_ledger: bool = True
    """Whether this broker applies fills to `portfolio` itself.

    True under `BacktestEngine`, which lets the broker own the ledger. **False
    under `LiveRunner`**, which applies fills in its own `_on_fill` handler
    after persisting them.

    Getting this wrong double-counts every fill: the position doubles, cash
    goes twice as negative, and the leverage guard trips on a book that was
    never actually built. A real broker never touches your ledger, so the
    simulator must be told when it is standing in for one.
    """
    _connected: bool = False
    _bars: dict[str, Bar] = field(default_factory=dict)
    _quotes: dict[str, Quote] = field(default_factory=dict)
    _ids: IdGenerator | None = None
    _submitted_at: dict[str, datetime] = field(default_factory=dict)
    _eligible_date: dict[str, object] = field(default_factory=dict)
    _fill_callbacks: list = field(default_factory=list)
    _order_callbacks: list = field(default_factory=list)
    rejected_for_capacity: int = 0

    def __post_init__(self) -> None:
        if self._ids is None:
            self._ids = IdGenerator(run_id=f"sim-{self.account_id}")

    # ------------------------------------------------------------ connection

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------ market data

    def update_bar(self, bar: Bar) -> None:
        self._bars[bar.instrument.key] = bar

    def update_quote(self, quote: Quote) -> None:
        self._quotes[quote.instrument.key] = quote

    def last_bar(self, instrument: Instrument) -> Bar | None:
        return self._bars.get(instrument.key)

    # ------------------------------------------------------------------ orders

    def submit(self, order_id: str, request: OrderRequest) -> Order:
        now = self.clock.now()
        order = Order(
            order_id=order_id,
            request=request,
            status=OrderStatus.NEW,
            broker_order_id=f"sim-{order_id}",
            created_at=now,
            updated_at=now,
        )
        self.book.add(order)
        self._submitted_at[order_id] = now
        self._emit_order(order)
        return order

    def cancel(self, order_id: str) -> bool:
        cancelled = self.book.mark_cancelled(order_id, self.clock.now())
        if cancelled:
            order = self.book.get(order_id)
            if order is not None:
                self._emit_order(order)
        return cancelled

    def open_orders(self) -> list[Order]:
        return self.book.open()

    def positions(self) -> list[Position]:
        return [p for p in self.portfolio.positions.values() if not p.is_flat]

    def account(self) -> BrokerAccount:
        return BrokerAccount(
            account_id=self.account_id,
            base_currency=self.portfolio.base_currency,
            cash=dict(self.portfolio.cash),
            equity=self.portfolio.equity,
            buying_power=max(ZERO, self.portfolio.cash_base),
        )

    # ------------------------------------------------------------- fill engine

    def process_bar(self, bar: Bar) -> list[Fill]:
        """Attempt to fill open orders for `bar`'s instrument against it.

        Called by the engine *after* the bar has been published to strategies,
        so an order placed on this bar is only eligible from the next one --
        which is rule 1 in the module docstring, enforced structurally.
        """
        self.update_bar(bar)
        fills: list[Fill] = []
        for order in self.book.open_for(bar.instrument):
            if not self._is_eligible(order, bar):
                continue
            # Record the first session in which this order could actually trade.
            first_session = self._eligible_date.setdefault(order.order_id, bar.timestamp.date())
            if self._is_expired(order, bar, first_session):
                order.status = OrderStatus.EXPIRED
                order.updated_at = bar.timestamp
                self._emit_order(order)
                continue
            fill = self._try_fill(order, bar)
            if fill is not None:
                fills.append(fill)
        return fills

    def _is_eligible(self, order: Order, bar: Bar) -> bool:
        """Whether `order` may trade against `bar`.

        Under `fill_on_next_bar`, an order is ineligible on the bar that
        produced its signal. This is the structural anti-lookahead rule: the
        close that triggered the order was not knowable until the bar ended.
        """
        if not self.config.fill_on_next_bar:
            return True
        submitted = self._submitted_at.get(order.order_id)
        return submitted is None or bar.timestamp > submitted

    def _is_expired(self, order: Order, bar: Bar, first_session) -> bool:
        """DAY orders get exactly one session of *eligibility*, not of existence.

        Measuring the window from submission would make DAY orders unfillable on
        daily bars: an order created at the close of session N is first eligible
        in session N+1, so a submission-based window expires it before it ever
        trades. Real DAY orders behave the same way -- one submitted after the
        close is worked in the following session.
        """
        if order.request.time_in_force is not TimeInForce.DAY:
            return False
        return bar.timestamp.date() > first_session

    def _try_fill(self, order: Order, bar: Bar) -> Fill | None:
        if self.config.reject_if_no_volume and bar.volume <= 0:
            return None

        quantity = self._fillable_quantity(order, bar)
        if quantity <= 0:
            return None

        reference, price, liquidity, est = self._fill_price(order, bar, quantity)
        if price is None:
            return None

        inst = order.instrument
        commission = self.commission_model.calculate(inst, order.side, quantity, price, liquidity)
        fill = Fill(
            fill_id=self._ids.next("fill"),
            order_id=order.order_id,
            instrument=inst,
            side=order.side,
            quantity=quantity,
            price=price,
            timestamp=bar.timestamp,
            commission=commission.broker + commission.clearing,
            fees=commission.exchange + commission.regulatory,
            spread_cost=(est.spread_cost_per_share * quantity) if est else ZERO,
            impact_cost=(est.impact_cost_per_share * quantity) if est else ZERO,
            liquidity=liquidity,
            reference_price=reference,
            strategy_id=order.strategy_id,
        )
        order.apply_fill(fill)
        if self.owns_ledger:
            self.portfolio.apply_fill(fill)
        self._emit_fill(fill)
        self._emit_order(order)
        return fill

    def _fillable_quantity(self, order: Order, bar: Bar) -> Decimal:
        wanted = order.leaves_quantity
        if wanted <= 0:
            return ZERO
        cap = bar.volume * self.config.max_volume_participation
        if wanted <= cap:
            return wanted
        if not self.config.allow_partial_fills:
            self.rejected_for_capacity += 1
            return ZERO
        self.rejected_for_capacity += 1
        return round_to_lot(cap, order.instrument.lot_size)

    def _fill_price(self, order: Order, bar: Bar, quantity: Decimal):
        """Return (reference_price, fill_price, liquidity, slippage_estimate)."""
        otype = order.request.order_type
        quote = self._quotes.get(order.instrument.key)

        if otype in (OrderType.MARKET, OrderType.MARKET_ON_OPEN, OrderType.MARKET_ON_CLOSE):
            reference = self._market_reference(otype, bar)
            est = self.slippage_model.estimate(
                order.instrument,
                order.side,
                quantity,
                reference,
                order_type=otype,
                quote=quote,
                bar=bar,
            )
            # Clamp to the bar's traded range: a fill outside [low, high] is
            # not a fill that could have happened.
            price = min(max(est.fill_price, bar.low), bar.high)
            return reference, quantize_price(price), LiquidityFlag.TAKER, est

        if otype in (OrderType.LIMIT, OrderType.LIMIT_ON_CLOSE):
            limit = order.request.limit_price
            if limit is None:
                return None, None, LiquidityFlag.UNKNOWN, None
            filled_price = self._limit_fill_price(order.side, limit, bar)
            if filled_price is None:
                return None, None, LiquidityFlag.UNKNOWN, None
            est = self.slippage_model.estimate(
                order.instrument,
                order.side,
                quantity,
                filled_price,
                order_type=otype,
                quote=quote,
                bar=bar,
            )
            return limit, quantize_price(filled_price), LiquidityFlag.MAKER, est

        return None, None, LiquidityFlag.UNKNOWN, None

    def _market_reference(self, otype: OrderType, bar: Bar) -> Decimal:
        if otype is OrderType.MARKET_ON_OPEN:
            return bar.open
        if otype is OrderType.MARKET_ON_CLOSE:
            return bar.close
        # A plain market order arriving during the bar is referenced to the open,
        # which is the first price actually available to it.
        return bar.open

    def _limit_fill_price(self, side: Side, limit: Decimal, bar: Bar) -> Decimal | None:
        """Price at which a resting limit order fills during `bar`, or None.

        Handles the gap case explicitly: if the bar opens through the limit, the
        fill is at the *open*, which is worse for us than the limit. Filling at
        the limit would book a windfall from an adverse gap.
        """
        strict = self.config.require_limit_penetration
        if side is Side.BUY:
            if bar.open <= limit:
                return bar.open
            crossed = bar.low < limit if strict else bar.low <= limit
            return limit if crossed else None
        if bar.open >= limit:
            return bar.open
        crossed = bar.high > limit if strict else bar.high >= limit
        return limit if crossed else None
