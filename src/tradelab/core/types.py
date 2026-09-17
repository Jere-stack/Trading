"""Core domain types: instruments, market data, orders, fills, positions.

These are plain frozen dataclasses rather than pydantic models. They sit on the
hot path of the backtest loop (millions of constructions), and pydantic
validation there costs more than it buys. Validation happens at the boundaries:
config loading (pydantic), risk gate (explicit checks), broker adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from tradelab.core.enums import (
    AssetClass,
    LiquidityFlag,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
    Venue,
)
from tradelab.core.money import ZERO, quantize_cash, quantize_price, quantize_qty, safe_div


@dataclass(frozen=True, slots=True)
class Instrument:
    """A tradable stock or ETF.

    `adv` (average daily volume, shares), `sigma_daily` (daily return stdev) and
    `spread_bps` (time-weighted median quoted spread) are calibration inputs
    carried on the instrument rather than assumed globally.

    Spread in particular *must* be per-instrument. A single global default is
    the most dangerous simplification in retail backtesting: Apple quotes ~1 bp
    while a Helsinki micro-cap quotes 100-300 bps, so one assumption is wrong by
    two orders of magnitude for half the universe -- and wrong in the flattering
    direction for exactly the illiquid names where naive signals look strongest.

    `adv` is the basis of the capacity check, the constraint that actually binds
    at EUR 10k-50k: a "profitable" strategy can be untradable simply because it
    wants 5% of a stock's daily volume.
    """

    symbol: str
    venue: Venue = Venue.SMART
    currency: str = "USD"
    asset_class: AssetClass = AssetClass.EQUITY
    primary_exchange: Venue | None = None
    lot_size: Decimal = Decimal("1")
    tick_size: Decimal = Decimal("0.01")
    adv: Decimal | None = None
    sigma_daily: Decimal | None = None
    spread_bps: Decimal | None = None
    multiplier: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("instrument symbol must be non-empty")
        if self.lot_size <= 0:
            raise ValueError(f"{self.symbol}: lot_size must be positive")
        if self.tick_size <= 0:
            raise ValueError(f"{self.symbol}: tick_size must be positive")

    @property
    def key(self) -> str:
        """Stable identity across venues, e.g. `AAPL.SMART.USD`."""
        return f"{self.symbol}.{self.venue.value}.{self.currency}"

    def __str__(self) -> str:
        return self.key


@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV bar. `timestamp` is the bar's **close** time, always UTC.

    Labelling bars by close time (not open time) is a deliberate anti-lookahead
    choice: a bar stamped 16:00 is only knowable at 16:00, so any comparison of
    `bar.timestamp <= clock.now()` is automatically safe.
    """

    instrument: Instrument
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal | None = None
    trades: int | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError(f"{self.instrument.symbol}: bar timestamp must be tz-aware")
        if self.low > self.high:
            raise ValueError(
                f"{self.instrument.symbol} @ {self.timestamp.isoformat()}: "
                f"low {self.low} > high {self.high}"
            )
        if self.volume < 0:
            raise ValueError(f"{self.instrument.symbol}: negative volume {self.volume}")

    @property
    def typical_price(self) -> Decimal:
        return (self.high + self.low + self.close) / Decimal("3")

    @property
    def true_range(self) -> Decimal:
        return self.high - self.low

    @property
    def dollar_volume(self) -> Decimal:
        ref = self.vwap if self.vwap is not None else self.close
        return ref * self.volume


