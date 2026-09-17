"""Enumerations for the trading domain.

All enums are `str`-valued so that they serialise cleanly to JSON/YAML and appear
readable in logs and persisted state without custom encoders.
"""

from __future__ import annotations

from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 for BUY, -1 for SELL. Used to convert quantities to signed deltas."""
        return 1 if self is Side.BUY else -1

    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    MARKET_ON_CLOSE = "MOC"
    LIMIT_ON_CLOSE = "LOC"
    MARKET_ON_OPEN = "MOO"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    OPG = "OPG"


class OrderStatus(StrEnum):
    """Order lifecycle.

    `PENDING_NEW` is the state between strategy submission and broker
    acknowledgement. It matters: an order in this state has consumed risk budget
    but has no broker id yet, so a crash in that window needs reconciliation.
    """

    PENDING_NEW = "PENDING_NEW"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES

    @property
    def is_open(self) -> bool:
        return not self.is_terminal


_TERMINAL_STATUSES = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
)


class AssetClass(StrEnum):
    """Deliberately narrow. The mandate is stocks only.

    Keeping this enum minimal is a design guardrail: adding leverage or
    derivatives requires an explicit, reviewable change here rather than
    silently passing a different contract type through the stack.
    """

    EQUITY = "EQUITY"
    ETF = "ETF"


class Venue(StrEnum):
    """Execution venues / primary listing exchanges we support."""

    SMART = "SMART"
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    ARCA = "ARCA"
    BATS = "BATS"
    HELSINKI = "HEX"
    STOCKHOLM = "SFB"
    XETRA = "IBIS"
    AMSTERDAM = "AEB"
    PARIS = "SBF"
    LSE = "LSE"


class RunMode(StrEnum):
    """Execution mode. Strategy code must behave identically across all three."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"

    @property
    def is_simulated(self) -> bool:
        return self in (RunMode.BACKTEST, RunMode.PAPER)


class RiskDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RESIZE = "RESIZE"


class LiquidityFlag(StrEnum):
    """Whether a fill added or removed liquidity. Drives exchange fee/rebate."""

    TAKER = "TAKER"
    MAKER = "MAKER"
    UNKNOWN = "UNKNOWN"
