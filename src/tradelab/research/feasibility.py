"""Pre-data feasibility screening.

The cheapest way to reject a strategy is arithmetic, before any data is
downloaded or any backtest is run. Two constraints kill most candidates, and
they can both be evaluated from a hypothesis statement alone:

**1. Cost feasibility.** Turnover times round-trip cost is a hard annual drag.
A strategy trading daily at 25 bps round trip needs 63% gross annual return to
break even. No amount of backtesting rescues that, and discovering it after two
weeks of data engineering is a waste.

**2. Statistical power.** A strategy with 30 independent events per year cannot
be distinguished from noise within a decade. This constraint is routinely
ignored, and it is why event-driven strategies with compelling economic stories
so often fail to be *validatable* even when they might be real.

**3. Fixed cost feasibility.** A data subscription is charged per year, not per
trade, so on a small account it behaves like a large fixed drag. EUR 199/year of
end-of-day data is **1.99% of a EUR 10,000 account annually** -- comparable to
or larger than the per-trade cost drag of a low-turnover strategy, and it is
paid whether or not the strategy trades. Per-trade cost models miss this
entirely, which is how a strategy that looks marginally profitable becomes a
guaranteed loss once the tooling is paid for.

These constraints are in direct tension, which is the central bind of
small-account quant research: **effects that are uncrowded are usually rare, and
rare effects cannot be validated with the data available.** A strategy must
thread all three, and most do not. Screening here makes that explicit rather
than letting it emerge after weeks of work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from scipy import stats


class Feasibility(StrEnum):
    VIABLE = "VIABLE"
    MARGINAL = "MARGINAL"
    COST_INFEASIBLE = "COST_INFEASIBLE"
    POWER_INFEASIBLE = "POWER_INFEASIBLE"
    INFEASIBLE_BOTH = "INFEASIBLE_BOTH"


@dataclass(frozen=True)
class EconomicsResult:
    """Expected annual economics in currency terms, after all costs.

    Converts a per-event edge into what the account actually earns, which is
    the only number that can be compared against a subscription price. Two
    effects that per-trade cost models miss are applied here:

    * **Capacity truncation.** A strategy cannot capture more events than it
      has position slots and time for. 100 signals a year with 10 slots held
      20 days each is capped at 126 -- but with 10 slots held 120 days it is
      capped at 21, and the other 79 signals are unreachable.
    * **Fixed costs.** Subscriptions are paid whether or not the strategy
      trades, so they do not scale down with a small account. They scale *up*
      as a percentage of it.
    """

    events_available: float
    events_captured: float
    capacity_limited: bool
    position_weight: float
    net_edge_bps: float
    gross_annual_return_pct: float
    trading_cost_drag_pct: float
    fixed_cost_drag_pct: float
    net_annual_return_pct: float
    net_annual_currency: float
    fixed_annual_cost: float
    account_equity: float

    @property
    def fixed_cost_share_of_gross(self) -> float:
        """Fraction of gross profit consumed by fixed costs."""
        if self.gross_annual_return_pct <= 0:
            return float("inf")
        return self.fixed_cost_drag_pct / self.gross_annual_return_pct

    @property
    def is_viable(self) -> bool:
        return self.net_annual_return_pct > 0

    def summary(self) -> str:
        cap = " (capacity-limited)" if self.capacity_limited else ""
        return (
            f"{self.events_captured:.0f}/{self.events_available:.0f} events{cap}, "
            f"net {self.net_edge_bps:.0f} bps each -> "
            f"gross {self.gross_annual_return_pct:+.2f}%/yr, "
            f"data {self.fixed_cost_drag_pct:.2f}%/yr, "
            f"net {self.net_annual_return_pct:+.2f}%/yr "
            f"(EUR {self.net_annual_currency:+,.0f})"
        )


def annual_economics(
    events_per_year: float,
    gross_edge_bps: float,
    holding_days: float,
    *,
    positions_held: int = 10,
    position_weight: float | None = None,
    round_trip_cost_bps: float = 30.0,
    fixed_annual_cost: float = 0.0,
    account_equity: float = 10_000.0,
    trading_days_per_year: int = 252,
) -> EconomicsResult:
    """Expected annual return in currency, after trading and fixed costs.

    This is the number to compare against a data subscription price. A strategy
    whose entire expected profit is consumed by tooling is not a strategy, and
    the per-trade cost view alone will not show that.
    """
    if holding_days <= 0:
        raise ValueError("holding_days must be positive")
    if positions_held < 1:
        raise ValueError("positions_held must be at least 1")
    if account_equity <= 0:
        raise ValueError("account_equity must be positive")

    if position_weight is None:
        position_weight = 1.0 / positions_held

    # A strategy cannot trade more events than it has slots and time for.
    max_capturable = positions_held * (trading_days_per_year / holding_days)
    events_captured = min(events_per_year, max_capturable)

    net_edge = gross_edge_bps - round_trip_cost_bps
    gross_pct = events_captured * gross_edge_bps * position_weight / 100.0
    trading_drag_pct = events_captured * round_trip_cost_bps * position_weight / 100.0
    fixed_drag_pct = fixed_annual_cost / account_equity * 100.0
    net_pct = gross_pct - trading_drag_pct - fixed_drag_pct

    return EconomicsResult(
        events_available=events_per_year,
        events_captured=events_captured,
        capacity_limited=events_captured < events_per_year,
        position_weight=position_weight,
        net_edge_bps=net_edge,
        gross_annual_return_pct=gross_pct,
        trading_cost_drag_pct=trading_drag_pct,
        fixed_cost_drag_pct=fixed_drag_pct,
        net_annual_return_pct=net_pct,
        net_annual_currency=net_pct / 100.0 * account_equity,
        fixed_annual_cost=fixed_annual_cost,
        account_equity=account_equity,
    )


def breakeven_account_size(
    events_per_year: float,
    gross_edge_bps: float,
    holding_days: float,
    *,
    positions_held: int = 10,
    round_trip_cost_bps: float = 30.0,
    fixed_annual_cost: float = 199.0,
    trading_days_per_year: int = 252,
) -> float:
    """Smallest account at which a fixed annual cost is worth paying.

    Because the trading edge scales with equity while a subscription does not,
    there is a hard threshold below which paying for data destroys value no
    matter how good the strategy is. Returns infinity when the strategy has no
    positive edge at any size.
    """
    reference = annual_economics(
        events_per_year,
        gross_edge_bps,
        holding_days,
        positions_held=positions_held,
        round_trip_cost_bps=round_trip_cost_bps,
        fixed_annual_cost=0.0,
        account_equity=10_000.0,
        trading_days_per_year=trading_days_per_year,
    )
    edge_fraction = reference.net_annual_return_pct / 100.0
    if edge_fraction <= 0:
        return float("inf")
    return fixed_annual_cost / edge_fraction


@dataclass(frozen=True)
class CostFeasibility:
    holding_days: float
    round_trips_per_year: float
    round_trip_cost_bps: float
    annual_cost_drag_pct: float
    required_gross_edge_bps: float
    expected_gross_edge_bps: float
    net_edge_bps: float
    edge_to_cost_ratio: float

    @property
    def is_viable(self) -> bool:
        """Requires the gross edge to be at least twice the cost.

        A 2x margin, not 1.1x, because the cost model is itself uncertain and
        because a live edge is almost always smaller than a backtested one. A
        strategy that only works if costs come in exactly as modelled is a
        strategy that does not work.
        """
        return self.edge_to_cost_ratio >= 2.0

    def summary(self) -> str:
        return (
            f"hold {self.holding_days:.0f}d -> {self.round_trips_per_year:.0f} round trips/yr, "
            f"cost drag {self.annual_cost_drag_pct:.1f}%/yr, "
            f"need >{self.required_gross_edge_bps:.0f} bps/trade, "
            f"expect {self.expected_gross_edge_bps:.0f} bps "
            f"(ratio {self.edge_to_cost_ratio:.2f})"
        )


def cost_feasibility(
    holding_days: float,
    expected_gross_edge_bps: float,
    round_trip_cost_bps: float = 30.0,
    positions_held: int = 10,
    trading_days_per_year: int = 252,
) -> CostFeasibility:
    """Can this holding period support this edge at this cost?

    `expected_gross_edge_bps` is the per-round-trip gross return you believe the
    signal delivers, *before* costs. State it from the literature or a prior,
    not from a backtest -- the point is to screen before fitting.
    """
    if holding_days <= 0:
        raise ValueError("holding_days must be positive")
    if round_trip_cost_bps < 0:
        raise ValueError("round_trip_cost_bps cannot be negative")

    # Each position turns over trading_days/holding_days times per year.
    round_trips_per_position = trading_days_per_year / holding_days
    # Portfolio-level turnover: all positions turn over at that rate.
    round_trips_per_year = round_trips_per_position * positions_held
    # Cost drag is per-position turnover times cost, since each position is a
    # fraction 1/positions_held of capital.
    annual_drag_pct = round_trips_per_position * round_trip_cost_bps / 100.0

    net = expected_gross_edge_bps - round_trip_cost_bps
    ratio = expected_gross_edge_bps / round_trip_cost_bps if round_trip_cost_bps > 0 else math.inf

    return CostFeasibility(
        holding_days=holding_days,
        round_trips_per_year=round_trips_per_year,
        round_trip_cost_bps=round_trip_cost_bps,
        annual_cost_drag_pct=annual_drag_pct,
        required_gross_edge_bps=round_trip_cost_bps * 2.0,
        expected_gross_edge_bps=expected_gross_edge_bps,
        net_edge_bps=net,
        edge_to_cost_ratio=ratio,
    )


@dataclass(frozen=True)
class PowerAnalysis:
    events_per_year: float
    effect_size_bps: float
    noise_bps: float
    events_needed: int
    years_needed: float
    power_at_available: float
    available_events: int

    @property
    def is_viable(self) -> bool:
        """Viable when the required sample fits in ~10 years of history.

        Ten years is roughly the limit of usable free equity data, and beyond
        that regime change makes the oldest data a poor guide anyway.
        """
        return self.years_needed <= 10.0

    def summary(self) -> str:
        return (
            f"{self.events_per_year:.0f} events/yr, effect {self.effect_size_bps:.0f} bps "
            f"vs noise {self.noise_bps:.0f} bps -> need {self.events_needed} events "
            f"({self.years_needed:.1f} yrs); power on {self.available_events} "
            f"available events = {self.power_at_available:.0%}"
        )


def power_analysis(
    events_per_year: float,
    effect_size_bps: float,
    noise_bps: float,
    available_years: float = 10.0,
    alpha: float = 0.05,
    target_power: float = 0.80,
) -> PowerAnalysis:
    """How many events are needed to detect `effect_size_bps` against noise?

    Standard one-sided t-test power calculation. `noise_bps` is the per-event
    return standard deviation -- for a daily-horizon equity event this is
    typically 200-400 bps, which is why event studies need large samples: the
    effect is an order of magnitude smaller than the noise.
    """
    if effect_size_bps <= 0:
        raise ValueError("effect_size_bps must be positive")
    if noise_bps <= 0:
        raise ValueError("noise_bps must be positive")
    if events_per_year <= 0:
        raise ValueError("events_per_year must be positive")

    z_alpha = stats.norm.ppf(1 - alpha)
    z_power = stats.norm.ppf(target_power)
    effect_ratio = effect_size_bps / noise_bps
    needed = math.ceil(((z_alpha + z_power) / effect_ratio) ** 2)

    available = int(events_per_year * available_years)
    if available > 0:
        achieved_z = effect_ratio * math.sqrt(available) - z_alpha
        power = float(stats.norm.cdf(achieved_z))
    else:
        power = 0.0

    return PowerAnalysis(
        events_per_year=events_per_year,
        effect_size_bps=effect_size_bps,
        noise_bps=noise_bps,
        events_needed=needed,
        years_needed=needed / events_per_year,
        power_at_available=power,
        available_events=available,
    )


@dataclass(frozen=True)
class ScreenResult:
    name: str
    verdict: Feasibility
    cost: CostFeasibility
    power: PowerAnalysis
    note: str = ""

    def report(self) -> str:
        return (
            f"{self.name}\n"
            f"  verdict : {self.verdict.value}\n"
            f"  cost    : {self.cost.summary()}\n"
            f"  power   : {self.power.summary()}\n"
            + (f"  note    : {self.note}\n" if self.note else "")
        )


def screen(
    name: str,
    holding_days: float,
    expected_gross_edge_bps: float,
    events_per_year: float,
    noise_bps: float = 300.0,
    round_trip_cost_bps: float = 30.0,
    positions_held: int = 10,
    available_years: float = 10.0,
    note: str = "",
) -> ScreenResult:
    """Screen one hypothesis on both constraints before touching any data."""
    cost = cost_feasibility(
        holding_days=holding_days,
        expected_gross_edge_bps=expected_gross_edge_bps,
        round_trip_cost_bps=round_trip_cost_bps,
        positions_held=positions_held,
    )
    power = power_analysis(
        events_per_year=events_per_year,
        effect_size_bps=expected_gross_edge_bps,
        noise_bps=noise_bps,
        available_years=available_years,
    )

    if not cost.is_viable and not power.is_viable:
        verdict = Feasibility.INFEASIBLE_BOTH
    elif not cost.is_viable:
        verdict = Feasibility.COST_INFEASIBLE
    elif not power.is_viable:
        verdict = Feasibility.POWER_INFEASIBLE
    elif cost.edge_to_cost_ratio < 3.0 or power.power_at_available < 0.9:
        verdict = Feasibility.MARGINAL
    else:
        verdict = Feasibility.VIABLE

    return ScreenResult(name=name, verdict=verdict, cost=cost, power=power, note=note)