@dataclass(frozen=True, slots=True)
class Quote:
    """Top-of-book snapshot. The authoritative source of spread cost."""

    instrument: Instrument
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    bid_size: Decimal = ZERO
    ask_size: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError(f"{self.instrument.symbol}: quote timestamp must be tz-aware")
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError(
                f"{self.instrument.symbol}: non-positive quote bid={self.bid} ask={self.ask}"
            )

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> Decimal:
        """Spread as basis points of mid. Negative/crossed books yield <= 0."""
        return safe_div(self.spread, self.mid) * Decimal("10000")

    @property
    def is_crossed(self) -> bool:
        return self.bid > self.ask

    def touch(self, side: Side) -> Decimal:
        """Price a taker of `side` would hit: BUY lifts the ask, SELL hits the bid."""
        return self.ask if side is Side.BUY else self.bid


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """What a strategy emits. Deliberately has no broker id and no status.

    Strategies express *intent*. The risk engine may reject or resize it, and the
    router assigns identity. Keeping intent and state in separate types means a
    strategy structurally cannot mutate live order state.
    """

    instrument: Instrument
    side: Side
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    strategy_id: str = "unassigned"
    reason: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(
                f"{self.instrument.symbol}: order quantity must be strictly positive "
                f"(got {self.quantity}); direction is carried by `side`, not by sign"
            )
        needs_limit = self.order_type in (OrderType.LIMIT, OrderType.LIMIT_ON_CLOSE)
        if needs_limit and self.limit_price is None:
            raise ValueError(f"{self.instrument.symbol}: {self.order_type} requires limit_price")
        if not needs_limit and self.limit_price is not None:
            raise ValueError(
                f"{self.instrument.symbol}: limit_price supplied for {self.order_type}"
            )
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError(f"{self.instrument.symbol}: limit_price must be positive")

    def with_quantity(self, quantity: Decimal) -> OrderRequest:
        """Return a copy resized to `quantity` (used by the risk engine)."""
        return replace(self, quantity=quantize_qty(quantity))

    @property
    def signed_quantity(self) -> Decimal:
        return self.quantity * self.side.sign

    def notional_at(self, price: Decimal) -> Decimal:
        return self.quantity * price * self.instrument.multiplier


@dataclass(frozen=True, slots=True)
class Fill:
    """A single execution. Costs are itemised, never netted into the price.

    Keeping commission, spread and impact separate is what makes it possible to
    answer "is this strategy dead because of commission or because of impact?"
    -- which determines whether trading less often or trading smaller is the fix.
    """

    fill_id: str
    order_id: str
    instrument: Instrument
    side: Side
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    commission: Decimal = ZERO
    fees: Decimal = ZERO
    spread_cost: Decimal = ZERO
    impact_cost: Decimal = ZERO
    liquidity: LiquidityFlag = LiquidityFlag.UNKNOWN
    reference_price: Decimal | None = None
    strategy_id: str = "unassigned"

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"fill {self.fill_id}: quantity must be positive")
        if self.price <= 0:
            raise ValueError(f"fill {self.fill_id}: price must be positive")

    @property
    def gross_notional(self) -> Decimal:
        return self.quantity * self.price * self.instrument.multiplier

    @property
    def total_cost(self) -> Decimal:
        """Explicit costs only (commission + fees).

        `spread_cost` and `impact_cost` are *implicit* -- they are already
        embedded in `price` relative to `reference_price`. Adding them here
        would double-count. They are recorded for attribution only.
        """
        return self.commission + self.fees

    @property
    def cash_delta(self) -> Decimal:
        """Signed cash impact: buying consumes cash, selling releases it."""
        return quantize_cash(-self.side.sign * self.gross_notional - self.total_cost)

    @property
    def implementation_shortfall(self) -> Decimal:
        """Cost vs the decision price, in currency units. Positive = worse."""
        if self.reference_price is None:
            return ZERO
        slip = (self.price - self.reference_price) * self.side.sign
        return quantize_cash(slip * self.quantity * self.instrument.multiplier + self.total_cost)


