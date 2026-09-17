"""Risk limit definitions.

These are pydantic models, not plain dataclasses, because limits arrive from
YAML written by a human under time pressure. A typo like `max_position_weight:
50` meaning "50%" but parsed as 5000% must fail at load time, loudly, rather
than at 03:00 in a live session. Every bound below is validated.

Limit philosophy for a EUR 10k account:

* Position sizing is driven by **cost efficiency**, not just risk. A position
  too small to amortise a EUR 1.25 commission is a guaranteed loser regardless
  of signal quality, so `min_order_notional` and `max_cost_bps_of_notional` are
  risk limits here in the same sense as loss limits.

* Concentration limits at EUR 10k cannot mirror institutional ones. With a EUR
  1.25 floor, a 2% position (EUR 200) pays 125 bps round trip in commission
  alone -- so wide diversification is *unaffordable*. This forces a
  concentrated book (8-15 names), which in turn makes per-position stops and a
  portfolio drawdown kill switch the primary defences rather than diversification.

* Loss limits are expressed against **start-of-day equity and peak equity**, not
  against a static initial figure, so they stay meaningful as the account grows
  or shrinks.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator


class PositionLimits(BaseModel):
    """Per-position sizing and cost-efficiency constraints."""

    model_config = {"extra": "forbid"}

    max_position_weight: Decimal = Field(
        default=Decimal("0.15"),
        gt=0,
        le=1,
        description="Max |market value| of one position as a fraction of equity.",
    )
    max_position_notional: Decimal | None = Field(
        default=None, gt=0, description="Absolute per-position cap in base currency."
    )
    min_order_notional: Decimal = Field(
        default=Decimal("400"),
        ge=0,
        description=(
            "Reject orders below this notional. At a EUR 1.25 commission floor, a "
            "EUR 400 order pays ~31 bps one way; below that, commission alone "
            "exceeds any plausible per-trade edge."
        ),
    )
    max_cost_bps_of_notional: Decimal = Field(
        default=Decimal("35"),
        gt=0,
        description=(
            "Reject an order whose modelled one-way cost (commission + spread + "
            "impact) exceeds this many bps of its notional. This is the single "
            "most effective guard against cost-driven losses at small account size."
        ),
    )
    max_participation_of_adv: Decimal = Field(
        default=Decimal("0.01"),
        gt=0,
        le=1,
        description=(
            "Max order quantity as a fraction of average daily volume. Bounds "
            "market impact and, critically, bounds the *exit* -- a position you "
            "cannot liquidate in a day is not a position, it is a hostage."
        ),
    )
    allow_short: bool = Field(
        default=False,
        description=(
            "Shorting is off by default. It requires a margin account, incurs "
            "borrow fees, carries unbounded loss and recall risk, and is outside "
            "the stated cash-equity mandate."
        ),
    )
    max_price: Decimal | None = Field(default=None, gt=0)
    min_price: Decimal = Field(
        default=Decimal("3"),
        ge=0,
        description=(
            "Reject sub-EUR/USD-3 stocks. Their quoted spreads in bps are wide, "
            "their tick size is a large fraction of price, and they are the "
            "natural habitat of both manipulation and survivorship bias."
        ),
    )


class PortfolioLimits(BaseModel):
    """Book-level exposure and concentration constraints."""

    model_config = {"extra": "forbid"}

    max_gross_exposure: Decimal = Field(
        default=Decimal("1.0"),
        gt=0,
        le=1,
        description="Gross exposure / equity ceiling. 1.0 = no leverage, as mandated.",
    )
    max_net_exposure: Decimal = Field(default=Decimal("1.0"), gt=0, le=1)
    max_open_positions: int = Field(default=12, gt=0)
    max_positions_per_currency: int | None = Field(default=None, gt=0)
    max_currency_exposure: Decimal = Field(
        default=Decimal("0.70"),
        gt=0,
        le=1,
        description=(
            "Cap on non-base-currency exposure. Unhedged USD exposure is an "
            "uncompensated risk for a EUR investor and can easily dominate a "
            "30 bps/trade edge."
        ),
    )
    max_sector_weight: Decimal | None = Field(default=Decimal("0.40"), gt=0, le=1)
    max_new_positions_per_day: int = Field(default=6, gt=0)


class LossLimits(BaseModel):
    """Loss limits and kill-switch thresholds."""

    model_config = {"extra": "forbid"}

    max_daily_loss_pct: Decimal = Field(
        default=Decimal("0.03"),
        gt=0,
        le=1,
        description="Halt new risk for the session at this loss vs start-of-day equity.",
    )
    max_drawdown_pct: Decimal = Field(
        default=Decimal("0.15"),
        gt=0,
        le=1,
        description=(
            "Hard kill switch against peak equity. Requires manual re-arming: an "
            "automated system that resumes on its own after a 15% drawdown has no "
            "kill switch, only a pause button."
        ),
    )
    max_consecutive_losing_days: int = Field(default=8, gt=0)
    max_strategy_drawdown_pct: Decimal = Field(
        default=Decimal("0.25"),
        gt=0,
        le=1,
        description="Per-strategy drawdown against its own high-water mark.",
    )


class OperationalLimits(BaseModel):
    """Guards against the failure modes that are operational, not market-driven.

    These catch the bugs that actually lose money in live automated trading:
    an order loop firing 400 orders a second, a stale data feed making decisions
    on yesterday's prices, or a restart resubmitting orders already working.
    """

    model_config = {"extra": "forbid"}

    max_orders_per_minute: int = Field(default=20, gt=0)
    max_orders_per_day: int = Field(default=100, gt=0)
    max_stale_data_seconds: int = Field(
        default=300,
        gt=0,
        description=(
            "Reject orders priced off data older than this. A frozen feed is more "
            "dangerous than no feed, because it looks healthy."
        ),
    )
    max_limit_price_deviation: Decimal = Field(
        default=Decimal("0.10"),
        gt=0,
        description="Fat-finger guard: reject limit prices this far from last traded.",
    )
    reject_duplicate_orders: bool = True
    duplicate_window_seconds: int = Field(default=60, gt=0)
    require_market_open: bool = Field(
        default=True,
        description=(
            "Reject orders outside regular trading hours. Pre/post-market spreads "
            "are multiples of RTH spreads, which invalidates the cost model the "
            "strategy was validated under."
        ),
    )


class RiskLimits(BaseModel):
    """Complete risk configuration."""

    model_config = {"extra": "forbid"}

    position: PositionLimits = Field(default_factory=PositionLimits)
    portfolio: PortfolioLimits = Field(default_factory=PortfolioLimits)
    loss: LossLimits = Field(default_factory=LossLimits)
    operational: OperationalLimits = Field(default_factory=OperationalLimits)
    symbol_whitelist: list[str] | None = Field(
        default=None,
        description="If set, only these symbols may be traded. None = allow any.",
    )
    symbol_blacklist: list[str] = Field(default_factory=list)
    strategy_budgets: dict[str, Decimal] = Field(
        default_factory=dict,
        description=(
            "Per-strategy gross exposure budget as a fraction of equity. Budgets "
            "are how multiple strategies coexist without one crowding out the rest."
        ),
    )

    @field_validator("symbol_whitelist", "symbol_blacklist", mode="before")
    @classmethod
    def _upper(cls, v):
        if v is None:
            return None
        return [str(s).upper() for s in v]

    @field_validator("strategy_budgets")
    @classmethod
    def _check_budgets(cls, v: dict[str, Decimal]) -> dict[str, Decimal]:
        for name, budget in v.items():
            if budget <= 0 or budget > 1:
                raise ValueError(
                    f"strategy_budgets[{name}] = {budget} must be in (0, 1] as a "
                    "fraction of equity"
                )
        return v

    @model_validator(mode="after")
    def _cross_checks(self) -> RiskLimits:
        total_budget = sum(self.strategy_budgets.values(), Decimal("0"))
        if total_budget > self.portfolio.max_gross_exposure:
            raise ValueError(
                f"strategy budgets sum to {total_budget} but max_gross_exposure is "
                f"{self.portfolio.max_gross_exposure}. Over-allocating guarantees that "
                "whichever strategy trades first consumes the shared capacity, making "
                "realised allocation depend on arrival order rather than on intent."
            )
        if self.position.max_position_weight > self.portfolio.max_gross_exposure:
            raise ValueError(
                f"max_position_weight {self.position.max_position_weight} exceeds "
                f"max_gross_exposure {self.portfolio.max_gross_exposure}"
            )
        if self.loss.max_daily_loss_pct >= self.loss.max_drawdown_pct:
            raise ValueError(
                f"max_daily_loss_pct {self.loss.max_daily_loss_pct} >= max_drawdown_pct "
                f"{self.loss.max_drawdown_pct}: the daily limit would never bind before "
                "the hard kill switch, making the softer control useless"
            )
        if self.symbol_whitelist is not None:
            overlap = set(self.symbol_whitelist) & set(self.symbol_blacklist)
            if overlap:
                raise ValueError(f"symbols both whitelisted and blacklisted: {sorted(overlap)}")
        return self

    def budget_for(self, strategy_id: str) -> Decimal | None:
        return self.strategy_budgets.get(strategy_id)
