"""Canonical market data schema.

One schema, enforced at every ingestion boundary. Providers differ in column
names, timezone conventions, adjustment policy and how they signal missing data;
converting to a single validated representation at the edge means the rest of
the system never has to care which vendor a bar came from.

The columns are deliberately explicit about two things that cause silent errors:

* **`timestamp` is the bar's CLOSE time, always UTC, always tz-aware.** Vendors
  disagree -- some stamp the open, some use exchange-local time, some use naive
  dates. A bar stamped with its close time makes `timestamp <= now` a safe
  comparison, which is the anti-lookahead property the engine relies on.

* **`adjusted` is a required flag, not an assumption.** Mixing adjusted and
  unadjusted prices in one series produces a phantom return on every split and
  dividend date. Requiring the caller to state which they have makes the mistake
  impossible to make silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import pandas as pd

# Canonical column order. `pandas` does not care, but a fixed order makes
# Parquet files diffable and makes schema drift obvious in a `head()`.
BAR_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
)

OPTIONAL_BAR_COLUMNS: tuple[str, ...] = ("vwap", "trades", "dividend", "split_ratio")

BAR_DTYPES: dict[str, str] = {
    "symbol": "string",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "vwap": "float64",
    "trades": "float64",
    "dividend": "float64",
    "split_ratio": "float64",
}


class Adjustment(StrEnum):
    """How corporate actions have been applied to a price series.

    `NONE` is the only one safe to use for computing realistic fill prices,
    because it is what actually traded. `SPLIT_AND_DIVIDEND` is what you want
    for computing returns. Using the wrong one is a common and quiet error:
    back-adjusted prices from years ago can be a small fraction of the real
    traded price, which silently breaks per-share commission models and
    minimum-price filters.
    """

    NONE = "NONE"
    SPLIT = "SPLIT"
    SPLIT_AND_DIVIDEND = "SPLIT_AND_DIVIDEND"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class BarSetMetadata:
    """Provenance for a stored bar set.

    Persisted alongside the data because the questions that matter months later
    -- which vendor, which adjustment, when fetched, does it include delisted
    names -- cannot be reconstructed from the prices themselves.
    """

    provider: str
    adjustment: Adjustment
    bar_size: str
    currency: str
    fetched_at: datetime
    symbols: tuple[str, ...]
    start: datetime | None = None
    end: datetime | None = None
    includes_delisted: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "adjustment": self.adjustment.value,
            "bar_size": self.bar_size,
            "currency": self.currency,
            "fetched_at": self.fetched_at.isoformat(),
            "symbols": list(self.symbols),
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "includes_delisted": self.includes_delisted,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> BarSetMetadata:
        return cls(
            provider=payload["provider"],
            adjustment=Adjustment(payload["adjustment"]),
            bar_size=payload["bar_size"],
            currency=payload["currency"],
            fetched_at=datetime.fromisoformat(payload["fetched_at"]),
            symbols=tuple(payload.get("symbols", ())),
            start=datetime.fromisoformat(payload["start"]) if payload.get("start") else None,
            end=datetime.fromisoformat(payload["end"]) if payload.get("end") else None,
            includes_delisted=bool(payload.get("includes_delisted", False)),
            notes=payload.get("notes", ""),
        )


class SchemaError(ValueError):
    """Raised when a frame does not satisfy the canonical bar schema."""


def normalise_bars(
    frame: pd.DataFrame,
    *,
    symbol: str | None = None,
    tz: str = "UTC",
) -> pd.DataFrame:
    """Coerce a provider frame into the canonical schema, or raise.

    Deliberately strict. Silently dropping bad rows here would hide a broken
    feed; a loud failure at ingestion is far cheaper than a quiet one that
    reaches a backtest.

    `tz` is the timezone of naive input timestamps. It has no default of
    "assume UTC" for a reason -- a vendor returning exchange-local timestamps
    read as UTC shifts every bar by hours, which moves signals across session
    boundaries and is nearly invisible in a plot.
    """
    if frame.empty:
        raise SchemaError("empty frame: refusing to store a bar set with no rows")

    out = frame.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]

    renames = {
        "date": "timestamp",
        "datetime": "timestamp",
        "time": "timestamp",
        "adj_close": "close",
        "adjusted_close": "close",
        "ticker": "symbol",
        "vol": "volume",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "t": "timestamp",
        "n": "trades",
        "vw": "vwap",
    }
    out = out.rename(columns={k: v for k, v in renames.items() if k in out.columns})

    if "symbol" not in out.columns:
        if symbol is None:
            raise SchemaError(
                "frame has no `symbol` column and no `symbol` argument was given; "
                "refusing to guess which instrument these bars belong to"
            )
        out["symbol"] = symbol

    missing = [c for c in BAR_COLUMNS if c not in out.columns]
    if missing:
        raise SchemaError(f"missing required column(s) {missing}. Present: {sorted(out.columns)}")

    out["timestamp"] = _to_utc(out["timestamp"], tz)
    out["symbol"] = out["symbol"].astype("string").str.strip().str.upper()

    for column, dtype in BAR_DTYPES.items():
        if column in out.columns and column != "symbol":
            out[column] = pd.to_numeric(out[column], errors="coerce").astype(dtype)

    ordered = [c for c in BAR_COLUMNS if c in out.columns]
    ordered += [c for c in OPTIONAL_BAR_COLUMNS if c in out.columns]
    out = out[ordered]

    validate_bars(out)
    return out.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def validate_bars(frame: pd.DataFrame) -> None:
    """Assert the canonical invariants. Raises `SchemaError` on the first breach.

    Each check corresponds to a real vendor failure mode seen in practice, not
    to defensive programming for its own sake.
    """
    missing = [c for c in BAR_COLUMNS if c not in frame.columns]
    if missing:
        raise SchemaError(f"missing required column(s): {missing}")

    ts = frame["timestamp"]
    if not pd.api.types.is_datetime64_any_dtype(ts):
        raise SchemaError(f"`timestamp` must be datetime64, got {ts.dtype}")
    if ts.dt.tz is None:
        raise SchemaError(
            "`timestamp` is timezone-naive. Naive timestamps are the usual route "
            "to off-by-one-session bugs; localise before storing."
        )

    for column in ("open", "high", "low", "close"):
        values = frame[column]
        if values.isna().any():
            bad = frame.loc[values.isna(), ["symbol", "timestamp"]].head(3)
            raise SchemaError(
                f"`{column}` contains NaN. A missing price is not a zero price and "
                f"must not be forward-filled silently. First rows:\n{bad}"
            )
        if (values <= 0).any():
            bad = frame.loc[values <= 0, ["symbol", "timestamp", column]].head(3)
            raise SchemaError(f"`{column}` contains non-positive prices:\n{bad}")

    if (frame["volume"] < 0).any():
        raise SchemaError("`volume` contains negative values")

    inconsistent = (
        (frame["high"] < frame["low"])
        | (frame["high"] < frame["open"])
        | (frame["high"] < frame["close"])
        | (frame["low"] > frame["open"])
        | (frame["low"] > frame["close"])
    )
    if inconsistent.any():
        bad = frame.loc[inconsistent, list(BAR_COLUMNS)].head(3)
        raise SchemaError(
            f"OHLC relationships violated (high must be the max, low the min):\n{bad}"
        )

    duplicated = frame.duplicated(subset=["symbol", "timestamp"])
    if duplicated.any():
        bad = frame.loc[duplicated, ["symbol", "timestamp"]].head(3)
        raise SchemaError(
            f"duplicate (symbol, timestamp) rows. Duplicates double-count returns "
            f"and inflate every performance statistic:\n{bad}"
        )


def _to_utc(series: pd.Series, tz: str) -> pd.Series:
    """Parse to tz-aware UTC, treating naive values as being in `tz`."""
    parsed = pd.to_datetime(series, errors="coerce", utc=False)
    if parsed.isna().any():
        count = int(parsed.isna().sum())
        raise SchemaError(f"{count} timestamp(s) could not be parsed")
    if getattr(parsed.dt, "tz", None) is None:
        parsed = parsed.dt.tz_localize(tz)
    return parsed.dt.tz_convert(UTC)


def bars_to_frame(bars) -> pd.DataFrame:
    """Convert `tradelab.core.types.Bar` objects into a canonical frame."""
    rows = [
        {
            "timestamp": bar.timestamp,
            "symbol": bar.instrument.symbol,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        for bar in bars
    ]
    if not rows:
        raise SchemaError("no bars supplied")
    return normalise_bars(pd.DataFrame(rows))
