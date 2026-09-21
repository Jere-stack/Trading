"""Price-series screening tests.

Each rule here exists because a specific backtest result was already corrupted
by the case it rejects. The tests name the corruption.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.research.screening import (
    MAX_PLAUSIBLE_PRICE,
    longest_flat_run,
    screen_price_series,
)


def series(n: int = 400, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 100 * np.cumprod(1 + rng.normal(0, 0.01, n))


class TestSentinelPrices:
    def test_a_million_dollar_close_is_rejected(self):
        """Prevents: a low-volatility strategy holding twenty placeholders.

        291 symbols in the US universe carry closes of exactly $1,000,000 --
        a placeholder emitted where a split adjustment overflowed. A constant
        series has zero volatility, so a low-vol sort ranks it FIRST. The
        low-volatility backtest returned -2.03%/yr holding these.
        """
        values = series()
        values[50:] = 1_000_000.0
        result = screen_price_series(values)
        assert not result.ok
        assert "sentinel" in result.reason

    def test_the_threshold_is_where_it_is_documented(self):
        """Scaled wholesale, so only the price rule can fire -- injecting a
        single high value would also trip the session-move rule and the test
        would pass for the wrong reason."""
        base = series()
        above = base * (MAX_PLAUSIBLE_PRICE / base.max()) * 1.01
        assert not screen_price_series(above).ok
        below = base * (MAX_PLAUSIBLE_PRICE / base.max()) * 0.5
        assert screen_price_series(below).ok


class TestStaleSeries:
    def test_a_long_flat_run_is_rejected(self):
        """Prevents: a halted stub reading as zero volatility AND a 52-week high.

        Even a quiet utility ticks. 991 symbols have 60+ consecutive identical
        closes; they poison any signal built on volatility or on distance from
        a high.
        """
        values = series()
        values[100:175] = values[100]
        result = screen_price_series(values)
        assert not result.ok
        assert "stale" in result.reason

    def test_a_short_flat_run_is_tolerated(self):
        values = series()
        values[100:110] = values[100]
        assert screen_price_series(values).ok

    def test_flat_run_length_is_measured_correctly(self):
        assert longest_flat_run(np.array([1.0, 1, 1, 2, 2, 2, 2, 3])) == 3
        assert longest_flat_run(np.array([1.0, 2, 3])) == 0
        assert longest_flat_run(np.array([5.0])) == 0

    def test_an_entirely_constant_series_is_rejected(self):
        assert not screen_price_series(np.full(400, 42.0)).ok


class TestImpossibleMoves:
    def test_a_failed_split_adjustment_is_rejected(self):
        """Prevents: POW_OLD 'moving' from $0.005 to $15,600 in one session.

        One such series put +562% into a placebo mean whose median was -0.67%.
        """
        # Scaled so the jump stays well under the sentinel threshold,
        # otherwise the price rule fires first and this asserts nothing about
        # the move rule.
        values = series() / 100.0  # ~$1
        values[200:] *= 1000.0  # -> ~$1,000, a 100,000% single-session move
        result = screen_price_series(values)
        assert not result.ok
        assert "single-session move" in result.reason, result.reason


class TestBasicSanity:
    def test_a_clean_series_passes(self):
        assert screen_price_series(series()).ok

    def test_too_little_history_is_rejected(self):
        assert not screen_price_series(series(100)).ok

    def test_non_positive_and_non_finite_are_rejected(self):
        values = series()
        values[5] = 0.0
        assert not screen_price_series(values).ok
        values = series()
        values[5] = np.nan
        assert not screen_price_series(values).ok

    @pytest.mark.parametrize("seed", range(5))
    def test_ordinary_random_walks_are_not_rejected(self, seed):
        """The screen must not quietly delete most of the universe."""
        assert screen_price_series(series(seed=seed)).ok
