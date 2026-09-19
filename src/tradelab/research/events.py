"""Event detection from price and volume alone.

The EODHD plan supplies daily bars, splits and dividend declarations. It does
not supply earnings dates, index membership or fund holdings, so the hypotheses
that depend on those (H4, H5, H7, H9 in the register) cannot be tested. What
remains is what can be *inferred* from price, volume and corporate actions.

One inference turns out to be unusually clean: a **cash acquisition** leaves a
signature no other event produces.

    1. A large one-day jump on heavy volume  (the announcement)
    2. Then the price pins near a fixed level, with realised volatility
       collapsing to a fraction of normal  (the market pricing a fixed cash
       payout at a known date)
    3. Then the ticker stops trading  (the deal closes)

Step 2 is the identifying feature. A normal equity runs at roughly 2% daily
volatility; a stock pinned to an agreed cash price runs at 0.1-0.3%. Nothing
else in equity markets produces a sustained volatility collapse of that size
while the price sits above its pre-event level. Measured on the US universe,
17% of delisted names carry it.

**Detection must be causal.** `detect_deal_candidates` uses only bars up to and
including the confirmation window, so a candidate is identified at a date when
a trader could actually have acted. Whether the ticker later delisted is never
an input -- that would be lookahead of the worst kind, since it is exactly the
outcome being predicted.

**Failed deals must be in the sample.** A deal that breaks sees the stock fall
back towards its pre-announcement price. Those cases are only present if the
universe includes delisted names AND the detector does not require delisting.
Both conditions hold here, which is what makes the resulting return
distribution honest rather than flattering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DealCandidate:
    """A possible cash acquisition, identified causally from price action."""

    symbol: str
    announcement_date: datetime
    detection_date: datetime
    pre_price: float
    announcement_return: float
    volume_ratio: float
    detection_price: float
    pinned_volatility: float
    normal_volatility: float

    @property
    def volatility_collapse(self) -> float:
        """How far volatility fell. The identifying statistic."""
        if self.normal_volatility <= 0:
            return 0.0
        return self.pinned_volatility / self.normal_volatility

    @property
    def implied_upside_to_pre(self) -> float:
        """Detection price relative to the pre-announcement price."""
        if self.pre_price <= 0:
            return 0.0
        return self.detection_price / self.pre_price - 1.0


@dataclass(frozen=True)
class DealOutcome:
    """What happened after a candidate was detected."""

    candidate: DealCandidate
    horizon_days: int
    exit_date: datetime
    exit_price: float
    gross_return: float
    delisted: bool
    hit_stop: bool

    @property
    def annualised(self) -> float:
        if self.horizon_days <= 0:
            return 0.0
        years = self.horizon_days / 252.0
        base = 1.0 + self.gross_return
        if base <= 0:
            return -1.0
        return base ** (1.0 / years) - 1.0


def detect_deal_candidates(
    frame: pd.DataFrame,
    *,
    min_jump: float = 0.12,
    min_volume_ratio: float = 2.5,
    confirm_days: int = 10,
    max_pinned_vol: float = 0.012,
    min_vol_collapse: float = 0.40,
    lookback: int = 60,
    min_price: float = 5.0,
) -> list[DealCandidate]:
    """Find cash-acquisition candidates using only data available at the time.

    `min_vol_collapse` requires post-event volatility to be at most this
    fraction of the stock's own prior volatility. Using a *relative* collapse
    rather than an absolute threshold alone matters: a naturally quiet utility
    and a biotech pinned by a deal can share an absolute volatility, but only
    the biotech shows the collapse.
    """
    out: list[DealCandidate] = []
    for symbol, group in frame.groupby("symbol", observed=True):
        group = group.sort_values("timestamp")
        close = group["close"].to_numpy(dtype=float)
        volume = group["volume"].to_numpy(dtype=float)
        stamps = group["timestamp"].to_numpy()
        n = close.size
        if n < lookback + confirm_days + 5:
            continue
        returns = np.diff(close) / close[:-1]

        t = lookback
        while t < n - confirm_days:
            jump = returns[t - 1]
            if jump < min_jump or close[t] < min_price:
                t += 1
                continue

            # Index care: returns[t-1] is the move INTO close[t], so the
            # announcement bar is t and its volume is volume[t]. Reading
            # volume[t-1] would test the day *before* the announcement, which
            # is both wrong and quietly selects for pre-announcement leakage.
            prior_vol = float(np.std(returns[t - lookback : t - 1]))
            prior_volume = float(np.mean(volume[t - lookback : t]))
            if prior_vol <= 0 or prior_volume <= 0:
                t += 1
                continue
            if volume[t] / prior_volume < min_volume_ratio:
                t += 1
                continue

            window = returns[t : t + confirm_days]
            if window.size < confirm_days:
                break
            pinned = float(np.std(window))
            if pinned > max_pinned_vol or pinned / prior_vol > min_vol_collapse:
                t += 1
                continue

            detect_index = t + confirm_days
            out.append(
                DealCandidate(
                    symbol=str(symbol),
                    announcement_date=pd.Timestamp(stamps[t]).to_pydatetime(),
                    detection_date=pd.Timestamp(stamps[detect_index]).to_pydatetime(),
                    pre_price=float(close[t - 1]),
                    announcement_return=float(jump),
                    volume_ratio=float(volume[t] / prior_volume),
                    detection_price=float(close[detect_index]),
                    pinned_volatility=pinned,
                    normal_volatility=prior_vol,
                )
            )
            # Skip a year forward so one deal is not counted repeatedly.
            t = detect_index + 252
    return out


def measure_outcomes(
    frame: pd.DataFrame,
    candidates: list[DealCandidate],
    *,
    max_horizon_days: int = 252,
    stop_loss: float = 0.15,
    delist_gap_days: int = 30,
    final_date: datetime | None = None,
) -> list[DealOutcome]:
    """Forward returns from detection to exit.

    Exit is whichever comes first: the ticker stops trading (deal closed), a
    stop-loss breach (deal broke), or the horizon. The stop matters -- a broken
    deal gaps down hard, and a model without a stop assumes you sat through it.
    """
    # The universe's last date, not the symbol's. Defaulting to the frame max
    # works for a multi-symbol universe; a single-symbol frame must pass it
    # explicitly, since there the symbol's own last bar IS the frame max and
    # nothing could ever register as delisted.
    universe_end = pd.Timestamp(final_date) if final_date else frame["timestamp"].max()
    outcomes: list[DealOutcome] = []
    by_symbol = {
        str(s): g.sort_values("timestamp") for s, g in frame.groupby("symbol", observed=True)
    }

    for candidate in candidates:
        group = by_symbol.get(candidate.symbol)
        if group is None:
            continue
        stamps = group["timestamp"]
        after = group[stamps > pd.Timestamp(candidate.detection_date)]
        if after.empty:
            continue
        path = after.head(max_horizon_days)
        prices = path["close"].to_numpy(dtype=float)
        dates = path["timestamp"].to_numpy()
        entry = candidate.detection_price
        if entry <= 0:
            continue

        stop_level = entry * (1.0 - stop_loss)
        hit = np.where(prices <= stop_level)[0]
        if hit.size:
            idx = int(hit[0])
            exit_price, exit_date, stopped = float(prices[idx]), dates[idx], True
        else:
            idx = len(prices) - 1
            exit_price, exit_date, stopped = float(prices[idx]), dates[idx], False

        symbol_last = stamps.max()
        delisted = symbol_last <= pd.Timestamp(dates[idx]) + pd.Timedelta(
            days=delist_gap_days
        ) and symbol_last < universe_end - pd.Timedelta(days=delist_gap_days)
        horizon = int(idx) + 1
        outcomes.append(
            DealOutcome(
                candidate=candidate,
                horizon_days=horizon,
                exit_date=pd.Timestamp(exit_date).to_pydatetime(),
                exit_price=exit_price,
                gross_return=exit_price / entry - 1.0,
                delisted=bool(delisted),
                hit_stop=stopped,
            )
        )
    return outcomes
