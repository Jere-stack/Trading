"""Tests for the engine's anti-lookahead and cost-realism guarantees.

These are the tests that matter most. A backtest engine with a subtle lookahead
bug produces plausible-looking results that cannot be reproduced live, and the
failure is silent. Each test here pins one specific way that could happen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradelab.core.enums import OrderType
from tradelab.core.types import Bar, Instrument
from tradelab.costs.commission import ZeroCommission, ibkr_default_router
from tradelab.costs.slippage import NoSlippage
from tradelab.engine.backtest import BacktestEngine
from tradelab.execution.sim_broker import SimulationConfig
from tradelab.risk.limits import PortfolioLimits, PositionLimits, RiskLimits
from tradelab.strategy.base import Strategy

START = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)


def make_instrument(symbol="TEST", currency="EUR", **kw):
    kw.setdefault("adv", D("10000000"))
    kw.setdefault("sigma_daily", D("0.015"))
    kw.setdefault("spread_bps", D("5"))
    return Instrument(symbol, currency=currency, **kw)


def series(inst, closes, start=START, volume=D("1000000")):
    """Build daily bars with a modest intrabar range around each close."""
    out = []
    for i, c in enumerate(closes):
        c = D(str(c))
        out.append(
            Bar(
                instrument=inst,
                timestamp=start + timedelta(days=i),
                open=c * D("0.998"),
                high=c * D("1.005"),
                low=c * D("0.995"),
                close=c,
                volume=volume,
            )
        )
    return out


class BuyOnceStrategy(Strategy):
    """Buys a fixed quantity on its first warm bar, then does nothing."""

    def __init__(self, inst, quantity=D("100"), strategy_id="buy_once", warmup=0):
        super().__init__(strategy_id, warmup_bars=warmup)
        self.inst = inst
        self.quantity = quantity
        self.bought = False
        self.seen_history_lengths = []

    @property
    def universe(self):
        return [self.inst]

    def on_bar(self, ctx, bars):
        self.seen_history_lengths.append(len(ctx.history(self.inst)))
        if not self.bought:
            ctx.buy(self.inst, self.quantity, reason="test entry")
            self.bought = True


class RecordFutureStrategy(Strategy):
    """Records the highest close it can see at each step, to detect lookahead."""

    def __init__(self, inst):
        super().__init__("peeker")
        self.inst = inst
        self.max_seen_per_bar = []

    @property
    def universe(self):
        return [self.inst]

    def on_bar(self, ctx, bars):
        closes = ctx.closes(self.inst)
        self.max_seen_per_bar.append(max(closes) if closes else None)


def permissive_limits(**overrides):
    base = dict(
        position=PositionLimits(
            max_position_weight=D("1.0"),
            min_order_notional=D("0"),
            max_cost_bps_of_notional=D("100000"),
            max_participation_of_adv=D("1"),
            min_price=D("0"),
        ),
        portfolio=PortfolioLimits(max_open_positions=50, max_new_positions_per_day=50),
    )
    base.update(overrides)
    return RiskLimits(**base)


class TestNoLookahead:
    def test_strategy_never_sees_future_bars(self):
        """Pins: the data window must exclude bars that have not closed."""
        inst = make_instrument()
        closes = [100, 101, 102, 150, 103]
        bars = series(inst, closes)
        strat = RecordFutureStrategy(inst)
        BacktestEngine(strategies=[strat], limits=permissive_limits()).run(bars)
        # At bar i the max visible close must be max(closes[:i+1]).
        expected = [max(D(str(c)) for c in closes[: i + 1]) for i in range(len(closes))]
        assert strat.max_seen_per_bar == expected
        # Specifically: the 150 spike must not be visible before it happens.
        assert strat.max_seen_per_bar[2] == D("102")

    def test_order_cannot_fill_on_its_signal_bar(self):
        """Pins: filling at the signal bar's close is the classic fake edge."""
        inst = make_instrument()
        bars = series(inst, [100, 110, 120])
        strat = BuyOnceStrategy(inst, quantity=D("10"))
        result = BacktestEngine(
            strategies=[strat],
            limits=permissive_limits(),
            commission_model=ZeroCommission(),
            slippage_model=NoSlippage(),
        ).run(bars)
        assert len(result.fills) == 1
        fill = result.fills[0]
        # Signal fires on bar 0 (close 100); fill must occur on bar 1 or later.
        assert fill.timestamp > bars[0].timestamp
        # And at bar 1's open (~109.78), not at bar 0's close of 100.
        assert fill.price > D("100")

    def test_warmup_suppresses_trading(self):
        """Pins: trading before indicators are populated fabricates early edge."""
        inst = make_instrument()
        bars = series(inst, [100] * 10)
        strat = BuyOnceStrategy(inst, quantity=D("10"), warmup=5)
        result = BacktestEngine(strategies=[strat], limits=permissive_limits()).run(bars)
        assert result.fills
        # First fill must come after the warmup window has been satisfied.
        assert result.fills[0].timestamp >= bars[5].timestamp


