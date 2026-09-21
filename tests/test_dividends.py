"""Dividend cut detection tests.

Each test names a false positive or a bias it prevents. The detector's job is
not to find events -- that part is easy and wrong. It is to find only real
ones, and to date them when the market actually learned.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from tradelab.research.dividends import detect_dividend_cuts, detect_omissions


def divs(rows) -> pd.DataFrame:
    """rows: (symbol, ex_date, declaration_date, value, period)"""
    frame = pd.DataFrame(rows, columns=["symbol", "ex_date", "declaration_date", "value", "period"])
    frame["ex_date"] = pd.to_datetime(frame["ex_date"], utc=True)
    frame["declaration_date"] = pd.to_datetime(frame["declaration_date"], utc=True)
    frame["period"] = frame["period"].astype("string")
    return frame


def quarterly(symbol, year_from, year_to, value, *, declare_lag=20):
    out = []
    for year in range(year_from, year_to + 1):
        for month in (2, 5, 8, 11):
            ex = datetime(year, month, 15, tzinfo=UTC)
            out.append((symbol, ex, ex - pd.Timedelta(days=declare_lag), value, "Quarterly"))
    return out


class TestFalsePositives:
    def test_a_special_dividend_is_not_a_cut(self):
        """Prevents GE's 2012 phantom cut.

        A $0.055 special between two regular $0.170 quarterlies reads as -67.6%
        and then +209% under naive consecutive comparison. GE did not cut its
        dividend in 2012. One false positive in four detections.
        """
        rows = quarterly("GE", 2011, 2011, 0.170)
        special = datetime(2012, 6, 15, tzinfo=UTC)
        rows.append(("GE", special, special - pd.Timedelta(days=10), 0.055, None))
        rows += quarterly("GE", 2013, 2013, 0.170)
        assert detect_dividend_cuts(divs(rows)) == []

    def test_a_split_does_not_register_as_a_cut(self):
        """`value` is split-adjusted, so the series is consistent across one."""
        rows = quarterly("XYZ", 2015, 2016, 0.40)
        assert detect_dividend_cuts(divs(rows)) == []

    def test_a_small_trim_is_not_a_cut(self):
        rows = quarterly("XYZ", 2015, 2015, 0.40) + quarterly("XYZ", 2016, 2016, 0.36)
        assert detect_dividend_cuts(divs(rows), min_cut=0.25) == []

    def test_a_raise_is_never_a_cut(self):
        rows = quarterly("XYZ", 2015, 2015, 0.20) + quarterly("XYZ", 2016, 2016, 0.40)
        assert detect_dividend_cuts(divs(rows)) == []


class TestTruePositives:
    def test_a_halved_dividend_is_detected(self):
        rows = quarterly("XYZ", 2015, 2015, 0.40) + quarterly("XYZ", 2016, 2016, 0.20)
        (cut,) = detect_dividend_cuts(divs(rows))
        assert cut.cut_fraction == pytest.approx(0.50)
        assert cut.previous_rate == pytest.approx(1.60)
        assert cut.new_rate == pytest.approx(0.80)

    def test_a_frequency_change_is_a_cut_even_at_the_same_payment(self):
        """Prevents: missing a halving because the per-payment amount is unchanged.

        Quarterly $0.20 -> semi-annual $0.20 halves the annual rate. Comparing
        payments sees no change; comparing annualised rates sees the cut.
        """
        rows = quarterly("XYZ", 2015, 2015, 0.20)
        for month in (5, 11):
            ex = datetime(2016, month, 15, tzinfo=UTC)
            rows.append(("XYZ", ex, ex - pd.Timedelta(days=20), 0.20, "SemiAnnual"))
        (cut,) = detect_dividend_cuts(divs(rows))
        assert cut.cut_fraction == pytest.approx(0.50)


class TestEventDating:
    def test_the_event_is_dated_by_declaration_not_ex_date(self):
        """Prevents: measuring a drift that began before the entry.

        The gap ran 12 to 43 days in the sample checked, up to 69. An ex-date
        anchor books returns the market had already produced.
        """
        rows = quarterly("XYZ", 2015, 2015, 0.40) + quarterly(
            "XYZ", 2016, 2016, 0.20, declare_lag=30
        )
        (cut,) = detect_dividend_cuts(divs(rows))
        assert (cut.ex_date - cut.event_date).days == 30
        assert not cut.date_is_inferred

    def test_a_missing_declaration_date_is_dropped_by_default(self):
        rows = quarterly("XYZ", 2015, 2015, 0.40)
        ex = datetime(2016, 2, 15, tzinfo=UTC)
        rows.append(("XYZ", ex, None, 0.20, "Quarterly"))
        assert detect_dividend_cuts(divs(rows)) == []

        # ...and is kept, flagged, when the caller accepts the weaker anchor.
        (cut,) = detect_dividend_cuts(divs(rows), require_declaration=False)
        assert cut.date_is_inferred
        assert cut.event_date == cut.ex_date


class TestOmissions:
    def test_a_stopped_dividend_is_detected_as_an_omission(self):
        rows = quarterly("XYZ", 2012, 2015, 0.40)
        found = detect_omissions(divs(rows), universe_end=datetime(2017, 1, 1, tzinfo=UTC))
        assert len(found) == 1
        assert found[0].is_omission
        assert found[0].date_is_inferred, "no declaration exists for a payment never made"

    def test_a_company_still_paying_is_not_an_omission(self):
        """Prevents: filling the sample with false omissions from healthy firms."""
        rows = quarterly("XYZ", 2012, 2016, 0.40)
        assert detect_omissions(divs(rows), universe_end=datetime(2017, 1, 1, tzinfo=UTC)) == []

    def test_universe_end_is_mandatory(self):
        """Without it, every survivor looks like it stopped at the sample edge."""
        with pytest.raises(ValueError, match="universe_end is required"):
            detect_omissions(divs(quarterly("XYZ", 2012, 2015, 0.40)))

    def test_too_little_history_is_not_an_omission(self):
        rows = quarterly("XYZ", 2012, 2012, 0.40)[:2]
        assert detect_omissions(divs(rows), universe_end=datetime(2017, 1, 1, tzinfo=UTC)) == []


class TestEventStudyIntegrity:
    """The placebo is the only thing standing between a pipeline and a fiction."""

    def test_a_stock_matching_the_index_shows_zero_alpha(self):
        """The core arithmetic: abnormal return is excess over the benchmark."""
        from datetime import UTC

        from tradelab.research.event_study import Event, abnormal_returns, summarise_car

        idx = pd.date_range("2020-01-01", periods=80, freq="D")
        path = [100.0 * (1.001**i) for i in range(80)]
        bars = pd.concat(
            [pd.DataFrame({"symbol": f"S{k}", "timestamp": idx, "close": path}) for k in range(10)],
            ignore_index=True,
        )
        same = pd.DataFrame({"timestamp": idx, "close": path})
        events = [Event(f"S{k}", datetime(2020, 1, 10, tzinfo=UTC)) for k in range(10)]
        result = summarise_car(abnormal_returns(bars, events, same, [21]), 21)
        assert result.mean == pytest.approx(0.0, abs=1e-9)

    def test_drift_against_a_flat_index_is_recovered_exactly(self):
        from datetime import UTC

        from tradelab.research.event_study import Event, abnormal_returns, summarise_car

        idx = pd.date_range("2020-01-01", periods=80, freq="D")
        bars = pd.concat(
            [
                pd.DataFrame(
                    {
                        "symbol": f"S{k}",
                        "timestamp": idx,
                        "close": [100.0 * (1.001**i) for i in range(80)],
                    }
                )
                for k in range(10)
            ],
            ignore_index=True,
        )
        flat = pd.DataFrame({"timestamp": idx, "close": [100.0] * 80})
        events = [Event(f"S{k}", datetime(2020, 1, 10, tzinfo=UTC)) for k in range(10)]
        result = summarise_car(abnormal_returns(bars, events, flat, [21]), 21)
        assert result.mean == pytest.approx(1.001**21 - 1, abs=1e-9)

    def test_tz_aware_and_naive_bars_align_identically(self):
        """Prevents: a silent one-day shift on a subset of events.

        Bar files are tz-naive; dividend declarations arrive tz-aware. An
        earlier version normalised only one of the two paths, and the bug was
        invisible because the unit test happened to use naive bars.
        """
        from datetime import UTC

        from tradelab.research.event_study import Event, abnormal_returns, summarise_car

        means = []
        for tz in (None, "UTC"):
            idx = pd.date_range("2020-01-01", periods=80, freq="D", tz=tz)
            bars = pd.concat(
                [
                    pd.DataFrame(
                        {
                            "symbol": f"S{k}",
                            "timestamp": idx,
                            "close": [100.0 * (1.001**i) for i in range(80)],
                        }
                    )
                    for k in range(10)
                ],
                ignore_index=True,
            )
            flat = pd.DataFrame({"timestamp": idx, "close": [100.0] * 80})
            events = [Event(f"S{k}", datetime(2020, 1, 10, tzinfo=UTC)) for k in range(10)]
            means.append(summarise_car(abnormal_returns(bars, events, flat, [21]), 21).mean)
        assert means[0] == pytest.approx(means[1], abs=1e-12)

    def test_entry_on_the_event_date_is_refused(self):
        """Prevents: buying at a close that already contains the news."""
        from datetime import UTC

        from tradelab.research.event_study import Event, abnormal_returns

        idx = pd.date_range("2020-01-01", periods=40, freq="D")
        bars = pd.DataFrame({"symbol": "S", "timestamp": idx, "close": [100.0] * 40})
        with pytest.raises(ValueError, match="already reflects the announcement"):
            abnormal_returns(
                bars,
                [Event("S", datetime(2020, 1, 10, tzinfo=UTC))],
                bars.rename(columns={"close": "close"}),
                [5],
                entry_lag=0,
            )

    def test_clustered_t_is_smaller_when_events_share_a_month(self):
        """Prevents: 400 events from one crisis counted as 400 independent bets.

        Dividend cuts cluster in crises. The naive t-statistic treats them as
        independent and will report significance on what is really a handful of
        correlated observations.
        """
        from datetime import UTC

        from tradelab.research.event_study import Event, abnormal_returns, summarise_car

        idx = pd.date_range("2020-01-01", periods=200, freq="D")
        bars = pd.concat(
            [
                pd.DataFrame(
                    {
                        "symbol": f"S{k}",
                        "timestamp": idx,
                        "close": [100.0 * (1.0005**i) for i in range(200)],
                    }
                )
                for k in range(40)
            ],
            ignore_index=True,
        )
        flat = pd.DataFrame({"timestamp": idx, "close": [100.0] * 200})
        # Every event in the same month: one real observation, not forty.
        clustered = [Event(f"S{k}", datetime(2020, 2, 3, tzinfo=UTC)) for k in range(40)]
        result = summarise_car(abnormal_returns(bars, clustered, flat, [21]), 21)
        assert result.monthly_n == 1
        assert result.monthly_t == 0.0, "a single month cannot support a t-statistic"
        assert not result.is_significant
