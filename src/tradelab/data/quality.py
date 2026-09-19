"""Data quality auditing.

Bad data produces confident, wrong backtests. Unlike a code bug, it does not
crash -- it quietly changes the answer, and the direction is almost always
flattering. The five failure modes below account for the large majority of
"my backtest worked but live didn't" cases:

1. **Survivorship bias.** A universe of *current* index members excludes every
   company that went bankrupt, was delisted, or was acquired at a discount.
   Measured effects on US equities are large -- typically 1-4% per year of
   spurious return. This is the single most damaging data problem in retail
   research and the hardest to fix, because the clean free datasets are exactly
   the biased ones.

2. **Unadjusted splits.** A 4:1 split appears as a -75% one-day return. A
   mean-reversion strategy will enthusiastically buy it, and the backtest will
   show a spectacular 300% gain the next day.

3. **Mixed adjustment.** Splicing adjusted and unadjusted series produces a
   phantom return at the join. Back-adjusted prices from years ago can also be
   a small fraction of the real traded price, which silently breaks per-share
   commission and minimum-price filters.

4. **Gaps and stale prices.** A repeated close is usually a halted or delisted
   name, not a flat market. Trading on it produces fills that could not have
   happened.

5. **Outliers.** Bad ticks -- a misplaced decimal, a crossed print -- create
   exactly the large moves that event-driven and reversal strategies trigger on.

Every check reports rather than repairs. Automatic repair hides the problem, and
the appropriate response differs by cause: a split needs adjustment, a bad tick
needs removal, and a halt needs exclusion from the tradable universe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import pandas as pd

from tradelab.data.schema import Adjustment, validate_bars


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class QualityIssue:
    check: str
    severity: Severity
    symbol: str | None
    message: str
    count: int = 1
    sample: tuple[str, ...] = ()

    def __str__(self) -> str:
        where = f"[{self.symbol}] " if self.symbol else ""
        tail = f" (e.g. {', '.join(self.sample[:3])})" if self.sample else ""
        return f"{self.severity.value:<8} {self.check}: {where}{self.message}{tail}"


@dataclass
class QualityReport:
    issues: list[QualityIssue] = field(default_factory=list)
    n_rows: int = 0
    n_symbols: int = 0

    @property
    def critical(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity is Severity.CRITICAL]

    @property
    def warnings(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def is_usable(self) -> bool:
        """Usable means no CRITICAL issues. Warnings still need a human read."""
        return not self.critical

    def summary(self) -> str:
        lines = [
            f"{self.n_rows:,} rows across {self.n_symbols} symbols; "
            f"{len(self.critical)} critical, {len(self.warnings)} warnings"
        ]
        lines.extend(f"  {issue}" for issue in self.issues)
        if self.is_usable and not self.warnings:
            lines.append("  no issues detected")
        return "\n".join(lines)

    def raise_if_unusable(self) -> None:
        if not self.is_usable:
            detail = "\n".join(f"  {i}" for i in self.critical)
            raise DataQualityError(
                f"{len(self.critical)} critical data quality issue(s); refusing to "
                f"proceed:\n{detail}"
            )


class DataQualityError(RuntimeError):
    """Raised when data is too broken to produce a trustworthy result."""


def audit(
    frame: pd.DataFrame,
    *,
    adjustment: Adjustment = Adjustment.UNKNOWN,
    expected_symbols: set[str] | None = None,
    max_daily_move: float = 0.35,
    max_stale_days: int = 5,
    min_history_rows: int = 60,
    expected_calendar: pd.DatetimeIndex | None = None,
) -> QualityReport:
    """Audit a canonical bar frame and report everything suspicious."""
    validate_bars(frame)
    report = QualityReport(n_rows=len(frame), n_symbols=int(frame["symbol"].nunique()))

    _check_survivorship(frame, expected_symbols, report)
    for symbol, group in frame.groupby("symbol", observed=True):
        group = group.sort_values("timestamp")
        _check_history_length(symbol, group, min_history_rows, report)
        _check_splits(symbol, group, adjustment, report)
        _check_outliers(symbol, group, max_daily_move, report)
        _check_stale(symbol, group, max_stale_days, report)
        _check_zero_volume(symbol, group, report)
        if expected_calendar is not None:
            _check_gaps(symbol, group, expected_calendar, report)
    return report


# ------------------------------------------------------------------- checks


def _check_survivorship(
    frame: pd.DataFrame, expected: set[str] | None, report: QualityReport
) -> None:
    """Flag a universe that ends uniformly -- the signature of survivorship bias.

    If every symbol's last bar falls on the same recent date, the universe was
    almost certainly built from *current* constituents. A genuine historical
    universe contains names whose series stop early because they were delisted
    or acquired.
    """
    last_dates = frame.groupby("symbol", observed=True)["timestamp"].max()
    if len(last_dates) < 3:
        return
    latest = last_dates.max()
    ending_late = (last_dates >= latest - pd.Timedelta(days=5)).sum()
    fraction = ending_late / len(last_dates)
    if fraction >= 0.99:
        report.issues.append(
            QualityIssue(
                check="survivorship",
                severity=Severity.CRITICAL,
                symbol=None,
                message=(
                    f"all {len(last_dates)} symbols have data to the final date. A "
                    "genuine historical universe contains delisted and acquired "
                    "names whose series end early. This universe is almost "
                    "certainly built from current constituents, which overstates "
                    "returns by roughly 1-4% per year"
                ),
                count=len(last_dates),
            )
        )
    if expected is not None:
        missing = expected - set(last_dates.index)
        if missing:
            report.issues.append(
                QualityIssue(
                    check="missing_symbols",
                    severity=Severity.WARNING,
                    symbol=None,
                    message=f"{len(missing)} expected symbol(s) absent from the data",
                    count=len(missing),
                    sample=tuple(sorted(missing)[:5]),
                )
            )


def _check_history_length(
    symbol: str, group: pd.DataFrame, minimum: int, report: QualityReport
) -> None:
    if len(group) < minimum:
        report.issues.append(
            QualityIssue(
                check="short_history",
                severity=Severity.WARNING,
                symbol=symbol,
                message=(
                    f"only {len(group)} bars (minimum {minimum}); too short for a "
                    "stable volatility estimate, so cost and sizing models will be "
                    "unreliable for this name"
                ),
                count=len(group),
            )
        )


# Ratios a corporate action realistically produces: splits below 1, reverse
# splits above.
_SPLIT_RATIOS = (
    1 / 20,
    1 / 10,
    1 / 8,
    1 / 7,
    1 / 6,
    1 / 5,
    1 / 4,
    1 / 3,
    2 / 5,
    1 / 2,
    2 / 3,
    3 / 2,
    2.0,
    3.0,
    4.0,
    5.0,
    10.0,
)

# Tolerance on the ratio match. Deliberately wide: a split lands on an ordinary
# trading day, so the observed ratio is the split ratio times that day's return.
# Apple split 4:1 on 2020-08-31 and moved +3.4%, giving an observed ratio of
# 0.2585 rather than 0.2500 -- a 2% tolerance misses it entirely.
_SPLIT_RATIO_TOLERANCE = 0.10


def _check_splits(
    symbol: str, group: pd.DataFrame, adjustment: Adjustment, report: QualityReport
) -> None:
    """Detect unadjusted splits from near-exact price ratios.

    A single-day move of -50%, -75% or -80% is extraordinary; one that also
    lands near an exact split ratio is nearly always a corporate action rather
    than a real return.

    Two things are deliberately NOT required, both learned from real data:

    * **A matching volume jump.** The intuition that a split multiplies share
      volume does not hold bar-to-bar. Measured on real splits: Apple 4:1
      showed a volume ratio of 1.20, Tesla 5:1 showed 1.18, and Tesla 3:1
      showed **0.93** -- volume actually fell. Requiring a jump produced a
      false negative on every real split tested. Volume is now reported as
      corroboration, never used as a gate.
    * **A tight ratio match.** See `_SPLIT_RATIO_TOLERANCE`.

    The asymmetry justifies being liberal: a false positive costs a minute of
    investigation, while a missed split silently destroys a backtest by
    presenting a -75% corporate action as a tradable return.
    """
    close = group["close"].to_numpy()
    volume = group["volume"].to_numpy()
    if close.size < 2:
        return
    ratio = close[1:] / close[:-1]
    timestamps = group["timestamp"].to_numpy()[1:]

    suspects: list[str] = []
    for i, r in enumerate(ratio):
        if r <= 0 or not np.isfinite(r):
            continue
        # Only consider moves too large to be plausible as real returns.
        if 0.65 < r < 1.45:
            continue
        # Match the CLOSEST ratio, not the first within tolerance. Tesla's 5:1
        # showed an observed ratio of 0.225, which is inside tolerance of both
        # 1/5 and 1/4; taking the first match would mislabel it.
        target = min(_SPLIT_RATIOS, key=lambda t: abs(r - t) / t)
        if abs(r - target) / target > _SPLIT_RATIO_TOLERANCE:
            continue
        vol_before, vol_after = volume[i], volume[i + 1]
        vol_note = ""
        if vol_before > 0 and vol_after > 0:
            vol_note = f" vol x{vol_after / vol_before:.2f}"
        suspects.append(
            f"{pd.Timestamp(timestamps[i]).date()} ratio {r:.3f}~{target:.3f}{vol_note}"
        )

    if suspects:
        severity = (
            Severity.CRITICAL
            if adjustment in (Adjustment.NONE, Adjustment.UNKNOWN)
            else Severity.WARNING
        )
        report.issues.append(
            QualityIssue(
                check="unadjusted_split",
                severity=severity,
                symbol=symbol,
                message=(
                    f"{len(suspects)} price jump(s) at near-exact split ratios, and "
                    f"adjustment is {adjustment.value}. "
                    "An unadjusted split reads as a huge one-day return and will be "
                    "traded enthusiastically by any reversal strategy"
                ),
                count=len(suspects),
                sample=tuple(suspects),
            )
        )


def _check_outliers(
    symbol: str, group: pd.DataFrame, threshold: float, report: QualityReport
) -> None:
    close = group["close"].to_numpy()
    if close.size < 2:
        return
    returns = np.diff(close) / close[:-1]
    timestamps = group["timestamp"].to_numpy()[1:]
    extreme = np.abs(returns) > threshold
    if not extreme.any():
        return
    samples = tuple(
        f"{pd.Timestamp(t).date()} {r:+.1%}"
        for t, r in zip(timestamps[extreme], returns[extreme], strict=True)
    )
    report.issues.append(
        QualityIssue(
            check="outlier_return",
            severity=Severity.WARNING,
            symbol=symbol,
            message=(
                f"{int(extreme.sum())} daily move(s) beyond {threshold:.0%}. Verify "
                "each against a corporate action or news; bad ticks create exactly "
                "the moves event-driven strategies trigger on"
            ),
            count=int(extreme.sum()),
            sample=samples[:5],
        )
    )


def _check_stale(symbol: str, group: pd.DataFrame, max_run: int, report: QualityReport) -> None:
    """Find runs of identical closes -- usually a halt, a delisting, or a stale feed."""
    close = group["close"].to_numpy()
    if close.size < max_run + 1:
        return
    unchanged = np.isclose(close[1:], close[:-1], rtol=0, atol=1e-12)
    longest = current = 0
    end_index = 0
    for i, same in enumerate(unchanged):
        if same:
            current += 1
            if current > longest:
                longest, end_index = current, i + 1
        else:
            current = 0
    if longest >= max_run:
        when = pd.Timestamp(group["timestamp"].to_numpy()[end_index]).date()
        report.issues.append(
            QualityIssue(
                check="stale_price",
                severity=Severity.WARNING,
                symbol=symbol,
                message=(
                    f"{longest} consecutive identical closes ending {when}. A repeated "
                    "close is usually a halt or a delisting rather than a flat market, "
                    "and trading on it produces fills that could not have happened"
                ),
                count=longest,
            )
        )


def _check_zero_volume(symbol: str, group: pd.DataFrame, report: QualityReport) -> None:
    zero = int((group["volume"] <= 0).sum())
    if zero == 0:
        return
    fraction = zero / len(group)
    report.issues.append(
        QualityIssue(
            check="zero_volume",
            severity=Severity.CRITICAL if fraction > 0.10 else Severity.WARNING,
            symbol=symbol,
            message=(
                f"{zero} bar(s) ({fraction:.1%}) with zero volume. No trade occurred, "
                "so no fill was possible on those bars"
            ),
            count=zero,
        )
    )


def _check_gaps(
    symbol: str, group: pd.DataFrame, calendar: pd.DatetimeIndex, report: QualityReport
) -> None:
    """Compare observed dates against an expected trading calendar."""
    observed = pd.DatetimeIndex(group["timestamp"]).normalize().tz_localize(None)
    start, end = observed.min(), observed.max()
    expected = calendar[(calendar >= start) & (calendar <= end)]
    missing = expected.difference(observed)
    if missing.empty:
        return
    fraction = len(missing) / max(len(expected), 1)
    report.issues.append(
        QualityIssue(
            check="calendar_gap",
            severity=Severity.CRITICAL if fraction > 0.05 else Severity.WARNING,
            symbol=symbol,
            message=(
                f"{len(missing)} expected trading day(s) missing ({fraction:.1%} of "
                "the period). Gaps shift lookback windows and silently change which "
                "bars a signal sees"
            ),
            count=len(missing),
            sample=tuple(str(d.date()) for d in missing[:5]),
        )
    )
