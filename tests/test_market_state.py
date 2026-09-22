"""Market-state signal tests.

These are candidate state variables for a lead-lag scan, so the thing that
matters is that each one measures what its name says. A signal that quietly
measures something else makes the scan's conclusion meaningless in a way no
downstream test would catch.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradelab.research.market_state import (
    clv_breadth,
    gap_breadth,
    return_dispersion,
    return_skew,
    reversal_breadth,
    volume_concentration,
)


def panel(values, index=None, cols=None) -> pd.DataFrame:
    arr = np.asarray(values, dtype=float)
    index = index if index is not None else pd.date_range("2020-01-01", periods=arr.shape[0])
    cols = cols if cols is not None else [f"S{i}" for i in range(arr.shape[1])]
    return pd.DataFrame(arr, index=index, columns=cols)


class TestClvBreadth:
    def test_closing_on_the_high_reads_one(self):
        high = panel([[10, 10], [10, 10]])
        low = panel([[8, 8], [8, 8]])
        close = panel([[10, 10], [10, 10]])
        assert clv_breadth(high, low, close).iloc[0] == pytest.approx(1.0)

    def test_closing_on_the_low_reads_zero(self):
        high = panel([[10, 10]])
        low = panel([[8, 8]])
        close = panel([[8, 8]])
        assert clv_breadth(high, low, close).iloc[0] == pytest.approx(0.0)

    def test_a_zero_range_day_is_neutral_not_undefined(self):
        """A halted or untraded name must not produce a division by zero that
        propagates a NaN through the whole cross-section."""
        high = panel([[10, 10]])
        low = panel([[10, 8]])
        close = panel([[10, 9]])
        value = clv_breadth(high, low, close).iloc[0]
        assert np.isfinite(value)
        assert value == pytest.approx(0.5)  # (0.5 neutral + 0.5 midpoint) / 2


class TestDispersionAndSkew:
    def test_dispersion_rises_when_stocks_disagree(self):
        together = panel([[100, 100], [101, 101]])
        apart = panel([[100, 100], [110, 90]])
        assert return_dispersion(apart).iloc[1] > return_dispersion(together).iloc[1]

    def test_skew_is_negative_when_one_name_is_hit(self):
        """Damage concentrated in a tail, rather than a uniform decline, is a
        different market state -- the signal must tell them apart."""
        prices = [[100.0] * 10, [101.0] * 9 + [50.0]]
        assert return_skew(panel(prices)).iloc[1] < -0.5

    def test_skew_is_near_zero_for_a_symmetric_cross_section(self):
        prices = [[100.0] * 6, [104.0, 102.0, 101.0, 99.0, 98.0, 96.0]]
        assert abs(return_skew(panel(prices)).iloc[1]) < 0.3


class TestGapAndReversal:
    def test_a_gap_beyond_the_prior_range_is_counted(self):
        open_ = panel([[10, 10], [20, 10]])
        high = panel([[11, 11], [21, 11]])
        low = panel([[9, 9], [19, 9]])
        assert gap_breadth(open_, high, low).iloc[1] == pytest.approx(0.5)

    def test_reversal_counts_only_days_that_undo_the_gap(self):
        # S0 gaps up then falls (reversal); S1 gaps up and rises (continuation).
        close = panel([[10, 10], [10.5, 12]])
        open_ = panel([[10, 10], [11, 11]])
        assert reversal_breadth(open_, close).iloc[1] == pytest.approx(0.5)

    def test_a_flat_open_is_excluded_rather_than_counted_either_way(self):
        close = panel([[10, 10], [9, 12]])
        open_ = panel([[10, 10], [10, 11]])
        # S0's overnight move is zero, so only S1 is eligible and it continued.
        assert reversal_breadth(open_, close).iloc[1] == pytest.approx(0.0)


class TestVolumeConcentration:
    def test_one_name_taking_all_volume_reads_one(self):
        assert volume_concentration(panel([[100.0, 0.0, 0.0]])).iloc[0] == pytest.approx(1.0)

    def test_evenly_spread_volume_reads_one_over_n(self):
        assert volume_concentration(panel([[10.0] * 4])).iloc[0] == pytest.approx(0.25)

    def test_concentration_rises_as_trading_narrows(self):
        spread = volume_concentration(panel([[25.0, 25.0, 25.0, 25.0]])).iloc[0]
        narrow = volume_concentration(panel([[70.0, 10.0, 10.0, 10.0]])).iloc[0]
        assert narrow > spread
