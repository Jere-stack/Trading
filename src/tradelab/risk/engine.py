"""Pre-trade risk engine.

Every order in the system passes through `RiskEngine.evaluate` before reaching a
broker -- in backtest, paper and live alike. That uniformity is deliberate: a
risk gate only tested in production is untested, and a gate that runs only in
live mode means the backtest measures a strategy that could never have traded.

Two properties are non-negotiable:

1. **Fail-closed.** A check that raises an exception is treated as a REJECT, not
   skipped. The common failure is a missing FX rate or absent market data; the
   safe response to "I don't know" is "no".

2. **Resize is a floor, not a negotiation.** When several checks want to shrink
   an order, the smallest wins, and if the survivor falls below the minimum
   viable size the order is rejected outright rather than sent as a stub that
   pays full commission for a token position.

Ordering matters for cost, not correctness: cheap checks (halt state, mandate,
whitelist) run before ones needing valuation or cost modelling, so the common
rejection path is fast.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from tradelab.core.enums import AssetClass, RiskDecision
from tradelab.core.money import ZERO, round_to_lot, safe_div
from tradelab.core.types import Instrument, OrderRequest, Quote
from tradelab.costs.commission import CommissionModel
from tradelab.costs.slippage import SlippageModel
from tradelab.portfolio.portfolio import Portfolio
from tradelab.risk.killswitch import HaltLevel, RiskState
from tradelab.risk.limits import RiskLimits


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    decision: RiskDecision
    check: str
    reason: str = ""
    quantity: Decimal | None = None

    @classmethod
    def approve(cls, check: str) -> RiskVerdict:
        return cls(RiskDecision.APPROVE, check)

    @classmethod
    def reject(cls, check: str, reason: str) -> RiskVerdict:
        return cls(RiskDecision.REJECT, check, reason)

    @classmethod
    def resize(cls, check: str, quantity: Decimal, reason: str) -> RiskVerdict:
        return cls(RiskDecision.RESIZE, check, reason, quantity)


@dataclass(frozen=True, slots=True)
class RiskResult:
    """Aggregated outcome. `approved_request` is None unless the order may go."""

    decision: RiskDecision
    approved_request: OrderRequest | None
    verdicts: list[RiskVerdict]
    original_quantity: Decimal

    @property
    def is_approved(self) -> bool:
        return self.decision in (RiskDecision.APPROVE, RiskDecision.RESIZE)

    @property
    def rejections(self) -> list[RiskVerdict]:
        return [v for v in self.verdicts if v.decision is RiskDecision.REJECT]

    @property
    def reason(self) -> str:
        return "; ".join(f"{v.check}: {v.reason}" for v in self.rejections)


@dataclass
class RiskContext:
    """Everything the checks need, assembled once per evaluation."""

    portfolio: Portfolio
    state: RiskState
    limits: RiskLimits
    now: datetime
    reference_prices: dict[str, Decimal] = field(default_factory=dict)
    quotes: dict[str, Quote] = field(default_factory=dict)
    price_timestamps: dict[str, datetime] = field(default_factory=dict)
    commission_model: CommissionModel | None = None
    slippage_model: SlippageModel | None = None
    market_open: bool = True
    sectors: dict[str, str] = field(default_factory=dict)

    def reference_price(self, instrument: Instrument) -> Decimal | None:
        quote = self.quotes.get(instrument.key)
        if quote is not None and not quote.is_crossed:
            return quote.mid
        return self.reference_prices.get(instrument.key)

    def is_reducing(self, request: OrderRequest) -> bool:
        """True when the order moves the position towards flat.

        Risk-reducing orders are exempted from most sizing limits. Without this
        exemption, a book that has breached a limit becomes un-exitable -- the
        gate would block the very orders that restore compliance.
        """
        current = self.portfolio.quantity_of(request.instrument)
        if current == 0:
            return False
        delta = request.signed_quantity
        return (current > 0 and delta < 0) or (current < 0 and delta > 0)


class RiskCheck(ABC):
    name: str = "unnamed"

    @abstractmethod
    def evaluate(self, request: OrderRequest, ctx: RiskContext) -> RiskVerdict: ...


# --------------------------------------------------------------------- checks


class HaltCheck(RiskCheck):
    name = "halt"

    def evaluate(self, request, ctx):
        ks = ctx.state.kill_switch
        if not ks.is_halted:
            return RiskVerdict.approve(self.name)
        if ks.blocks_closing:
            return RiskVerdict.reject(
                self.name,
                f"HARD halt active ({ks.current.reason if ks.current else 'unknown'}); "
                "manual re-arm required",
            )
        if ctx.is_reducing(request):
            return RiskVerdict.approve(self.name)
        return RiskVerdict.reject(
            self.name,
            f"SOFT halt active ({ks.current.reason if ks.current else 'unknown'}); "
            "risk-reducing orders only",
        )


class MandateCheck(RiskCheck):
    """Enforces the stocks-only, long-only-by-default mandate structurally."""

    name = "mandate"

    def evaluate(self, request, ctx):
        inst = request.instrument
        if inst.asset_class not in (AssetClass.EQUITY, AssetClass.ETF):
            return RiskVerdict.reject(
                self.name, f"{inst.asset_class} is outside the stocks-only mandate"
            )
        if not ctx.limits.position.allow_short:
            current = ctx.portfolio.quantity_of(inst)
            resulting = current + request.signed_quantity
            if resulting < 0:
                if current <= 0:
                    return RiskVerdict.reject(
                        self.name, f"shorting disabled; order would result in {resulting} shares"
                    )
                # Allow the closing portion, drop the short overshoot.
                return RiskVerdict.resize(
                    self.name,
                    abs(current),
                    f"clamped to flat: full size would leave a {resulting} short position",
                )
        return RiskVerdict.approve(self.name)


class SymbolPermissionCheck(RiskCheck):
    name = "symbol_permission"

    def evaluate(self, request, ctx):
        symbol = request.instrument.symbol.upper()
        if symbol in ctx.limits.symbol_blacklist:
            return RiskVerdict.reject(self.name, f"{symbol} is blacklisted")
        wl = ctx.limits.symbol_whitelist
        if wl is not None and symbol not in wl:
            return RiskVerdict.reject(self.name, f"{symbol} is not in the whitelist")
        return RiskVerdict.approve(self.name)


class MarketHoursCheck(RiskCheck):
    name = "market_hours"

    def evaluate(self, request, ctx):
        if not ctx.limits.operational.require_market_open and not ctx.market_open:
            return RiskVerdict.approve(self.name)
        if ctx.market_open:
            return RiskVerdict.approve(self.name)
        return RiskVerdict.reject(
            self.name,
            "market closed; outside-RTH spreads invalidate the cost model the "
            "strategy was validated under",
        )


class PriceSanityCheck(RiskCheck):
    """Fat-finger and stale-data guard."""

    name = "price_sanity"

    def evaluate(self, request, ctx):
        inst = request.instrument
        ref = ctx.reference_price(inst)
        if ref is None:
            return RiskVerdict.reject(
                self.name, f"no reference price for {inst.key}; refusing to price blind"
            )
        if ref <= 0:
            return RiskVerdict.reject(self.name, f"non-positive reference price {ref}")

        limits = ctx.limits
        if ref < limits.position.min_price:
            return RiskVerdict.reject(
                self.name, f"price {ref} below min_price {limits.position.min_price}"
            )
        if limits.position.max_price is not None and ref > limits.position.max_price:
            return RiskVerdict.reject(
                self.name, f"price {ref} above max_price {limits.position.max_price}"
            )

        stamp = ctx.price_timestamps.get(inst.key)
        if stamp is not None:
            age = (ctx.now - stamp).total_seconds()
            if age > limits.operational.max_stale_data_seconds:
                return RiskVerdict.reject(
                    self.name,
                    f"reference price is {age:.0f}s stale (limit "
                    f"{limits.operational.max_stale_data_seconds}s); a frozen feed looks "
                    "healthy but prices the past",
                )

        if request.limit_price is not None:
            deviation = abs(safe_div(request.limit_price - ref, ref))
            if deviation > limits.operational.max_limit_price_deviation:
                return RiskVerdict.reject(
                    self.name,
                    f"limit {request.limit_price} deviates {deviation:.1%} from reference "
                    f"{ref}, above {limits.operational.max_limit_price_deviation:.1%}",
                )
        return RiskVerdict.approve(self.name)


class OrderRateCheck(RiskCheck):
    """Bounds runaway order loops -- the classic way automated systems lose money."""

    name = "order_rate"

    def evaluate(self, request, ctx):
        op = ctx.limits.operational
        if ctx.state.orders_today >= op.max_orders_per_day:
            return RiskVerdict.reject(
                self.name,
                f"daily order cap reached ({ctx.state.orders_today}/{op.max_orders_per_day})",
            )
        in_window = ctx.state.orders_in_window(ctx.now, timedelta(minutes=1))
        if in_window >= op.max_orders_per_minute:
            return RiskVerdict.reject(
                self.name, f"rate cap reached ({in_window}/{op.max_orders_per_minute} per minute)"
            )
        return RiskVerdict.approve(self.name)


class DuplicateOrderCheck(RiskCheck):
    """Blocks accidental resubmission, e.g. after a restart or a double signal."""

    name = "duplicate"

    def evaluate(self, request, ctx):
        op = ctx.limits.operational
        if not op.reject_duplicate_orders:
            return RiskVerdict.approve(self.name)
        key = dedupe_key(request)
        if ctx.state.seen_recently(key, ctx.now, timedelta(seconds=op.duplicate_window_seconds)):
            return RiskVerdict.reject(
                self.name,
                f"identical order seen within {op.duplicate_window_seconds}s "
                f"(key={key}); suspected duplicate submission",
            )
        return RiskVerdict.approve(self.name)


class LossLimitCheck(RiskCheck):
    """Trips the kill switch on daily-loss and drawdown breaches."""

    name = "loss_limits"

    def evaluate(self, request, ctx):
        equity = ctx.portfolio.equity
        state, loss = ctx.state, ctx.limits.loss
        if equity <= 0:
            state.kill_switch.trip(HaltLevel.HARD, f"equity is {equity}", ctx.now, self.name)
            return RiskVerdict.reject(self.name, f"equity is {equity}")

        drawdown = state.drawdown_pct(equity)
        if drawdown >= loss.max_drawdown_pct:
            state.kill_switch.trip(
                HaltLevel.HARD,
                f"drawdown {drawdown:.2%} >= limit {loss.max_drawdown_pct:.2%} "
                f"(peak {state.peak_equity}, now {equity})",
                ctx.now,
                self.name,
            )
            return RiskVerdict.reject(
                self.name,
                f"HARD kill switch: drawdown {drawdown:.2%} >= {loss.max_drawdown_pct:.2%}",
            )

        if state.day_start_equity > 0:
            daily = state.daily_pnl_pct(equity)
            if daily <= -loss.max_daily_loss_pct:
                state.kill_switch.trip(
                    HaltLevel.SOFT,
                    f"daily loss {daily:.2%} breached limit {loss.max_daily_loss_pct:.2%}",
                    ctx.now,
                    self.name,
                )
                if not ctx.is_reducing(request):
                    return RiskVerdict.reject(
                        self.name,
                        f"daily loss {daily:.2%} breached {loss.max_daily_loss_pct:.2%}; "
                        "risk-reducing orders only",
                    )

        if state.consecutive_losing_days >= loss.max_consecutive_losing_days:
            state.kill_switch.trip(
                HaltLevel.SOFT,
                f"{state.consecutive_losing_days} consecutive losing days",
                ctx.now,
                self.name,
            )
            if not ctx.is_reducing(request):
                return RiskVerdict.reject(
                    self.name,
                    f"{state.consecutive_losing_days} consecutive losing days >= "
                    f"{loss.max_consecutive_losing_days}",
                )
        return RiskVerdict.approve(self.name)


class PositionSizeCheck(RiskCheck):
    """Caps position weight and absolute notional, resizing where possible."""

    name = "position_size"

    def evaluate(self, request, ctx):
        if ctx.is_reducing(request):
            return RiskVerdict.approve(self.name)
        inst = request.instrument
        ref = ctx.reference_price(inst)
        if ref is None or ref <= 0:
            return RiskVerdict.reject(self.name, "no usable reference price for sizing")
        equity = ctx.portfolio.equity
        if equity <= 0:
            return RiskVerdict.reject(self.name, f"non-positive equity {equity}")

        pos_limits = ctx.limits.position
        rate = ctx.portfolio.fx_rate(inst.currency)
        existing = abs(ctx.portfolio.quantity_of(inst))

        cap_base = equity * pos_limits.max_position_weight
        if pos_limits.max_position_notional is not None:
            cap_base = min(cap_base, pos_limits.max_position_notional)
        # Convert the base-currency cap into a share count in local currency.
        cap_shares = safe_div(cap_base, ref * rate * inst.multiplier)
        allowed = round_to_lot(max(ZERO, cap_shares - existing), inst.lot_size)

        if allowed <= 0:
            return RiskVerdict.reject(
                self.name,
                f"position already at the {pos_limits.max_position_weight:.1%} weight cap "
                f"({existing} shares held)",
            )
        if allowed < request.quantity:
            return RiskVerdict.resize(
                self.name,
                allowed,
                f"capped at {pos_limits.max_position_weight:.1%} of equity "
                f"({request.quantity} -> {allowed} shares)",
            )
        return RiskVerdict.approve(self.name)


class CapacityCheck(RiskCheck):
    """Bounds participation in average daily volume -- and thus the exit."""

    name = "capacity"

    def evaluate(self, request, ctx):
        inst = request.instrument
        if inst.adv is None or inst.adv <= 0:
            if ctx.is_reducing(request):
                return RiskVerdict.approve(self.name)
            return RiskVerdict.reject(
                self.name,
                f"{inst.symbol} has no ADV; capacity is unknowable and an unexitable "
                "position is not a position",
            )
        cap = inst.adv * ctx.limits.position.max_participation_of_adv
        allowed = round_to_lot(cap, inst.lot_size)
        if request.quantity <= allowed:
            return RiskVerdict.approve(self.name)
        if ctx.is_reducing(request):
            # Never block an exit on capacity; flag it instead. The position
            # should not have been opened this large in the first place.
            return RiskVerdict.approve(self.name)
        if allowed <= 0:
            return RiskVerdict.reject(
                self.name, f"ADV {inst.adv} too small for any compliant order size"
            )
        return RiskVerdict.resize(
            self.name,
            allowed,
            f"capped at {ctx.limits.position.max_participation_of_adv:.2%} of ADV "
            f"({request.quantity} -> {allowed} shares)",
        )


class CostEfficiencyCheck(RiskCheck):
    """Rejects orders whose modelled cost eats the plausible edge.

    This is the check that matters most at EUR 10k and the one retail systems
    almost never have. It is applied only to risk-increasing orders: an exit
    must always be permitted, however expensive, because refusing to sell is
    not a cost control.
    """

    name = "cost_efficiency"

    def evaluate(self, request, ctx):
        if ctx.is_reducing(request):
            return RiskVerdict.approve(self.name)
        inst = request.instrument
        ref = ctx.reference_price(inst)
        if ref is None or ref <= 0:
            return RiskVerdict.reject(self.name, "no reference price for cost estimation")

        rate = ctx.portfolio.fx_rate(inst.currency)
        notional_local = request.quantity * ref * inst.multiplier
        notional_base = notional_local * rate
        pos_limits = ctx.limits.position

        if notional_base < pos_limits.min_order_notional:
            return RiskVerdict.reject(
                self.name,
                f"notional {notional_base:.2f} below min_order_notional "
                f"{pos_limits.min_order_notional}; commission alone would exceed any "
                "plausible per-trade edge",
            )

        if ctx.commission_model is None:
            return RiskVerdict.approve(self.name)

        commission = ctx.commission_model.total(inst, request.side, request.quantity, ref)
        cost_local = commission
        if ctx.slippage_model is not None:
            est = ctx.slippage_model.estimate(
                inst,
                request.side,
                request.quantity,
                ref,
                order_type=request.order_type,
                quote=ctx.quotes.get(inst.key),
            )
            cost_local += (est.spread_cost_per_share + est.impact_cost_per_share) * request.quantity

        cost_bps = safe_div(cost_local, notional_local) * Decimal("10000")
        if cost_bps > pos_limits.max_cost_bps_of_notional:
            return RiskVerdict.reject(
                self.name,
                f"modelled one-way cost {cost_bps:.1f} bps exceeds "
                f"{pos_limits.max_cost_bps_of_notional} bps of notional "
                f"(commission {commission:.2f} on {notional_local:.2f})",
            )
        return RiskVerdict.approve(self.name)


class PortfolioExposureCheck(RiskCheck):
    """Caps gross exposure, position count, and per-currency concentration."""

    name = "portfolio_exposure"

    def evaluate(self, request, ctx):
        if ctx.is_reducing(request):
            return RiskVerdict.approve(self.name)
        pf, limits = ctx.portfolio, ctx.limits.portfolio
        inst = request.instrument
        ref = ctx.reference_price(inst)
        if ref is None or ref <= 0:
            return RiskVerdict.reject(self.name, "no reference price for exposure check")
        equity = pf.equity
        if equity <= 0:
            return RiskVerdict.reject(self.name, f"non-positive equity {equity}")

        is_new = pf.quantity_of(inst) == 0
        if is_new:
            if len(pf.open_positions) >= limits.max_open_positions:
                return RiskVerdict.reject(
                    self.name,
                    f"already holding {len(pf.open_positions)} positions "
                    f"(max {limits.max_open_positions})",
                )
            if ctx.state.new_positions_today >= limits.max_new_positions_per_day:
                return RiskVerdict.reject(
                    self.name,
                    f"opened {ctx.state.new_positions_today} new positions today "
                    f"(max {limits.max_new_positions_per_day})",
                )

        rate = pf.fx_rate(inst.currency)
        per_share_base = ref * rate * inst.multiplier
        headroom_base = equity * limits.max_gross_exposure - pf.gross_exposure
        if headroom_base <= 0:
            return RiskVerdict.reject(
                self.name,
                f"gross exposure {pf.gross_exposure} already at the "
                f"{limits.max_gross_exposure}x ceiling on equity {equity}",
            )
        allowed = round_to_lot(safe_div(headroom_base, per_share_base), inst.lot_size)

        ccy = inst.currency.upper()
        if ccy != pf.base_currency:
            exposures = pf.exposure_by_currency()
            current_ccy = abs(exposures.get(ccy, ZERO))
            ccy_headroom = equity * limits.max_currency_exposure - current_ccy
            if ccy_headroom <= 0:
                return RiskVerdict.reject(
                    self.name,
                    f"{ccy} exposure {current_ccy} at the "
                    f"{limits.max_currency_exposure:.0%} cap; unhedged FX risk is "
                    "uncompensated for a base-currency investor",
                )
            allowed = min(
                allowed, round_to_lot(safe_div(ccy_headroom, per_share_base), inst.lot_size)
            )

        if allowed <= 0:
            return RiskVerdict.reject(self.name, "no exposure headroom for any compliant size")
        if allowed < request.quantity:
            return RiskVerdict.resize(
                self.name, allowed, f"exposure headroom caps size at {allowed} shares"
            )
        return RiskVerdict.approve(self.name)


class StrategyBudgetCheck(RiskCheck):
    """Enforces per-strategy gross exposure budgets.

    Without this, the first strategy to fire consumes shared capacity and the
    realised allocation depends on arrival order rather than intent.
    """

    name = "strategy_budget"

    def evaluate(self, request, ctx):
        if ctx.is_reducing(request):
            return RiskVerdict.approve(self.name)
        budget = ctx.limits.budget_for(request.strategy_id)
        if budget is None:
            return RiskVerdict.approve(self.name)
        pf = ctx.portfolio
        equity = pf.equity
        if equity <= 0:
            return RiskVerdict.reject(self.name, f"non-positive equity {equity}")
        inst = request.instrument
        ref = ctx.reference_price(inst)
        if ref is None or ref <= 0:
            return RiskVerdict.reject(self.name, "no reference price for budget check")
        used = pf.exposure_by_strategy().get(request.strategy_id, ZERO)
        headroom = equity * budget - used
        if headroom <= 0:
            return RiskVerdict.reject(
                self.name,
                f"strategy {request.strategy_id} has used its {budget:.0%} budget "
                f"({used} of {equity * budget})",
            )
        per_share_base = ref * pf.fx_rate(inst.currency) * inst.multiplier
        allowed = round_to_lot(safe_div(headroom, per_share_base), inst.lot_size)
        if allowed <= 0:
            return RiskVerdict.reject(self.name, "strategy budget headroom below one lot")
        if allowed < request.quantity:
            return RiskVerdict.resize(
                self.name,
                allowed,
                f"strategy {request.strategy_id} budget caps size at {allowed} shares",
            )
        return RiskVerdict.approve(self.name)


def dedupe_key(request: OrderRequest) -> str:
    return (
        f"{request.strategy_id}|{request.instrument.key}|{request.side}|"
        f"{request.quantity}|{request.order_type}|{request.limit_price}"
    )


DEFAULT_CHECKS: tuple[type[RiskCheck], ...] = (
    HaltCheck,
    MandateCheck,
    SymbolPermissionCheck,
    MarketHoursCheck,
    OrderRateCheck,
    DuplicateOrderCheck,
    PriceSanityCheck,
    LossLimitCheck,
    PositionSizeCheck,
    CapacityCheck,
    PortfolioExposureCheck,
    StrategyBudgetCheck,
    CostEfficiencyCheck,
)


class RiskEngine:
    """The single gate every order passes through."""

    def __init__(
        self,
        limits: RiskLimits | None = None,
        checks: list[RiskCheck] | None = None,
    ) -> None:
        self.limits = limits or RiskLimits()
        self.checks: list[RiskCheck] = checks or [cls() for cls in DEFAULT_CHECKS]

    def evaluate(self, request: OrderRequest, ctx: RiskContext) -> RiskResult:
        verdicts: list[RiskVerdict] = []
        smallest: Decimal | None = None
        rejected = False

        for check in self.checks:
            try:
                verdict = check.evaluate(request, ctx)
            except Exception as exc:
                verdict = RiskVerdict.reject(
                    getattr(check, "name", check.__class__.__name__),
                    f"check raised {type(exc).__name__}: {exc}. Failing closed: an "
                    "unevaluable limit is treated as a breached limit.",
                )
            verdicts.append(verdict)
            if verdict.decision is RiskDecision.REJECT:
                rejected = True
            elif verdict.decision is RiskDecision.RESIZE and verdict.quantity is not None:
                smallest = verdict.quantity if smallest is None else min(smallest, verdict.quantity)

        if rejected:
            return RiskResult(RiskDecision.REJECT, None, verdicts, request.quantity)

        if smallest is None:
            return RiskResult(RiskDecision.APPROVE, request, verdicts, request.quantity)

        lot = round_to_lot(smallest, request.instrument.lot_size)
        if lot <= 0:
            verdicts.append(
                RiskVerdict.reject("resize", f"resized quantity {smallest} rounds to zero lots")
            )
            return RiskResult(RiskDecision.REJECT, None, verdicts, request.quantity)

        resized = request.with_quantity(lot)
        # Re-run the cost gate on the resized order: a resize can push a
        # previously economic order below the cost-efficiency floor, and
        # approving a stub that pays full commission defeats the purpose.
        recheck = CostEfficiencyCheck().evaluate(resized, ctx)
        if recheck.decision is RiskDecision.REJECT:
            verdicts.append(
                RiskVerdict.reject(
                    "cost_efficiency_post_resize",
                    f"after resize to {lot} shares: {recheck.reason}",
                )
            )
            return RiskResult(RiskDecision.REJECT, None, verdicts, request.quantity)

        return RiskResult(RiskDecision.RESIZE, resized, verdicts, request.quantity)
