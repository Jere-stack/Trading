"""One place that decides whether a price series is real enough to trade.

Every study shares this, because a screen that lives in one script gets
forgotten by the next one, and the failures it catches are invisible in
aggregate statistics. Each rule below exists because a specific result was
already corrupted by the case it rejects.

**Sentinel prices.** 291 symbols in the US universe carry closes of exactly
$1,000,000.00 -- a placeholder emitted where a split adjustment overflowed. A
constant series has zero volatility, so a low-volatility strategy ranks it
*first*. The "low volatility anomaly" backtest returned -2.03%/yr with a -48.7%
drawdown because it was holding twenty of these.

**Stale runs.** 991 symbols have 60 or more consecutive identical closes. Even
a quiet utility ticks; a series that does not is halted, or a placeholder, or a
stub. It reads as zero volatility and as sitting exactly at its 52-week high,
so it poisons any signal built on either.

**Impossible moves.** 183 liquid symbols contain a single-session move above
+500%: POW_OLD "goes" from $0.005 to $15,600, a 312-million-percent jump. One
such series put +562% into a placebo mean whose median was -0.67%.

None of these are exotic. They are what a survivorship-free universe looks like
before anyone cleans it, and they all bias in the direction of looking like a
free lunch -- zero volatility, infinite return, permanent highs -- which is
exactly the direction that gets a strategy funded.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MAX_PLAUSIBLE_PRICE = 100_000.0
"""Above this a US equity close is a data artefact, not a price."""

MAX_SESSION_MOVE = 5.0
"""+500% in one session. A failed split adjustment, not a return."""

MAX_STALE_RUN = 60
"""Consecutive identical closes tolerated before the series is called stale."""

MIN_BARS = 300


@dataclass(frozen=True)
class ScreenResult:
    ok: bool
    reason: str = ""
    rule: str = ""
    """Which rule fired, without the instance-specific detail.

    `reason` says "771 identical closes in a row"; `rule` says "stale series".
    Aggregating on `reason` produces one bucket per symbol, which hides the
    shape of the contamination behind a wall of counts."""


def longest_flat_run(closes: np.ndarray) -> int:
    """Longest run of consecutive unchanged closes."""
    if closes.size < 2:
        return 0
    unchanged = np.diff(closes) == 0
    if not unchanged.any():
        return 0
    # Run lengths via the positions where the flag flips.
    padded = np.concatenate([[False], unchanged, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return int((edges[1::2] - edges[::2]).max())


def screen_price_series(closes: np.ndarray, *, min_bars: int = MIN_BARS) -> ScreenResult:
    """Decide whether a close series can be trusted in a backtest."""
    closes = np.asarray(closes, dtype=float)
    if closes.size < min_bars:
        return ScreenResult(False, f"only {closes.size} bars", "too few bars")
    if not np.isfinite(closes).all():
        return ScreenResult(False, "non-finite closes", "non-finite")
    if (closes <= 0).any():
        return ScreenResult(False, "non-positive close", "non-positive")
    if (closes >= MAX_PLAUSIBLE_PRICE).any():
        return ScreenResult(False, f"close >= ${MAX_PLAUSIBLE_PRICE:,.0f}", "sentinel price")
    with np.errstate(divide="ignore", invalid="ignore"):
        moves = np.abs(np.diff(closes) / closes[:-1])
    if (np.isfinite(moves) & (moves > MAX_SESSION_MOVE)).any():
        return ScreenResult(
            False, f"single-session move > {MAX_SESSION_MOVE:.0%}", "impossible move"
        )
    flat = longest_flat_run(closes)
    if flat >= MAX_STALE_RUN:
        return ScreenResult(False, f"{flat} identical closes in a row", "stale series")
    return ScreenResult(True)
