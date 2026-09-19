"""Interactive Brokers adapter via `ib_async`.

`ib_async` is imported lazily so the rest of the system -- backtesting,
research, the whole test suite -- runs without IBKR installed. Only live and
paper execution need it.

This adapter is where the integration bugs live, so it is written defensively
around the things that actually go wrong with the TWS API:

* **Contract ambiguity.** `Stock("NOKIA", "SMART", "EUR")` can match several
  listings. Always qualify contracts and reject ambiguous matches rather than
  silently taking the first, which is how you end up trading a different
  listing of the same company on another exchange in another currency.

* **Order id collisions.** Client order ids must be unique per client id, and
  two processes sharing a client id will collide. We tag every order with a
  run-scoped id and reconcile on startup.

* **Silent rejections.** Some invalid orders (bad tick size, unsupported TIF for
  the venue) produce an error event, not an exception. Error codes are mapped to
  order state rather than logged and forgotten.

* **Pacing violations.** IBKR throttles request rates; exceeding them gets the
  connection dropped. The risk engine's rate limits sit upstream of this, which
  is the real defence.

* **Daily re-authentication.** IB Gateway requires a daily login. A disconnect
  is expected operationally, so reconnection is bounded and a failure to
  reconnect is escalated rather than retried forever.

The one behaviour worth stating plainly: **this adapter never retries an order
submission whose outcome is unknown.** Retrying a submit that may have succeeded
can double a position. An ambiguous submit escalates to a human.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from tradelab.core.clock import Clock, LiveClock
from tradelab.core.enums import (
    LiquidityFlag,
    OrderStatus,
    OrderType,
    RunMode,
    Side,
    TimeInForce,
    Venue,
)
from tradelab.core.money import ZERO, quantize_price, to_decimal
from tradelab.core.types import Fill, Instrument, Order, OrderRequest, Position
from tradelab.execution.broker import (
    Broker,
    BrokerAccount,
    BrokerError,
    OrderBook,
    OrderRejected,
)

if TYPE_CHECKING:  # pragma: no cover
    pass

# IBKR error codes that mean the order is dead. Mapping these explicitly matters
# because several arrive as informational events rather than exceptions.
FATAL_ORDER_ERRORS = frozenset(
    {
        201,  # Order rejected - reason follows
        202,  # Order cancelled
        203,  # Security is not available or allowed
        321,  # Server error validating the order
        382,  # Order size exceeds allowed limit
        383,  # Order price does not conform to tick size
        387,  # Unsupported order type for this exchange
        10147,  # Order to cancel is not found
    }
)

# Codes that are informational only. Treating these as failures causes spurious
# halts, which trains the operator to ignore alerts.
BENIGN_CODES = frozenset({2104, 2106, 2107, 2108, 2158, 2119, 399})

_TIF_MAP = {
    TimeInForce.DAY: "DAY",
    TimeInForce.GTC: "GTC",
    TimeInForce.IOC: "IOC",
    TimeInForce.FOK: "FOK",
    TimeInForce.OPG: "OPG",
}

_STATUS_MAP = {
    "PendingSubmit": OrderStatus.PENDING_NEW,
    "PreSubmitted": OrderStatus.NEW,
    "Submitted": OrderStatus.NEW,
    "ApiPending": OrderStatus.PENDING_NEW,
    "PendingCancel": OrderStatus.PENDING_CANCEL,
    "Cancelled": OrderStatus.CANCELLED,
    "ApiCancelled": OrderStatus.CANCELLED,
    "Filled": OrderStatus.FILLED,
    "Inactive": OrderStatus.REJECTED,
}


def _require_ib_async():
    """Import `ib_async` with an actionable message if it is missing."""
    try:
        import ib_async
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise BrokerError(
            "ib_async is required for IBKR execution but is not installed. "
            "Install it with:  uv pip install 'tradelab[ibkr]'   "
            "(backtesting and research do not need it)",
            retryable=False,
        ) from exc
    return ib_async


PAPER_PORTS = {7497: "TWS", 4002: "IB Gateway"}
"""Ports that serve a paper account."""

LIVE_PORTS = {7496: "TWS", 4001: "IB Gateway"}
"""Ports that serve a real account with real money.

