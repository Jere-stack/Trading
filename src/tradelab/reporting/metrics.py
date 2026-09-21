"""Performance metrics computed from the persisted state database.

Read-only. Floats are used throughout because these are analytics, not
accounting -- the Decimal ledger remains the source of truth and nothing here
feeds back into it.

Two things this module refuses to do, both for the same reason:

* **It never sums P&L across currencies.** Fills carry no FX rate, and the rate
  that applied on the day of a fill is not recoverable from the fills table. A
  single "total P&L" spanning USD and EUR trades would be a number computed at
  an exchange rate nobody chose. Results are grouped by currency instead.
* **It never annualises a Sharpe from a handful of sessions.** Below
  `MIN_SESSIONS_FOR_SHARPE` the figure is noise wearing a decimal point, and a
  displayed number invites belief in proportion to its precision.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

TRADING_DAYS = 252
MIN_SESSIONS_FOR_SHARPE = 60
"""Below this, a Sharpe ratio is not reported at all.

Sixty sessions is still far too few to distinguish an edge from luck -- a
Sharpe-1.0 strategy needs about 2.7 years -- but it is the point below which
the number is not even arithmetically stable.
"""


@dataclass(frozen=True)
class EquityPoint:
    timestamp: datetime
    equity: float
    cash: float
    gross_exposure: float
    net_exposure: float
    position_count: int

    @property
    def leverage(self) -> float:
        return self.gross_exposure / self.equity if self.equity else 0.0


@dataclass(frozen=True)
class DrawdownPoint:
    timestamp: datetime
    drawdown: float
    """Fraction below the running peak, as a negative number."""


@dataclass(frozen=True)
class StrategyResult:
    """One strategy's trading record, in one currency."""

    strategy_id: str
    currency: str
    realised: float
    commission: float
    fees: float
    fills: int
    symbols: int
    bought: float
    sold: float

    @property
    def net(self) -> float:
        """Realised P&L after the costs actually charged to it."""
        return self.realised - self.commission - self.fees

    @property
    def cost_bps(self) -> float:
        """Costs as basis points of the value traded.

        The number that decides whether a strategy is viable at this account
        size. An edge of 30 bps against 45 bps of cost is not a small problem.
        """
        traded = self.bought + self.sold
        return (self.commission + self.fees) / traded * 10_000 if traded else 0.0


@dataclass(frozen=True)
class PerformanceSummary:
    start: datetime | None
    end: datetime | None
    sessions: int
    starting_equity: float
    equity: float
    max_drawdown: float
    max_drawdown_date: datetime | None
    open_positions: int
    leverage: float
    fills: int
    rejections: int
    sharpe: float | None
    """None when there are too few sessions for the figure to mean anything."""
    currencies: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_return(self) -> float:
        if not self.starting_equity:
            return 0.0
        return self.equity / self.starting_equity - 1.0

    @property
    def sharpe_is_meaningful(self) -> bool:
        return self.sharpe is not None


def _connect(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_equity_curve(path: Path | str) -> list[EquityPoint]:
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT timestamp, equity, cash, gross_exposure, net_exposure, "
            "position_count FROM equity_curve ORDER BY timestamp"
        ).fetchall()
    return [
        EquityPoint(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            equity=float(row["equity"]),
            cash=float(row["cash"]),
            gross_exposure=float(row["gross_exposure"]),
            net_exposure=float(row["net_exposure"]),
            position_count=int(row["position_count"]),
        )
        for row in rows
    ]


def max_drawdown(curve: list[EquityPoint]) -> list[DrawdownPoint]:
    """Drawdown from the running peak, per session.

    Reported on the equity curve rather than on closed trades, because an open
    position that has halved has already cost you the money whether or not it
    has been sold.
    """
    out: list[DrawdownPoint] = []
    peak = float("-inf")
    for point in curve:
        peak = max(peak, point.equity)
        out.append(DrawdownPoint(point.timestamp, point.equity / peak - 1.0 if peak > 0 else 0.0))
    return out


def _currency_of(instrument_key: str) -> str:
    """`AAPL.SMART.USD` -> `USD`. Unknown shapes report as UNKNOWN, not a guess."""
    parts = instrument_key.rsplit(".", 1)
    return parts[1].upper() if len(parts) == 2 and len(parts[1]) == 3 else "UNKNOWN"


