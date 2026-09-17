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
