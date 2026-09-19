"""Tests for price-based event detection.

The detector's value depends entirely on being causal and on not quietly
selecting for the outcome it predicts, so those are what these tests pin.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradelab.research.events import detect_deal_candidates, measure_outcomes


def synth(
    symbol="TEST",
    n=400,
    jump_at=200,
    jump=0.25,
    pre_vol=0.02,
    post_vol=0.002,
    volume_spike=5.0,
    truncate_at=None,
    post_drift=0.0,
    seed=1,
):
    """Build a series with a controllable announcement-then-pin signature."""
    rng = np.random.default_rng(seed)
    price = 50.0
    closes, volumes = [], []
    for i in range(n):
        if i == jump_at:
            price *= 1 + jump
            volumes.append(1_000_000 * volume_spike)
        else:
            vol = post_vol if i > jump_at else pre_vol
            price *= 1 + rng.normal(post_drift if i > jump_at else 0.0, vol)
            volumes.append(1_000_000.0)
        closes.append(price)
    closes = np.array(closes)
    if truncate_at is not None:
        closes, volumes = closes[:truncate_at], volumes[:truncate_at]
    dates = pd.bdate_range("2015-01-01", periods=len(closes), tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": symbol,
            "open": closes,
            "high": closes * 1.002,
            "low": closes * 0.998,
            "close": closes,
            "volume": volumes,
        }
    )


class TestDetection:
    def test_finds_the_announcement_and_pin_signature(self):
        found = detect_deal_candidates(synth())
        assert len(found) == 1
        candidate = found[0]
        assert candidate.announcement_return > 0.2
        assert candidate.volatility_collapse < 0.4
        assert candidate.volume_ratio > 2.5

    def test_ignores_a_jump_without_a_volatility_collapse(self):
        """An earnings jump into continued normal volatility is not a deal."""
        assert detect_deal_candidates(synth(post_vol=0.02)) == []

    def test_ignores_a_quiet_stock_with_no_jump(self):
        assert detect_deal_candidates(synth(jump=0.0, volume_spike=1.0)) == []

    def test_ignores_a_jump_on_ordinary_volume(self):
        assert detect_deal_candidates(synth(volume_spike=1.0)) == []

    def test_relative_collapse_matters_not_just_absolute(self):
        """Pins why the test is relative.

        A naturally quiet stock that jumps does not qualify, because its
        post-event volatility is not a collapse relative to its own norm --
        only an absolute threshold would wrongly accept it.
        """
        assert detect_deal_candidates(synth(pre_vol=0.004, post_vol=0.003)) == []

    def test_detection_is_causal(self):
        """Detection must not use bars after the confirmation window.

        Truncating the series immediately after confirmation must not change
        the detection, or the detector is reading the future.
        """
        full = detect_deal_candidates(synth())
        truncated = detect_deal_candidates(synth(truncate_at=211))
        assert len(full) == len(truncated) == 1
        assert full[0].detection_date == truncated[0].detection_date
        assert full[0].detection_price == pytest.approx(truncated[0].detection_price)

    def test_delisting_is_never_an_input(self):
        """A candidate that stays listed must be detected identically.

        This is the property that keeps failed deals in the sample. Requiring
        delisting would select for the outcome being predicted.
        """
        delisted = detect_deal_candidates(synth(symbol="DEAD", truncate_at=260))
        alive = detect_deal_candidates(synth(symbol="ALIVE"))
        assert len(delisted) == len(alive) == 1
        assert delisted[0].announcement_return == pytest.approx(alive[0].announcement_return)

    def test_one_event_is_not_counted_repeatedly(self):
        found = detect_deal_candidates(synth(n=700))
        assert len(found) == 1


class TestOutcomes:
    def test_stop_loss_exits_a_broken_deal(self):
        """Pins that a broken deal is exited, not held through the collapse."""
        frame = synth(post_drift=-0.01, post_vol=0.002, n=400)
        candidates = detect_deal_candidates(frame)
        assert candidates
        outcomes = measure_outcomes(frame, candidates, stop_loss=0.15)
        assert outcomes[0].hit_stop
        assert outcomes[0].gross_return < -0.10

    def test_delisting_is_recognised_as_a_closed_deal(self):
        import pandas as pd

        frame = synth(truncate_at=240)
        candidates = detect_deal_candidates(frame)
        assert candidates
        # The universe runs past this symbol's last bar, which is what makes
        # its disappearance a delisting rather than the end of the data.
        outcomes = measure_outcomes(
            frame, candidates, final_date=pd.Timestamp("2017-01-01", tz="UTC")
        )
        assert outcomes[0].delisted

    def test_still_listed_at_horizon_is_not_marked_delisted(self):
        """The bucket that inflated the naive result must be identifiable."""
        frame = synth(n=700)
        outcomes = measure_outcomes(frame, detect_deal_candidates(frame))
        assert outcomes and not outcomes[0].delisted
        assert not outcomes[0].hit_stop

    def test_annualisation_handles_a_total_loss(self):
        frame = synth(post_drift=-0.05, n=400)
        candidates = detect_deal_candidates(frame)
        if candidates:
            outcome = measure_outcomes(frame, candidates)[0]
            assert outcome.annualised >= -1.0