def strategy_results(path: Path | str) -> list[StrategyResult]:
    """Per-strategy realised P&L, reconstructed by replaying the fills.

    Realised P&L needs position accounting -- a sale is only a profit relative
    to what the shares cost -- so fills are replayed in time order per
    (strategy, instrument) on an average-cost basis, matching the ledger.

    Grouped by currency and never summed across them. See the module docstring.
    """
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT strategy_id, instrument_key, side, quantity, price, "
            "commission, fees FROM fills ORDER BY timestamp, fill_id"
        ).fetchall()

    realised: dict[tuple[str, str], float] = defaultdict(float)
    commission: dict[tuple[str, str], float] = defaultdict(float)
    fees: dict[tuple[str, str], float] = defaultdict(float)
    count: dict[tuple[str, str], int] = defaultdict(int)
    bought: dict[tuple[str, str], float] = defaultdict(float)
    sold: dict[tuple[str, str], float] = defaultdict(float)
    symbols: dict[tuple[str, str], set[str]] = defaultdict(set)
    # (strategy, instrument) -> [quantity, average cost]
    book: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])

    for row in rows:
        strategy = row["strategy_id"]
        key = row["instrument_key"]
        currency = _currency_of(key)
        bucket = (strategy, currency)
        quantity = float(row["quantity"])
        price = float(row["price"])
        is_buy = row["side"].upper() == "BUY"

        commission[bucket] += float(row["commission"])
        fees[bucket] += float(row["fees"])
        count[bucket] += 1
        symbols[bucket].add(key)
        (bought if is_buy else sold)[bucket] += quantity * price

        position = book[(strategy, key)]
        held, average = position
        if is_buy:
            if held >= 0:
                total = held + quantity
                position[1] = (held * average + quantity * price) / total if total else 0.0
                position[0] = total
            else:  # buying back a short
                closing = min(quantity, -held)
                realised[bucket] += closing * (average - price)
                position[0] = held + quantity
                if position[0] > 0:
                    position[1] = price
        else:
            if held > 0:
                closing = min(quantity, held)
                realised[bucket] += closing * (price - average)
                position[0] = held - quantity
                if position[0] < 0:
                    position[1] = price
            else:
                total = -held + quantity
                position[1] = (-held * average + quantity * price) / total if total else 0.0
                position[0] = held - quantity

    return sorted(
        (
            StrategyResult(
                strategy_id=strategy,
                currency=currency,
                realised=realised[(strategy, currency)],
                commission=commission[(strategy, currency)],
                fees=fees[(strategy, currency)],
                fills=count[(strategy, currency)],
                symbols=len(symbols[(strategy, currency)]),
                bought=bought[(strategy, currency)],
                sold=sold[(strategy, currency)],
            )
            for strategy, currency in count
        ),
        key=lambda result: (result.strategy_id, result.currency),
    )


def _sharpe(curve: list[EquityPoint]) -> float | None:
    if len(curve) < MIN_SESSIONS_FOR_SHARPE:
        return None
    returns = [
        curve[i].equity / curve[i - 1].equity - 1.0
        for i in range(1, len(curve))
        if curve[i - 1].equity > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    deviation = math.sqrt(variance)
    if deviation == 0:
        return None
    return mean / deviation * math.sqrt(TRADING_DAYS)


def summarise(path: Path | str) -> PerformanceSummary:
    curve = load_equity_curve(path)
    with _connect(path) as connection:
        fills = connection.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"]
        rejections = connection.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind = 'risk_reject'"
        ).fetchone()["n"]

    if not curve:
        return PerformanceSummary(
            start=None,
            end=None,
            sessions=0,
            starting_equity=0.0,
            equity=0.0,
            max_drawdown=0.0,
            max_drawdown_date=None,
            open_positions=0,
            leverage=0.0,
            fills=fills,
            rejections=rejections,
            sharpe=None,
        )

    drawdowns = max_drawdown(curve)
    worst = min(drawdowns, key=lambda point: point.drawdown)
    last = curve[-1]
    currencies = tuple(sorted({result.currency for result in strategy_results(path)}))

    return PerformanceSummary(
        start=curve[0].timestamp,
        end=last.timestamp,
        sessions=len(curve),
        starting_equity=curve[0].equity,
        equity=last.equity,
        max_drawdown=worst.drawdown,
        max_drawdown_date=worst.timestamp,
        open_positions=last.position_count,
        leverage=last.leverage,
        fills=fills,
        rejections=rejections,
        sharpe=_sharpe(curve),
        currencies=currencies,
    )
