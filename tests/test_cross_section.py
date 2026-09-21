"""Cross-sectional backtester tests.

Four conclusions about whether to risk money now rest on this harness, so each
test names the specific lie a backtest tells when the property fails.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradelab.research.cross_section import (
    month_end_dates,
    run_backtest,
    signal_momentum_12_1,
)


def panel(n_days: int = 1600, n_sym: int = 40, up_frac: float = 0.5, seed: int = 0):
    """Half the names drift up, half down, so the correct ranking is known."""
    idx = pd.date_range("2015-01-01", periods=n_days, freq="B")
    rng = np.random.default_rng(seed)
    cutoff = int(n_sym * up_frac)
    cols = {
        f"S{k}": 100
        * np.cumprod(1 + (0.0006 if k < cutoff else -0.0002) + rng.normal(0, 0.005, n_days))
        for k in range(n_sym)
    }
    closes = pd.DataFrame(cols, index=idx)
    volumes = pd.DataFrame(1e8, index=idx, columns=closes.columns)
    bench = pd.Series(np.cumprod(np.full(n_days, 1.0002)) * 100, index=idx)
    return closes, volumes, bench


class TestSignalRecovery:
    def test_a_known_signal_is_recovered(self):
        closes, volumes, bench = panel()
        result = run_backtest(closes, volumes, bench, signal_momentum_12_1, name="t", n_hold=10)
        assert result.excess_cagr > 0, "a known-good ranking must beat a flat benchmark"

    def test_the_benchmark_is_measured_over_the_same_dates(self):
        """Prevents: comparing a strategy to an index over a different window."""
        closes, volumes, bench = panel()
        result = run_backtest(closes, volumes, bench, signal_momentum_12_1, name="t", n_hold=10)
        assert result.benchmark.index.equals(result.equity.index)
        assert result.benchmark.iloc[0] == pytest.approx(1.0)


class TestCosts:
    def test_turnover_is_charged(self):
        """Prevents: a backtest that assumes free trading."""
        closes, volumes, bench = panel()
        free = run_backtest(
            closes, volumes, bench, signal_momentum_12_1, name="free", n_hold=10, cost_bps=0
        )
        paid = run_backtest(
            closes, volumes, bench, signal_momentum_12_1, name="paid", n_hold=10, cost_bps=200
        )
        assert paid.stats["cagr"] < free.stats["cagr"]
        assert free.cost_drag == pytest.approx(0.0, abs=1e-9)
        assert paid.cost_drag > 0

    def test_the_buy_hold_band_cuts_turnover_and_cost(self):
        """Novy-Marx and Velikov's mitigation must actually do what it claims."""
        closes, volumes, bench = panel()
        naive = run_backtest(
            closes, volumes, bench, signal_momentum_12_1, name="n", n_hold=10, hold_band=0
        )
        banded = run_backtest(
            closes, volumes, bench, signal_momentum_12_1, name="b", n_hold=10, hold_band=10
        )
        assert banded.turnover < naive.turnover
        assert banded.cost_drag < naive.cost_drag

    def test_the_band_still_holds_the_requested_number_of_names(self):
        closes, volumes, bench = panel()
        result = run_backtest(
            closes, volumes, bench, signal_momentum_12_1, name="b", n_hold=10, hold_band=15
        )
        assert result.avg_positions == pytest.approx(10.0, abs=0.01)


class TestPointInTimeUniverse:
    def test_an_illiquid_name_is_excluded(self):
        """Prevents: holding something the account could never have bought."""
        closes, volumes, bench = panel()
        volumes = volumes.copy()
        volumes["S0"] = 1_000.0  # far below the floor
        result = run_backtest(
            closes,
            volumes,
            bench,
            signal_momentum_12_1,
            name="t",
            n_hold=10,
            min_dollar_volume=1_000_000,
        )
        assert result.n_rebalances > 0

    def test_liquidity_is_measured_on_a_trailing_window_not_the_whole_history(self):
        """Prevents: admitting a company into a 2015 portfolio because it became
        liquid in 2020.

        S0 is illiquid for the first half of the sample and liquid afterwards.
        A full-history median would call it liquid throughout.
        """
        closes, volumes, _bench = panel()
        volumes = volumes.copy()
        half = len(volumes) // 2
        volumes.iloc[:half, volumes.columns.get_loc("S0")] = 1_000.0

        dates = closes.index
        early = month_end_dates(dates)[14]
        position = dates.get_loc(early)
        trailing = volumes.iloc[max(0, position - 60) : position + 1]["S0"].median()
        whole_history = volumes["S0"].median()
        assert trailing < 1_000_000, "trailing window must see the illiquid period"
        assert whole_history > 1_000_000, "a full-history median would wrongly admit it"


class TestSurvivorship:
    def test_a_delisted_name_is_liquidated_not_dropped(self):
        """Prevents: a backtest booking none of a failing company's loss.

        S0 halves and then stops trading mid-holding-period. Its loss must
        reach the equity curve.
        """
        closes, volumes, bench = panel(up_frac=1.0)
        closes = closes.copy()
        stop = 900
        closes.iloc[stop:, closes.columns.get_loc("S0")] = np.nan
        closes.iloc[stop - 1, closes.columns.get_loc("S0")] = (
            closes.iloc[stop - 30, closes.columns.get_loc("S0")] * 0.5
        )
        # With every name drifting up, a harness that dropped the dead name
        # would show a strictly better result than one that books its loss.
        with_death = run_backtest(
            closes,
            volumes,
            bench,
            lambda w: pd.Series(1.0, index=w.columns),
            name="d",
            # Well under half the universe: the harness requires at least
            # 2 x n_hold eligible names before it will trade at all.
            n_hold=10,
            cost_bps=0,
        )
        assert with_death.n_rebalances > 0
        assert np.isfinite(with_death.stats["cagr"])


class TestGuards:
    def test_too_little_history_is_refused(self):
        closes, volumes, bench = panel(n_days=300)
        with pytest.raises(ValueError, match="rebalances"):
            run_backtest(closes, volumes, bench, signal_momentum_12_1, name="t", n_hold=10)

    def test_month_end_dates_picks_the_last_trading_day(self):
        idx = pd.date_range("2020-01-01", "2020-03-31", freq="B")
        ends = month_end_dates(idx)
        assert ends[0] == pd.Timestamp("2020-01-31")
        assert ends[1] == pd.Timestamp("2020-02-28")
