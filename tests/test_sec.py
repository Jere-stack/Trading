"""Tests for the SEC fundamentals client and name matching.

The matching rules decide which company's accounts are grafted onto which
price series. A wrong match is worse than no match: it produces a confident,
clean-looking number about the wrong firm.
"""

from __future__ import annotations

import gzip
import json

import pytest
from scripts.n13_map_ciks import base_ticker, vendor_mislabel

from tradelab.data.sec import SecClient, name_similarity, normalise_name


class TestNormaliseName:
    def test_vendor_spellings_of_one_company_agree(self):
        assert normalise_name("Apple Inc.") == normalise_name("APPLE INC")

    def test_state_of_incorporation_suffix_is_dropped(self):
        assert normalise_name("AETNA INC /PA/") == normalise_name("Aetna Inc")

    def test_straight_and_curly_apostrophes_both_vanish(self):
        """The gate-0 audit left O'Reilly Automotive unmatched over one glyph."""
        assert normalise_name("O" + chr(0x2019) + "Reilly Automotive Inc") == normalise_name("O'Reilly Automotive")
        assert normalise_name("O'Reilly Automotive") == "oreilly automotive"

    def test_distinct_companies_sharing_a_word_stay_distinct(self):
        assert normalise_name("American Airlines Group") != normalise_name("American Express Co")

    def test_empty_and_none_are_empty(self):
        assert normalise_name(None) == ""
        assert normalise_name("") == ""


class TestNameSimilarity:
    def test_surname_first_filing_matches(self):
        """The SEC files Charles Schwab as SCHWAB CHARLES CORP."""
        a, b = normalise_name("Charles Schwab Corp"), normalise_name("SCHWAB CHARLES CORP")
        assert name_similarity(a, b) == pytest.approx(1.0)

    def test_spacing_differences_match(self):
        assert name_similarity("oreilly automotive", "o reilly automotive") == pytest.approx(1.0)

    def test_unrelated_names_score_low(self):
        assert name_similarity(normalise_name("Microsoft"), normalise_name("Exxon Mobil")) < 0.5

    def test_empty_scores_zero(self):
        assert name_similarity("", "apple") == 0.0


class TestMatchingRules:
    def test_reissued_ticker_suffix_is_stripped(self):
        assert base_ticker("ABX_OLD") == "ABX"
        assert base_ticker("PARA_OLD1") == "PARA"
        assert base_ticker("BRK-B") == "BRK-B"

    def test_foreign_legal_forms_flag_as_mislabel(self):
        assert vendor_mislabel("SBER", "Sberbank of Russia PJSC ADR")
        assert vendor_mislabel("TTB", "TMB Bank Public Co Ltd")
        assert vendor_mislabel("MABANEE", "Mabanee Co KPSC")

    def test_long_tickers_flag_as_mislabel(self):
        """No NYSE or NASDAQ ticker exceeds five characters."""
        assert vendor_mislabel("HUMANSOFT", "HumanSoft Holding")

    def test_ordinary_us_names_are_not_flagged(self):
        assert not vendor_mislabel("AAPL", "Apple Inc.")
        assert not vendor_mislabel("PSCO", "Public Service Co of Colorado")


class TestClientCache:
    def test_cached_response_is_served_without_a_request(self, tmp_path, monkeypatch):
        client = SecClient(cache_dir=tmp_path)
        path = "/submissions/CIK0000000001.json"
        target = client._cache_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(target, "wt", encoding="utf-8") as fh:
            json.dump({"name": "CACHED CO"}, fh)

        def boom(*_a, **_k):
            raise AssertionError("network must not be touched when cached")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert client.get_json(path) == {"name": "CACHED CO"}
        assert client.requests_made == 0

    def test_cached_404_returns_none(self, tmp_path, monkeypatch):
        """A filer with no XBRL is an answer, and it must stay answered."""
        client = SecClient(cache_dir=tmp_path)
        path = "/api/xbrl/companyfacts/CIK0000000002.json"
        client._write(client._cache_path(path), {"__missing__": True})
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
        assert client.get_json(path) is None

    def test_default_user_agent_carries_no_personal_address(self, monkeypatch):
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
        assert "@" not in SecClient().user_agent


class TestRegistryIdentification:
    """The clean-universe rules: identify against the SEC registry, never guess."""

    @staticmethod
    def _index():
        import pandas as pd
        from scripts.build_clean_universe import registry_index

        reg = pd.DataFrame([
            {"cik": 1, "name": "APPLE INC", "frame_name": "Apple Inc.", "tickers": "AAPL",
             "former_names": "APPLE COMPUTER INC"},
            {"cik": 2, "name": "Capri Holdings Ltd", "frame_name": "Capri Holdings Ltd",
             "tickers": "CPRI", "former_names": "Michael Kors Holdings Ltd"},
            {"cik": 3, "name": "NEWCO ABX INC", "frame_name": "NEWCO ABX INC", "tickers": "ABX",
             "former_names": ""},
        ])
        return registry_index(reg)

    @staticmethod
    def _row(symbol, name, delisted):
        from types import SimpleNamespace

        from scripts.n13_map_ciks import base_ticker

        return SimpleNamespace(symbol=symbol, ticker=base_ticker(symbol), name=name, delisted=delisted)

    def test_listed_symbol_is_identified_by_current_ticker(self):
        from scripts.build_clean_universe import identify

        cik, method, _ = identify(self._row("AAPL", "Apple Inc", False), *self._index())
        assert (cik, method) == (1, "ticker")

    def test_renamed_delisted_firm_is_found_under_its_former_name(self):
        from scripts.build_clean_universe import identify

        cik, method, _ = identify(self._row("KORS", "Michael Kors Holdings Limited", True), *self._index())
        assert (cik, method) == (2, "name")

    def test_reissued_ticker_is_not_grafted_onto_a_delisted_symbol(self):
        """ABX_OLD was Barrick; the current holder of ABX is someone else."""
        from scripts.build_clean_universe import identify

        cik, method, _ = identify(self._row("ABX_OLD", "Barrick Gold Corporation", True), *self._index())
        assert cik != 3
        assert method == "unresolved"

    def test_known_contaminant_is_never_identified(self):
        from scripts.build_clean_universe import identify

        cik, method, _ = identify(self._row("SBER", "Sberbank of Russia PJSC ADR", True), *self._index())
        assert (cik, method) == (None, "contaminant")

    def test_code_without_a_real_name_is_a_contaminant(self):
        from scripts.build_clean_universe import identify

        cik, method, _ = identify(self._row("SQ", "SQ", True), *self._index())
        assert (cik, method) == (None, "contaminant")


class TestClientRetries:
    def test_dropped_connection_is_retried_not_fatal(self, tmp_path, monkeypatch):
        import http.client
        import io

        calls = {"n": 0}

        class Response(io.BytesIO):
            def __init__(self, data: bytes) -> None:
                super().__init__(data)
                self.headers: dict = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def flaky(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http.client.RemoteDisconnected("closed without response")
            return Response(b'{"ok": true}')

        monkeypatch.setattr("urllib.request.urlopen", flaky)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        client = SecClient(cache_dir=tmp_path, min_interval=0)
        assert client.get_json("/submissions/CIK0000000003.json") == {"ok": True}
        assert calls["n"] == 2
