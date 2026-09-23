"""Point-in-time tests for SEC fundamentals.

The pre-registration for N13 requires one of these by name: no value may be
observable before its filing date. A fundamentals backtest with look-ahead
does not look slightly optimistic; it looks like a discovery.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradelab.research.fundamentals import annual_ratio_rows, as_of, with_predecessors


def _fact(start, end, val, filed, accn, form="10-K"):
    row = {"end": end, "val": val, "filed": filed, "accn": accn, "form": form}
    if start:
        row["start"] = start
    return row


def _facts(tagdata: dict[tuple[str, str], list[dict]], unit: str = "USD") -> dict:
    out: dict = {"facts": {}}
    for (taxonomy, tag), rows in tagdata.items():
        out["facts"].setdefault(taxonomy, {})[tag] = {"units": {unit: rows}}
    return out


def _company(filed="2021-02-20", accn="A1", gp=40.0, assets=100.0, opinc=20.0, equity=50.0):
    return _facts({
        ("us-gaap", "GrossProfit"): [_fact("2020-01-01", "2020-12-31", gp, filed, accn)],
        ("us-gaap", "Assets"): [_fact(None, "2020-12-31", assets, filed, accn)],
        ("us-gaap", "OperatingIncomeLoss"): [_fact("2020-01-01", "2020-12-31", opinc, filed, accn)],
        ("us-gaap", "StockholdersEquity"): [_fact(None, "2020-12-31", equity, filed, accn)],
    })


class TestRatios:
    def test_ratios_are_formed_within_one_filing(self):
        rows = annual_ratio_rows(_company()).frame
        assert len(rows) == 1
        assert rows["profitability"].iloc[0] == pytest.approx(0.40)
        assert rows["quality"].iloc[0] == pytest.approx(0.40)

    def test_gross_profit_is_derived_from_revenue_minus_cost_in_the_same_filing(self):
        facts = _facts({
            ("us-gaap", "Revenues"): [_fact("2020-01-01", "2020-12-31", 100.0, "2021-02-20", "A1")],
            ("us-gaap", "CostOfRevenue"): [_fact("2020-01-01", "2020-12-31", 70.0, "2021-02-20", "A1")],
            ("us-gaap", "Assets"): [_fact(None, "2020-12-31", 60.0, "2021-02-20", "A1")],
            ("us-gaap", "OperatingIncomeLoss"): [_fact("2020-01-01", "2020-12-31", 10.0, "2021-02-20", "A1")],
            ("us-gaap", "StockholdersEquity"): [_fact(None, "2020-12-31", 40.0, "2021-02-20", "A1")],
        })
        assert annual_ratio_rows(facts).frame["profitability"].iloc[0] == pytest.approx(0.5)

    def test_quarterly_flows_are_not_mistaken_for_annual(self):
        facts = _company()
        facts["facts"]["us-gaap"]["GrossProfit"]["units"]["USD"].append(
            _fact("2020-10-01", "2020-12-31", 999.0, "2021-02-20", "A1", form="10-K")
        )
        # The 3-month column in the same filing must not replace the annual one.
        assert annual_ratio_rows(facts).frame["profitability"].iloc[0] == pytest.approx(0.40)

    def test_negative_equity_leaves_quality_undefined(self):
        rows = annual_ratio_rows(_company(equity=-10.0)).frame
        assert pd.isna(rows["quality"].iloc[0])

    def test_ifrs_filer_in_home_currency_gives_the_same_ratio(self):
        facts = _facts({
            ("ifrs-full", "GrossProfit"): [_fact("2020-01-01", "2020-12-31", 4000.0, "2021-04-15", "B1", "20-F")],
            ("ifrs-full", "Assets"): [_fact(None, "2020-12-31", 10000.0, "2021-04-15", "B1", "20-F")],
            ("ifrs-full", "ProfitLossFromOperatingActivities"): [
                _fact("2020-01-01", "2020-12-31", 2000.0, "2021-04-15", "B1", "20-F")],
            ("ifrs-full", "EquityAttributableToOwnersOfParent"): [
                _fact(None, "2020-12-31", 5000.0, "2021-04-15", "B1", "20-F")],
        }, unit="TWD")
        result = annual_ratio_rows(facts)
        assert result.unit == "TWD"
        assert result.frame["profitability"].iloc[0] == pytest.approx(0.40)


class TestPointInTime:
    """The pre-registered test: no value observable before its filing."""

    def test_nothing_is_visible_on_or_before_the_filing_date(self):
        rows = annual_ratio_rows(_company(filed="2021-02-20")).frame
        assert pd.isna(as_of(rows, pd.Timestamp("2021-02-19"), "profitability"))
        assert pd.isna(as_of(rows, pd.Timestamp("2021-02-20"), "profitability")), (
            "a report filed today is usable from the NEXT session, not today"
        )
        assert as_of(rows, pd.Timestamp("2021-02-22"), "profitability") == pytest.approx(0.40)

    def test_a_later_restatement_is_invisible_until_it_is_filed(self):
        merged = _company(filed="2021-02-20", accn="A1", gp=40.0)
        restated = _company(filed="2021-09-01", accn="A2", gp=10.0)
        for tag, spec in restated["facts"]["us-gaap"].items():
            merged["facts"]["us-gaap"][tag]["units"]["USD"].extend(spec["units"]["USD"])
        rows = annual_ratio_rows(merged).frame
        assert as_of(rows, pd.Timestamp("2021-06-30"), "profitability") == pytest.approx(0.40)
        assert as_of(rows, pd.Timestamp("2021-09-02"), "profitability") == pytest.approx(0.10)

    def test_stale_reports_expire(self):
        """A company that stopped filing must not keep ranking on its last report."""
        rows = annual_ratio_rows(_company(filed="2021-02-20")).frame
        assert as_of(rows, pd.Timestamp("2022-06-01"), "profitability") == pytest.approx(0.40)
        assert pd.isna(as_of(rows, pd.Timestamp("2022-08-01"), "profitability"))

    def test_empty_history_is_nan(self):
        assert pd.isna(as_of(pd.DataFrame(), pd.Timestamp("2021-01-01"), "profitability"))


class TestSparseFilers:
    def test_filer_with_only_quarterly_rows_is_empty_not_an_error(self):
        """Found by the smoke run: a balance sheet exists, no annual report does."""
        facts = _facts({
            ("us-gaap", "Assets"): [_fact(None, "2021-03-31", 100.0, "2021-05-10", "Q1", form="10-Q")],
            ("us-gaap", "OperatingIncomeLoss"): [
                _fact("2021-01-01", "2021-03-31", 5.0, "2021-05-10", "Q1", form="10-Q")],
        })
        result = annual_ratio_rows(facts)
        assert result.unit == "USD"
        assert result.frame.empty


class TestPredecessors:
    """A reorganised company keeps its history; the successor's own reports win once filed."""

    def _rows(self, filed, gp, accn):
        return annual_ratio_rows(_company(filed=filed, gp=gp, accn=accn)).frame

    def test_successor_without_reports_uses_predecessor_history(self):
        old = self._rows("2019-02-20", 30.0, "P1")
        merged = with_predecessors({1: old}, {2: 1})
        assert as_of(merged[2], pd.Timestamp("2019-06-30"), "profitability") == pytest.approx(0.30)

    def test_successor_report_takes_over_once_filed(self):
        old = annual_ratio_rows(_facts({
            ("us-gaap", "GrossProfit"): [_fact("2019-01-01", "2019-12-31", 30.0, "2020-02-20", "P1")],
            ("us-gaap", "Assets"): [_fact(None, "2019-12-31", 100.0, "2020-02-20", "P1")],
        })).frame
        new = self._rows("2021-02-20", 50.0, "S1")
        merged = with_predecessors({1: old, 2: new}, {2: 1})
        assert as_of(merged[2], pd.Timestamp("2020-06-30"), "profitability") == pytest.approx(0.30)
        assert as_of(merged[2], pd.Timestamp("2021-03-01"), "profitability") == pytest.approx(0.50)

    def test_chains_are_followed_and_cycles_do_not_hang(self):
        old = self._rows("2019-02-20", 30.0, "P1")
        merged = with_predecessors({1: old}, {3: 2, 2: 1, 1: 3})
        assert as_of(merged[3], pd.Timestamp("2019-06-30"), "profitability") == pytest.approx(0.30)

    def test_unlinked_companies_are_untouched(self):
        rows = self._rows("2019-02-20", 30.0, "P1")
        merged = with_predecessors({1: rows}, {})
        assert list(merged) == [1]
        assert merged[1] is rows