Gateway, not TWS, is what a headless server runs -- so 4001/4002 are the ports
an unattended deployment actually uses, and leaving them out of the guard would
mean the check that exists to stop a paper run sending real orders never fires
in the one place it matters most.
"""


@dataclass
class IbkrBroker(Broker):
    """IBKR adapter. Paper and live differ only by port.

    TWS serves paper on 7497 and live on 7496; IB Gateway serves paper on 4002
    and live on 4001. That the only difference is a port number is the entire
    basis of the paper-to-live parity claim: the message protocol, order types
    and market data are identical, so integration validated on paper is
    integration validated for live.

    It is also why the port is checked against the mode before connecting. The
    two are configured independently, in different files, by a person -- and
    the failure is silent and expensive in exactly one direction.
    """

    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    account_id: str | None = None
    mode: RunMode = RunMode.PAPER
    clock: Clock = field(default_factory=LiveClock)
    readonly: bool = False
    allow_nonstandard_port: bool = False
    """Opt out of the LIVE-mode port check, for a deliberate tunnel or relay."""
    connect_timeout: int = 30
    max_reconnect_attempts: int = 5
    reconnect_backoff: float = 2.0
    book: OrderBook = field(default_factory=OrderBook)
    _ib: Any = None
    _contracts: dict[str, Any] = field(default_factory=dict)
    _trades: dict[str, Any] = field(default_factory=dict)
    _seen_fill_ids: set[str] = field(default_factory=set)
    _fill_callbacks: list = field(default_factory=list)
    _order_callbacks: list = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode is RunMode.PAPER and self.port in LIVE_PORTS:
            raise BrokerError(
                f"refusing to start: mode is PAPER but port {self.port} is "
                f"{LIVE_PORTS[self.port]}'s LIVE port. This would send real orders "
                "from a run believed to be paper.",
                retryable=False,
            )
        if self.mode is RunMode.LIVE and self.port in PAPER_PORTS:
            raise BrokerError(
                f"refusing to start: mode is LIVE but port {self.port} is "
                f"{PAPER_PORTS[self.port]}'s paper port. Live is "
                f"{'7496 (TWS)' if self.port == 7497 else '4001 (IB Gateway)'}. "
                "One of the two is misconfigured.",
                retryable=False,
            )
        # An unrecognised port in LIVE mode is not necessarily wrong -- a relay
        # or SSH tunnel may remap it -- but it is unverifiable, and an
        # unverifiable port in live mode is worth a deliberate opt-in.
        if (
            self.mode is RunMode.LIVE
            and self.port not in LIVE_PORTS
            and not self.allow_nonstandard_port
        ):
            raise BrokerError(
                f"refusing to start: mode is LIVE on port {self.port}, which is "
                "neither 7496 (TWS) nor 4001 (IB Gateway), so the port cannot "
                "confirm the account is the one intended. Set "
                "allow_nonstandard_port=True if this is a deliberate tunnel.",
                retryable=False,
            )

    # ------------------------------------------------------------- connection

    def connect(self) -> None:
        ib_async = _require_ib_async()
        self._ib = ib_async.IB()
        last_error: Exception | None = None
        for attempt in range(self.max_reconnect_attempts + 1):
            try:
                self._ib.connect(
                    self.host,
                    self.port,
                    clientId=self.client_id,
                    timeout=self.connect_timeout,
                    readonly=self.readonly,
                )
                self._wire_events()
                return
            except Exception as exc:
                last_error = exc
                if attempt < self.max_reconnect_attempts:
                    time.sleep(self.reconnect_backoff * (2**attempt))
        raise BrokerError(
            f"could not connect to IB Gateway at {self.host}:{self.port} after "
            f"{self.max_reconnect_attempts + 1} attempts: {last_error}. "
            "Check that IB Gateway is running, the API is enabled "
            "(Global Configuration > API > Enable ActiveX and Socket Clients), "
            "the port matches, and that today's re-authentication is done.",
            retryable=True,
        ) from last_error

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def _wire_events(self) -> None:
        self._ib.execDetailsEvent += self._on_exec_details
        self._ib.orderStatusEvent += self._on_order_status
        self._ib.errorEvent += self._on_error

    # ---------------------------------------------------------------- contracts

    def qualify(self, instrument: Instrument) -> Any:
        """Resolve an `Instrument` to a unique IBKR contract.

        Rejects ambiguity rather than guessing. An ambiguous ticker can resolve
        to a different listing of the same company, on a different exchange, in
        a different currency -- and the resulting position looks correct in a
        position report while being the wrong instrument.
        """
        if instrument.key in self._contracts:
            return self._contracts[instrument.key]

        ib_async = _require_ib_async()
        self._require_connection()

        exchange = "SMART" if instrument.venue is Venue.SMART else instrument.venue.value
        contract = ib_async.Stock(
            symbol=instrument.symbol,
            exchange=exchange,
            currency=instrument.currency.upper(),
        )
        if instrument.primary_exchange is not None:
            contract.primaryExchange = instrument.primary_exchange.value

        matches = self._ib.qualifyContracts(contract)
        if not matches:
            raise OrderRejected(
                f"IBKR could not resolve {instrument.key}. Check the symbol, "
                f"currency and exchange; for SMART routing a primary_exchange is "
                "often required to disambiguate."
            )
        if len(matches) > 1:
            detail = ", ".join(
                f"{m.symbol}@{m.primaryExchange or m.exchange}/{m.currency}" for m in matches[:5]
            )
            raise OrderRejected(
                f"{instrument.key} is ambiguous at IBKR ({len(matches)} matches: {detail}). "
                "Set `primary_exchange` on the Instrument. Refusing to guess, because "
                "the wrong listing trades the wrong instrument while looking correct."
            )
        self._contracts[instrument.key] = matches[0]
        return matches[0]

    # ------------------------------------------------------------------ orders

    def submit(self, order_id: str, request: OrderRequest) -> Order:
        """Submit an order. Never retried if the outcome is unknown."""
        self._require_connection()
        if self.readonly:
            raise OrderRejected("broker is connected read-only; order submission is disabled")

        ib_async = _require_ib_async()
        contract = self.qualify(request.instrument)
        ib_order = self._build_order(ib_async, request)
        ib_order.orderRef = order_id[:60]  # IBKR truncates long refs

        now = self.clock.now()
        order = Order(
            order_id=order_id,
            request=request,
            status=OrderStatus.PENDING_NEW,
            created_at=now,
            updated_at=now,
        )
        self.book.add(order)

        try:
            trade = self._ib.placeOrder(contract, ib_order)
        except Exception as exc:
            order.status = OrderStatus.REJECTED
            order.reject_reason = f"placeOrder raised {type(exc).__name__}: {exc}"
            self._emit_order(order)
            # Deliberately non-retryable: the order may have reached IBKR.
            raise BrokerError(
                f"order {order_id} submission failed with an unknown outcome: {exc}. "
                "Not retrying -- a resubmission could double the position. "
                "Reconcile against the broker before acting.",
                retryable=False,
            ) from exc

        self._trades[order_id] = trade
        order.broker_order_id = str(trade.order.orderId)
        order.status = OrderStatus.NEW
        self._emit_order(order)
        return order

    def _build_order(self, ib_async, request: OrderRequest):
        action = "BUY" if request.side is Side.BUY else "SELL"
        quantity = float(request.quantity)
        tif = _TIF_MAP[request.time_in_force]

        if request.order_type is OrderType.MARKET:
            return ib_async.MarketOrder(action, quantity, tif=tif)
        if request.order_type is OrderType.LIMIT:
            return ib_async.LimitOrder(action, quantity, float(request.limit_price), tif=tif)
        if request.order_type is OrderType.MARKET_ON_CLOSE:
            return ib_async.MarketOrder(action, quantity, tif="DAY", orderType="MOC")
        if request.order_type is OrderType.LIMIT_ON_CLOSE:
            order = ib_async.LimitOrder(action, quantity, float(request.limit_price), tif="DAY")
            order.orderType = "LOC"
            return order
        if request.order_type is OrderType.MARKET_ON_OPEN:
            return ib_async.MarketOrder(action, quantity, tif="OPG")
        raise OrderRejected(f"unsupported order type for IBKR: {request.order_type}")

    def cancel(self, order_id: str) -> bool:
        self._require_connection()
        trade = self._trades.get(order_id)
        if trade is None:
            return False
        order = self.book.get(order_id)
        if order is None or not order.is_open:
            return False
        self._ib.cancelOrder(trade.order)
        order.status = OrderStatus.PENDING_CANCEL
        order.updated_at = self.clock.now()
        self._emit_order(order)
        return True

    def open_orders(self) -> list[Order]:
        return self.book.open()

    # ------------------------------------------------------------------ events

    def _on_exec_details(self, trade, execution) -> None:
        """Translate an IBKR execution into a `Fill`.

        Deduplicates on IBKR's execution id, because `execDetailsEvent` can
        deliver the same execution more than once (notably after a reconnect).
        Applying a fill twice corrupts the position ledger.
        """
        exec_id = str(execution.execId)
        if exec_id in self._seen_fill_ids:
            return
        self._seen_fill_ids.add(exec_id)

        order_id = str(getattr(trade.order, "orderRef", "") or "")
        order = self.book.get(order_id)
        if order is None:
            # An execution for an order we do not know about: either from a
            # previous run or placed manually. Escalate rather than absorb it.
            self.errors.append(
                (0, f"execution {exec_id} for unknown orderRef {order_id!r}; reconcile")
            )
            return

        commission, fees = self._commission_for(exec_id)
        fill = Fill(
            fill_id=exec_id,
            order_id=order_id,
            instrument=order.instrument,
            side=Side.BUY if execution.side.upper().startswith("B") else Side.SELL,
            quantity=to_decimal(abs(execution.shares)),
            price=quantize_price(to_decimal(execution.price)),
            timestamp=self.clock.now(),
            commission=commission,
            fees=fees,
            liquidity=LiquidityFlag.UNKNOWN,
            strategy_id=order.strategy_id,
        )
        order.apply_fill(fill)
        self._emit_fill(fill)
        self._emit_order(order)

    def _commission_for(self, exec_id: str) -> tuple[Decimal, Decimal]:
        """Look up realised commission from IBKR's commission report.

        The report often arrives *after* the execution, so this may return zero
        on the first pass. That is why realised cost must be reconciled from
        statements rather than trusted from the event stream -- and why the cost
        model needs calibrating against statements, not against these numbers.
        """
        try:
            for report in self._ib.fills():
                if str(report.execution.execId) == exec_id and report.commissionReport:
                    return to_decimal(report.commissionReport.commission), ZERO
        except Exception:
            pass
        return ZERO, ZERO

    def _on_order_status(self, trade) -> None:
        order_id = str(getattr(trade.order, "orderRef", "") or "")
        order = self.book.get(order_id)
        if order is None:
            return
        mapped = _STATUS_MAP.get(trade.orderStatus.status)
        if mapped is None:
            return
        # Never downgrade a filled order: fill events and status events race,
        # and a stale "Submitted" arriving after a fill would reopen it.
        if order.status is OrderStatus.FILLED and mapped is not OrderStatus.FILLED:
            return
        if mapped is OrderStatus.FILLED and order.filled_quantity < order.request.quantity:
            # IBKR says filled but we have not seen all the executions yet.
            # Wait for them rather than fabricating a quantity.
            return
        order.status = mapped
        order.updated_at = self.clock.now()
        self._emit_order(order)

    def _on_error(self, req_id, error_code, error_string, contract) -> None:
        if error_code in BENIGN_CODES:
            return
        self.errors.append((int(error_code), str(error_string)))
        if error_code not in FATAL_ORDER_ERRORS:
            return
        for order in self.book.open():
            if order.broker_order_id == str(req_id):
                order.status = OrderStatus.REJECTED
                order.reject_reason = f"IBKR {error_code}: {error_string}"
                order.updated_at = self.clock.now()
                self._emit_order(order)

    # -------------------------------------------------------- account/positions

    def positions(self) -> list[Position]:
        """Broker-reported positions. Authoritative for reconciliation."""
        self._require_connection()
        out: list[Position] = []
        for item in self._ib.positions(account=self.account_id or ""):
            contract = item.contract
            instrument = Instrument(
                symbol=contract.symbol,
                venue=_venue_from_exchange(contract.exchange or contract.primaryExchange),
                currency=(contract.currency or "USD").upper(),
            )
            position = Position(instrument=instrument)
            position.quantity = to_decimal(item.position)
            position.average_price = quantize_price(to_decimal(item.avgCost))
            out.append(position)
        return out

    def account(self) -> BrokerAccount:
        self._require_connection()
        values = self._ib.accountValues(account=self.account_id or "")
        cash: dict[str, Decimal] = {}
        equity = ZERO
        buying_power = ZERO
        base_currency = "EUR"
        for value in values:
            if value.tag == "CashBalance" and value.currency not in ("BASE", ""):
                cash[value.currency.upper()] = to_decimal(value.value)
            elif value.tag == "NetLiquidation" and value.currency not in ("BASE", ""):
                equity = to_decimal(value.value)
                base_currency = value.currency.upper()
            elif value.tag == "BuyingPower":
                buying_power = to_decimal(value.value)
        return BrokerAccount(
            account_id=self.account_id or "unknown",
            base_currency=base_currency,
            cash=cash,
            equity=equity,
            buying_power=buying_power,
        )

    def _require_connection(self) -> None:
        if not self.is_connected:
            raise BrokerError(
                "not connected to IB Gateway. Call connect() first; if this "
                "happened mid-session the daily re-authentication may have lapsed.",
                retryable=True,
            )


def _venue_from_exchange(exchange: str | None) -> Venue:
    if not exchange:
        return Venue.SMART
    try:
        return Venue(exchange.upper())
    except ValueError:
        return Venue.SMART
