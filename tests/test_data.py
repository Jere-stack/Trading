"""Tests for the data layer.

Data bugs do not crash -- they change the answer, and almost always in the
flattering direction. These tests construct data with known defects and assert
that each is caught, and construct data with a known spread and assert it is
recovered.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradelab.data.calibration import (
    SpreadEstimator,
    abdi_ranaldo_spread,
    build_instruments,
    calibrate,
    corwin_schultz_spread,
    screen_universe,
)
from tradelab.data.quality import DataQualityError, Severity, audit
from tradelab.data.schema import (
    Adjustment,
    BarSetMetadata,
    SchemaError,
    normalise_bars,
    validate_bars,
)
from tradelab.data.store import BarStore


def make_frame(symbol="TEST", n=300, seed=1, **defects):
    rng = np.random.default_rng(seed)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.012, n))
    volume = np.full(n, 1_000_000.0)

    if (split_at := defects.get("split_at")) is not None:
        prices[split_at:] /= 4
        volume[split_at:] *= 4
    if (stale_at := defects.get("stale_at")) is not None:
        prices[stale_at : stale_at + 8] = prices[stale_at]
    if (outlier_at := defects.get("outlier_at")) is not None:
        prices[outlier_at] *= 1.8
    if defects.get("zero_volume"):
        volume[::3] = 0.0

    dates = pd.bdate_range("2024-01-01", periods=n)
    if (truncate := defects.get("truncate")) is not None:
        prices, volume, dates = prices[:truncate], volume[:truncate], dates[:truncate]

    return pd.DataFrame(
        {
            "date": dates,
            "symbol": symbol,
            "open": prices * 0.999,
            "high": prices * 1.008,
            "low": prices * 0.992,
            "close": prices,
            "volume": volume,
        }
    )


def canonical(*frames):
    return normalise_bars(pd.concat(frames, ignore_index=True), tz="UTC")


class TestSchema:
    def test_vendor_frame_is_normalised(self):
        """Pins: provider column names and naive local times must be converted."""
        raw = pd.DataFrame(
            {
                "Date": ["2026-01-02", "2026-01-05"],
                "Open": [10.0, 11.0],
                "High": [11.0, 12.0],
                "Low": [9.5, 10.5],
                "Close": [10.5, 11.5],
                "Volume": [1000, 2000],
            }
        )
        out = normalise_bars(raw, symbol="aapl", tz="America/New_York")
        assert list(out.columns)[:7] == [
            "timestamp",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        assert str(out["timestamp"].dt.tz) == "UTC"
        assert out["symbol"].iloc[0] == "AAPL"
        # 2026-01-02 midnight New York is 05:00 UTC, not midnight UTC.
        assert out["timestamp"].iloc[0].hour == 5

    def test_naive_timestamps_rejected_after_storage(self):
        """Pins: naive timestamps are the usual route to off-by-one-session bugs."""
        frame = canonical(make_frame())
        frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)
        with pytest.raises(SchemaError, match="timezone-naive"):
            validate_bars(frame)

    def test_ohlc_violation_rejected(self):
        raw = pd.DataFrame(
            {
                "date": ["2026-01-02"],
                "open": [10.0],
                "high": [9.0],
                "low": [9.5],
                "close": [10.5],
                "volume": [100],
            }
        )
        with pytest.raises(SchemaError, match="OHLC"):
            normalise_bars(raw, symbol="X", tz="UTC")

    def test_nan_price_rejected_not_filled(self):
        """Pins: a missing price is not a zero price and must not be forward-filled."""
        raw = pd.DataFrame(
            {
                "date": ["2026-01-02"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.5],
                "close": [None],
                "volume": [100],
            }
        )
        with pytest.raises(SchemaError, match="NaN"):
            normalise_bars(raw, symbol="X", tz="UTC")

    def test_duplicate_rows_rejected(self):
        """Pins: duplicates double-count returns and inflate every statistic."""
        raw = pd.concat([make_frame(n=5), make_frame(n=5)], ignore_index=True)
        with pytest.raises(SchemaError, match="duplicate"):
            normalise_bars(raw, tz="UTC")

    def test_missing_symbol_is_not_guessed(self):
        with pytest.raises(SchemaError, match="refusing to guess"):
            normalise_bars(make_frame().drop(columns=["symbol"]), tz="UTC")


class TestQuality:
    def test_survivorship_bias_is_critical(self):
        """Pins the most damaging data problem: a universe where everything survived."""
        frame = canonical(*[make_frame(f"S{i}", seed=i) for i in range(5)])
        report = audit(frame, adjustment=Adjustment.SPLIT_AND_DIVIDEND)
        assert not report.is_usable
        assert any(i.check == "survivorship" for i in report.critical)

    def test_universe_with_delisting_passes_survivorship(self):
        """A genuine universe has names whose series end early."""
        frames = [make_frame(f"S{i}", seed=i) for i in range(4)]
        frames.append(make_frame("DEAD", seed=9, truncate=200))
        report = audit(canonical(*frames), adjustment=Adjustment.SPLIT_AND_DIVIDEND)
        assert not any(i.check == "survivorship" for i in report.issues)

    def test_unadjusted_split_is_critical(self):
        """Pins: a 4:1 split reads as -75% and gets bought by any reversal rule."""
        frames = [
            make_frame("A", seed=1),
            make_frame("B", seed=2, split_at=150),
            make_frame("DEAD", seed=3, truncate=100),
        ]
        report = audit(canonical(*frames), adjustment=Adjustment.NONE)
        splits = [i for i in report.issues if i.check == "unadjusted_split"]
        assert splits and splits[0].severity is Severity.CRITICAL
        assert splits[0].symbol == "B"

    def test_split_is_only_a_warning_when_data_claims_adjustment(self):
        frames = [make_frame("B", seed=2, split_at=150), make_frame("DEAD", seed=3, truncate=100)]
        report = audit(canonical(*frames), adjustment=Adjustment.SPLIT_AND_DIVIDEND)
        splits = [i for i in report.issues if i.check == "unadjusted_split"]
        assert splits and splits[0].severity is Severity.WARNING

    def test_stale_prices_flagged(self):
        frames = [make_frame("C", seed=4, stale_at=100), make_frame("DEAD", seed=5, truncate=100)]
        report = audit(canonical(*frames), adjustment=Adjustment.SPLIT_AND_DIVIDEND)
        assert any(i.check == "stale_price" for i in report.issues)

    def test_outlier_flagged(self):
        frames = [make_frame("E", seed=6, outlier_at=200), make_frame("DEAD", seed=7, truncate=100)]
        report = audit(canonical(*frames), adjustment=Adjustment.SPLIT_AND_DIVIDEND)
        assert any(i.check == "outlier_return" for i in report.issues)

    def test_pervasive_zero_volume_is_critical(self):
        frames = [
            make_frame("Z", seed=8, zero_volume=True),
            make_frame("DEAD", seed=9, truncate=100),
        ]
        report = audit(canonical(*frames), adjustment=Adjustment.SPLIT_AND_DIVIDEND)
        zeros = [i for i in report.issues if i.check == "zero_volume"]
        assert zeros and zeros[0].severity is Severity.CRITICAL

    def test_raise_if_unusable_blocks_bad_data(self):
        frame = canonical(*[make_frame(f"S{i}", seed=i) for i in range(5)])
        report = audit(frame, adjustment=Adjustment.SPLIT_AND_DIVIDEND)
        with pytest.raises(DataQualityError, match="refusing to proceed"):
            report.raise_if_unusable()

    def test_calendar_gaps_detected(self):
        frame = canonical(make_frame("G", n=200), make_frame("DEAD", seed=2, truncate=100))
        gapped = frame[~frame["timestamp"].dt.day.isin([10, 11, 12])]
        calendar = pd.bdate_range("2024-01-01", periods=200)
        report = audit(gapped, adjustment=Adjustment.SPLIT_AND_DIVIDEND, expected_calendar=calendar)
        assert any(i.check == "calendar_gap" for i in report.issues)


class TestSpreadEstimators:
    """The estimators decide every cost figure, so they are validated against
    known injected spreads rather than trusted because they are published."""

    @staticmethod
    def simulate(true_bps, n_days=1500, sigma_daily=0.015, steps=390, seed=11):
        """`steps` is intraday trade count. 390 represents an actively traded
        name -- one trade per minute of a US session. The estimator's accuracy
        depends on it, which `test_thin_trading_understates_spread` pins."""
        rng = np.random.default_rng(seed)
        half = true_bps / 10_000.0 / 2.0
        per_step = sigma_daily / np.sqrt(steps)
        highs, lows, closes = np.empty(n_days), np.empty(n_days), np.empty(n_days)
        price = 100.0
        for t in range(n_days):
            path = price * np.exp(np.cumsum(rng.normal(0, per_step, steps)))
            side = rng.choice([-1.0, 1.0], size=steps)
            observed = path * (1 + side * half)
            highs[t], lows[t], closes[t] = observed.max(), observed.min(), observed[-1]
            price = path[-1]
        return highs, lows, closes

    @pytest.mark.parametrize("true_bps", [10, 25, 50, 100, 200])
    def test_abdi_ranaldo_recovers_known_spread(self, true_bps):
        """Pins the default estimator's accuracy across the realistic range."""
        highs, lows, closes = self.simulate(true_bps)
        estimated = abdi_ranaldo_spread(highs, lows, closes) * 10_000
        assert estimated == pytest.approx(true_bps, abs=max(6.0, true_bps * 0.15))

    def test_corwin_schultz_has_a_bias_floor(self):
        """Pins the finding that made Abdi-Ranaldo the default.

        Corwin-Schultz reports ~60 bps for a genuinely 5 bps instrument. A
        calibration using it would exceed the 35 bps one-way cost budget and
        reject the entire liquid universe as untradable -- silently, and with
        the appearance of rigour.
        """
        highs, lows, closes = self.simulate(5)
        cs = corwin_schultz_spread(highs, lows) * 10_000
        ar = abdi_ranaldo_spread(highs, lows, closes) * 10_000
        assert cs > 40, f"expected the documented upward bias, got {cs:.1f}"
        assert ar < 15, f"Abdi-Ranaldo should stay near the truth, got {ar:.1f}"

    def test_max_of_both_inherits_the_bias(self):
        """Pins why 'take the more conservative estimate' is unsafe here.

        Taking a maximum is only conservative when both estimators are
        unbiased. When one has a systematic floor, the maximum inherits the
        bias rather than the caution.
        """
        highs, lows, closes = self.simulate(10)
        frame = pd.DataFrame(
            {
                "timestamp": pd.date_range("2020-01-01", periods=len(highs), tz="UTC"),
                "symbol": "SIM",
                "open": closes,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": np.full(len(highs), 1e6),
            }
        )
        ar = calibrate(frame, lookback=None, estimator=SpreadEstimator.ABDI_RANALDO)
        both = calibrate(frame, lookback=None, estimator=SpreadEstimator.MAX_OF_BOTH)

        # The raw estimate still carries the bias...
        assert both["SIM"].spread_bps_cs > ar["SIM"].spread_bps_ar * 2
        # ...but the liquidity plausibility guard now catches it and falls back
        # rather than letting a number wrong by 4x reach the cost model.
        assert not both["SIM"].spread_reliable
        assert both["SIM"].spread_source == "adv_prior"
        assert ar["SIM"].spread_reliable

    def test_thin_trading_understates_spread(self):
        """Pins the estimator's dangerous failure mode.

        With few intraday trades the high-low mid stops approximating the
        efficient price and Abdi-Ranaldo UNDERSTATES the spread -- at a small
        true spread it can collapse to zero. That is the flattering direction,
        and it occurs precisely for illiquid names where the real cost is worst.

        This is why `screen_universe` rejects on ADV participation
        independently: a thin name must not qualify on an optimistic spread.
        """
        thin_highs, thin_lows, thin_closes = self.simulate(25, steps=200)
        liquid_highs, liquid_lows, liquid_closes = self.simulate(25, steps=390)
        thin = abdi_ranaldo_spread(thin_highs, thin_lows, thin_closes) * 10_000
        liquid = abdi_ranaldo_spread(liquid_highs, liquid_lows, liquid_closes) * 10_000
        assert thin < 15, f"expected the documented understatement, got {thin:.1f}"
        assert liquid == pytest.approx(25, abs=6)

    def test_insufficient_data_returns_nan_not_a_guess(self):
        assert np.isnan(corwin_schultz_spread(np.array([100.0]), np.array([99.0])))
        assert np.isnan(abdi_ranaldo_spread(*[np.array([100.0])] * 3))


