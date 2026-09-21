"""Reporting tests.

The report is the page a number gets believed from, so the tests here are about
refusing to display figures that would mislead.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from tradelab.reporting.metrics import (
    MIN_SESSIONS_FOR_SHARPE,
    EquityPoint,
    max_drawdown,
    strategy_results,
    summarise,
)


def build_state(path, equities, fills=()):
    """A minimal state database: equity curve plus optional fills."""
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE equity_curve (timestamp TEXT PRIMARY KEY, equity TEXT, cash TEXT,
            gross_exposure TEXT, net_exposure TEXT, position_count INTEGER);
        CREATE TABLE fills (fill_id TEXT PRIMARY KEY, order_id TEXT, strategy_id TEXT,
            symbol TEXT, instrument_key TEXT, side TEXT, quantity TEXT, price TEXT,
            commission TEXT, fees TEXT, liquidity TEXT, timestamp TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp TEXT, kind TEXT,
            severity TEXT, message TEXT, payload TEXT);
    """)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for i, equity in enumerate(equities):
        connection.execute(
            "INSERT INTO equity_curve VALUES (?,?,?,?,?,?)",
            ((start + timedelta(days=i)).isoformat(), str(equity), "0", "0", "0", 1),
        )
    for i, (strategy, key, side, quantity, price, commission) in enumerate(fills):
        connection.execute(
            "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"f{i}",
                f"o{i}",
                strategy,
                key.split(".")[0],
                key,
                side,
                str(quantity),
                str(price),
                str(commission),
                "0",
                "ADDED",
                (start + timedelta(days=i)).isoformat(),
            ),
        )
    connection.commit()
    connection.close()
    return path


class TestSharpeSuppression:
    def test_sharpe_is_withheld_below_the_session_floor(self, tmp_path):
        """Prevents: a precise-looking number computed from nothing.

        A displayed figure invites belief in proportion to its decimal places.
        Ten sessions cannot produce a Sharpe ratio worth printing.
        """
        path = build_state(tmp_path / "s.sqlite", [10_000 + i * 10 for i in range(10)])
        assert summarise(path).sharpe is None

    def test_sharpe_appears_once_there_is_enough_history(self, tmp_path):
        equities = [10_000 * (1.0005**i) for i in range(MIN_SESSIONS_FOR_SHARPE + 5)]
        assert summarise(build_state(tmp_path / "s.sqlite", equities)).sharpe is not None

    def test_a_flat_curve_reports_no_sharpe_rather_than_infinity(self, tmp_path):
        equities = [10_000.0] * (MIN_SESSIONS_FOR_SHARPE + 5)
        assert summarise(build_state(tmp_path / "s.sqlite", equities)).sharpe is None


class TestDrawdown:
    def test_drawdown_measures_from_the_running_peak(self, tmp_path):
        curve = [
            EquityPoint(datetime(2025, 1, d, tzinfo=UTC), e, 0, 0, 0, 0)
            for d, e in enumerate([100.0, 120.0, 90.0, 110.0], start=1)
        ]
        values = [round(p.drawdown, 4) for p in max_drawdown(curve)]
        # Peak 120 then 90 is -25%, and recovering to 110 is still -8.33%.
        assert values == [0.0, 0.0, -0.25, pytest.approx(-0.0833, abs=1e-4)]

    def test_worst_drawdown_is_reported_with_its_date(self, tmp_path):
        path = build_state(tmp_path / "s.sqlite", [100.0, 120.0, 90.0, 110.0])
        summary = summarise(path)
        assert summary.max_drawdown == pytest.approx(-0.25)
        assert summary.max_drawdown_date == datetime(2025, 1, 3, tzinfo=UTC)


class TestStrategyResults:
    def test_realised_pnl_uses_average_cost(self, tmp_path):
        """A sale is only a profit relative to what the shares cost."""
        path = build_state(
            tmp_path / "s.sqlite",
            [10_000.0],
            fills=[
                ("s1", "AAPL.SMART.USD", "BUY", 10, 100.0, 1.0),
                ("s1", "AAPL.SMART.USD", "BUY", 10, 120.0, 1.0),
                ("s1", "AAPL.SMART.USD", "SELL", 10, 130.0, 1.0),
            ],
        )
        (result,) = strategy_results(path)
        # Average cost 110, sold 10 at 130 -> 200 realised, less 3 commission.
        assert result.realised == pytest.approx(200.0)
        assert result.net == pytest.approx(197.0)

    def test_currencies_are_never_summed_together(self, tmp_path):
        """Prevents: a total computed at an exchange rate nobody chose.

        Fills carry no FX rate, and the rate that applied on the day of a fill
        is not recoverable from the fills table.
        """
        path = build_state(
            tmp_path / "s.sqlite",
            [10_000.0],
            fills=[
                ("s1", "AAPL.SMART.USD", "BUY", 10, 100.0, 1.0),
                ("s1", "NOKIA.HEL.EUR", "BUY", 10, 4.0, 1.0),
            ],
        )
        results = strategy_results(path)
        assert {r.currency for r in results} == {"USD", "EUR"}
        assert len(results) == 2, "one row per currency, never merged"

    def test_an_unparseable_instrument_key_is_not_guessed(self, tmp_path):
        path = build_state(
            tmp_path / "s.sqlite",
            [10_000.0],
            fills=[("s1", "WEIRD", "BUY", 10, 100.0, 1.0)],
        )
        assert strategy_results(path)[0].currency == "UNKNOWN"

    def test_cost_bps_is_measured_against_value_traded(self, tmp_path):
        path = build_state(
            tmp_path / "s.sqlite",
            [10_000.0],
            fills=[("s1", "AAPL.SMART.USD", "BUY", 10, 100.0, 1.0)],
        )
        # 1.00 of commission on 1,000 traded = 10 bps.
        assert strategy_results(path)[0].cost_bps == pytest.approx(10.0)


class TestEmptyState:
    def test_a_state_with_no_sessions_does_not_crash(self, tmp_path):
        summary = summarise(build_state(tmp_path / "s.sqlite", []))
        assert summary.sessions == 0
        assert summary.sharpe is None
        assert summary.total_return == 0.0
