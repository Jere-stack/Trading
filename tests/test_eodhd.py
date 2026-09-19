"""Tests for the EODHD provider.

The HTTP layer is stubbed rather than hit, so the suite stays fast, offline and
free of API-call consumption. The response shapes below are copied from real
demo-token responses.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from tradelab.data.providers.base import ProviderError
from tradelab.data.providers.eodhd import (
    EXCHANGE_CODES,
    EodhdProvider,
    adjustment_distortion,
)
from tradelab.data.schema import Adjustment

START = datetime(2024, 1, 2, tzinfo=UTC)
END = datetime(2024, 1, 10, tzinfo=UTC)

# Shape taken from a real /api/eod response. `close` and `adjusted_close`
# differ, which is the case the provider has to handle correctly.
EOD_ROWS = [
    {
        "date": "2024-01-02",
        "open": 295.05,
        "high": 297.28,
        "low": 295.05,
        "close": 297.04,
        "adjusted_close": 277.9465,
        "volume": 4458400,
    },
    {
        "date": "2024-01-03",
        "open": 297.0,
        "high": 297.99,
        "low": 294.25,
        "close": 294.39,
        "adjusted_close": 275.4668,
        "volume": 3114800,
    },
    {
        "date": "2024-01-04",
        "open": 295.32,
        "high": 297.27,
        "low": 290.92,
        "close": 291.74,
        "adjusted_close": 272.9872,
        "volume": 4615400,
    },
]


class StubProvider(EodhdProvider):
    """EodhdProvider with the network replaced by canned responses."""

    def __init__(self, responses: dict, **kwargs):
        kwargs.setdefault("api_token", "test-token")
        kwargs.setdefault("pacing_seconds", 0.0)
        super().__init__(**kwargs)
        self.responses = responses
        self.calls: list[str] = []

    def _get(self, path, **params):
        self.calls.append(path)
        value = self.responses.get(path)
        if value is None:
            raise ProviderError(f"stub has no response for {path}", retryable=False)
        if isinstance(value, Exception):
            raise value
        return value


class TestCredentials:
    def test_missing_token_is_refused(self, monkeypatch):
        monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
        with pytest.raises(ProviderError, match="no EODHD API token"):
            EodhdProvider(api_token=None)

    def test_token_read_from_environment(self, monkeypatch):
        """Pins that the token can stay out of code and shell history."""
        monkeypatch.setenv("EODHD_API_TOKEN", "from-env")
        assert EodhdProvider().api_token == "from-env"


class TestAdjustment:
    def test_adjusted_scales_the_whole_bar(self):
        """Pins: scaling close alone would put it outside the high-low range."""
        provider = StubProvider({"eod/MCD.US": EOD_ROWS})
        frame = provider.fetch(["MCD"], START, END)
        assert (frame["high"] >= frame["close"]).all()
        assert (frame["low"] <= frame["close"]).all()
        assert (frame["high"] >= frame["open"]).all()
        # The adjusted close must match what the API reported.
        assert frame["close"].iloc[0] == pytest.approx(277.9465, abs=0.001)

    def test_unadjusted_returns_what_actually_traded(self):
        provider = StubProvider({"eod/MCD.US": EOD_ROWS}, adjustment=Adjustment.NONE)
        frame = provider.fetch(["MCD"], START, END)
        assert frame["close"].iloc[0] == pytest.approx(297.04, abs=0.001)

    def test_both_price_views_are_retained(self):
        """Pins: the cost model needs traded prices, signals need adjusted ones."""
        provider = StubProvider({"eod/MCD.US": EOD_ROWS})
        frame = provider.fetch(["MCD"], START, END)
        assert "unadjusted_close" in frame.columns
        assert "adjustment_factor" in frame.columns
        assert frame["unadjusted_close"].iloc[0] == pytest.approx(297.04, abs=0.001)

    def test_distortion_is_measurable_not_assumed(self):
        """Pins the bounded error adjusted prices introduce into cost models."""
        provider = StubProvider({"eod/MCD.US": EOD_ROWS})
        frame = provider.fetch(["MCD"], START, END)
        report = adjustment_distortion(frame)
        assert report.loc["MCD", "min_factor"] == pytest.approx(0.9357, abs=0.001)
        # ~6.9% more shares for the same notional at the adjusted price.
        assert 5.0 < report.loc["MCD", "commission_error_pct"] < 9.0

    def test_distortion_requires_the_factor_column(self):
        with pytest.raises(ValueError, match="adjustment_factor"):
            adjustment_distortion(pd.DataFrame({"symbol": ["X"], "close": [1.0]}))


class TestUniverse:
    def test_survivorship_free_universe_includes_delisted(self):
        """The whole reason for this subscription."""
        provider = StubProvider(
            {
                "exchange-symbol-list/US": [
                    {"Code": "AAPL", "Name": "Apple"},
                    {"Code": "MSFT", "Name": "Microsoft"},
                ]
            }
        )
        # Active and delisted hit the same path; return different sets per call.
        responses = iter(
            [
                [{"Code": "AAPL", "Name": "Apple"}, {"Code": "MSFT", "Name": "Microsoft"}],
                [{"Code": "ENRN", "Name": "Enron"}, {"Code": "LEH", "Name": "Lehman"}],
            ]
        )
        provider._get = lambda path, **kw: next(responses)
        universe = provider.survivorship_free_universe("US")
        codes = set(universe["Code"])
        assert {"AAPL", "MSFT", "ENRN", "LEH"} == codes
        assert universe["delisted"].sum() == 2

    def test_delisted_failure_is_not_silently_downgraded(self):
        """Pins: falling back to active-only would reintroduce the exact bias
        this provider exists to remove."""
        calls = iter(
            [
                [{"Code": "AAPL"}],
                ProviderError("403 forbidden", retryable=False),
            ]
        )

        def fake_get(path, **kw):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        provider = StubProvider({})
        provider._get = fake_get
        with pytest.raises(ProviderError, match="survivorship-biased"):
            provider.survivorship_free_universe("US")

    def test_helsinki_is_configured(self):
        assert EXCHANGE_CODES["HE"] == "Nasdaq Helsinki"
        assert "ST" in EXCHANGE_CODES and "XETRA" in EXCHANGE_CODES


class TestFetchBehaviour:
    def test_partial_failure_is_refused(self):
        """Pins: silently dropping symbols is survivorship bias at the fetch step."""
        provider = StubProvider({"eod/GOOD.US": EOD_ROWS})
        with pytest.raises(ProviderError, match="survivorship bias at the fetch step"):
            provider.fetch(["GOOD", "MISSING"], START, END)

    def test_total_failure_reports_every_reason(self):
        provider = StubProvider({})
        with pytest.raises(ProviderError, match="no symbols returned data"):
            provider.fetch(["A", "B"], START, END)

    def test_suffixed_and_bare_symbols_both_work(self):
        provider = StubProvider({"eod/NOKIA.HE": EOD_ROWS}, exchange="HE")
        bare = provider.fetch(["NOKIA"], START, END)
        suffixed = provider.fetch(["NOKIA.HE"], START, END)
        assert bare["symbol"].iloc[0] == "NOKIA"
        assert suffixed["symbol"].iloc[0] == "NOKIA"

    def test_intraday_is_refused(self):
        provider = StubProvider({"eod/X.US": EOD_ROWS})
        with pytest.raises(ProviderError, match="daily bars only"):
            provider.fetch(["X"], START, END, "5 mins")

    def test_reversed_dates_refused(self):
        provider = StubProvider({"eod/X.US": EOD_ROWS})
        with pytest.raises(ProviderError, match="not before"):
            provider.fetch(["X"], END, START)

    def test_missing_columns_surface_clearly(self):
        provider = StubProvider({"eod/X.US": [{"date": "2024-01-02", "close": 10}]})
        with pytest.raises(ProviderError, match="missing columns"):
            provider.fetch(["X"], START, END)


class TestIntegrationWithPipeline:
    def test_fetched_data_flows_into_the_store_and_auditor(self, tmp_path):
        """The provider must produce data the rest of the pipeline accepts."""
        from tradelab.data.quality import audit
        from tradelab.data.schema import BarSetMetadata
        from tradelab.data.store import BarStore

        rows = []
        for i in range(120):
            price = 100.0 + i * 0.1
            rows.append(
                {
                    "date": (pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)).strftime(
                        "%Y-%m-%d"
                    ),
                    "open": price * 0.999,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "adjusted_close": price * 0.95,
                    "volume": 1_000_000,
                }
            )
        # Three symbols minimum: survivorship cannot be inferred from fewer.
        names = ["AAA", "BBB", "CCC"]
        provider = StubProvider({f"eod/{n}.US": rows for n in names})
        frame = provider.fetch(names, datetime(2024, 1, 1, tzinfo=UTC), END)

        store = BarStore(tmp_path)
        metadata = BarSetMetadata(
            provider="eodhd",
            adjustment=Adjustment.SPLIT_AND_DIVIDEND,
            bar_size="1 day",
            currency="USD",
            fetched_at=datetime.now(UTC),
            symbols=tuple(names),
            includes_delisted=True,
        )
        store.write("eodhd-test", frame, metadata)
        back = store.read("eodhd-test")
        assert len(back) == len(frame)
        assert "adjustment_factor" in back.columns

        report = audit(back, adjustment=Adjustment.SPLIT_AND_DIVIDEND)
        # Every symbol ends on the same date, so survivorship must be flagged
        # even though the metadata claims delisted names are included -- the
        # auditor checks the data, not the claim.
        assert any(i.check == "survivorship" for i in report.critical)

    def test_calibration_consumes_eodhd_output(self):
        from tradelab.data.calibration import build_instruments, calibrate

        rows = []
        for i in range(200):
            price = 100.0 * (1 + 0.001 * ((i % 7) - 3))
            rows.append(
                {
                    "date": (pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)).strftime(
                        "%Y-%m-%d"
                    ),
                    "open": price,
                    "high": price * 1.006,
                    "low": price * 0.994,
                    "close": price,
                    "adjusted_close": price,
                    "volume": 2_000_000,
                }
            )
        provider = StubProvider({"eod/AAA.US": rows})
        frame = provider.fetch(["AAA"], datetime(2024, 1, 1, tzinfo=UTC), END)
        stats = calibrate(frame)
        assert "AAA" in stats
        instrument = build_instruments(stats, currency="USD")["AAA"]
        assert instrument.spread_bps is not None
        assert instrument.adv is not None