class TestCalibration:
    def test_produces_instruments_with_measured_parameters(self):
        frame = canonical(make_frame("AAA", seed=1), make_frame("BBB", seed=2))
        stats = calibrate(frame)
        assert set(stats) == {"AAA", "BBB"}
        instruments = build_instruments(stats, currency="EUR")
        for instrument in instruments.values():
            assert instrument.spread_bps is not None
            assert instrument.adv is not None and instrument.adv > 0
            assert instrument.sigma_daily is not None and instrument.sigma_daily > 0

    def test_calibrated_instruments_satisfy_require_calibrated_spread(self):
        """The point of calibration: the cost model no longer has to guess."""
        from decimal import Decimal

        from tradelab.core.enums import Side
        from tradelab.costs.slippage import SpreadImpactSlippage

        frame = canonical(make_frame("AAA", seed=1))
        instrument = build_instruments(calibrate(frame), currency="EUR")["AAA"]
        model = SpreadImpactSlippage(require_calibrated_spread=True)
        estimate = model.estimate(instrument, Side.BUY, Decimal("100"), Decimal("100"))
        assert estimate.total_bps > 0

    def test_short_history_is_skipped_not_guessed(self):
        frame = canonical(make_frame("SHORT", n=30))
        assert calibrate(frame, min_bars=60) == {}

    def test_adv_uses_median_to_resist_volume_spikes(self):
        """A rebalance-day volume spike must not inflate assumed liquidity."""
        raw = make_frame("SPIKE", n=300)
        raw.loc[150, "volume"] = 500_000_000.0
        stats = calibrate(canonical(raw))
        assert stats["SPIKE"].adv_shares == pytest.approx(1_000_000.0, rel=0.01)