@dataclass(slots=True)
class Order:
    """Mutable order state, owned exclusively by the execution layer."""

    order_id: str
    request: OrderRequest
    status: OrderStatus = OrderStatus.PENDING_NEW
    broker_order_id: str | None = None
    filled_quantity: Decimal = ZERO
    average_fill_price: Decimal = ZERO
    commission: Decimal = ZERO
    fees: Decimal = ZERO
    created_at: datetime | None = None
    updated_at: datetime | None = None
    reject_reason: str | None = None
    fills: list[Fill] = field(default_factory=list)

    @property
    def instrument(self) -> Instrument:
        return self.request.instrument

    @property
    def side(self) -> Side:
        return self.request.side

    @property
    def strategy_id(self) -> str:
        return self.request.strategy_id

    @property
    def leaves_quantity(self) -> Decimal:
        """Unfilled remainder. This is the quantity still at risk in the market."""
        return max(ZERO, self.request.quantity - self.filled_quantity)

    @property
    def is_open(self) -> bool:
        return self.status.is_open

    def apply_fill(self, fill: Fill) -> None:
        """Fold a fill into order state, maintaining a volume-weighted average.

        Over-fills are rejected rather than clamped: a broker reporting more
        filled quantity than requested means the reconciliation view is wrong,
        and silently accepting it would corrupt the position ledger.
        """
        if fill.order_id != self.order_id:
            raise ValueError(
                f"fill {fill.fill_id} belongs to order {fill.order_id}, not {self.order_id}"
            )
        new_filled = self.filled_quantity + fill.quantity
        if new_filled > self.request.quantity:
            raise ValueError(
                f"order {self.order_id}: fill would over-fill "
                f"({new_filled} > {self.request.quantity})"
            )
        prior_value = self.average_fill_price * self.filled_quantity
        self.average_fill_price = quantize_price(
            (prior_value + fill.price * fill.quantity) / new_filled
        )
        self.filled_quantity = quantize_qty(new_filled)
        self.commission = quantize_cash(self.commission + fill.commission)
        self.fees = quantize_cash(self.fees + fill.fees)
        self.fills.append(fill)
        self.status = (
            OrderStatus.FILLED
            if self.filled_quantity >= self.request.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        self.updated_at = fill.timestamp


@dataclass(slots=True)
class Position:
    """Net position in one instrument, with realised/unrealised P&L split.

    Uses average-cost basis. Note that this is *not* the same as the FIFO basis
    the Finnish Tax Administration requires for capital gains; tax lots are
    tracked separately in the accounting layer. Conflating the two is a common
    and expensive mistake.
    """

    instrument: Instrument
    quantity: Decimal = ZERO
    average_price: Decimal = ZERO
    realised_pnl: Decimal = ZERO
    total_commission: Decimal = ZERO
    total_fees: Decimal = ZERO
    last_price: Decimal = ZERO
    opened_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def market_value(self) -> Decimal:
        """Signed mark-to-market value. Negative for shorts."""
        return quantize_cash(self.quantity * self.last_price * self.instrument.multiplier)

    @property
    def gross_exposure(self) -> Decimal:
        return abs(self.market_value)

    @property
    def cost_basis(self) -> Decimal:
        return quantize_cash(self.quantity * self.average_price * self.instrument.multiplier)

    @property
    def unrealised_pnl(self) -> Decimal:
        if self.quantity == 0:
            return ZERO
        return quantize_cash(
            (self.last_price - self.average_price) * self.quantity * self.instrument.multiplier
        )

    @property
    def total_pnl(self) -> Decimal:
        """Net of all explicit costs incurred over the position's life."""
        return quantize_cash(
            self.realised_pnl + self.unrealised_pnl - self.total_commission - self.total_fees
        )

    def mark(self, price: Decimal, timestamp: datetime | None = None) -> None:
        if price <= 0:
            raise ValueError(f"{self.instrument.symbol}: cannot mark at non-positive {price}")
        self.last_price = quantize_price(price)
        if timestamp is not None:
            self.updated_at = timestamp

    def apply_fill(self, fill: Fill) -> Decimal:
        """Apply a fill and return the realised P&L generated by it.

        Handles the four cases explicitly -- open/increase, partial reduce, full
        close, and flip through zero. The flip case is the one that is usually
        wrong in hand-rolled ledgers: the closing leg realises P&L at the old
        average, and only the residual opens a new position at the fill price.
        """
        if fill.instrument.key != self.instrument.key:
            raise ValueError(
                f"fill for {fill.instrument.key} applied to position {self.instrument.key}"
            )
        delta = fill.quantity * fill.side.sign
        prior_qty = self.quantity
        new_qty = prior_qty + delta
        realised = ZERO

        if prior_qty == 0:
            self.average_price = fill.price
            self.opened_at = fill.timestamp
        elif (prior_qty > 0) == (delta > 0):
            # Increasing an existing position: blend the average price.
            total = prior_qty * self.average_price + delta * fill.price
            self.average_price = quantize_price(total / new_qty)
        else:
            closing_qty = min(abs(delta), abs(prior_qty))
            direction = Decimal(1) if prior_qty > 0 else Decimal(-1)
            realised = quantize_cash(
                (fill.price - self.average_price)
                * closing_qty
                * direction
                * self.instrument.multiplier
            )
            if abs(delta) > abs(prior_qty):
                # Flipped through zero: residual opens fresh at the fill price.
                self.average_price = fill.price
                self.opened_at = fill.timestamp
            elif new_qty == 0:
                self.average_price = ZERO

        self.quantity = quantize_qty(new_qty)
        self.realised_pnl = quantize_cash(self.realised_pnl + realised)
        self.total_commission = quantize_cash(self.total_commission + fill.commission)
        self.total_fees = quantize_cash(self.total_fees + fill.fees)
        self.last_price = fill.price
        self.updated_at = fill.timestamp
        if self.quantity == 0:
            self.opened_at = None
        return realised
