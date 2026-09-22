"""Tests for information-coefficient analysis.

The IC is the cheapest honest test of a signal, which makes it the thing most
worth getting right: an IC pipeline with a look-ahead bug or a mis-stated
standard error will bless noise and no later gate will catch it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradelab.research.ic import (
    forward_returns,
    information_coefficient,
    neutralise,
    summarise_ic,
)


def _closes(values: dict[str, list[float]], n: int) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.date_range("2020-01-01", periods=n, freq="B"))


class TestForwardReturns:
    def test_return_is_measured_from_the_date_forward_not_backward(self):
        closes = _closes({"A": [10.0, 11.0, 12.0, 13.0, 14.0]}, 5)
        dates = [closes.index[0], closes.index[2], closes.index[4]]
        fwd = forward_returns(closes, dates)
        # 10 -> 12 is +20%, 12 -> 14 is +16.67%
        assert fwd.loc[dates[0], "A"] == pytest.approx(0.2)
        assert fwd.loc[dates[1], "A"] == pytest.approx(2.0 / 12.0)

    def test_final_date_has_no_forward_return(self):
        """There is no next rebalance, so it must not appear at all rather than
        silently becoming a zero or a partial-period return."""
        closes = _closes({"A": [10.0, 11.0, 12.0]}, 3)
        dates = list(closes.index)
        fwd = forward_returns(closes, dates)
        assert dates[-1] not in fwd.index

    def test_a_name_that_stops_trading_is_carried_not_dropped(self):
        """Dropping it deletes exactly the failures survivorship bias is about."""
        closes = _closes({"A": [10.0, 9.0, np.nan, np.nan, np.nan]}, 5)
        dates = [closes.index[0], closes.index[4]]
        fwd = forward_returns(closes, dates)
        # Last observed price is 9.0, so the return is -10%, not NaN.
        assert fwd.loc[dates[0], "A"] == pytest.approx(-0.1)


class TestInformationCoefficient:
    def test_a_perfect_signal_scores_one(self):
        index = pd.date_range("2020-01-01", periods=1, freq="BME")
        cols = [f"S{i}" for i in range(30)]
        signal = pd.DataFrame([np.arange(30.0)], index=index, columns=cols)
        forward = pd.DataFrame([np.arange(30.0)], index=index, columns=cols)
        ic = information_coefficient(signal, forward)
        assert ic.iloc[0] == pytest.approx(1.0)

    def test_an_inverted_signal_scores_minus_one(self):
        index = pd.date_range("2020-01-01", periods=1, freq="BME")
        cols = [f"S{i}" for i in range(30)]
        signal = pd.DataFrame([np.arange(30.0)], index=index, columns=cols)
        forward = pd.DataFrame([np.arange(30.0)[::-1]], index=index, columns=cols)
        ic = information_coefficient(signal, forward)
        assert ic.iloc[0] == pytest.approx(-1.0)

    def test_a_thin_cross_section_is_skipped_not_reported(self):
        """A 5-name rank correlation is noise with a decimal point."""
        index = pd.date_range("2020-01-01", periods=1, freq="BME")
        cols = ["A", "B", "C", "D", "E"]
        signal = pd.DataFrame([[1.0, 2, 3, 4, 5]], index=index, columns=cols)
        forward = pd.DataFrame([[1.0, 2, 3, 4, 5]], index=index, columns=cols)
        assert information_coefficient(signal, forward, min_names=20).empty

    def test_a_constant_signal_is_skipped_rather_than_scored_nan(self):
        index = pd.date_range("2020-01-01", periods=1, freq="BME")
        cols = [f"S{i}" for i in range(30)]
        signal = pd.DataFrame([np.zeros(30)], index=index, columns=cols)
        forward = pd.DataFrame([np.arange(30.0)], index=index, columns=cols)
        assert information_coefficient(signal, forward).empty

    def test_random_signal_has_ic_near_zero(self):
        rng = np.random.default_rng(0)
        index = pd.date_range("2020-01-31", periods=120, freq="BME")
        cols = [f"S{i}" for i in range(60)]
        signal = pd.DataFrame(rng.normal(size=(120, 60)), index=index, columns=cols)
        forward = pd.DataFrame(rng.normal(size=(120, 60)), index=index, columns=cols)
        summary = summarise_ic(information_coefficient(signal, forward))
        assert abs(summary.mean) < 0.03
        assert abs(summary.t_stat) < 2.0


class TestSummarise:
    def test_t_stat_uses_effective_sample_not_raw_count(self):
        """A persistent IC series must not borrow significance from its own
        repetition. This is the correction that killed N11's listing_rate."""
        n = 200
        base = np.full(n, 0.02)
        noise = np.zeros(n)
        # Build a highly autocorrelated series around a positive mean.
        rng = np.random.default_rng(1)
        for i in range(1, n):
            noise[i] = 0.97 * noise[i - 1] + rng.normal(scale=0.01)
        ic = pd.Series(base + noise, index=pd.date_range("2010-01-31", periods=n, freq="BME"))
        summary = summarise_ic(ic)
        # A finite draw from an AR(1) at phi=0.97 realises well below 0.97, so
        # the threshold is set to what persistence actually looks like in 200
        # observations rather than to the generating parameter.
        assert summary.autocorr > 0.8
        assert summary.n_eff < summary.n / 10

    def test_independent_series_keeps_most_of_its_sample(self):
        rng = np.random.default_rng(2)
        n = 200
        ic = pd.Series(
            rng.normal(scale=0.1, size=n),
            index=pd.date_range("2010-01-31", periods=n, freq="BME"),
        )
        summary = summarise_ic(ic)
        assert summary.n_eff > summary.n * 0.7

    def test_hit_rate_counts_positive_periods(self):
        ic = pd.Series([0.1, -0.1, 0.1, 0.1], index=pd.date_range("2020-01-31", periods=4, freq="BME"))
        assert summarise_ic(ic).hit_rate == pytest.approx(0.75)

    def test_too_short_a_series_returns_zeros_rather_than_a_spurious_t(self):
        ic = pd.Series([0.5, 0.5], index=pd.date_range("2020-01-31", periods=2, freq="BME"))
        assert summarise_ic(ic).t_stat == 0.0


class TestNeutralise:
    def test_a_signal_identical_to_the_control_is_fully_removed(self):
        index = pd.date_range("2020-01-31", periods=3, freq="BME")
        cols = [f"S{i}" for i in range(20)]
        values = np.tile(np.arange(20.0), (3, 1))
        panel = pd.DataFrame(values, index=index, columns=cols)
        residual = neutralise(panel, panel)
        assert np.allclose(residual.to_numpy(dtype=float), 0.0, atol=1e-9)

    def test_an_orthogonal_signal_survives_neutralisation(self):
        rng = np.random.default_rng(3)
        index = pd.date_range("2020-01-31", periods=6, freq="BME")
        cols = [f"S{i}" for i in range(40)]
        signal = pd.DataFrame(rng.normal(size=(6, 40)), index=index, columns=cols)
        control = pd.DataFrame(rng.normal(size=(6, 40)), index=index, columns=cols)
        residual = neutralise(signal, control)
        # Ranks are preserved well enough that the residual still ranks like the
        # original: an orthogonal control should not erase the signal.
        for date in index:
            a = signal.loc[date].rank()
            b = residual.loc[date].rank()
            assert a.corr(b, method="spearman") > 0.8