class TestUniverseScreen:
    def test_wide_spread_names_are_rejected_on_cost(self):
        frame = canonical(make_frame("LIQUID", seed=1))
        stats = calibrate(frame)
        # Force a wide spread to represent an illiquid name.
        from dataclasses import replace

        stats["WIDE"] = replace(stats["LIQUID"], symbol="WIDE", spread_bps=300.0)
        screen = screen_universe(stats, position_notional=1000.0, max_cost_bps=35.0)
        assert "WIDE" in [s.symbol for s, _ in screen.rejected]
        reasons = {s.symbol: reason for s, reason in screen.rejected}
        assert reasons["WIDE"].startswith("cost")

    def test_thin_names_rejected_on_capacity(self):
        from dataclasses import replace

        frame = canonical(make_frame("LIQUID", seed=1))
        stats = calibrate(frame)
        stats["THIN"] = replace(stats["LIQUID"], symbol="THIN", adv_shares=500.0)
        screen = screen_universe(stats, position_notional=1000.0)
        reasons = {s.symbol: reason for s, reason in screen.rejected}
        assert reasons.get("THIN", "").startswith("capacity")


class TestStore:
    def test_roundtrip_preserves_data_and_metadata(self, tmp_path):
        from datetime import UTC, datetime

        store = BarStore(tmp_path)
        frame = canonical(make_frame("AAA", seed=1), make_frame("BBB", seed=2))
        metadata = BarSetMetadata(
            provider="test",
            adjustment=Adjustment.SPLIT_AND_DIVIDEND,
            bar_size="1 day",
            currency="EUR",
            fetched_at=datetime.now(UTC),
            symbols=("AAA", "BBB"),
            includes_delisted=True,
        )
        store.write("demo", frame, metadata)
        back = store.read("demo")
        assert len(back) == len(frame)
        restored = store.metadata("demo")
        assert restored.adjustment is Adjustment.SPLIT_AND_DIVIDEND
        assert restored.includes_delisted is True

    def test_overwrite_requires_explicit_opt_in(self, tmp_path):
        """Pins: mixing adjustment policies creates a phantom return at the splice."""
        from datetime import UTC, datetime

        store = BarStore(tmp_path)
        frame = canonical(make_frame("AAA"))
        metadata = BarSetMetadata("t", Adjustment.NONE, "1 day", "EUR", datetime.now(UTC), ("AAA",))
        store.write("demo", frame, metadata)
        with pytest.raises(FileExistsError, match="phantom return"):
            store.write("demo", frame, metadata)
        store.write("demo", frame, metadata, overwrite=True)

    def test_missing_metadata_is_an_error_not_a_default(self, tmp_path):
        """Provenance is required: without it, results cannot be trusted."""
        store = BarStore(tmp_path)
        (store.dataset_path("orphan")).mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="Provenance is required"):
            store.metadata("orphan")

    def test_to_bars_avoids_float_error_in_the_ledger(self, tmp_path):
        from decimal import Decimal

        store = BarStore(tmp_path)
        frame = canonical(make_frame("AAA", n=70))
        bars = store.to_bars(frame.head(1))
        assert isinstance(bars[0].close, Decimal)
        # Decimal(str(x)) must not inherit binary float representation error.
        assert "0000000" not in str(bars[0].close)


