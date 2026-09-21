"""Live and paper execution runner.

Runs the same `Strategy` objects, through the same `RiskEngine`, as the
backtester. The differences are confined to three things: the clock is real, the
broker is real, and state is persisted across restarts.

**The startup sequence is the safety-critical part**, and its order is not
arbitrary -- each step depends on the previous one being correct:

    1. Connect to the broker. Fail loudly rather than degrading.
    2. Restore risk state. Skipping this resets the daily loss limit, so a
       system that has already lost 3% today would begin with a fresh 3% of
       rope, and the limit becomes unenforceable by crashing.
    3. Reconcile positions against the broker. The broker is authoritative. A
       restart that skips this will happily open a second position in a name it
       already holds. Any break is a HARD halt.
    4. Verify market data is live. A frozen feed looks healthy and prices the
       past.
    5. Only then begin the event loop.

A failure at any step halts rather than continuing in a degraded state. The
asymmetry justifies it: not trading costs an opportunity, while trading on
wrong state costs capital.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from tradelab.core.clock import Clock, LiveClock
from tradelab.core.enums import RiskDecision, RunMode, Side
from tradelab.core.ids import IdGenerator, new_run_id
from tradelab.core.types import Bar, Fill, Instrument, Order, OrderRequest, Quote
from tradelab.costs.commission import CommissionModel, ibkr_default_router
from tradelab.costs.slippage import SlippageModel, SpreadImpactSlippage
from tradelab.execution.broker import Broker, BrokerError
from tradelab.portfolio.portfolio import Portfolio
from tradelab.portfolio.state import StateStore
from tradelab.portfolio.treasury import BlockFxPolicy, FxPolicy
from tradelab.risk.engine import RiskContext, RiskEngine, dedupe_key
from tradelab.risk.killswitch import HaltLevel, RiskState
from tradelab.risk.limits import RiskLimits
from tradelab.strategy.base import Strategy, StrategyContext


class StartupError(RuntimeError):
    """Raised when the runner cannot establish a safe state to trade from."""


@dataclass
class ReconciliationPolicy:
    """What to do when the local ledger disagrees with the broker.

    `halt_on_break` defaults to True and should stay that way. A position
    discrepancy means every risk limit computed from local equity is wrong, so
    the safe response is to stop and have a human look -- not to adopt the
    broker's numbers and carry on, which would hide the bug that caused it.
    """

    halt_on_break: bool = True
    adopt_broker_positions: bool = False
    tolerance: Decimal = Decimal("0")


@dataclass
class LiveRunner:
    """Event-driven runner for PAPER and LIVE modes."""

    strategies: list[Strategy]
    broker: Broker
    limits: RiskLimits = field(default_factory=RiskLimits)
    mode: RunMode = RunMode.PAPER
    base_currency: str = "EUR"
    clock: Clock = field(default_factory=LiveClock)
    state_store: StateStore | None = None
    commission_model: CommissionModel = field(default_factory=ibkr_default_router)
    slippage_model: SlippageModel = field(default_factory=SpreadImpactSlippage)
    reconciliation: ReconciliationPolicy = field(default_factory=ReconciliationPolicy)
    fx_policy: FxPolicy = field(default_factory=BlockFxPolicy)
    """How foreign currency is funded. The default converts in infrequent
    blocks; see `tradelab.portfolio.treasury` for why per-trade conversion is
    38x more expensive at this account size."""
    fx_rates: dict[str, Decimal] = field(default_factory=dict)
    max_data_age_seconds: int = 300

    portfolio: Portfolio = field(init=False)
    risk: RiskEngine = field(init=False)
    state: RiskState = field(init=False)
    run_id: str = field(init=False)
    _ids: IdGenerator = field(init=False)
    _contexts: dict[str, StrategyContext] = field(init=False, default_factory=dict)
    _histories: dict[str, object] = field(init=False, default_factory=dict)
    _quotes: dict[str, Quote] = field(init=False, default_factory=dict)
    _price_stamps: dict[str, datetime] = field(init=False, default_factory=dict)
    _last_prices: dict[str, Decimal] = field(init=False, default_factory=dict)
    _orders: dict[str, Order] = field(init=False, default_factory=dict)
    _running: bool = field(init=False, default=False)
    _stop_requested: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        if self.mode is RunMode.BACKTEST:
            raise ValueError(
                "LiveRunner is for PAPER and LIVE only; use BacktestEngine for historical replay"
            )
        self.run_id = new_run_id(self.mode.value, self.clock.now())
        self._ids = IdGenerator(self.run_id)
        self.portfolio = Portfolio(
            base_currency=self.base_currency,
            max_leverage=self.limits.portfolio.max_gross_exposure,
            fx_rates=dict(self.fx_rates),
        )
        self.risk = RiskEngine(limits=self.limits)
        self.state = RiskState()

    # ------------------------------------------------------------- startup

    def start(self) -> None:
        """Run the startup sequence. Raises `StartupError` rather than degrading."""
        self._log("startup", f"starting {self.mode.value} run {self.run_id}")

        # --- 1. connect
        try:
            self.broker.connect()
        except BrokerError as exc:
            raise StartupError(f"broker connection failed: {exc}") from exc
        if not self.broker.is_connected:
            raise StartupError("broker reported success but is not connected")

        # --- 2. restore risk state BEFORE any trading decision
        self._restore_risk_state()

        # --- 3. reconcile against the broker, which is authoritative
        self._reconcile()

        # --- 4. seed the portfolio from broker truth
        self._seed_account()

        for strategy in self.strategies:
            self._contexts[strategy.strategy_id] = StrategyContext(
                now=self.clock.now(),
                strategy_id=strategy.strategy_id,
                equity=self.portfolio.equity,
                base_currency=self.portfolio.base_currency,
                _bars=self._histories,
                _quotes=self._quotes,
                _positions=self.portfolio.positions,
                # The portfolio's own dict, by reference, so a rate updated
                # mid-run is visible to the strategy without a refresh step.
                _fx_rates=self.portfolio.fx_rates,
            )
            strategy.on_start(self._contexts[strategy.strategy_id])

        self.broker.on_fill(self._on_fill)
        self._running = True
        self._log(
            "startup",
            f"ready: equity {self.portfolio.equity} {self.base_currency}, "
            f"{len(self.portfolio.open_positions)} position(s), "
            f"halt={self.state.kill_switch.level.value}",
        )

    def _restore_risk_state(self) -> None:
        if self.state_store is None:
            self._log(
                "startup",
                "no state store configured: risk state will not survive a restart, "
                "so the daily loss limit can be reset by crashing. Acceptable for a "
                "short experiment, not for an unattended run.",
                severity="WARNING",
            )
            return
        restored = self.state_store.load_risk_state()
        if restored is None:
            self._log("startup", "no persisted risk state; treating this as a first run")
            return
        self.state = restored
        level = self.state.kill_switch.level
        if level is HaltLevel.HARD:
            self._log(
                "startup",
                f"restored a HARD halt: {self.state.kill_switch.current.reason}. "
                "Restarting is not a re-arm; a human must clear this explicitly.",
                severity="CRITICAL",
            )
        else:
            self._log(
                "startup",
                f"restored risk state: session {self.state.session_date}, "
                f"{self.state.orders_today} orders today, halt={level.value}",
            )

    def _reconcile(self) -> None:
        """Compare the local ledger against broker positions. Broker wins."""
        local = {}
        if self.state_store is not None:
            local = self.state_store.position_quantities()

        try:
            remote_positions = self.broker.positions()
        except BrokerError as exc:
            raise StartupError(f"could not read broker positions to reconcile: {exc}") from exc

        remote = {p.instrument.key: p.quantity for p in remote_positions}
        breaks: list[str] = []
        for key in set(local) | set(remote):
            local_qty = local.get(key, Decimal(0))
            remote_qty = remote.get(key, Decimal(0))
            if abs(local_qty - remote_qty) > self.reconciliation.tolerance:
                breaks.append(f"{key}: local {local_qty} vs broker {remote_qty}")

        if not breaks:
            self._log("reconcile", f"clean: {len(remote)} broker position(s) match the ledger")
            return

        message = f"{len(breaks)} reconciliation break(s): " + "; ".join(breaks[:5])
        self._log("reconcile", message, severity="CRITICAL", payload={"breaks": breaks})

        if self.reconciliation.halt_on_break:
            self.state.kill_switch.trip(HaltLevel.HARD, message, self.clock.now(), "reconciliation")
            self._persist_risk_state()
            raise StartupError(
                f"{message}. Halting: a position discrepancy means every risk limit "
                "computed from local equity is wrong. Investigate before trading; "
                "adopting the broker's numbers would hide the cause."
            )

    def _seed_account(self) -> None:
        """Adopt broker cash and positions as the starting ledger.

        Adopting is a *set*, not a deposit. The broker is authoritative, so its
        balance replaces the local one. Adding instead doubles the balance
        whenever the local ledger already reflects it -- which is latent
        against a real broker starting from an empty ledger, and immediate
        against a simulator sharing this portfolio.
        """
        try:
            account = self.broker.account()
            positions = self.broker.positions()
        except BrokerError as exc:
            raise StartupError(f"could not read broker account: {exc}") from exc

        for currency, amount in account.cash.items():
            self.portfolio.set_cash(amount, currency)
        for position in positions:
            existing = self.portfolio.position(position.instrument)
            existing.quantity = position.quantity
            existing.average_price = position.average_price
            existing.last_price = position.average_price

        equity = self.portfolio.equity
        self.state.start_session(self.clock.today(), equity)
        self.state.observe_equity(equity)
        self._persist_risk_state()

    # --------------------------------------------------------- market data

    def on_bar(self, bar: Bar) -> None:
        """Feed a single closed bar and dispatch immediately.

        Correct for a live intraday feed, where bars arrive one at a time. For
        a batch of bars sharing a timestamp -- a daily close across a universe
        -- use `on_bars`, which publishes them all before dispatching.
        """
        self.on_bars([bar])

    def on_bars(self, bars: list[Bar]) -> None:
        """Feed all bars for one timestamp, then dispatch once.

        Publishing before dispatching is not a detail. Dispatching after each
        bar individually means the strategy runs while part of the universe
        still carries yesterday's prices, so a cross-sectional rule sees a
        mixture of dates. Observed in a real paper run: a rebalance triggered
        on the alphabetically-first symbol was rejected for every other name
        with "reference price is 259200s stale", because their bars for that
        same session had not been published yet.

        This mirrors `BacktestEngine`, which groups by timestamp for the same
        reason -- and the divergence between the two paths is exactly the kind
        of thing that makes a paper result fail to reproduce a backtest.
        """
        if not self._running:
            raise RuntimeError("runner is not started")
        if not bars:
            return
        from collections import deque

        published: dict[str, Bar] = {}
        for bar in bars:
            key = bar.instrument.key
            self._histories.setdefault(key, deque(maxlen=512)).append(bar)
            self._last_prices[key] = bar.close
            self._price_stamps[key] = bar.timestamp
            self.portfolio.mark(bar.instrument, bar.close, bar.timestamp)
            published[key] = bar

        self._roll_session()
        self._dispatch(published)

    def on_quote(self, quote: Quote) -> None:
        self._quotes[quote.instrument.key] = quote
        self._price_stamps[quote.instrument.key] = quote.timestamp

    def _roll_session(self) -> None:
        today = self.clock.today()
        if self.state.session_date is not None and today > self.state.session_date:
            self.state.end_session(self.portfolio.equity)
            # A SOFT halt expires with the session; a HARD halt does not.
            if self.state.kill_switch.clear_soft(self.clock.now()):
                self._log("session", "soft halt cleared at the session boundary")
            self.state.start_session(today, self.portfolio.equity)
            self._persist_risk_state()
            self._log("session", f"new session {today}, equity {self.portfolio.equity}")

    # ------------------------------------------------------------ dispatch

    def _dispatch(self, bars: dict[str, Bar]) -> None:
        now = self.clock.now()
        self.state.observe_equity(self.portfolio.equity)
        open_keys = {o.instrument.key for o in self.broker.open_orders()}

        intents: list[OrderRequest] = []
        for strategy in self.strategies:
            context = self._contexts[strategy.strategy_id]
            context.now = now
            context.equity = self.portfolio.equity
            context._open_order_keys = open_keys
            relevant = {inst.key: bars[inst.key] for inst in strategy.universe if inst.key in bars}
            if not relevant or not strategy.is_warm(context):
                context.drain()
                continue
            try:
                strategy.on_bar(context, relevant)
            except Exception as exc:
                # A strategy exception must not take down the runner or leave
                # partially-emitted intents in the queue.
                context.drain()
                self._log(
                    "strategy_error",
                    f"{strategy.strategy_id} raised {type(exc).__name__}: {exc}",
                    severity="CRITICAL",
                )
                continue
            intents.extend(context.drain())

        if intents:
            self._submit(intents, now)

        if self.state_store is not None:
            self.state_store.record_equity(self.portfolio.record_equity(now))

    def _submit(self, intents: list[OrderRequest], now: datetime) -> None:
        context = RiskContext(
            portfolio=self.portfolio,
            state=self.state,
            limits=self.limits,
            now=now,
            reference_prices=dict(self._last_prices),
            quotes=self._quotes,
            price_timestamps=self._price_stamps,
            commission_model=self.commission_model,
            slippage_model=self.slippage_model,
            market_open=self._is_data_fresh(now),
        )
        # Reserve exposure for orders already working, so a new batch cannot
        # re-spend headroom those orders have committed.
        for resting in self.broker.open_orders():
            leaves = resting.leaves_quantity
            price = self._last_prices.get(resting.instrument.key)
            if leaves > 0 and price is not None:
                context.reserve(resting.request.with_quantity(leaves), price)

        # Fund foreign currency for the whole batch BEFORE risk evaluation.
        # Funding after the gate is too late: CashSufficiencyCheck rejects
        # the order first and nothing ever converts. Funding the batch
        # together is also the point of a block policy -- one conversion
        # covers the session rather than one per order.
        self._fund_batch(intents, now)

        for intent in intents:
            result = self.risk.evaluate(intent, context)
            if not result.is_approved:
                self._log(
                    "risk_reject",
                    f"{intent.strategy_id} {intent.side.value} {intent.quantity} "
                    f"{intent.instrument.symbol}: {result.reason}",
                    severity="WARNING",
                )
                continue

            approved = result.approved_request
            if result.decision is RiskDecision.RESIZE:
                self._log(
                    "risk_resize",
                    f"{approved.instrument.symbol} {result.original_quantity} -> "
                    f"{approved.quantity}",
                )

            order_id = self._ids.next("ord")
            try:
                order = self.broker.submit(order_id, approved)
            except BrokerError as exc:
                severity = "WARNING" if exc.retryable else "CRITICAL"
                self._log(
                    "submit_failed",
                    f"{order_id} {approved.instrument.symbol}: {exc}",
                    severity=severity,
                )
                if not exc.retryable:
                    # Unknown outcome: the order may be working at the broker.
                    self.state.kill_switch.trip(
                        HaltLevel.HARD,
                        f"order {order_id} failed with an unknown outcome: {exc}",
                        now,
                        "submit",
                    )
                    self._persist_risk_state()
                continue

            self._orders[order_id] = order
            was_flat = self.portfolio.quantity_of(approved.instrument) == 0
            self.state.record_order(now, dedupe_key(approved))
            if was_flat:
                self.state.new_positions_today += 1
            price = self._last_prices.get(approved.instrument.key)
            if price is not None:
                context.reserve(approved, price)
            if self.state_store is not None:
                self.state_store.record_order(order)
            self._persist_risk_state()
            self._log(
                "order",
                f"{approved.strategy_id} {approved.side.value} {approved.quantity} "
                f"{approved.instrument.symbol} ({approved.order_type.value})",
            )

    def _fund_batch(self, intents: list[OrderRequest], now: datetime) -> None:
        """Convert currency once per currency for a whole batch of intents.

        Sums what the session's buy orders need in each foreign currency and
        asks the treasury policy to cover it in a single conversion. Funding
        per order would pay the USD 2.00 minimum repeatedly -- 38x more
        expensive at this account size, measured on the first paper run.

        Failure to fund is logged rather than raised: CashSufficiencyCheck is
        the backstop that stops an unfunded order from borrowing.
        """
        base = self.portfolio.base_currency
        needs: dict[str, Decimal] = {}
        for intent in intents:
            if intent.side is not Side.BUY:
                continue
            currency = intent.instrument.currency.upper()
            if currency == base:
                continue
            price = self._last_prices.get(intent.instrument.key)
            if price is None:
                continue
            required = intent.quantity * price * intent.instrument.multiplier
            required += self.commission_model.total(
                intent.instrument, intent.side, intent.quantity, price
            )
            needs[currency] = needs.get(currency, Decimal(0)) + required

        for currency, required in needs.items():
            if self.portfolio.cash.get(currency, Decimal(0)) >= required:
                continue
            try:
                action = self.fx_policy.fund(self.portfolio, currency, required)
            except Exception as exc:
                self._log("fx_error", f"funding {currency} failed: {exc}", severity="CRITICAL")
                continue
            if action is None:
                self._log(
                    "fx_unfunded",
                    f"cannot fund {required:.2f} {currency}; orders will be trimmed "
                    "or rejected rather than borrowing",
                    severity="WARNING",
                )
                continue
            # `_log` already persists to the state store. Calling log_event
            # again here wrote every conversion twice, which makes an audit
            # trail overstate how often the account actually traded currency.
            self._log(
                "fx_convert",
                str(action),
                payload={
                    "from": action.from_currency,
                    "to": action.to_currency,
                    "amount": str(action.amount_from),
                    "cost": str(action.cost),
                },
            )

    def _is_data_fresh(self, now: datetime) -> bool:
        if not self._price_stamps:
            return False
        newest = max(self._price_stamps.values())
        return (now - newest) <= timedelta(seconds=self.max_data_age_seconds)

    # --------------------------------------------------------------- fills

    def _on_fill(self, fill: Fill) -> None:
        """Apply a fill. Idempotent, because brokers redeliver executions."""
        if self.state_store is not None and not self.state_store.record_fill(fill):
            self._log("fill_duplicate", f"ignoring already-recorded fill {fill.fill_id}")
            return
        try:
            self.portfolio.apply_fill(fill)
        except Exception as exc:
            self.state.kill_switch.trip(
                HaltLevel.HARD,
                f"fill {fill.fill_id} could not be applied: {exc}",
                self.clock.now(),
                "fill",
            )
            self._persist_risk_state()
            self._log(
                "fill_error",
                f"{fill.fill_id}: {exc}. Ledger and broker now disagree.",
                severity="CRITICAL",
            )
            return

        self._log(
            "fill",
            f"{fill.strategy_id} {fill.side.value} {fill.quantity} "
            f"{fill.instrument.symbol} @ {fill.price} (cost {fill.total_cost})",
        )
        context = self._contexts.get(fill.strategy_id)
        strategy = next((s for s in self.strategies if s.strategy_id == fill.strategy_id), None)
        if context is not None and strategy is not None:
            strategy.on_fill(context, fill)
        self._persist_risk_state()

    # ------------------------------------------------------------ shutdown

    def install_signal_handlers(self) -> None:
        """Stop cleanly on SIGINT/SIGTERM rather than dying mid-write."""

        def handler(signum, frame):
            self._stop_requested = True
            self._log("signal", f"received signal {signum}; stopping after this event")

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, handler)

    @property
    def should_stop(self) -> bool:
        return self._stop_requested

    def stop(self, *, cancel_open_orders: bool = True) -> None:
        """Shut down. Cancels working orders by default.

        Leaving orders working after the supervising process exits means
        positions can open with nothing watching them, no risk gate, and no
        kill switch.
        """
        if cancel_open_orders:
            for order in self.broker.open_orders():
                try:
                    self.broker.cancel(order.order_id)
                    self._log("cancel", f"cancelled {order.order_id} on shutdown")
                except BrokerError as exc:
                    self._log(
                        "cancel_failed",
                        f"{order.order_id}: {exc}. An order may still be working "
                        "with nothing supervising it.",
                        severity="CRITICAL",
                    )
        self.state.end_session(self.portfolio.equity)
        self._persist_risk_state()
        self.broker.disconnect()
        self._running = False
        self._log("shutdown", f"stopped; final equity {self.portfolio.equity}")

    # -------------------------------------------------------------- helpers

    def _persist_risk_state(self) -> None:
        if self.state_store is not None:
            self.state_store.save_risk_state(self.state)

    def _log(
        self, kind: str, message: str, severity: str = "INFO", payload: dict | None = None
    ) -> None:
        # Always the injected clock, never the wall clock. Mixing the two makes
        # event ordering in a post-mortem meaningless, and it is exactly the
        # divergence that clock injection exists to prevent.
        stamp = self.clock.now()
        if self.state_store is not None:
            self.state_store.log_event(kind, message, severity, payload, stamp)
        print(f"[{stamp:%Y-%m-%d %H:%M:%S}] {severity:<8} {kind}: {message}")

    def register_instrument(self, instrument: Instrument, price: Decimal) -> None:
        """Seed a reference price, e.g. from a snapshot before the first bar."""
        self._last_prices[instrument.key] = price
        self._price_stamps[instrument.key] = self.clock.now()
