"""Shared fixtures.

Fixtures build a realistic EUR 10k account, because limits that pass on a
EUR 1m account routinely fail on a small one -- the per-order commission floor
changes which orders are economic at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from tradelab.core.enums import Venue
from tradelab.core.types import Instrument, Quote
from tradelab.costs.commission import ibkr_default_router
from tradelab.costs.slippage import SpreadImpactSlippage
from tradelab.portfolio.portfolio import Portfolio
from tradelab.risk.engine import RiskContext
from tradelab.risk.killswitch import RiskState
from tradelab.risk.limits import RiskLimits

NOW = datetime(2026, 9, 17, 13, 30, tzinfo=UTC)


@pytest.fixture
def us_stock() -> Instrument:
    return Instrument(
        "AAPL", currency="USD", adv=D("50000000"), sigma_daily=D("0.018"), spread_bps=D("1.5")
    )


@pytest.fixture
def fi_stock() -> Instrument:
    return Instrument(
        "NOKIA",
        venue=Venue.HELSINKI,
        currency="EUR",
        adv=D("8000000"),
        sigma_daily=D("0.020"),
        spread_bps=D("8"),
    )


@pytest.fixture
def micro_cap() -> Instrument:
    """A Helsinki micro-cap: thin, wide, and the natural home of fake backtest edge."""
    return Instrument(
        "MICRO",
        venue=Venue.HELSINKI,
        currency="EUR",
        adv=D("15000"),
        sigma_daily=D("0.045"),
        spread_bps=D("180"),
    )


@pytest.fixture
def portfolio() -> Portfolio:
    """A EUR account holding a standing USD balance.

    Total equity is EUR 19,200: 10,000 EUR plus 10,000 USD at 0.92. Funding
    both currencies mirrors what the block-conversion treasury policy actually
    maintains -- an account holding only EUR would have every USD order
    funded by overdraft, which CashSufficiencyCheck correctly refuses.
    """
    pf = Portfolio(base_currency="EUR")
    pf.deposit(D("10000"))
    # A standing USD balance, which is what the block-conversion treasury
    # policy actually maintains. Funding only EUR would leave every USD order
    # relying on an overdraft, which CashSufficiencyCheck correctly refuses.
    pf.deposit(D("10000"), "USD")
    pf.set_fx_rate("USD", D("0.92"))
    return pf


@pytest.fixture
def state(portfolio: Portfolio) -> RiskState:
    st = RiskState()
    st.start_session(NOW.date(), portfolio.equity)
    return st


@pytest.fixture
def make_ctx(portfolio, state):
    def _make(
        prices: dict[Instrument, D],
        limits: RiskLimits | None = None,
        now: datetime = NOW,
        market_open: bool = True,
        with_quotes: bool = True,
    ) -> RiskContext:
        ref = {inst.key: px for inst, px in prices.items()}
        quotes = {}
        if with_quotes:
            for inst, px in prices.items():
                half = px * (inst.spread_bps or D("5")) / D("20000")
                quotes[inst.key] = Quote(inst, now, px - half, px + half)
        return RiskContext(
            portfolio=portfolio,
            state=state,
            limits=limits or RiskLimits(),
            now=now,
            reference_prices=ref,
            quotes=quotes,
            price_timestamps={inst.key: now for inst in prices},
            commission_model=ibkr_default_router(),
            slippage_model=SpreadImpactSlippage(),
            market_open=market_open,
        )

    return _make
