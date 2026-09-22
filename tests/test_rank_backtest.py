"""Tests for the ranked-portfolio simulator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradelab.research.rank_backtest import perf_stats, run_ranked


def _closes(data: dict[str, list[float]]) -> pd.DataFrame:
    n = len(next(iter(data.values())))
    return pd.DataFrame(data, index=pd.date_range("2020-01-01", periods=n, freq="B"))


class TestSelection:
    def test_holds_the_highest_scores(self):
        closes = _closes({"A": [1.0] * 6, "B": [1.0] * 6, "C": [1.0] * 6})
        dates = [closes.index[0], closes.index[3]]
        scores = pd.DataFrame({"A": [3, 3], "B": [2, 2], "C": [1, 1]}, index=dates, dtype=float)
        res = run_ranked(scores, closes, n_hold=2, cost_bps=0)
        assert res.holdings[dates[0]] == ["A", "B"]

    def test_hold_band_keeps_a_name_that_slips_but_stays_inside(self):
        closes = _closes({s: [1.0] * 6 for s in "ABCD"})
        dates = [closes.index[0], closes.index[3]]
        scores = pd.DataFrame(
            {"A": [4, 4], "B": [3, 2], "C": [2, 3], "D": [1, 1]}, index=dates, dtype=float
        )
        res = run_ranked(scores, closes, n_hold=2, hold_band=1, cost_bps=0)
        assert set(res.holdings[dates[1]]) == {"A", "B"}, "B fell to rank 3 but is inside the band"

    def test_ineligible_names_are_never_bought(self):
        closes = _closes({"A": [1.0] * 4, "B": [1.0] * 4})
        dates = [closes.index[0], closes.index[2]]
        scores = pd.DataFrame({"A": [np.nan, np.nan], "B": [1.0, 1.0]}, index=dates)
        res = run_ranked(scores, closes, n_hold=2, cost_bps=0)
        assert res.holdings[dates[0]] == ["B"]


class TestReturnsAndCosts:
    def test_equal_weight_return_is_the_average_of_names(self):
        closes = _closes({"A": [100, 110, 120], "B": [100, 100, 100]})
        dates = [closes.index[0], closes.index[2]]
        scores = pd.DataFrame({"A": [1.0, 1.0], "B": [1.0, 1.0]}, index=dates)
        res = run_ranked(scores, closes, n_hold=2, cost_bps=0)
        assert res.equity.iloc[-1] == pytest.approx(1.10)

    def test_initial_purchase_costs_half_a_round_trip(self):
        """Buying a full book from cash is only the BUY leg of a round trip, so it
        costs half the round-trip rate: turnover is half the absolute weight
        change from an empty book, 0.5."""
        closes = _closes({"A": [100.0] * 4})
        dates = [closes.index[0], closes.index[2]]
        scores = pd.DataFrame({"A": [1.0, 1.0]}, index=dates)
        res = run_ranked(scores, closes, n_hold=1, cost_bps=100)
        assert res.turnover[0] == pytest.approx(0.5)
        assert res.equity.iloc[0] == pytest.approx(1 - 0.005)

    def test_unchanged_book_trades_only_the_drift(self):
        closes = _closes({"A": [100, 200, 200, 200], "B": [100, 100, 100, 100]})
        dates = [closes.index[0], closes.index[2]]
        scores = pd.DataFrame({"A": [1.0, 1.0], "B": [1.0, 1.0]}, index=dates)
        res = run_ranked(scores, closes, n_hold=2, cost_bps=0)
        # A drifted to 2/3, B to 1/3; back to 1/2 each means moving 1/6 of the book.
        assert res.turnover[1] == pytest.approx(1 / 6)

    def test_delisted_name_is_carried_at_its_last_price(self):
        closes = _closes({"A": [100, 50, np.nan, np.nan], "B": [100, 100, 100, 100]})
        dates = [closes.index[0], closes.index[3]]
        scores = pd.DataFrame({"A": [1.0, np.nan], "B": [1.0, 1.0]}, index=dates)
        res = run_ranked(scores, closes, n_hold=2, cost_bps=0)
        assert res.equity.loc[closes.index[3]] == pytest.approx(0.75), (
            "a 50% loss on half the book must survive the delisting, not vanish"
        )


class TestPerfStats:
    def test_flat_equity_has_zero_return(self):
        eq = pd.Series(1.0, index=pd.date_range("2020-01-01", periods=300, freq="B"))
        eq.iloc[::2] = 1.0001
        s = perf_stats(eq)
        assert abs(s["cagr"]) < 0.01

    def test_drawdown_is_measured_from_the_peak(self):
        eq = pd.Series(
            [1.0, 2.0, 1.0, 1.5] + [1.5] * 40,
            index=pd.date_range("2020-01-01", periods=44, freq="B"),
        )
        assert perf_stats(eq)["maxdd"] == pytest.approx(-0.5)
