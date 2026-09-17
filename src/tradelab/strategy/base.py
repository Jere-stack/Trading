"""Strategy interface.

A strategy receives data and emits `OrderRequest` intents. It deliberately
cannot:

* see the broker (so it cannot bypass the risk gate),
* see the future (the context exposes only bars already closed),
* read the wall clock (it gets `ctx.now`, which the engine controls),
* mutate the portfolio (it gets a read-only view).

These are structural restrictions, not guidelines. Every one of them closes a
route to a backtest that cannot be reproduced live. The most valuable is the
data window: `ctx.history()` returns only bars strictly at or before the current
bar, so lookahead requires deliberate circumvention rather than an off-by-one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tradelab.core.enums import OrderType, Side, TimeInForce
from tradelab.core.money import ZERO
from tradelab.core.types import Bar, Fill, Instrument, OrderRequest, Position, Quote


@dataclass
class StrategyContext:
    """Read-only view of the world, plus the order intake."""

    now: datetime
    strategy_id: str
    equity: Decimal
    _bars: dict[str, deque[Bar]] = field(default_factory=dict)
    _quotes: dict[str, Quote] = field(default_factory=dict)
    _positions: dict[str, Position] = field(default_factory=dict)
    _pending: list[OrderRequest] = field(default_factory=list)
    _open_order_keys: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------- data

    def history(self, instrument: Instrument, lookback: int | None = None) -> list[Bar]:
        """Closed bars for `instrument`, oldest first. Never includes the future."""
        bars = self._bars.get(instrument.key)
        if not bars:
            return []
        items = list(bars)
        return items if lookback is None else items[-lookback:]

    def last_bar(self, instrument: Instrument) -> Bar | None:
        bars = self._bars.get(instrument.key)
        return bars[-1] if bars else None

    def last_price(self, instrument: Instrument) -> Decimal | None:
        quote = self._quotes.get(instrument.key)
        if quote is not None and not quote.is_crossed:
            return quote.mid
        bar = self.last_bar(instrument)
        return bar.close if bar else None

    def quote(self, instrument: Instrument) -> Quote | None:
        return self._quotes.get(instrument.key)

    def closes(self, instrument: Instrument, lookback: int | None = None) -> list[Decimal]:
        return [b.close for b in self.history(instrument, lookback)]

    # -------------------------------------------------------------- positions

    def position(self, instrument: Instrument) -> Position | None:
        return self._positions.get(instrument.key)

    def quantity(self, instrument: Instrument) -> Decimal:
        pos = self._positions.get(instrument.key)
        return pos.quantity if pos else ZERO

    def is_flat(self, instrument: Instrument) -> bool:
        return self.quantity(instrument) == 0

    def has_open_order(self, instrument: Instrument) -> bool:
        """True if a working order exists.

        Strategies should check this before submitting. Without it, a signal that
        persists across bars submits a new order every bar, and the rate limiter
        ends up doing the strategy's thinking for it.
        """
        return instrument.key in self._open_order_keys

    # ----------------------------------------------------------------- orders

    def submit(self, request: OrderRequest) -> None:
        """Queue an order intent. It still has to clear the risk gate."""
        if request.strategy_id in ("", "unassigned"):
            request = OrderRequest(
                instrument=request.instrument,
                side=request.side,
                quantity=request.quantity,
                order_type=request.order_type,
                limit_price=request.limit_price,
                time_in_force=request.time_in_force,
                strategy_id=self.strategy_id,
                reason=request.reason,
                metadata=request.metadata,
            )
        self._pending.append(request)

    def buy(
        self,
        instrument: Instrument,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        reason: str = "",
    ) -> None:
        self.submit(
            OrderRequest(
                instrument=instrument,
                side=Side.BUY,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                strategy_id=self.strategy_id,
                reason=reason,
            )
        )

    def sell(
        self,
        instrument: Instrument,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        reason: str = "",
    ) -> None:
        self.submit(
            OrderRequest(
                instrument=instrument,
                side=Side.SELL,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                strategy_id=self.strategy_id,
                reason=reason,
            )
        )

    def close(self, instrument: Instrument, reason: str = "close") -> None:
        """Flatten a position, whichever way it is facing."""
        qty = self.quantity(instrument)
        if qty == 0:
            return
        side = Side.SELL if qty > 0 else Side.BUY
        self.submit(
            OrderRequest(
                instrument=instrument,
                side=side,
                quantity=abs(qty),
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                strategy_id=self.strategy_id,
                reason=reason,
            )
        )

    def drain(self) -> list[OrderRequest]:
        pending, self._pending = self._pending, []
        return pending


class Strategy(ABC):
    """Base class for all strategies.

    `warmup_bars` is mandatory rather than optional: a strategy that trades
    before its indicators are populated produces signals from partial data,
    which shows up as a spurious edge in the first weeks of every backtest.
    """

    def __init__(self, strategy_id: str, warmup_bars: int = 0) -> None:
        if not strategy_id:
            raise ValueError("strategy_id is required for P&L attribution and risk budgeting")
        self.strategy_id = strategy_id
        self.warmup_bars = warmup_bars

    @property
    @abstractmethod
    def universe(self) -> list[Instrument]:
        """Instruments this strategy may trade. Fixed for the run."""

    def on_start(self, ctx: StrategyContext) -> None:
        """Called once before the first bar."""

    @abstractmethod
    def on_bar(self, ctx: StrategyContext, bars: dict[str, Bar]) -> None:
        """Called once per timestamp with all bars closing at that timestamp."""

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        """Called after each fill on this strategy's orders."""

    def on_finish(self, ctx: StrategyContext) -> None:
        """Called once after the last bar."""

    def is_warm(self, ctx: StrategyContext) -> bool:
        """True when every instrument has at least `warmup_bars` of history."""
        if self.warmup_bars <= 0:
            return True
        return all(
            len(ctx.history(inst)) >= self.warmup_bars for inst in self.universe
        )
