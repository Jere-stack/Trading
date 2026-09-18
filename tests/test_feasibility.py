"""Tests for pre-data feasibility screening.

The screen's job is to reject candidates using arithmetic before any data is
acquired, so these tests assert that known-infeasible shapes are rejected and
known-feasible ones are not.
"""

from __future__ import annotations

import pytest

from tradelab.research.feasibility import (
    Feasibility,
    cost_feasibility,
    power_analysis,
    screen,
)


class TestCostFeasibility:
    def test_daily_trading_is_infeasible(self):
        """Pins the headline constraint: daily turnover cannot be afforded."""
        c = cost_feasibility(holding_days=1, expected_gross_edge_bps=10, round_trip_cost_bps=30)
        assert not c.is_viable
        assert c.annual_cost_drag_pct > 50

    def test_quarterly_holding_is_affordable(self):
        c = cost_feasibility(holding_days=63, expected_gross_edge_bps=120, round_trip_cost_bps=30)
        assert c.is_viable
        assert c.annual_cost_drag_pct < 2

    def test_cost_drag_scales_inversely_with_holding_period(self):
        drags = [
            cost_feasibility(holding_days=d, expected_gross_edge_bps=100).annual_cost_drag_pct
            for d in (1, 5, 21, 63, 252)
        ]
        assert drags == sorted(drags, reverse=True)

    def test_requires_two_times_cost_margin(self):
        """A 1.1x margin is not enough: cost models are themselves uncertain."""
        marginal = cost_feasibility(
            holding_days=30, expected_gross_edge_bps=33, round_trip_cost_bps=30
        )
        comfortable = cost_feasibility(
            holding_days=30, expected_gross_edge_bps=60, round_trip_cost_bps=30
        )
        assert not marginal.is_viable
        assert comfortable.is_viable

    def test_rejects_invalid_inputs(self):
        with pytest.raises(ValueError):
            cost_feasibility(holding_days=0, expected_gross_edge_bps=100)
        with pytest.raises(ValueError):
            cost_feasibility(holding_days=10, expected_gross_edge_bps=100, round_trip_cost_bps=-1)


class TestPowerAnalysis:
    def test_smaller_effects_need_more_events(self):
        needed = [
            power_analysis(events_per_year=50, effect_size_bps=e, noise_bps=300).events_needed
            for e in (400, 200, 100, 50)
        ]
        assert needed == sorted(needed)

    def test_rare_events_are_power_infeasible(self):
        """Pins the second constraint: a real mechanism can still be unprovable."""
        p = power_analysis(events_per_year=12, effect_size_bps=25, noise_bps=300)
        assert not p.is_viable
        assert p.years_needed > 10

    def test_frequent_events_with_clear_effect_are_viable(self):
        p = power_analysis(events_per_year=200, effect_size_bps=150, noise_bps=400)
        assert p.is_viable
        assert p.power_at_available > 0.9

    def test_power_rises_with_available_history(self):
        short = power_analysis(
            events_per_year=30, effect_size_bps=100, noise_bps=500, available_years=2
        )
        long = power_analysis(
            events_per_year=30, effect_size_bps=100, noise_bps=500, available_years=10
        )
        assert long.power_at_available > short.power_at_available

    def test_rejects_invalid_inputs(self):
        with pytest.raises(ValueError):
            power_analysis(events_per_year=10, effect_size_bps=0, noise_bps=100)
        with pytest.raises(ValueError):
            power_analysis(events_per_year=10, effect_size_bps=10, noise_bps=0)


class TestScreen:
    def test_overnight_premium_fails_both_constraints(self):
        """The real H1: a documented effect that is nonetheless untradable."""
        r = screen(
            "overnight",
            holding_days=1,
            expected_gross_edge_bps=3,
            events_per_year=252,
            noise_bps=150,
        )
        assert r.verdict is Feasibility.INFEASIBLE_BOTH

    def test_momentum_fails_on_cost_alone(self):
        """The real H10: adequate power, insufficient edge against cost."""
        r = screen(
            "momentum 12-1",
            holding_days=21,
            expected_gross_edge_bps=45,
            events_per_year=250,
            noise_bps=687,
        )
        assert r.verdict is Feasibility.COST_INFEASIBLE

    def test_tax_loss_selling_fails_on_power_alone(self):
        """The real H8: affordable, plausible, and still not provable."""
        r = screen(
            "tax-loss reversal",
            holding_days=30,
            expected_gross_edge_bps=120,
            events_per_year=20,
            noise_bps=822,
        )
        assert r.verdict is Feasibility.POWER_INFEASIBLE
        assert r.cost.is_viable

    def test_pead_survives_screening(self):
        """The real H4: survives the screen, which is not the same as working."""
        r = screen(
            "PEAD",
            holding_days=45,
            expected_gross_edge_bps=90,
            events_per_year=200,
            noise_bps=1006,
        )
        assert r.verdict in (Feasibility.VIABLE, Feasibility.MARGINAL)

    def test_report_is_human_readable(self):
        r = screen("x", holding_days=45, expected_gross_edge_bps=90, events_per_year=200)
        text = r.report()
        assert "verdict" in text and "cost" in text and "power" in text


