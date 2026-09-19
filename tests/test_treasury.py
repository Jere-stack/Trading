"""Tests for currency funding.

A EUR account buying USD stocks needs USD. Left unfunded the balance simply
goes negative, which at a real broker is a margin loan -- the implicit leverage
a cash-equity mandate forbids. The first paper run accumulated -7,717 USD this
way across 19 fills before any of this existed.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from tests.conftest import NOW
from tradelab.core.types import Instrument, Quote
from tradelab.portfolio.portfolio import InsufficientFundsError, Portfolio
from tradelab.portfolio.treasury import BlockFxPolicy, NoFxPolicy


def account(eur="10000", usd="0", rate="0.87") -> Portfolio:
    pf = Portfolio(base_currency="EUR")
    pf.deposit(D(eur))
    if D(usd) != 0:
        pf.deposit(D(usd), "USD")
    pf.set_fx_rate("USD", D(rate))
    return pf


class TestConversion:
    def test_conversion_moves_cash_and_charges_cost(self):
        pf = account()
        credited = pf.convert("EUR", "USD", D("870"), D("1.149425"), cost=D("2"))
        assert pf.cash["EUR"] == D("9130.0000")
        assert credited == pytest.approx(D("1000"), abs=D("1"))
        # Cost is charged in the credited currency by default.
        assert pf.cash["USD"] == pytest.approx(D("998"), abs=D("1"))

    def test_cannot_convert_more_than_held(self):
        """Converting beyond the balance is borrowing, not converting."""
        pf = account(eur="100")
        with pytest.raises(InsufficientFundsError, match="borrowing"):
            pf.convert("EUR", "USD", D("500"), D("1.15"))

    def test_conversion_preserves_equity_net_of_cost(self):
        pf = account()
        before = pf.equity
        pf.convert("EUR", "USD", D("870"), D("1.149425"), cost=D("2"))
        after = pf.equity
        # Equity falls by the conversion cost and nothing else.
        assert before - after == pytest.approx(D("2") * D("0.87"), abs=D("0.5"))

    def test_rejects_invalid_inputs(self):
        pf = account()
        with pytest.raises(ValueError):
            pf.convert("EUR", "USD", D("0"), D("1.15"))
        with pytest.raises(ValueError):
            pf.convert("EUR", "USD", D("100"), D("0"))


class TestNegativeBalances:
    def test_detects_a_debit_balance(self):
        """Equity nets the currencies, so a loan is invisible without this."""
        pf = account()
        pf.cash["USD"] = D("-7716.89")
        assert "USD" in pf.negative_balances()
        assert pf.equity > 0, "equity still looks healthy, which is the danger"

    def test_funded_account_has_none(self):
        assert account(usd="5000").negative_balances() == {}


class TestBlockFxPolicy:
    def test_converts_a_block_not_the_exact_need(self):
        """Pins the economics: per-trade conversion costs 38x more."""
        pf = account()
        action = BlockFxPolicy(min_block=D("2500")).fund(pf, "USD", D("1000"))
        assert action is not None
        assert action.amount_to >= D("2500")
        # The USD 2.00 minimum over a 2,500 block is under 1 bp.
        assert action.cost / action.amount_from * 10_000 < D("15")

    def test_a_funded_balance_needs_no_conversion(self):
        pf = account(usd="5000")
        assert BlockFxPolicy().fund(pf, "USD", D("1000")) is None

    def test_second_order_rides_the_buffer(self):
        """The buffer is the point: one conversion funds several orders."""
        pf = account()
        policy = BlockFxPolicy()
        first = policy.fund(pf, "USD", D("1000"))
        second = policy.fund(pf, "USD", D("900"))
        assert first is not None
        assert second is None, "the block should already cover this"

    def test_base_currency_never_converts(self):
        assert BlockFxPolicy().fund(account(), "EUR", D("1000")) is None

    def test_refuses_to_breach_the_foreign_exposure_ceiling(self):
        """Unhedged FX is uncompensated risk -- measured at 6.70%/yr on EUR/USD."""
        pf = account(eur="10000")
        policy = BlockFxPolicy(max_foreign_share=D("0.10"), min_block=D("100"))
        assert policy.fund(pf, "USD", D("9000")) is None

    def test_refuses_when_base_cash_is_insufficient(self):
        pf = account(eur="100")
        assert BlockFxPolicy().fund(pf, "USD", D("5000")) is None

    def test_partial_funding_is_refused_not_attempted(self):
        """A half-funded order would still drive the balance negative."""
        pf = account(eur="10000")
        policy = BlockFxPolicy(max_foreign_share=D("0.30"), min_block=D("100"))
        action = policy.fund(pf, "USD", D("20000"))
        assert action is None
        assert pf.negative_balances() == {}

    def test_no_policy_leaves_the_balance_unfunded(self):
        pf = account()
        assert NoFxPolicy().fund(pf, "USD", D("1000")) is None
        assert pf.cash.get("USD", D("0")) == 0


class TestCashSufficiencyCheck:
    def test_unfunded_order_is_trimmed_to_what_cash_affords(self, us_stock, make_ctx, portfolio):
        from tradelab.core.enums import RiskDecision, Side
        from tradelab.core.types import OrderRequest
        from tradelab.risk.engine import RiskEngine

        portfolio.cash["USD"] = D("500")
        ctx = make_ctx({us_stock: D("200")})
        result = RiskEngine().evaluate(
            OrderRequest(us_stock, Side.BUY, D("50"), strategy_id="t"), ctx
        )
        assert result.decision is RiskDecision.REJECT or (
            result.approved_request.quantity <= D("3")
        )

    def test_empty_balance_rejects_outright(self, us_stock, make_ctx, portfolio):
        from tradelab.core.enums import RiskDecision, Side
        from tradelab.core.types import OrderRequest
        from tradelab.risk.engine import RiskEngine

        portfolio.cash["USD"] = D("0")
        ctx = make_ctx({us_stock: D("200")})
        result = RiskEngine().evaluate(
            OrderRequest(us_stock, Side.BUY, D("10"), strategy_id="t"), ctx
        )
        assert result.decision is RiskDecision.REJECT
        assert "margin loan" in result.reason

    def test_sells_are_never_blocked_on_cash(self, us_stock, make_ctx, portfolio):
        """Selling raises cash; blocking it would trap a position."""
        from tests.conftest import NOW
        from tradelab.core.enums import Side
        from tradelab.core.types import Fill, OrderRequest
        from tradelab.risk.engine import CashSufficiencyCheck

        portfolio.apply_fill(Fill("f", "o", us_stock, Side.BUY, D("10"), D("200"), NOW))
        portfolio.cash["USD"] = D("0")
        ctx = make_ctx({us_stock: D("200")})
        verdict = CashSufficiencyCheck().evaluate(
            OrderRequest(us_stock, Side.SELL, D("10"), strategy_id="t"), ctx
        )
        assert verdict.decision.value == "APPROVE"


class TestCurrencyExposureAccounting:
    """Buying in a currency you already hold is exposure-neutral."""

    def test_same_currency_purchase_does_not_consume_the_budget(
        self, us_stock, make_ctx, portfolio
    ):
        """Pins the bug that blocked a USD-only book.

        `exposure_by_currency` counts cash AND positions, so a purchase funded
        from same-currency cash leaves total exposure unchanged. Charging it
        against the currency budget double-counted: after converting EUR 9,500
        to USD, the budget was exhausted before a single share was bought and
        gross exposure stalled at 0.51x against a 0.90 target.
        """
        from tradelab.core.enums import RiskDecision, Side
        from tradelab.core.types import OrderRequest
        from tradelab.risk.engine import RiskEngine
        from tradelab.risk.limits import PortfolioLimits, PositionLimits, RiskLimits

        limits = RiskLimits(
            position=PositionLimits(max_position_weight=D("1.0")),
            portfolio=PortfolioLimits(max_currency_exposure=D("1.0")),
        )
        ctx = make_ctx({us_stock: D("200")}, limits=limits)
        result = RiskEngine().evaluate(
            OrderRequest(us_stock, Side.BUY, D("5"), strategy_id="t"), ctx
        )
        assert result.decision is not RiskDecision.REJECT, result.reason

    def test_exposure_is_unchanged_by_a_same_currency_purchase(self, us_stock, portfolio):
        """The accounting identity the fix relies on."""
        from tests.conftest import NOW
        from tradelab.core.enums import Side
        from tradelab.core.types import Fill

        before = portfolio.exposure_by_currency()["USD"]
        portfolio.apply_fill(Fill("f", "o", us_stock, Side.BUY, D("10"), D("200"), NOW))
        after = portfolio.exposure_by_currency()["USD"]
        # Cash fell, position rose; only commission leaves the currency.
        assert abs(after - before) < D("5")


class TestCurrencyAwareSizing:
    """A base-currency budget must be converted before it meets a price."""

    def test_context_converts_a_base_budget_into_instrument_currency(self, us_stock, portfolio):
        """Pins the arithmetic the equal-weight vehicle got wrong.

        `ctx.equity` is EUR; `ctx.last_price` is USD. Dividing one by the other
        sizes every US name short by the EUR/USD rate. It is invisible in a
        single-currency backtest and it does not look like a bug in a live run
        -- it looks like the risk gate refusing to fill the book.
        """
        from tradelab.strategy.base import StrategyContext

        ctx = StrategyContext(
            now=NOW,
            strategy_id="t",
            equity=portfolio.equity,
            base_currency=portfolio.base_currency,
            _fx_rates=portfolio.fx_rates,
        )
        # EUR 920 of a USD name at 0.92 EUR/USD is USD 1,000, not USD 920.
        assert ctx.budget_in(us_stock, D("920")) == D("1000")
        assert ctx.to_base(D("1000"), "USD") == D("920")

    def test_unknown_currency_raises_rather_than_assuming_parity(self, portfolio):
        """A silent 1.0 would misprice a book by the size of the FX move."""
        from tradelab.strategy.base import StrategyContext

        ctx = StrategyContext(
            now=NOW,
            strategy_id="t",
            equity=portfolio.equity,
            base_currency=portfolio.base_currency,
            _fx_rates=portfolio.fx_rates,
        )
        with pytest.raises(KeyError, match="no FX rate for JPY"):
            ctx.budget_in(
                Instrument("7203", currency="JPY", adv=D("1000000")),
                D("1000"),
            )

    def test_equal_weight_sizes_a_usd_name_from_converted_equity(self, us_stock, portfolio):
        """End to end through the strategy, not just the helper.

        EUR 19,200 equity, 90% gross, one name -> EUR 17,280 -> USD 18,782 at
        0.92, which is 93 shares at USD 200. Sizing off unconverted EUR would
        ask for 86.
        """
        from tradelab.strategy.base import StrategyContext
        from tradelab.strategy.examples.monthly_equal_weight import MonthlyEqualWeight

        strategy = MonthlyEqualWeight([us_stock], min_order_value=D("0"))
        ctx = StrategyContext(
            now=NOW,
            strategy_id=strategy.strategy_id,
            equity=portfolio.equity,
            base_currency=portfolio.base_currency,
            _fx_rates=portfolio.fx_rates,
            _quotes={us_stock.key: Quote(us_stock, NOW, D("199.9"), D("200.1"))},
        )
        strategy.on_bar(ctx, {})
        (request,) = ctx.drain()
        assert request.quantity == D("93")

    def test_uneconomic_top_up_is_not_submitted(self, us_stock, portfolio):
        """Prevents: re-submitting the same rejected order every month forever.

        A rejected order never changes the position that produced it, so the
        strategy asks again next month, and the month after. The two-year paper
        run logged 39 identical rejections this way.
        """
        from tradelab.core.enums import Side
        from tradelab.core.types import Fill
        from tradelab.strategy.base import StrategyContext
        from tradelab.strategy.examples.monthly_equal_weight import MonthlyEqualWeight

        portfolio.apply_fill(Fill("f", "o", us_stock, Side.BUY, D("92"), D("200"), NOW))
        strategy = MonthlyEqualWeight([us_stock], min_order_value=D("400"))
        ctx = StrategyContext(
            now=NOW,
            strategy_id=strategy.strategy_id,
            equity=portfolio.equity,
            base_currency=portfolio.base_currency,
            _positions=portfolio.positions,
            _fx_rates=portfolio.fx_rates,
            _quotes={us_stock.key: Quote(us_stock, NOW, D("199.9"), D("200.1"))},
        )
        strategy.on_bar(ctx, {})
        # The one-share gap is worth EUR 184, under the EUR 400 floor.
        assert ctx.drain() == []
