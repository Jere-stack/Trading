"""Time sources.

The single most common cause of backtest/live divergence is code that reads the
wall clock. In this system, **nothing outside `LiveClock` may call
`datetime.now()`**. Every component that needs the current time takes a `Clock`.

That makes the backtest deterministic, makes time-dependent logic unit-testable
without monkeypatching, and guarantees that a strategy cannot accidentally
behave differently in simulation than in production.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta


class Clock(ABC):
    """Abstract time source."""

    @abstractmethod
    def now(self) -> datetime:
        """Current time as a timezone-aware UTC datetime."""

    def today(self):
        return self.now().date()


class LiveClock(Clock):
    """Wall-clock time. The *only* sanctioned caller of `datetime.now`."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class SimulationClock(Clock):
    """Manually advanced clock used by the backtester and by tests.

    Time only moves forward. Attempting to set time backwards raises, because a
    backwards jump in a replay loop means events are being processed out of
    order -- which would silently produce lookahead bias.
    """

    def __init__(self, start: datetime) -> None:
        self._now = _require_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        moment = _require_utc(moment)
        if moment < self._now:
            raise ValueError(
                f"simulation clock cannot move backwards: {self._now.isoformat()} -> "
                f"{moment.isoformat()}"
            )
        self._now = moment

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("cannot advance by a negative timedelta")
        self._now = self._now + delta


def _require_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(
            "naive datetime rejected: all timestamps must be timezone-aware. "
            "Naive datetimes are the usual route to off-by-one-session bugs."
        )
    return moment.astimezone(UTC)
