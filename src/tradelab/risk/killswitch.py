"""Kill switch and risk state tracking.

The kill switch distinguishes two severities, and the distinction is the point:

* **SOFT halt** (daily loss limit, order rate limit): blocks *new* risk for the
  session, clears automatically at the next session boundary. Existing positions
  may still be closed -- a halt that prevents you exiting is a liability, not a
  control.

* **HARD halt** (max drawdown, reconciliation break, repeated broker rejects):
  blocks all new risk and requires explicit human re-arming. An automated system
  that resumes on its own after a 15% drawdown does not have a kill switch; it
  has a pause button. The whole value of the control is that a human has to look
  at what happened before capital is risked again.

Both are recorded with a reason and timestamp so that post-mortems answer "why
did it stop?" without log archaeology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from tradelab.core.money import ZERO, safe_div


class HaltLevel(StrEnum):
    NONE = "NONE"
    SOFT = "SOFT"
    HARD = "HARD"


@dataclass(frozen=True, slots=True)
class HaltRecord:
    level: HaltLevel
    reason: str
    timestamp: datetime
    triggered_by: str


@dataclass
class KillSwitch:
    """Tracks halt state. Defaults to armed-and-trading."""

    level: HaltLevel = HaltLevel.NONE
    history: list[HaltRecord] = field(default_factory=list)
    current: HaltRecord | None = None

    @property
    def is_halted(self) -> bool:
        return self.level is not HaltLevel.NONE

    @property
    def blocks_new_risk(self) -> bool:
        return self.is_halted

    @property
    def blocks_closing(self) -> bool:
        """Only a HARD halt blocks risk-reducing orders."""
        return self.level is HaltLevel.HARD

    def trip(self, level: HaltLevel, reason: str, timestamp: datetime, triggered_by: str) -> None:
        """Engage a halt. Never downgrades: HARD cannot be overwritten by SOFT."""
        if level is HaltLevel.NONE:
            raise ValueError("use reset()/clear_soft() to release a halt")
        if self.level is HaltLevel.HARD and level is HaltLevel.SOFT:
            return
        record = HaltRecord(
            level=level, reason=reason, timestamp=timestamp, triggered_by=triggered_by
        )
        self.level = level
        self.current = record
        self.history.append(record)

    def clear_soft(self, timestamp: datetime) -> bool:
        """Release a SOFT halt, e.g. at a session boundary. HARD is untouched."""
        if self.level is HaltLevel.SOFT:
            self.level = HaltLevel.NONE
            self.current = None
            return True
        return False

    def reset(self, operator: str, timestamp: datetime) -> None:
        """Manually re-arm after a HARD halt. Requires naming the operator."""
        if not operator or not operator.strip():
            raise ValueError(
                "re-arming after a hard halt requires an operator identity for the audit trail"
            )
        self.history.append(
            HaltRecord(
                level=HaltLevel.NONE,
                reason=f"manually re-armed by {operator}",
                timestamp=timestamp,
                triggered_by="operator",
            )
        )
        self.level = HaltLevel.NONE
        self.current = None


@dataclass
class RiskState:
    """Mutable risk state that spans orders and sessions.

    Kept separate from `Portfolio` because it is control-plane state (what are we
    allowed to do) rather than accounting state (what do we own). They have
    different persistence and recovery requirements: a restart must restore risk
    state too, or the daily loss limit silently resets to zero used.
    """

    session_date: date | None = None
    day_start_equity: Decimal = ZERO
    peak_equity: Decimal = ZERO
    orders_today: int = 0
    new_positions_today: int = 0
    consecutive_losing_days: int = 0
    last_day_close_equity: Decimal = ZERO
    order_timestamps: list[datetime] = field(default_factory=list)
    recent_order_keys: dict[str, datetime] = field(default_factory=dict)
    strategy_peak_equity: dict[str, Decimal] = field(default_factory=dict)
    kill_switch: KillSwitch = field(default_factory=KillSwitch)

    def start_session(self, session: date, equity: Decimal) -> None:
        """Roll to a new session, resetting per-day counters.

        Called on the first event of each trading day. Updating
        `consecutive_losing_days` here (rather than at close) means it survives a
        crash between sessions, since it is derived from persisted equity.
        """
        if self.session_date is not None and session <= self.session_date:
            return
        if self.session_date is not None and self.last_day_close_equity > 0:
            if equity < self.last_day_close_equity:
                self.consecutive_losing_days += 1
            else:
                self.consecutive_losing_days = 0
        self.session_date = session
        self.day_start_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        self.orders_today = 0
        self.new_positions_today = 0
        self.order_timestamps.clear()
        self.recent_order_keys.clear()

    def end_session(self, equity: Decimal) -> None:
        self.last_day_close_equity = equity

    def observe_equity(self, equity: Decimal) -> None:
        self.peak_equity = max(self.peak_equity, equity)

    def record_order(self, timestamp: datetime, dedupe_key: str | None = None) -> None:
        self.orders_today += 1
        self.order_timestamps.append(timestamp)
        if dedupe_key is not None:
            self.recent_order_keys[dedupe_key] = timestamp

    def orders_in_window(self, now: datetime, window: timedelta) -> int:
        cutoff = now - window
        self.order_timestamps = [t for t in self.order_timestamps if t >= cutoff]
        return len(self.order_timestamps)

    def seen_recently(self, dedupe_key: str, now: datetime, window: timedelta) -> bool:
        seen = self.recent_order_keys.get(dedupe_key)
        if seen is None:
            return False
        if now - seen > window:
            del self.recent_order_keys[dedupe_key]
            return False
        return True

    def daily_pnl_pct(self, equity: Decimal) -> Decimal:
        return safe_div(equity - self.day_start_equity, self.day_start_equity)

    def drawdown_pct(self, equity: Decimal) -> Decimal:
        """Current drawdown from peak, as a positive fraction."""
        if self.peak_equity <= 0:
            return ZERO
        return max(ZERO, safe_div(self.peak_equity - equity, self.peak_equity))