class TestAnnualEconomics:
    """Fixed costs behave differently from per-trade costs on a small account.

    A subscription is paid whether or not the strategy trades, so it does not
    scale down with the account -- it scales *up* as a percentage of it. Per-trade
    cost models miss this entirely.
    """

    def test_fixed_cost_consumes_a_large_share_of_a_small_account(self):
        """Pins the headline: EUR 199/yr is ~2% of a EUR 10k account."""
        from tradelab.research.feasibility import annual_economics

        result = annual_economics(
            events_per_year=30,
            gross_edge_bps=140,
            holding_days=20,
            positions_held=10,
            round_trip_cost_bps=30,
            fixed_annual_cost=199.0,
            account_equity=10_000.0,
        )
        assert result.fixed_cost_drag_pct == pytest.approx(1.99, abs=0.01)
        # Data cost must consume a material share of gross profit, not a rounding.
        assert 0.3 < result.fixed_cost_share_of_gross < 0.7

    def test_same_subscription_is_trivial_on_a_large_account(self):
        """The same cost that bites at EUR 10k is negligible at EUR 200k."""
        from tradelab.research.feasibility import annual_economics

        small = annual_economics(30, 140, 20, fixed_annual_cost=199.0, account_equity=10_000.0)
        large = annual_economics(30, 140, 20, fixed_annual_cost=199.0, account_equity=200_000.0)
        # The drag scales exactly inversely with equity: 20x the account, 1/20th
        # the drag. That exact inverse relationship IS the point -- a fixed cost
        # does not shrink with the account, so it grows as a share of it.
        assert small.fixed_cost_drag_pct == pytest.approx(20 * large.fixed_cost_drag_pct, rel=1e-9)
        assert small.fixed_cost_drag_pct == pytest.approx(1.99, abs=0.01)
        assert large.fixed_cost_drag_pct == pytest.approx(0.0995, abs=0.001)
        assert large.net_annual_return_pct > small.net_annual_return_pct

    def test_capacity_truncates_reachable_events(self):
        """Pins: a long holding period makes most signals unreachable.

        10 slots held 120 days each can capture ~21 events a year. Signals
        beyond that are not a bigger edge -- they are unreachable.
        """
        from tradelab.research.feasibility import annual_economics

        result = annual_economics(
            events_per_year=200, gross_edge_bps=400, holding_days=120, positions_held=10
        )
        assert result.capacity_limited
        assert result.events_captured == pytest.approx(21.0, abs=1.0)
        assert result.events_captured < result.events_available

    def test_short_holding_period_is_not_capacity_limited(self):
        from tradelab.research.feasibility import annual_economics

        result = annual_economics(
            events_per_year=30, gross_edge_bps=140, holding_days=20, positions_held=10
        )
        assert not result.capacity_limited
        assert result.events_captured == 30

    def test_fixed_cost_can_turn_a_profitable_strategy_negative(self):
        """The failure this function exists to surface."""
        from tradelab.research.feasibility import annual_economics

        free = annual_economics(
            10, 60, 30, positions_held=10, fixed_annual_cost=0.0, account_equity=5_000.0
        )
        paid = annual_economics(
            10, 60, 30, positions_held=10, fixed_annual_cost=199.0, account_equity=5_000.0
        )
        assert free.is_viable
        assert not paid.is_viable

    def test_breakeven_account_size_is_where_the_edge_covers_the_fee(self):
        from tradelab.research.feasibility import annual_economics, breakeven_account_size

        threshold = breakeven_account_size(30, 140, 20, fixed_annual_cost=199.0)
        assert 3_000 < threshold < 12_000
        just_above = annual_economics(
            30, 140, 20, fixed_annual_cost=199.0, account_equity=threshold * 1.1
        )
        just_below = annual_economics(
            30, 140, 20, fixed_annual_cost=199.0, account_equity=threshold * 0.9
        )
        assert just_above.is_viable
        assert not just_below.is_viable

    def test_no_edge_means_no_account_size_justifies_the_fee(self):
        """Pins: a subscription cannot rescue a strategy with no edge."""
        from tradelab.research.feasibility import breakeven_account_size

        assert breakeven_account_size(50, 20, 5, round_trip_cost_bps=30) == float("inf")

    def test_rejects_invalid_inputs(self):
        from tradelab.research.feasibility import annual_economics

        with pytest.raises(ValueError):
            annual_economics(30, 140, 0)
        with pytest.raises(ValueError):
            annual_economics(30, 140, 20, positions_held=0)
        with pytest.raises(ValueError):
            annual_economics(30, 140, 20, account_equity=0)
