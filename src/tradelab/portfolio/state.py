"""Durable state: SQLite persistence for orders, fills and risk state.

An automated trading system restarts -- on a crash, a deploy, a nightly IB
Gateway re-authentication. What happens across that boundary decides whether it
is safe to leave running.

Two things must survive, and the consequences of losing either are concrete:

1. **Risk state.** Without it, a restart resets `day_start_equity`, so a system
   that has already lost 3% today begins the day with a fresh 3% of rope. The
   daily loss limit becomes unenforceable by the simple expedient of crashing.

2. **Order and fill history.** Without it, reconciliation has nothing to compare
   against, and a restart cannot tell an order it placed from one a human
   placed, nor detect a fill that arrived while it was down.

SQLite rather than Postgres: one file, no server, ACID, and the write volume of
a 10-40 trades-per-year strategy is trivial. `PRAGMA journal_mode=WAL` and
`synchronous=FULL` are set because durability matters far more here than write
throughput -- losing the last committed fill to an OS buffer is exactly the
failure this module exists to prevent.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from tradelab.core.enums import OrderStatus
from tradelab.core.money import ZERO, to_decimal
from tradelab.core.types import Fill, Order
from tradelab.risk.killswitch import HaltLevel, KillSwitch, RiskState

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    broker_order_id TEXT,
    strategy_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    instrument_key  TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    limit_price     TEXT,
    time_in_force   TEXT NOT NULL,
    status          TEXT NOT NULL,
    filled_quantity TEXT NOT NULL DEFAULT '0',
    avg_fill_price  TEXT NOT NULL DEFAULT '0',
    commission      TEXT NOT NULL DEFAULT '0',
    fees            TEXT NOT NULL DEFAULT '0',
    reason          TEXT,
    reject_reason   TEXT,
    created_at      TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id);

CREATE TABLE IF NOT EXISTS fills (
    fill_id        TEXT PRIMARY KEY,
    order_id       TEXT NOT NULL,
    strategy_id    TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    instrument_key TEXT NOT NULL,
    side           TEXT NOT NULL,
    quantity       TEXT NOT NULL,
    price          TEXT NOT NULL,
    commission     TEXT NOT NULL DEFAULT '0',
    fees           TEXT NOT NULL DEFAULT '0',
    liquidity      TEXT,
    timestamp      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(timestamp);

CREATE TABLE IF NOT EXISTS risk_state (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    session_date             TEXT,
    day_start_equity         TEXT NOT NULL DEFAULT '0',
    peak_equity              TEXT NOT NULL DEFAULT '0',
    orders_today             INTEGER NOT NULL DEFAULT 0,
    new_positions_today      INTEGER NOT NULL DEFAULT 0,
    consecutive_losing_days  INTEGER NOT NULL DEFAULT 0,
    last_day_close_equity    TEXT NOT NULL DEFAULT '0',
    halt_level               TEXT NOT NULL DEFAULT 'NONE',
    halt_reason              TEXT,
    halt_timestamp           TEXT,
    halt_triggered_by        TEXT,
    updated_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_curve (
    timestamp      TEXT PRIMARY KEY,
    equity         TEXT NOT NULL,
    cash           TEXT NOT NULL,
    gross_exposure TEXT NOT NULL,
    net_exposure   TEXT NOT NULL,
    position_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    message    TEXT NOT NULL,
    payload    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
"""


