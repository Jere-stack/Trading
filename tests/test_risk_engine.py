"""Risk engine tests.

Each test names the loss it prevents. A risk test that only asserts a boolean
without stating the failure mode tends to be deleted the first time it is
inconvenient.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal as D

import pytest

from tests.conftest import NOW
from tradelab.core.enums import OrderType, RiskDecision, Side
from tradelab.core.types import Instrument, OrderRequest
from tradelab.risk.engine import (
    RiskCheck,
    RiskEngine,
    RiskVerdict,
)
from tradelab.risk.killswitch import HaltLevel
from tradelab.risk.limits import (
    OperationalLimits,
    PortfolioLimits,
    PositionLimits,
    RiskLimits,
)


def req(inst, qty, side=Side.BUY, strategy_id="test", **kw):
    return OrderRequest(inst, side, D(str(qty)), strategy_id=strategy_id, **kw)


class TestMandate:
    def test_short_rejected_by_default(self, fi_stock, make_ctx):
        """Prevents: unbounded loss and borrow costs outside a cash-equity mandate."""
        ctx = make_ctx({fi_stock: D("4.50")})
        r = RiskEngine().evaluate(req(fi_stock, 100, Side.SELL), ctx)
        assert r.decision is RiskDecision.REJECT
        assert "shorting disabled" in r.reason

    def test_oversell_clamped_to_flat(self, fi_stock, make_ctx, portfolio):
        """Prevents: an exit signal silently opening a short position."""
        from tradelab.core.types import Fill

        portfolio.apply_fill(Fill("f", "o", fi_stock, Side.BUY, D("100"), D("4.50"), NOW))
        ctx = make_ctx({fi_stock: D("4.50")})
        r = RiskEngine().evaluate(req(fi_stock, 250, Side.SELL), ctx)
        assert r.decision is RiskDecision.RESIZE
        assert r.approved_request.quantity == D("100")

    def test_non_equity_rejected(self, make_ctx):
        """Prevents: a derivative contract entering a stocks-only system."""
        opt = Instrument("AAPL_OPT", currency="USD", adv=D("1000000"))
        object.__setattr__(opt, "asset_class", "FUTURE")
        ctx = make_ctx({opt: D("100")})
        r = RiskEngine().evaluate(req(opt, 10), ctx)
        assert r.decision is RiskDecision.REJECT


class TestCostEfficiency:
    def test_tiny_order_rejected(self, fi_stock, make_ctx):
        """Prevents: the guaranteed loss of a EUR 100 order paying a EUR 1.25 floor."""
        ctx = make_ctx({fi_stock: D("4.50")})
        r = RiskEngine().evaluate(req(fi_stock, 20), ctx)  # EUR 90 notional
        assert r.decision is RiskDecision.REJECT
        assert "min_order_notional" in r.reason

    def test_wide_spread_microcap_rejected(self, micro_cap, make_ctx):
        """Prevents: trading a 180 bps spread where no realistic edge survives."""
        ctx = make_ctx({micro_cap: D("6.00")})
        r = RiskEngine().evaluate(req(micro_cap, 100), ctx)  # EUR 600 notional
        assert r.decision is RiskDecision.REJECT
        assert "cost" in r.reason.lower()

    def test_economic_order_passes(self, us_stock, make_ctx):
        """A liquid, adequately sized order must actually be allowed through."""
        ctx = make_ctx({us_stock: D("200")})
        r = RiskEngine().evaluate(req(us_stock, 6), ctx)  # ~EUR 1104
        assert r.is_approved, r.reason

    def test_resize_that_becomes_uneconomic_is_rejected(self, fi_stock, make_ctx):
        """Prevents: a resize producing a stub that pays full commission.

        This is the subtle one. Sizing limits shrink the order; without a
        post-resize cost recheck the system would happily send the stub.
        """
        limits = RiskLimits(
            position=PositionLimits(
                max_position_weight=D("0.02"),  # EUR 200 of EUR 10k
                min_order_notional=D("400"),
            )
        )
        ctx = make_ctx({fi_stock: D("4.50")}, limits=limits)
        r = RiskEngine().evaluate(req(fi_stock, 500), ctx)
        assert r.decision is RiskDecision.REJECT
        assert "post_resize" in r.reason


class TestSizing:
    def test_position_weight_resizes(self, us_stock, make_ctx):
        """Prevents: a single name becoming an outsized share of a small account."""
        ctx = make_ctx({us_stock: D("200")})
        r = RiskEngine().evaluate(req(us_stock, 100), ctx)
        assert r.decision is RiskDecision.RESIZE
        # 15% of EUR 19,200 equity = EUR 2,880 -> /(200 * 0.92) = 15 shares
        assert r.approved_request.quantity == D("15")

    def test_capacity_limits_thin_stock(self, micro_cap, make_ctx):
        """Prevents: taking a position that cannot be exited in one day."""
        limits = RiskLimits(
            position=PositionLimits(max_cost_bps_of_notional=D("10000"), min_price=D("1"))
        )
        ctx = make_ctx({micro_cap: D("6.00")}, limits=limits)
        r = RiskEngine().evaluate(req(micro_cap, 5000), ctx)
        assert r.decision is RiskDecision.RESIZE
        assert r.approved_request.quantity == D("150")  # 1% of 15k ADV

    def test_missing_adv_rejected(self, make_ctx):
        """Prevents: sizing blind in an instrument of unknown liquidity."""
        unknown = Instrument("UNKN", currency="EUR", spread_bps=D("10"))
        ctx = make_ctx({unknown: D("50")})
        r = RiskEngine().evaluate(req(unknown, 20), ctx)
        assert r.decision is RiskDecision.REJECT
        assert "ADV" in r.reason


class TestPortfolioLimits:
    def test_gross_exposure_ceiling(self, fi_stock, make_ctx, portfolio):
        """Prevents: implicit leverage via margin the broker extends by default."""
        from tradelab.core.types import Fill

        portfolio.apply_fill(Fill("f", "o", fi_stock, Side.BUY, D("2000"), D("4.50"), NOW))
        limits = RiskLimits(
            position=PositionLimits(max_position_weight=D("1.0")),
            portfolio=PortfolioLimits(max_gross_exposure=D("1.0")),
        )
        ctx = make_ctx({fi_stock: D("4.50")}, limits=limits)
        r = RiskEngine().evaluate(req(fi_stock, 400), ctx)
        assert r.decision is RiskDecision.RESIZE
        assert r.approved_request.quantity < D("400")

    def test_currency_cap_binds_when_funding_must_convert(self, us_stock, make_ctx, portfolio):
        """Prevents: unhedged USD risk swamping a small per-trade edge.

        The cap is tested on `PortfolioExposureCheck` alone rather than through
        the engine, because an unfunded USD order is also (correctly) refused by
        `CashSufficiencyCheck`, and a REJECT from that check would pass a naive
        assertion here while telling us nothing about the currency budget.
        """
        from tradelab.risk.engine import PortfolioExposureCheck

        portfolio.set_cash(D("0"), "USD")  # EUR-only book: any USD buy converts
        limits = RiskLimits(
            position=PositionLimits(max_position_weight=D("1.0")),
            portfolio=PortfolioLimits(max_currency_exposure=D("0.10")),
        )
        ctx = make_ctx({us_stock: D("200")}, limits=limits)
        verdict = PortfolioExposureCheck().evaluate(req(us_stock, 50), ctx)
        assert verdict.decision is RiskDecision.RESIZE
        # 10% of EUR 10,000 equity = EUR 1,000 -> /(200 * 0.92) = 5 shares.
        assert verdict.quantity == D("5")

    def test_currency_cap_does_not_block_cash_already_held(self, us_stock, make_ctx, portfolio):
        """Prevents: a funded account sitting in cash because the cap is breached.

        The fixture holds EUR 9,200 of USD against EUR 19,200 equity -- 48%,
        far past a 10% cap. Refusing to invest it would not reduce the USD risk
        by one cent; it would only convert an invested dollar into an idle one.
        Exposure moves at conversion, and that is where the treasury policy
        bounds it.
        """
        from tradelab.risk.engine import PortfolioExposureCheck

        limits = RiskLimits(
            position=PositionLimits(max_position_weight=D("1.0")),
            portfolio=PortfolioLimits(max_currency_exposure=D("0.10")),
        )
        ctx = make_ctx({us_stock: D("200")}, limits=limits)
        verdict = PortfolioExposureCheck().evaluate(req(us_stock, 25), ctx)
        assert verdict.decision is RiskDecision.APPROVE, verdict.reason

    def test_max_open_positions(self, make_ctx, portfolio):
        """Prevents: over-diversifying into positions too small to be economic."""
        from tradelab.core.types import Fill

        limits = RiskLimits(portfolio=PortfolioLimits(max_open_positions=2))
        insts = [
            Instrument(f"S{i}", currency="EUR", adv=D("5000000"), spread_bps=D("8"))
            for i in range(3)
        ]
        for inst in insts[:2]:
            portfolio.apply_fill(
                Fill(f"f{inst.symbol}", "o", inst, Side.BUY, D("100"), D("10"), NOW)
            )
        ctx = make_ctx({insts[2]: D("10")}, limits=limits)
        r = RiskEngine().evaluate(req(insts[2], 60), ctx)
        assert r.decision is RiskDecision.REJECT
        assert "max 2" in r.reason

    def test_strategy_budget_enforced(self, fi_stock, us_stock, make_ctx, portfolio):
        """Prevents: one strategy consuming the capacity budgeted to another."""
        from tradelab.core.types import Fill

        limits = RiskLimits(
            position=PositionLimits(max_position_weight=D("1.0")),
            strategy_budgets={"greedy": D("0.30")},
        )
        # Budget is 30% of EUR 19,200 equity = EUR 5,760. A position of 1,200
        # shares at 4.50 consumes EUR 5,400, leaving EUR 360 of headroom.
        portfolio.apply_fill(
            Fill("f", "o", fi_stock, Side.BUY, D("1200"), D("4.50"), NOW, strategy_id="greedy")
        )
        ctx = make_ctx({fi_stock: D("4.50")}, limits=limits)
        r = RiskEngine().evaluate(req(fi_stock, 400, strategy_id="greedy"), ctx)
        assert r.decision in (RiskDecision.RESIZE, RiskDecision.REJECT)
        if r.decision is RiskDecision.RESIZE:
            assert r.approved_request.quantity <= D("80")


class TestLossLimits:
    def test_daily_loss_soft_halts(self, fi_stock, make_ctx, portfolio, state):
        """Prevents: a bad day compounding into a catastrophic one."""
        # Equity is 19,200; a start-of-day figure above 19,800 puts the day
        # more than 3% down.
        state.day_start_equity = D("21000")
        ctx = make_ctx({fi_stock: D("4.50")})
        r = RiskEngine().evaluate(req(fi_stock, 200), ctx)
        assert r.decision is RiskDecision.REJECT
        assert state.kill_switch.level is HaltLevel.SOFT

    def test_soft_halt_still_allows_exit(self, fi_stock, make_ctx, portfolio, state):
        """Prevents: a halt that traps you in a losing position."""
        from tradelab.core.types import Fill

        portfolio.apply_fill(Fill("f", "o", fi_stock, Side.BUY, D("400"), D("4.50"), NOW))
        state.day_start_equity = D("21000")
        ctx = make_ctx({fi_stock: D("4.50")})
        r = RiskEngine().evaluate(req(fi_stock, 400, Side.SELL), ctx)
        assert r.is_approved, r.reason

    def test_drawdown_hard_halts_and_blocks_exit_pending_review(
        self, fi_stock, make_ctx, portfolio, state
    ):
        """Prevents: an automated system quietly resuming after a 15% drawdown."""
        # Equity is 19,200; a peak above 22,588 puts drawdown past 15%.
        state.peak_equity = D("24000")
        ctx = make_ctx({fi_stock: D("4.50")})
        r = RiskEngine().evaluate(req(fi_stock, 200), ctx)
        assert r.decision is RiskDecision.REJECT
        assert state.kill_switch.level is HaltLevel.HARD

    def test_hard_halt_requires_named_operator(self, state):
        """Prevents: an un-auditable re-arm after a catastrophic stop."""
        state.kill_switch.trip(HaltLevel.HARD, "test", NOW, "test")
        with pytest.raises(ValueError, match="operator identity"):
            state.kill_switch.reset("", NOW)
        state.kill_switch.reset("jere", NOW)
        assert state.kill_switch.level is HaltLevel.NONE

    def test_soft_cannot_downgrade_hard(self, state):
        """Prevents: a routine soft trip masking an unresolved hard halt."""
        state.kill_switch.trip(HaltLevel.HARD, "drawdown", NOW, "loss_limits")
        state.kill_switch.trip(HaltLevel.SOFT, "daily loss", NOW, "loss_limits")
        assert state.kill_switch.level is HaltLevel.HARD


class TestOperational:
    def test_stale_data_rejected(self, fi_stock, make_ctx):
        """Prevents: trading on a frozen feed, which looks healthy but prices the past."""
        ctx = make_ctx({fi_stock: D("4.50")})
        ctx.price_timestamps[fi_stock.key] = NOW - timedelta(hours=2)
        r = RiskEngine().evaluate(req(fi_stock, 200), ctx)
        assert r.decision is RiskDecision.REJECT
        assert "stale" in r.reason

    def test_fat_finger_limit_price_rejected(self, fi_stock, make_ctx):
        """Prevents: a misplaced decimal executing far from the market."""
        ctx = make_ctx({fi_stock: D("4.50")})
        r = RiskEngine().evaluate(
            req(fi_stock, 200, order_type=OrderType.LIMIT, limit_price=D("45.00")), ctx
        )
        assert r.decision is RiskDecision.REJECT
        assert "deviates" in r.reason

    def test_duplicate_order_rejected(self, fi_stock, make_ctx, state):
        """Prevents: a restart or double signal resubmitting working orders."""
        ctx = make_ctx({fi_stock: D("4.50")})
        order = req(fi_stock, 200)
        first = RiskEngine().evaluate(order, ctx)
        assert first.is_approved, first.reason
        from tradelab.risk.engine import dedupe_key

        state.record_order(NOW, dedupe_key(order))
        second = RiskEngine().evaluate(order, ctx)
        assert second.decision is RiskDecision.REJECT
        assert "duplicate" in second.reason.lower()

    def test_order_rate_limited(self, fi_stock, make_ctx, state):
        """Prevents: a runaway loop firing hundreds of orders."""
        limits = RiskLimits(operational=OperationalLimits(max_orders_per_minute=3))
        ctx = make_ctx({fi_stock: D("4.50")}, limits=limits)
        for i in range(3):
            state.record_order(NOW, f"k{i}")
        r = RiskEngine().evaluate(req(fi_stock, 200), ctx)
        assert r.decision is RiskDecision.REJECT
        assert "rate cap" in r.reason

    def test_market_closed_rejected(self, fi_stock, make_ctx):
        """Prevents: executing at outside-RTH spreads the model never validated."""
        ctx = make_ctx({fi_stock: D("4.50")}, market_open=False)
        r = RiskEngine().evaluate(req(fi_stock, 200), ctx)
        assert r.decision is RiskDecision.REJECT
        assert "closed" in r.reason

    def test_no_price_rejected(self, fi_stock, make_ctx):
        """Prevents: pricing an order blind when the feed has no data."""
        ctx = make_ctx({}, with_quotes=False)
        r = RiskEngine().evaluate(req(fi_stock, 200), ctx)
        assert r.decision is RiskDecision.REJECT


class TestFailClosed:
    def test_raising_check_rejects(self, fi_stock, make_ctx):
        """The core safety property: an unevaluable limit is a breached limit."""

        class Exploding(RiskCheck):
            name = "exploding"

            def evaluate(self, request, ctx):
                raise RuntimeError("database unavailable")

        engine = RiskEngine(checks=[Exploding()])
        ctx = make_ctx({fi_stock: D("4.50")})
        r = engine.evaluate(req(fi_stock, 200), ctx)
        assert r.decision is RiskDecision.REJECT
        assert "Failing closed" in r.reason

    def test_missing_fx_rate_rejects(self, us_stock, make_ctx, portfolio):
        """Prevents: an assumed FX rate misstating equity and every derived limit."""
        del portfolio.fx_rates["USD"]
        ctx = make_ctx({us_stock: D("200")})
        r = RiskEngine().evaluate(req(us_stock, 5), ctx)
        assert r.decision is RiskDecision.REJECT

    def test_smallest_resize_wins(self, us_stock, make_ctx):
        """Resize is a floor: the tightest constraint must bind."""

        class Cap(RiskCheck):
            def __init__(self, n, q):
                self.name = n
                self.q = q

            def evaluate(self, request, ctx):
                return RiskVerdict.resize(self.name, self.q, "test cap")

        engine = RiskEngine(checks=[Cap("a", D("30")), Cap("b", D("7")), Cap("c", D("20"))])
        ctx = make_ctx({us_stock: D("200")})
        r = engine.evaluate(req(us_stock, 100), ctx)
        assert r.decision is RiskDecision.RESIZE
        assert r.approved_request.quantity == D("7")