class TestSpreadReliability:
    """The high-low estimators fail in two directions on real data.

    Both were found by running against genuine prices, not in simulation, and
    both are dangerous: one reports a liquid stock as free to trade, the other
    reports it as untradable.
    """

    def test_collapse_to_zero_is_caught(self):
        """Pins the mega-cap case: Abdi-Ranaldo returned 0.0 bps for MSFT/AAPL.

        For a mega-cap the spread (~1 bp) is ~200x smaller than daily
        volatility (~200 bps), so it is buried. Reporting 0.0 says "free to
        trade", which is the flattering direction.
        """
        from tradelab.data.calibration import assess_spread_reliability

        reliable, note = assess_spread_reliability(0.0, adv_currency=12e9)
        assert not reliable
        assert "collapsed" in note

    def test_volatility_mistaken_for_spread_is_caught(self):
        """Pins the Tesla case: 107.7 bps estimated where the truth is 1-2 bps."""
        from tradelab.data.calibration import assess_spread_reliability

        reliable, note = assess_spread_reliability(107.7, adv_currency=24e9)
        assert not reliable
        assert "liquidity prior" in note

    def test_plausible_estimate_is_accepted(self):
        from tradelab.data.calibration import assess_spread_reliability

        reliable, _ = assess_spread_reliability(12.0, adv_currency=3e7)
        assert reliable

    def test_prior_decreases_with_liquidity(self):
        from tradelab.data.calibration import spread_prior_from_adv

        priors = [spread_prior_from_adv(v) for v in (1e10, 1e9, 1e8, 1e7, 1e6, 1e4)]
        assert priors == sorted(priors)
        assert priors[0] <= 2.0  # mega-cap ~1 bp
        assert priors[-1] >= 100.0  # micro-cap wide

    def test_fallback_is_recorded_never_silent(self):
        """A substituted value must be traceable, or the cost model lies quietly."""
        import numpy as np
        import pandas as pd

        from tradelab.data.calibration import calibrate

        # A mega-cap-like series: huge volume, tight range -> estimator collapses.
        rng = np.random.default_rng(5)
        n = 300
        prices = 300 * np.cumprod(1 + rng.normal(0, 0.02, n))
        frame = pd.DataFrame(
            {
                "timestamp": pd.date_range("2024-01-01", periods=n, tz="UTC"),
                "symbol": "MEGA",
                "open": prices,
                "high": prices * 1.0001,
                "low": prices * 0.9999,
                "close": prices,
                "volume": np.full(n, 5e7),
            }
        )
        stats = calibrate(frame, lookback=None)["MEGA"]
        assert not stats.spread_reliable
        assert stats.spread_source == "adv_prior"
        assert stats.spread_note
        # The raw estimate is retained for inspection.
        assert stats.spread_bps != stats.spread_bps_ar
