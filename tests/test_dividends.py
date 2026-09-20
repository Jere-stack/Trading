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