class TestFillRealism:
    def test_limit_requires_penetration_not_touch(self):
        """Pins: a touched limit assumes queue priority you did not have."""
        inst = make_instrument()
        # Bar 1 low is exactly 99.5; a buy limit at 99.5 is touched, not crossed.
        bar0 = Bar(inst, START, D("100"), D("100.5"), D("99.6"), D("100"), D("1000000"))
        bar1 = Bar(
            inst, START + timedelta(days=1), D("100"), D("100.5"), D("99.5"), D("100"), D("1000000")
        )

        class LimitAtLow(Strategy):
            @property
            def universe(self):
                return [inst]

            def on_bar(self, ctx, bars):
                if ctx.is_flat(inst) and not ctx.has_open_order(inst):
                    ctx.buy(inst, D("10"), OrderType.LIMIT, D("99.5"))

        strat = LimitAtLow("limit_test")
        result = BacktestEngine(
            strategies=[strat],
            limits=permissive_limits(),
            commission_model=ZeroCommission(),
        ).run([bar0, bar1])
        assert result.fills == [], "a merely-touched limit must not fill"

    def test_adverse_gap_fills_at_open_not_limit(self):
        """Pins: filling at the limit books a windfall from an adverse gap."""
        inst = make_instrument()
        bar0 = Bar(inst, START, D("100"), D("100.5"), D("99.5"), D("100"), D("1000000"))
        # Bar 1 gaps down hard: opens at 90, well through a 99 buy limit.
        bar1 = Bar(
            inst, START + timedelta(days=1), D("90"), D("91"), D("89"), D("90"), D("1000000")
        )

        class LimitBuy(Strategy):
            @property
            def universe(self):
                return [inst]

            def on_bar(self, ctx, bars):
                if ctx.is_flat(inst) and not ctx.has_open_order(inst):
                    ctx.buy(inst, D("10"), OrderType.LIMIT, D("99"))

        strat = LimitBuy("gap_test")
        result = BacktestEngine(
            strategies=[strat],
            limits=permissive_limits(),
            commission_model=ZeroCommission(),
            slippage_model=NoSlippage(),
        ).run([bar0, bar1])
        assert len(result.fills) == 1
        # Must fill at the open (90), not the limit (99).
        assert result.fills[0].price == pytest.approx(D("90"), abs=D("0.01"))

    def test_volume_participation_caps_fill(self):
        """Pins: an order larger than the bar's volume cannot fully fill."""
        inst = make_instrument(adv=D("10000"))
        bars = series(inst, [100, 100, 100], volume=D("1000"))
        strat = BuyOnceStrategy(inst, quantity=D("500"))
        result = BacktestEngine(
            strategies=[strat],
            limits=permissive_limits(),
            commission_model=ZeroCommission(),
            sim_config=SimulationConfig(max_volume_participation=D("0.05")),
        ).run(bars)
        # 5% of 1000 shares = 50 per bar, so the 500-share order cannot complete.
        assert result.fills
        assert sum(f.quantity for f in result.fills) < D("500")
        assert all(f.quantity <= D("50") for f in result.fills)

    def test_zero_volume_bar_does_not_fill(self):
        """Pins: a halt or data gap must not produce a phantom fill."""
        inst = make_instrument()
        bars = series(inst, [100, 100], volume=D("1000000"))
        bars[1] = Bar(inst, bars[1].timestamp, D("100"), D("100"), D("100"), D("100"), D("0"))
        strat = BuyOnceStrategy(inst, quantity=D("10"))
        result = BacktestEngine(
            strategies=[strat], limits=permissive_limits(), commission_model=ZeroCommission()
        ).run(bars)
        assert result.fills == []


