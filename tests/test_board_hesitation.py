"""Board Hesitation Breadth tests.

The measure is built out of absences -- a declaration that has not happened yet
-- which is an unusually easy thing to get silently wrong. Each test names the
artefact it prevents.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradelab.research.board_hesitation import (
    BreadthSeries,
    board_hesitation_breadth,
    lead_lag_profile,
)


def punctual(n_firms: int = 100, n_decl: int = 40, cadence: int = 91) -> pd.DataFrame:
    """A universe of perfectly regular quarterly payers, staggered across the month."""
    rows = []
    for k in range(n_firms):
        start = pd.Timestamp("2010-01-15") + pd.Timedelta(days=k % 30)
        for i in range(n_decl):
            rows.append(
                {"symbol": f"S{k}", "declaration_date": start + pd.Timedelta(days=cadence * i)}
            )
    return pd.DataFrame(rows)


class TestSignalBehaviour:
    def test_a_punctual_universe_reads_zero(self):
        """If boards on schedule registered as hesitating, everything downstream
        would be measuring the calendar."""
        breadth = board_hesitation_breadth(punctual())
        mid = breadth.raw.loc["2014":"2018"].dropna()
        assert mid.mean() < 0.02

    def test_a_market_wide_delay_is_detected(self):
        frame = punctual()
        delayed = frame.copy()
        mask = delayed["declaration_date"].dt.year == 2016
        delayed.loc[mask, "declaration_date"] += pd.Timedelta(days=40)
        breadth = board_hesitation_breadth(delayed)
        assert breadth.raw.loc["2016"].mean() > breadth.raw.loc["2014"].mean() + 0.02

    def test_earliness_does_not_register(self):
        """One-sided by design: boards do not rush good news, and averaging
        earliness in would cancel the signal."""
        frame = punctual()
        early = frame.copy()
        mask = early["declaration_date"].dt.year == 2016
        early.loc[mask, "declaration_date"] -= pd.Timedelta(days=30)
        breadth = board_hesitation_breadth(early)
        assert breadth.raw.loc["2016"].mean() < 0.05


class TestTheOmissionTrap:
    def test_a_firm_that_stops_paying_is_retired_not_permanently_late(self):
        """Prevents: an index built entirely out of dead companies.

        A firm that stops paying is overdue forever. Counted naively, every
        omission inflates the measure for the rest of the sample and breadth
        drifts upward as the universe ages.
        """
        frame = punctual(n_firms=50)
        # Half the firms stop after 2015 entirely.
        stopped = frame[
            ~(
                (frame["symbol"].str.removeprefix("S").astype(int) < 25)
                & (frame["declaration_date"] > pd.Timestamp("2015-01-01"))
            )
        ]
        breadth = board_hesitation_breadth(stopped)
        late_2018 = breadth.raw.loc["2018"].dropna()
        assert late_2018.mean() < 0.15, "retired firms must not stay 'late' forever"

    def test_the_eligible_count_falls_when_firms_retire(self):
        frame = punctual(n_firms=50)
        stopped = frame[
            ~(
                (frame["symbol"].str.removeprefix("S").astype(int) < 25)
                & (frame["declaration_date"] > pd.Timestamp("2015-01-01"))
            )
        ]
        breadth = board_hesitation_breadth(stopped)
        assert breadth.eligible.loc["2018"].mean() < breadth.eligible.loc["2014"].mean()


class TestProjectionArtefact:
    def test_the_series_stops_at_the_last_real_declaration(self):
        """Prevents: breadth reading 100% for years past the data.

        Firms are projected forward to a retirement date, so beyond the last
        declaration every remaining firm is trivially late.
        """
        frame = punctual()
        breadth = board_hesitation_breadth(frame)
        assert breadth.raw.index.max() <= frame["declaration_date"].max()


class TestSeasonalAdjustment:
    def test_a_recurring_seasonal_is_removed_but_an_anomaly_survives(self):
        index = pd.date_range("2010-01-01", "2020-12-31", freq="D")
        week = index.isocalendar().week.to_numpy()
        raw = pd.Series(0.05, index=index)
        raw[week == 10] = 0.10
        raw[(week == 10) & (index.year == 2020)] = 0.20
        series = BreadthSeries(raw, pd.Series(100, index=index), pd.Series(5, index=index))
        adjusted = series.seasonally_adjusted(years=3)
        assert adjusted[(week == 10) & (index.year == 2018)].mean() == pytest.approx(0, abs=0.01)
        assert adjusted[(week == 10) & (index.year == 2020)].mean() == pytest.approx(0.10, abs=0.01)

    def test_the_baseline_never_uses_its_own_year(self):
        """Point-in-time: a date must not contribute to the baseline it is
        measured against."""
        index = pd.date_range("2010-01-01", "2016-12-31", freq="D")
        raw = pd.Series(0.05, index=index)
        raw[index.year == 2016] = 0.50
        series = BreadthSeries(raw, pd.Series(100, index=index), pd.Series(5, index=index))
        adjusted = series.seasonally_adjusted(years=3).dropna()
        # 2016 is entirely anomalous, so its deviation must be the full 0.45.
        assert adjusted[adjusted.index.year == 2016].mean() == pytest.approx(0.45, abs=0.01)


class TestLeadLag:
    def test_a_leading_signal_peaks_at_positive_lag(self):
        """The gate test must actually detect a lead when one exists."""
        months = pd.date_range("2005-01-31", periods=200, freq="ME")
        rng = np.random.default_rng(0)
        signal = pd.Series(rng.normal(size=len(months)), index=months)
        # Returns three months later are driven by the signal, negatively.
        returns = -signal.shift(3).fillna(0.0) * 0.02 + rng.normal(0, 0.001, len(months))
        prices = pd.Series((1 + returns).cumprod() * 100, index=months)
        profile = lead_lag_profile(signal, prices, months=6)
        peak = profile.iloc[profile["correlation"].abs().idxmax()]
        assert peak["lag_months"] == 3
        assert peak["correlation"] < 0

    def test_a_coincident_signal_peaks_at_zero(self):
        """The failure mode the gate exists to catch."""
        months = pd.date_range("2005-01-31", periods=200, freq="ME")
        rng = np.random.default_rng(1)
        returns = pd.Series(rng.normal(0, 0.03, len(months)), index=months)
        prices = pd.Series((1 + returns).cumprod() * 100, index=months)
        signal = -returns * 10  # reacts to the market, same month
        profile = lead_lag_profile(signal, prices, months=6)
        peak = profile.iloc[profile["correlation"].abs().idxmax()]
        assert peak["lag_months"] == 0
