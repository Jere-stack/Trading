"""Tests for durable state and the live/paper runner.

These pin the restart-safety properties. An automated system restarts on
crashes, deploys and IB Gateway's nightly re-authentication, and what happens
across that boundary decides whether it is safe to leave running unattended.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradelab.core.clock import SimulationClock
from tradelab.core.enums import OrderStatus, RunMode, Side
from tradelab.core.types import Bar, Fill, Instrument, Order, Position
from tradelab.engine.live import LiveRunner, StartupError
from tradelab.execution.broker import Broker, BrokerAccount, BrokerError, OrderBook
from tradelab.portfolio.state import StateStore
from tradelab.risk.killswitch import HaltLevel, RiskState
from tradelab.risk.limits import PortfolioLimits, PositionLimits, RiskLimits
from tradelab.strategy.base import Strategy

NOW = datetime(2026, 9, 17, 13, 30, tzinfo=UTC)


class FakeBroker(Broker):
    """In-memory broker for runner tests."""

    mode = RunMode.PAPER

    def __init__(self, positions=None, cash=None, fail_submit=None):
        self.book = OrderBook()
        self._positions = positions or []
        self._cash = cash or {"EUR": D("10000")}
        self._connected = False
        self._fill_callbacks = []
        self._order_callbacks = []
        self.fail_submit = fail_submit
        self.cancelled: list[str] = []
        self.submitted: list[Order] = []

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    @property
    def is_connected(self):
        return self._connected

    def submit(self, order_id, request):
        if self.fail_submit is not None:
            raise self.fail_submit
        order = Order(
            order_id=order_id,
            request=request,
            status=OrderStatus.NEW,
            created_at=NOW,
            updated_at=NOW,
        )
        self.book.add(order)
        self.submitted.append(order)
        return order

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        return self.book.mark_cancelled(order_id, NOW)

    def open_orders(self):
        return self.book.open()

    def positions(self):
        return list(self._positions)

    def account(self):
        return BrokerAccount(
            "FAKE", "EUR", dict(self._cash), sum(self._cash.values(), D("0")), D("10000")
        )

    def push_fill(self, fill: Fill):
        self._emit_fill(fill)


def instrument(symbol="TEST"):
    return Instrument(
        symbol, currency="EUR", adv=D("5000000"), sigma_daily=D("0.015"), spread_bps=D("6")
    )


def permissive_limits():
    return RiskLimits(
        position=PositionLimits(
            max_position_weight=D("0.5"),
            min_order_notional=D("0"),
            max_cost_bps_of_notional=D("100000"),
            max_participation_of_adv=D("1"),
        ),
        portfolio=PortfolioLimits(max_open_positions=20, max_new_positions_per_day=20),
    )


class BuyOnce(Strategy):
    def __init__(self, inst, qty=D("10")):
        super().__init__("buyer")
        self.inst = inst
        self.qty = qty
        self.done = False

    @property
    def universe(self):
        return [self.inst]

    def on_bar(self, ctx, bars):
        if not self.done:
            ctx.buy(self.inst, self.qty)
            self.done = True


def bar(inst, ts=NOW, close=D("100")):
    return Bar(inst, ts, close, close * D("1.01"), close * D("0.99"), close, D("1000000"))


class TestStatePersistence:
    def test_hard_halt_survives_restart(self, tmp_path):
        """The single most important restart property: crashing is not a re-arm.

        A system that trips a drawdown kill switch and then restarts must not
        come back up trading.
        """
        path = tmp_path / "s.sqlite"
        state = RiskState()
        state.start_session(date(2026, 9, 17), D("10000"))
        state.kill_switch.trip(HaltLevel.HARD, "drawdown 15%", NOW, "loss_limits")
        StateStore(path).save_risk_state(state)

        restored = StateStore(path).load_risk_state()
        assert restored.kill_switch.level is HaltLevel.HARD
        assert restored.kill_switch.blocks_new_risk
        assert restored.kill_switch.blocks_closing
        assert "drawdown" in restored.kill_switch.current.reason

    def test_daily_loss_budget_cannot_be_reset_by_crashing(self, tmp_path):
        """Pins: without persistence, restarting grants a fresh loss budget."""
        path = tmp_path / "s.sqlite"
        state = RiskState()
        state.start_session(date(2026, 9, 17), D("10000"))
        state.orders_today = 40
        StateStore(path).save_risk_state(state)

        restored = StateStore(path).load_risk_state()
        assert restored.day_start_equity == D("10000")
        assert restored.orders_today == 40
        assert restored.session_date == date(2026, 9, 17)

    def test_fills_are_idempotent(self, tmp_path):
        """Pins: brokers redeliver executions after a reconnect."""
        store = StateStore(tmp_path / "s.sqlite")
        inst = instrument()
        fill = Fill("exec-1", "o1", inst, Side.BUY, D("10"), D("100"), NOW)
        assert store.record_fill(fill) is True
        assert store.record_fill(fill) is False
        assert store.position_quantities() == {inst.key: D("10")}

    def test_positions_are_derived_from_the_fill_log(self, tmp_path):
        """Derived, not stored, so the ledger cannot drift from its own fills."""
        store = StateStore(tmp_path / "s.sqlite")
        inst = instrument()
        store.record_fill(Fill("f1", "o1", inst, Side.BUY, D("100"), D("10"), NOW))
        store.record_fill(Fill("f2", "o1", inst, Side.SELL, D("40"), D("11"), NOW))
        assert store.position_quantities() == {inst.key: D("60")}

    def test_flat_positions_are_excluded(self, tmp_path):
        store = StateStore(tmp_path / "s.sqlite")
        inst = instrument()
        store.record_fill(Fill("f1", "o1", inst, Side.BUY, D("10"), D("10"), NOW))
        store.record_fill(Fill("f2", "o1", inst, Side.SELL, D("10"), D("11"), NOW))
        assert store.position_quantities() == {}

    def test_decimals_survive_storage_exactly(self, tmp_path):
        """Pins: SQLite REAL is a float; the ledger must not round-trip through one."""
        store = StateStore(tmp_path / "s.sqlite")
        inst = instrument()
        store.record_fill(Fill("f1", "o1", inst, Side.BUY, D("3"), D("100.123456"), NOW))
        store.record_fill(Fill("f2", "o1", inst, Side.BUY, D("0.1"), D("0.1"), NOW))
        assert store.position_quantities()[inst.key] == D("3.1")

    def test_schema_version_mismatch_is_refused(self, tmp_path):
        import sqlite3

        path = tmp_path / "s.sqlite"
        StateStore(path)
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE schema_version SET version = 99")
        with pytest.raises(RuntimeError, match="schema version"):
            StateStore(path)


class TestStartupSequence:
    def _runner(self, broker, tmp_path, strategies=None, **kwargs):
        inst = instrument()
        return LiveRunner(
            strategies=strategies if strategies is not None else [BuyOnce(inst)],
            broker=broker,
            limits=permissive_limits(),
            mode=RunMode.PAPER,
            clock=SimulationClock(NOW),
            state_store=StateStore(tmp_path / "s.sqlite"),
            **kwargs,
        )

    def test_clean_startup(self, tmp_path):
        runner = self._runner(FakeBroker(), tmp_path)
        runner.start()
        assert runner.portfolio.equity == D("10000")

    def test_reconciliation_break_halts_startup(self, tmp_path):
        """Pins: a restart must not open a second position in a name it holds.

        The local ledger says 100 shares; the broker says none. Continuing
        would mean every risk limit computed from local equity is wrong.
        """
        path = tmp_path / "s.sqlite"
        store = StateStore(path)
        inst = instrument()
        store.record_fill(Fill("f1", "o1", inst, Side.BUY, D("100"), D("10"), NOW))

        runner = LiveRunner(
            strategies=[BuyOnce(inst)],
            broker=FakeBroker(positions=[]),
            limits=permissive_limits(),
            mode=RunMode.PAPER,
            clock=SimulationClock(NOW),
            state_store=store,
        )
        with pytest.raises(StartupError, match="reconciliation break"):
            runner.start()
        assert runner.state.kill_switch.level is HaltLevel.HARD
        # And the halt must persist, so a naive restart does not clear it.
        assert StateStore(path).load_risk_state().kill_switch.level is HaltLevel.HARD

    def test_matching_positions_reconcile_cleanly(self, tmp_path):
        path = tmp_path / "s.sqlite"
        store = StateStore(path)
        inst = instrument()
        store.record_fill(Fill("f1", "o1", inst, Side.BUY, D("100"), D("10"), NOW))

        position = Position(instrument=inst)
        position.quantity = D("100")
        position.average_price = D("10")

        runner = LiveRunner(
            strategies=[BuyOnce(inst)],
            broker=FakeBroker(positions=[position]),
            limits=permissive_limits(),
            mode=RunMode.PAPER,
            clock=SimulationClock(NOW),
            state_store=store,
        )
        runner.start()
        assert runner.portfolio.quantity_of(inst) == D("100")

    def test_broker_failure_raises_rather_than_degrading(self, tmp_path):
        class DeadBroker(FakeBroker):
            def connect(self):
                raise BrokerError("gateway unreachable", retryable=True)

        runner = self._runner(DeadBroker(), tmp_path)
        with pytest.raises(StartupError, match="broker connection failed"):
            runner.start()

    def test_restored_hard_halt_blocks_trading(self, tmp_path):
        """A restored HARD halt must actually stop orders, not merely be logged."""
        path = tmp_path / "s.sqlite"
        state = RiskState()
        state.start_session(NOW.date(), D("10000"))
        state.kill_switch.trip(HaltLevel.HARD, "drawdown", NOW, "loss_limits")
        StateStore(path).save_risk_state(state)

        inst = instrument()
        broker = FakeBroker()
        runner = LiveRunner(
            strategies=[BuyOnce(inst)],
            broker=broker,
            limits=permissive_limits(),
            mode=RunMode.PAPER,
            clock=SimulationClock(NOW),
            state_store=StateStore(path),
        )
        runner.start()
        assert runner.state.kill_switch.level is HaltLevel.HARD
        runner.on_bar(bar(inst))
        assert broker.submitted == [], "a restored HARD halt must block new orders"


class TestRunnerBehaviour:
    def _started(self, tmp_path, broker=None, strategies=None):
        inst = instrument()
        broker = broker or FakeBroker()
        runner = LiveRunner(
            strategies=strategies if strategies is not None else [BuyOnce(inst)],
            broker=broker,
            limits=permissive_limits(),
            mode=RunMode.PAPER,
            clock=SimulationClock(NOW),
            state_store=StateStore(tmp_path / "s.sqlite"),
        )
        runner.start()
        return runner, broker, inst

    def test_orders_flow_through_the_risk_gate(self, tmp_path):
        runner, broker, inst = self._started(tmp_path)
        runner.on_bar(bar(inst))
        assert len(broker.submitted) == 1
        assert broker.submitted[0].request.quantity == D("10")

    def test_risk_rejection_blocks_submission(self, tmp_path):
        """Default limits must reject a sub-minimum order, not merely warn."""
        inst = instrument()
        broker = FakeBroker()
        runner = LiveRunner(
            strategies=[BuyOnce(inst, qty=D("1"))],  # EUR 100 notional
            broker=broker,
            limits=RiskLimits(),
            mode=RunMode.PAPER,
            clock=SimulationClock(NOW),
            state_store=StateStore(tmp_path / "s.sqlite"),
        )
        runner.start()
        runner.on_bar(bar(inst))
        assert broker.submitted == []

    def test_duplicate_fills_are_ignored(self, tmp_path):
        """Pins: a redelivered execution must not be applied twice."""
        runner, broker, inst = self._started(tmp_path)
        fill = Fill("exec-1", "o1", inst, Side.BUY, D("10"), D("100"), NOW, strategy_id="buyer")
        broker.push_fill(fill)
        broker.push_fill(fill)
        assert runner.portfolio.quantity_of(inst) == D("10")

    def test_strategy_exception_does_not_stop_the_runner(self, tmp_path):
        class Exploding(Strategy):
            def __init__(self, inst):
                super().__init__("boom")
                self.inst = inst

            @property
            def universe(self):
                return [self.inst]

            def on_bar(self, ctx, bars):
                raise RuntimeError("bad signal")

        inst = instrument()
        runner, broker, _ = self._started(tmp_path, strategies=[Exploding(inst)])
        runner.on_bar(bar(inst))  # must not raise
        assert broker.submitted == []

    def test_unknown_outcome_submit_trips_a_hard_halt(self, tmp_path):
        """Pins: an order whose fate is unknown must stop the system.

        Retrying could double the position; continuing could leave an
        unsupervised order working.
        """
        inst = instrument()
        broker = FakeBroker(fail_submit=BrokerError("unknown outcome", retryable=False))
        runner = LiveRunner(
            strategies=[BuyOnce(inst)],
            broker=broker,
            limits=permissive_limits(),
            mode=RunMode.PAPER,
            clock=SimulationClock(NOW),
            state_store=StateStore(tmp_path / "s.sqlite"),
        )
        runner.start()
        runner.on_bar(bar(inst))
        assert runner.state.kill_switch.level is HaltLevel.HARD

    def test_retryable_submit_failure_does_not_halt(self, tmp_path):
        inst = instrument()
        broker = FakeBroker(fail_submit=BrokerError("transient", retryable=True))
        runner = LiveRunner(
            strategies=[BuyOnce(inst)],
            broker=broker,
            limits=permissive_limits(),
            mode=RunMode.PAPER,
            clock=SimulationClock(NOW),
            state_store=StateStore(tmp_path / "s.sqlite"),
        )
        runner.start()
        runner.on_bar(bar(inst))
        assert runner.state.kill_switch.level is HaltLevel.NONE

    def test_shutdown_cancels_working_orders(self, tmp_path):
        """Pins: orders must not outlive the process supervising them."""
        runner, broker, inst = self._started(tmp_path)
        runner.on_bar(bar(inst))
        assert broker.open_orders()
        runner.stop()
        assert broker.cancelled == [broker.submitted[0].order_id]
        assert not broker.is_connected

    def test_soft_halt_clears_at_the_session_boundary(self, tmp_path):
        runner, _broker, inst = self._started(tmp_path)
        runner.state.kill_switch.trip(HaltLevel.SOFT, "daily loss", NOW, "loss_limits")
        runner.clock.set(NOW + timedelta(days=1))
        runner.on_bar(bar(inst, ts=NOW + timedelta(days=1)))
        assert runner.state.kill_switch.level is HaltLevel.NONE

    def test_hard_halt_does_not_clear_at_the_session_boundary(self, tmp_path):
        runner, _broker, inst = self._started(tmp_path)
        runner.state.kill_switch.trip(HaltLevel.HARD, "drawdown", NOW, "loss_limits")
        runner.clock.set(NOW + timedelta(days=1))
        runner.on_bar(bar(inst, ts=NOW + timedelta(days=1)))
        assert runner.state.kill_switch.level is HaltLevel.HARD

    def test_events_are_logged_for_post_mortem(self, tmp_path):
        runner, _broker, inst = self._started(tmp_path)
        runner.on_bar(bar(inst))
        kinds = {e["kind"] for e in runner.state_store.recent_events(50)}
        assert "startup" in kinds and "order" in kinds