class TestCostAccounting:
    def test_costs_are_recorded_and_reduce_equity(self):
        """Pins: commission must be deducted from equity, not merely reported.

        Isolated by differencing against a zero-cost run rather than by
        asserting a sign on the absolute return. The bar fixtures open below
        their close, so buying at the open and marking at the close is genuinely
        profitable -- an absolute assertion here would test the fixture's price
        path instead of the cost accounting.
        """
        inst = make_instrument()
        bars = series(inst, [100, 100, 100])

        def run(commission, slippage):
            return BacktestEngine(
                strategies=[BuyOnceStrategy(inst, quantity=D("50"))],
                initial_cash=D("10000"),
                limits=permissive_limits(),
                commission_model=commission,
                slippage_model=slippage,
            ).run(bars)

        costed = run(ibkr_default_router(), NoSlippage())
        free = run(ZeroCommission(), NoSlippage())

        assert costed.total_commission > 0
        assert free.total_commission == 0
        assert costed.cost_drag_bps() > 0
        # Same fills, same prices -- the only difference is the commission.
        assert len(costed.fills) == len(free.fills) == 1
        assert costed.fills[0].price == free.fills[0].price
        equity_gap = free.portfolio.equity - costed.portfolio.equity
        assert equity_gap == costed.total_commission

    def test_rejections_are_a_first_class_result(self):
        """Pins: a strategy blocked on cost grounds must be visible as such."""
        inst = make_instrument(currency="EUR", spread_bps=D("5"))
        bars = series(inst, [100, 100, 100])
        # Default limits reject a EUR 200 order as below min_order_notional.
        strat = BuyOnceStrategy(inst, quantity=D("2"))
        result = BacktestEngine(
            strategies=[strat], initial_cash=D("10000"), limits=RiskLimits()
        ).run(bars)
        assert result.fills == []
        assert result.rejections
        assert "cost_efficiency" in result.rejection_summary()

    def test_risk_gate_is_not_bypassable(self):
        """Pins: the engine must route every intent through risk, not some."""
        inst = make_instrument()
        bars = series(inst, [100] * 5)

        class Spammer(Strategy):
            @property
            def universe(self):
                return [inst]

            def on_bar(self, ctx, bars):
                for _ in range(50):
                    ctx.buy(inst, D("10"))

        result = BacktestEngine(
            strategies=[Spammer("spam")],
            initial_cash=D("10000"),
            limits=RiskLimits(),
        ).run(bars)
        # The rate limiter and position caps must bind; 250 intents cannot all pass.
        assert result.orders_submitted < 250
        assert result.rejections


class TestDeterminism:
    def test_identical_runs_produce_identical_results(self):
        """Pins: reproducibility. A non-deterministic backtest cannot be validated."""
        inst = make_instrument()
        bars = series(inst, [100, 102, 101, 105, 103, 107])

        def run():
            strat = BuyOnceStrategy(inst, quantity=D("20"))
            return BacktestEngine(
                strategies=[strat], initial_cash=D("10000"), limits=permissive_limits()
            ).run(bars)

        a, b = run(), run()
        assert [p.equity for p in a.equity_curve] == [p.equity for p in b.equity_curve]
        assert [(f.price, f.quantity) for f in a.fills] == [(f.price, f.quantity) for f in b.fills]
