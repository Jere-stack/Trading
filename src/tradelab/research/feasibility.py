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

These two constraints are in direct tension, which is the central bind of
small-account quant research: **effects that are uncrowded are usually rare, and
rare effects cannot be validated with the data available.** A strategy must
thread both, and most do not. Screening here makes that explicit rather than
letting it emerge after weeks of work.
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
