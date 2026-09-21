"""Universe construction tests.

The rules here decide which symbols exist at all, so a mistake is invisible in
every downstream number. Each test names the contamination or the bias it
prevents.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradelab.research.universe import (
    filter_report,
    is_us_common_stock,
    survivorship_check,
    us_common_stocks,
)


def row(code, name="Some Co Inc", type_="Common Stock", exchange="NASDAQ", isin="US0000000001"):
    return pd.Series(
        {"Code": code, "Name": name, "Type": type_, "Exchange": exchange, "Isin": isin}
    )


class TestExclusions:
    def test_an_etf_is_excluded(self):
        assert not is_us_common_stock(row("SPY", "SPDR S&P 500 ETF", type_="ETF"))

    def test_an_otc_listing_is_excluded(self):
        """OTC spreads are far outside anything the cost model was calibrated on."""
        assert not is_us_common_stock(row("XYZ", exchange="PINK"))
        assert not is_us_common_stock(row("XYZ", exchange="OTCGREY"))

    def test_the_mutual_fund_quotation_service_is_excluded(self):
        """NMFQS carries 47,722 symbols that are not stocks at all."""
        assert not is_us_common_stock(row("XYZ", exchange="NMFQS"))

    @pytest.mark.parametrize("code", ["JPM-P-D", "MTB-P-J", "AAC-WT", "AAC-U", "AAC-UN", "ISF-CL"])
    def test_preferred_warrant_and_unit_tickers_are_excluded(self, code):
        """Prevents: JPMorgan's preferred series D entering as 'JPMorgan Chase & Co'.

        EODHD labels these Common Stock, so the Type field cannot be trusted
        and the ticker pattern has to do the work.
        """
        assert not is_us_common_stock(row(code))

    @pytest.mark.parametrize(
        "name",
        [
            "BlackRock Municipal Target Term Trust",
            "Western Asset Diversified Income Fund",
            "Some Index Portfolio",
            "Acme Warrant",
        ],
    )
    def test_funds_and_trusts_are_excluded_by_name(self, name):
        """BTT and WDI both carry US ISINs and the Common Stock label, and
        both are closed-end funds."""
        assert not is_us_common_stock(row("XYZ", name=name))


class TestInclusions:
    def test_an_ordinary_common_stock_is_kept(self):
        assert is_us_common_stock(row("AAPL", "Apple Inc"))

    def test_a_collapsed_company_is_kept(self):
        """Prevents: re-introducing survivorship bias through data hygiene.

        Companies that fell 99% and reverse-split leave a high adjusted price.
        They are real US common stocks and real losses, and excluding them
        because the numbers look strange is the exact bias the survivorship-free
        universe was bought to remove.
        """
        assert is_us_common_stock(row("ACOR", "Acorda Therapeutics Inc"))

    def test_a_missing_isin_does_not_exclude(self):
        """The ISIN rule was removed for cause: coverage is 73.3% on live names
        and 24.9% on delisted ones, so requiring one deletes failures."""
        assert is_us_common_stock(row("XYZ", isin=None))
        assert is_us_common_stock(row("XYZ", isin=float("nan")))


class TestSurvivorshipCheck:
    def test_a_survival_correlated_rule_is_detected(self):
        """The check must actually catch the bias it exists to catch."""
        live = [
            {**row(f"L{i}").to_dict(), "delisted": False, "Isin": "US0000000001"}
            for i in range(100)
        ]
        # Every delisted name is given a fund-like name, so the filter removes
        # them all -- a rule perfectly correlated with survival.
        dead = [
            {**row(f"D{i}", name="Something Trust").to_dict(), "delisted": True} for i in range(100)
        ]
        result = survivorship_check(pd.DataFrame(live + dead))
        gap = result.loc["live", "keep_rate"] - result.loc["delisted", "keep_rate"]
        assert gap > 0.9, "a perfectly survival-correlated rule must show a large gap"

    def test_a_neutral_rule_shows_no_gap(self):
        rows = [{**row(f"S{i}").to_dict(), "delisted": i % 2 == 0} for i in range(100)]
        result = survivorship_check(pd.DataFrame(rows))
        gap = result.loc["live", "keep_rate"] - result.loc["delisted", "keep_rate"]
        assert abs(gap) < 0.01

    def test_a_missing_delisted_column_is_refused(self):
        """Without it the check silently passes and the bias goes unseen."""
        with pytest.raises(ValueError, match="delisted"):
            survivorship_check(pd.DataFrame([row("A").to_dict()]))


class TestReporting:
    def test_the_filter_report_accounts_for_every_symbol(self):
        rows = [
            {**row("AAPL", "Apple Inc").to_dict(), "delisted": False},
            {**row("SPY", "SPDR ETF", type_="ETF").to_dict(), "delisted": False},
            {**row("XYZ", exchange="PINK").to_dict(), "delisted": False},
            {**row("JPM-P-D").to_dict(), "delisted": False},
            {**row("BTT", "Municipal Trust").to_dict(), "delisted": False},
        ]
        frame = pd.DataFrame(rows)
        report = filter_report(frame)
        assert report.iloc[0]["remaining"] == 5
        assert report.iloc[-1]["remaining"] == len(us_common_stocks(frame)) == 1
