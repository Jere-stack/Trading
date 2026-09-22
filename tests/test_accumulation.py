"""Tests for the N12 accumulation footprint.

The test that matters is `test_sustained_accumulation_outranks_a_larger_spike`.
Every other test here checks plumbing; that one checks the actual claim the
construction is making, and if it fails the hypothesis is not worth running.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradelab.research.accumulation import (
    FootprintParams,
    accumulation_footprint,
    close_location_value,
    shuffle_clv,
    shuffle_volume,
)


def _panel(values: dict[str, list[float]], n: int) -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame(values, index=index)


class TestCloseLocationValue:
    def test_close_on_the_high_scores_plus_one(self):
        high = _panel({"A": [10.0]}, 1)
        low = _panel({"A": [8.0]}, 1)
        close = _panel({"A": [10.0]}, 1)
        assert close_location_value(high, low, close).iloc[0, 0] == pytest.approx(1.0)

    def test_close_on_the_low_scores_minus_one(self):
        high = _panel({"A": [10.0]}, 1)
        low = _panel({"A": [8.0]}, 1)
        close = _panel({"A": [8.0]}, 1)
        assert close_location_value(high, low, close).iloc[0, 0] == pytest.approx(-1.0)

    def test_close_at_the_midpoint_scores_zero(self):
        high = _panel({"A": [10.0]}, 1)
        low = _panel({"A": [8.0]}, 1)
        close = _panel({"A": [9.0]}, 1)
        assert close_location_value(high, low, close).iloc[0, 0] == pytest.approx(0.0)

    def test_a_session_with_no_range_scores_zero_rather_than_nan(self):
        """A limit-locked or untraded session carries no balance information.

        It must not become NaN: NaN would silently shorten the rolling window
        for illiquid names only, which biases the measure across the very
        dimension the strategy sorts on.
        """
        high = _panel({"A": [9.0]}, 1)
        low = _panel({"A": [9.0]}, 1)
        close = _panel({"A": [9.0]}, 1)
        assert close_location_value(high, low, close).iloc[0, 0] == 0.0


class TestFootprintConstruction:
    """The discrimination the whole hypothesis rests on."""

    @staticmethod
    def _build(volume: list[float], clv_target: list[float]) -> dict[str, pd.DataFrame]:
        """Panels with a chosen volume path and a chosen close-location path."""
        n = len(volume)
        low = [10.0] * n
        high = [12.0] * n
        # invert clv = 2*(c-low)/(high-low) - 1  ->  c = low + (clv+1)/2 * span
        close = [10.0 + (c + 1.0) / 2.0 * 2.0 for c in clv_target]
        return {
            "high": _panel({"A": high}, n),
            "low": _panel({"A": low}, n),
            "close": _panel({"A": close}, n),
            "dollar_volume": _panel({"A": volume}, n),
        }

    def test_sustained_accumulation_outranks_a_larger_spike(self):
        """Fifteen quiet elevated days must beat one enormous day.

        This is the entire premise: an institution splitting an order leaves
        many modest sessions, a news pop leaves one huge one. If the measure
        ranks the spike higher, it is just a volume-level measure and adds
        nothing to the published high-volume premium.
        """
        params = FootprintParams(k=1.25, window=21, baseline_window=60, baseline_lag=5)
        n = 150
        baseline_vol = 1_000_000.0

        # Case 1: one 8x session closing on its high, inside the final window.
        spike_vol = [baseline_vol] * n
        spike_clv = [0.0] * n
        spike_vol[n - 10] = baseline_vol * 8
        spike_clv[n - 10] = 1.0

        # Case 2: fifteen sessions at 1.4x, each closing two-thirds up.
        drip_vol = [baseline_vol] * n
        drip_clv = [0.0] * n
        for i in range(n - 16, n - 1):
            drip_vol[i] = baseline_vol * 1.4
            drip_clv[i] = 0.33

        spike = accumulation_footprint(**self._build(spike_vol, spike_clv), params=params)
        drip = accumulation_footprint(**self._build(drip_vol, drip_clv), params=params)

        spike_score = float(spike["A"].iloc[-1])
        drip_score = float(drip["A"].iloc[-1])
        assert drip_score > spike_score, (
            f"sustained {drip_score:.4f} must outrank spike {spike_score:.4f}; "
            "if it does not, the measure is a volume-level proxy"
        )

    def test_distribution_closing_on_lows_scores_negative(self):
        params = FootprintParams(k=1.25, window=21, baseline_window=60, baseline_lag=5)
        n = 150
        vol = [1_000_000.0] * n
        clv = [0.0] * n
        for i in range(n - 16, n - 1):
            vol[i] = 1_400_000.0
            clv[i] = -0.5
        panel = accumulation_footprint(**self._build(vol, clv), params=params)
        assert float(panel["A"].iloc[-1]) < 0

    def test_quiet_stock_scores_zero(self):
        params = FootprintParams(k=1.25, window=21, baseline_window=60, baseline_lag=5)
        n = 150
        panel = accumulation_footprint(
            **self._build([1_000_000.0] * n, [0.5] * n), params=params
        )
        # Never elevated, so no session contributes regardless of its CLV.
        assert float(panel["A"].iloc[-1]) == pytest.approx(0.0)

    def test_baseline_is_lagged_so_the_window_cannot_set_its_own_bar(self):
        """A sustained ramp must not normalise itself away.

        With an unlagged baseline, a fortnight of elevated volume raises the
        median it is compared against and the signal decays toward zero. The
        lag is what stops the measure eating its own evidence.
        """
        n = 200
        vol = [1_000_000.0] * n
        clv = [0.0] * n
        for i in range(n - 40, n - 1):
            vol[i] = 1_500_000.0
            clv[i] = 0.5
        built = self._build(vol, clv)
        lagged = accumulation_footprint(
            **built, params=FootprintParams(k=1.25, window=21, baseline_window=60, baseline_lag=60)
        )
        unlagged = accumulation_footprint(
            **built, params=FootprintParams(k=1.25, window=21, baseline_window=60, baseline_lag=0)
        )
        assert float(lagged["A"].iloc[-1]) > float(unlagged["A"].iloc[-1])


class TestPlacebos:
    def test_shuffling_volume_preserves_the_distribution(self):
        rng = np.random.default_rng(0)
        frame = pd.DataFrame(
            {"A": rng.lognormal(14, 1, 500), "B": rng.lognormal(15, 1, 500)},
            index=pd.date_range("2020-01-01", periods=500, freq="B"),
        )
        shuffled = shuffle_volume(frame, seed=1)
        for col in frame.columns:
            assert np.allclose(
                np.sort(frame[col].to_numpy(dtype=np.float32)),
                np.sort(shuffled[col].to_numpy(dtype=np.float32)),
            )

    def test_shuffling_volume_changes_the_ordering(self):
        rng = np.random.default_rng(0)
        frame = pd.DataFrame(
            {"A": rng.lognormal(14, 1, 500)},
            index=pd.date_range("2020-01-01", periods=500, freq="B"),
        )
        shuffled = shuffle_volume(frame, seed=1)
        assert not np.allclose(frame["A"].to_numpy(), shuffled["A"].to_numpy())

    def test_shuffle_leaves_missing_values_in_place(self):
        """NaNs mark sessions a stock did not trade -- permuting them would
        invent trading days for a name that was not listed yet."""
        frame = pd.DataFrame(
            {"A": [np.nan, np.nan, 1.0, 2.0, 3.0]},
            index=pd.date_range("2020-01-01", periods=5, freq="B"),
        )
        shuffled = shuffle_volume(frame, seed=3)
        assert shuffled["A"].isna().tolist() == [True, True, False, False, False]

    def test_clv_shuffle_preserves_distribution(self):
        rng = np.random.default_rng(2)
        frame = pd.DataFrame(
            {"A": rng.uniform(-1, 1, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="B"),
        )
        shuffled = shuffle_clv(frame, seed=5)
        assert np.allclose(
            np.sort(frame["A"].to_numpy(dtype=np.float32)),
            np.sort(shuffled["A"].to_numpy(dtype=np.float32)),
        )


class TestParams:
    def test_label_names_every_free_parameter(self):
        """If a parameter is missing from the label it will be missing from the
        grid, and N11 died of exactly one unvaried parameter."""
        label = FootprintParams().label()
        for token in ("k", "w", "b", "l"):
            assert token in label