class StateStore:
    """Durable session state.

    Deliberately stores decimals as TEXT. SQLite's REAL is a float, and routing
    the ledger through float storage reintroduces exactly the representation
    error that using `Decimal` in memory was meant to avoid.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            # Durability over throughput: losing the last committed fill to an
            # OS buffer is the failure this module exists to prevent.
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            row = connection.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"state file {self.path} has schema version {row['version']}, "
                    f"expected {SCHEMA_VERSION}. Migrate or start a new state file; "
                    "silently reading a mismatched schema risks restoring wrong "
                    "risk state."
                )

    # ------------------------------------------------------------------ orders

    def record_order(self, order: Order) -> None:
        request = order.request
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (
                    order_id, broker_order_id, strategy_id, symbol, instrument_key,
                    side, quantity, order_type, limit_price, time_in_force, status,
                    filled_quantity, avg_fill_price, commission, fees, reason,
                    reject_reason, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(order_id) DO UPDATE SET
                    broker_order_id = excluded.broker_order_id,
                    status          = excluded.status,
                    filled_quantity = excluded.filled_quantity,
                    avg_fill_price  = excluded.avg_fill_price,
                    commission      = excluded.commission,
                    fees            = excluded.fees,
                    reject_reason   = excluded.reject_reason,
                    updated_at      = excluded.updated_at
                """,
                (
                    order.order_id,
                    order.broker_order_id,
                    request.strategy_id,
                    request.instrument.symbol,
                    request.instrument.key,
                    request.side.value,
                    str(request.quantity),
                    request.order_type.value,
                    str(request.limit_price) if request.limit_price is not None else None,
                    request.time_in_force.value,
                    order.status.value,
                    str(order.filled_quantity),
                    str(order.average_fill_price),
                    str(order.commission),
                    str(order.fees),
                    request.reason,
                    order.reject_reason,
                    order.created_at.isoformat() if order.created_at else None,
                    (order.updated_at or datetime.now(UTC)).isoformat(),
                ),
            )

    def record_fill(self, fill: Fill) -> bool:
        """Persist a fill. Returns False if it was already recorded.

        Idempotent on `fill_id`, because IBKR redelivers executions after a
        reconnect and applying one twice corrupts the position ledger.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO fills (
                    fill_id, order_id, strategy_id, symbol, instrument_key, side,
                    quantity, price, commission, fees, liquidity, timestamp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.strategy_id,
                    fill.instrument.symbol,
                    fill.instrument.key,
                    fill.side.value,
                    str(fill.quantity),
                    str(fill.price),
                    str(fill.commission),
                    str(fill.fees),
                    fill.liquidity.value,
                    fill.timestamp.isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def has_fill(self, fill_id: str) -> bool:
        with self._connect() as connection:
            return (
                connection.execute("SELECT 1 FROM fills WHERE fill_id = ?", (fill_id,)).fetchone()
                is not None
            )

    def open_order_ids(self) -> list[str]:
        terminal = tuple(s.value for s in OrderStatus if s.is_terminal)
        placeholders = ",".join("?" * len(terminal))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT order_id FROM orders WHERE status NOT IN ({placeholders})",
                terminal,
            ).fetchall()
        return [row["order_id"] for row in rows]

    def position_quantities(self) -> dict[str, Decimal]:
        """Rebuild net position per instrument from the fill log.

        Derived from fills rather than stored directly, so it cannot drift from
        the fills that justify it. The broker remains authoritative -- this is
        the local view that reconciliation compares *against*.
        """
        with self._connect() as connection:
            rows = connection.execute("SELECT instrument_key, side, quantity FROM fills").fetchall()
        positions: dict[str, Decimal] = {}
        for row in rows:
            sign = Decimal(1) if row["side"] == "BUY" else Decimal(-1)
            key = row["instrument_key"]
            positions[key] = positions.get(key, ZERO) + sign * to_decimal(row["quantity"])
        return {k: v for k, v in positions.items() if v != 0}

    # -------------------------------------------------------------- risk state

    def save_risk_state(self, state: RiskState) -> None:
        halt = state.kill_switch.current
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO risk_state (
                    id, session_date, day_start_equity, peak_equity, orders_today,
                    new_positions_today, consecutive_losing_days,
                    last_day_close_equity, halt_level, halt_reason, halt_timestamp,
                    halt_triggered_by, updated_at
                ) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    session_date            = excluded.session_date,
                    day_start_equity        = excluded.day_start_equity,
                    peak_equity             = excluded.peak_equity,
                    orders_today            = excluded.orders_today,
                    new_positions_today     = excluded.new_positions_today,
                    consecutive_losing_days = excluded.consecutive_losing_days,
                    last_day_close_equity   = excluded.last_day_close_equity,
                    halt_level              = excluded.halt_level,
                    halt_reason             = excluded.halt_reason,
                    halt_timestamp          = excluded.halt_timestamp,
                    halt_triggered_by       = excluded.halt_triggered_by,
                    updated_at              = excluded.updated_at
                """,
                (
                    state.session_date.isoformat() if state.session_date else None,
                    str(state.day_start_equity),
                    str(state.peak_equity),
                    state.orders_today,
                    state.new_positions_today,
                    state.consecutive_losing_days,
                    str(state.last_day_close_equity),
                    state.kill_switch.level.value,
                    halt.reason if halt else None,
                    halt.timestamp.isoformat() if halt else None,
                    halt.triggered_by if halt else None,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def load_risk_state(self) -> RiskState | None:
        """Restore risk state, or None if this is a first run.

        A HARD halt is restored as a HARD halt. That is the point: a system
        that crashes after tripping a drawdown kill switch must not come back
        up trading. Restarting is not a re-arm.
        """
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
        if row is None:
            return None

        state = RiskState(
            session_date=date.fromisoformat(row["session_date"]) if row["session_date"] else None,
            day_start_equity=to_decimal(row["day_start_equity"]),
            peak_equity=to_decimal(row["peak_equity"]),
            orders_today=int(row["orders_today"]),
            new_positions_today=int(row["new_positions_today"]),
            consecutive_losing_days=int(row["consecutive_losing_days"]),
            last_day_close_equity=to_decimal(row["last_day_close_equity"]),
            kill_switch=KillSwitch(),
        )
        level = HaltLevel(row["halt_level"])
        if level is not HaltLevel.NONE:
            state.kill_switch.trip(
                level,
                row["halt_reason"] or "restored from persisted state",
                datetime.fromisoformat(row["halt_timestamp"])
                if row["halt_timestamp"]
                else datetime.now(UTC),
                row["halt_triggered_by"] or "restored",
            )
        return state

    # ------------------------------------------------------------------ events

    def log_event(
        self,
        kind: str,
        message: str,
        severity: str = "INFO",
        payload: dict | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO events (timestamp, kind, severity, message, payload) "
                "VALUES (?,?,?,?,?)",
                (
                    (timestamp or datetime.now(UTC)).isoformat(),
                    kind,
                    severity,
                    message,
                    json.dumps(payload, default=str) if payload else None,
                ),
            )

    def recent_events(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def record_equity(self, point) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO equity_curve "
                "(timestamp, equity, cash, gross_exposure, net_exposure, position_count) "
                "VALUES (?,?,?,?,?,?)",
                (
                    point.timestamp.isoformat(),
                    str(point.equity),
                    str(point.cash),
                    str(point.gross_exposure),
                    str(point.net_exposure),
                    point.position_count,
                ),
            )

    def equity_history(self) -> list[tuple[datetime, Decimal]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT timestamp, equity FROM equity_curve ORDER BY timestamp"
            ).fetchall()
        return [(datetime.fromisoformat(r["timestamp"]), to_decimal(r["equity"])) for r in rows]
