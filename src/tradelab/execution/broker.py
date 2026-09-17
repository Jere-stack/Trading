"""Broker abstraction.

This interface is the mechanism by which "seamless paper-to-live switching"
becomes a property of the system rather than a promise. Strategies never see a
broker; the engine does, and it is handed one of three implementations selected
by config:

    BACKTEST -> SimulatedBroker over historical bars
    PAPER    -> SimulatedBroker over live data, or IbkrBroker on a paper account
    LIVE     -> IbkrBroker on a funded account

Both paper routes matter and they test different things:

* `SimulatedBroker` on live data gives full control over the fill model and lets
  us log counterfactuals (what would a limit at the touch have done?). It
  validates the *strategy*.
* `IbkrBroker` against IBKR's paper account exercises the real API: order types,
  rejects, partial fills, reconnects, contract resolution quirks. It validates
  the *integration*. A system that only ever ran against its own simulator will
  break on its first live order, usually on something mundane like a contract
  ambiguity or a tick-size rejection.

Run both before risking capital. They catch disjoint classes of bug.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from tradelab.core.enums import OrderStatus, RunMode
from tradelab.core.types import Fill, Instrument, Order, OrderRequest, Position


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    """Broker-reported account state, used for reconciliation."""

    account_id: str
    base_currency: str
    cash: dict[str, Decimal]
    equity: Decimal
    buying_power: Decimal


@dataclass
class ReconciliationBreak:
    """A disagreement between our ledger and the broker's.

    Any break is treated as serious. The broker is authoritative on positions and
    cash; our ledger being wrong means every risk limit computed from it is also
    wrong, so the correct response is to halt and investigate, not to auto-correct
    and carry on.
    """

    kind: str
    detail: str
    local: object = None
    remote: object = None


class Broker(ABC):
    """Order submission, cancellation and state reporting."""

    mode: RunMode

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def submit(self, order_id: str, request: OrderRequest) -> Order:
        """Submit an order. Returns it in PENDING_NEW or NEW."""

    @abstractmethod
    def cancel(self, order_id: str) -> bool: ...

    @abstractmethod
    def open_orders(self) -> list[Order]: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def account(self) -> BrokerAccount: ...

    def on_fill(self, callback: Callable[[Fill], None]) -> None:
        """Register a fill callback. Fills are pushed, never polled."""
        self._fill_callbacks.append(callback)

    def on_order_update(self, callback: Callable[[Order], None]) -> None:
        self._order_callbacks.append(callback)

    _fill_callbacks: list[Callable[[Fill], None]] = field(default_factory=list)
    _order_callbacks: list[Callable[[Order], None]] = field(default_factory=list)

    def _emit_fill(self, fill: Fill) -> None:
        for cb in self._fill_callbacks:
            cb(fill)

    def _emit_order(self, order: Order) -> None:
        for cb in self._order_callbacks:
            cb(order)

    def reconcile(self, local_positions: dict[str, Position]) -> list[ReconciliationBreak]:
        """Compare local positions against the broker's. Broker wins.

        Called at startup and periodically. A restart that skips reconciliation
        will happily open a second position in a name it already holds.
        """
        breaks: list[ReconciliationBreak] = []
        remote = {p.instrument.key: p for p in self.positions()}
        for key in set(local_positions) | set(remote):
            local_qty = local_positions[key].quantity if key in local_positions else Decimal(0)
            remote_qty = remote[key].quantity if key in remote else Decimal(0)
            if local_qty != remote_qty:
                breaks.append(
                    ReconciliationBreak(
                        kind="position_quantity",
                        detail=f"{key}: local {local_qty} vs broker {remote_qty}",
                        local=local_qty,
                        remote=remote_qty,
                    )
                )
        return breaks


class BrokerError(RuntimeError):
    """Broker-side failure. Carries whether a retry is safe.

    The distinction is essential for order submission: retrying a submit that
    may have succeeded can double a position. When in doubt, `retryable` is
    False and a human reconciles.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class OrderRejected(BrokerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@dataclass
class OrderBook:
    """Local view of order state, shared by broker implementations."""

    orders: dict[str, Order] = field(default_factory=dict)

    def add(self, order: Order) -> None:
        if order.order_id in self.orders:
            raise ValueError(f"duplicate order_id {order.order_id}")
        self.orders[order.order_id] = order

    def get(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    def open(self) -> list[Order]:
        return [o for o in self.orders.values() if o.is_open]

    def open_for(self, instrument: Instrument) -> list[Order]:
        return [o for o in self.open() if o.instrument.key == instrument.key]

    def terminal(self) -> list[Order]:
        return [o for o in self.orders.values() if not o.is_open]

    def mark_cancelled(self, order_id: str, timestamp=None) -> bool:
        order = self.orders.get(order_id)
        if order is None or not order.is_open:
            return False
        order.status = OrderStatus.CANCELLED
        order.updated_at = timestamp
        return True
