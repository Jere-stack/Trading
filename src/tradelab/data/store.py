"""Parquet-backed bar storage.

Parquet with per-symbol partitioning, and no database server. The access
pattern is "read a few years of daily bars for 10-50 symbols, repeatedly",
which columnar files serve well and which does not justify operating a
database.

Metadata travels with the data. Months later the questions that matter --
which vendor, which adjustment policy, when was this fetched, does it include
delisted names -- cannot be reconstructed from prices, so they are persisted
in a sidecar JSON and are required, not optional.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from tradelab.core.enums import Venue
from tradelab.core.types import Bar, Instrument
from tradelab.data.schema import (
    Adjustment,
    BarSetMetadata,
    SchemaError,
    normalise_bars,
    validate_bars,
)


class BarStore:
    """Read/write canonical bar sets under a root directory.

    Layout:
        <root>/bars/<dataset>/<symbol>.parquet
        <root>/bars/<dataset>/_metadata.json
    """

    def __init__(self, root: Path | str = "data") -> None:
        self.root = Path(root)

    def dataset_path(self, dataset: str) -> Path:
        return self.root / "bars" / dataset

    # -------------------------------------------------------------- writing

    def write(
        self,
        dataset: str,
        frame: pd.DataFrame,
        metadata: BarSetMetadata,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Persist a canonical frame plus its metadata.

        Refuses to overwrite silently. A dataset directory that already exists
        may hold a differently-adjusted or differently-sourced series, and
        mixing them produces a phantom return at the join.
        """
        validate_bars(frame)
        path = self.dataset_path(dataset)
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"dataset '{dataset}' already exists at {path}. Pass overwrite=True "
                "deliberately: mixing series with different providers or adjustment "
                "policies creates a phantom return at the splice point."
            )
        path.mkdir(parents=True, exist_ok=True)

        for symbol, group in frame.groupby("symbol", observed=True):
            target = path / f"{_safe(symbol)}.parquet"
            group.reset_index(drop=True).to_parquet(target, index=False, compression="zstd")

        (path / "_metadata.json").write_text(
            json.dumps(metadata.to_dict(), indent=2), encoding="utf-8"
        )
        return path

    # -------------------------------------------------------------- reading

    def read(
        self,
        dataset: str,
        symbols: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        path = self.dataset_path(dataset)
        if not path.exists():
            raise FileNotFoundError(
                f"no dataset '{dataset}' at {path}. Available: {self.list_datasets()}"
            )

        files = (
            [path / f"{_safe(s)}.parquet" for s in symbols]
            if symbols
            else sorted(path.glob("*.parquet"))
        )
        missing = [f.name for f in files if not f.exists()]
        if missing:
            raise FileNotFoundError(f"dataset '{dataset}' has no data for: {missing}")
        if not files:
            raise FileNotFoundError(f"dataset '{dataset}' contains no parquet files")

        frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        if start is not None:
            frame = frame[frame["timestamp"] >= pd.Timestamp(start)]
        if end is not None:
            frame = frame[frame["timestamp"] <= pd.Timestamp(end)]
        if frame.empty:
            raise SchemaError(
                f"dataset '{dataset}' has no rows in the requested window [{start} .. {end}]"
            )
        return frame.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    # ------------------------------------------------- incremental writing

    def open_dataset(self, dataset: str, *, overwrite: bool = False) -> Path:
        """Prepare a dataset directory for streaming writes.

        Used for universes too large to hold in memory, where losing an
        hour-long download to a crash is a real cost. The dataset is only
        considered complete once `finalize` writes its metadata -- a directory
        of parquet files without `_metadata.json` is an interrupted download,
        and `metadata()` will refuse to read it.
        """
        path = self.dataset_path(dataset)
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"dataset '{dataset}' already exists at {path}. Pass overwrite=True "
                "deliberately: mixing series with different providers or adjustment "
                "policies creates a phantom return at the splice point."
            )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_symbol(self, dataset: str, frame: pd.DataFrame) -> Path:
        """Append one symbol's bars to an open dataset."""
        validate_bars(frame)
        symbols = frame["symbol"].unique()
        if len(symbols) != 1:
            raise ValueError(f"expected exactly one symbol per call, got {len(symbols)}")
        target = self.dataset_path(dataset) / f"{_safe(symbols[0])}.parquet"
        frame.reset_index(drop=True).to_parquet(target, index=False, compression="zstd")
        return target

    def finalize(self, dataset: str, metadata: BarSetMetadata) -> Path:
        """Mark a streamed dataset complete by writing its provenance."""
        path = self.dataset_path(dataset)
        if not path.exists():
            raise FileNotFoundError(f"dataset '{dataset}' was never opened")
        (path / "_metadata.json").write_text(
            json.dumps(metadata.to_dict(), indent=2), encoding="utf-8"
        )
        return path

    def metadata(self, dataset: str) -> BarSetMetadata:
        path = self.dataset_path(dataset) / "_metadata.json"
        if not path.exists():
            raise FileNotFoundError(
                f"dataset '{dataset}' has no _metadata.json. Provenance is required: "
                "without it the adjustment policy and survivorship status are unknown, "
                "and results computed from it cannot be trusted."
            )
        return BarSetMetadata.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_datasets(self) -> list[str]:
        base = self.root / "bars"
        if not base.exists():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    def symbols(self, dataset: str) -> list[str]:
        path = self.dataset_path(dataset)
        if not path.exists():
            return []
        return sorted(p.stem for p in path.glob("*.parquet"))

    # ------------------------------------------------------- domain objects

    def to_bars(
        self,
        frame: pd.DataFrame,
        instruments: dict[str, Instrument] | None = None,
        *,
        venue: Venue = Venue.SMART,
        currency: str = "USD",
    ) -> list[Bar]:
        """Convert a canonical frame into engine `Bar` objects.

        Uses `Decimal(str(...))` rather than `Decimal(float)` so that a stored
        100.25 becomes exactly Decimal("100.25") instead of inheriting binary
        float error into the ledger.
        """
        from decimal import Decimal

        instruments = instruments or {}
        bars: list[Bar] = []
        for row in frame.itertuples(index=False):
            instrument = instruments.get(row.symbol)
            if instrument is None:
                instrument = Instrument(symbol=row.symbol, venue=venue, currency=currency)
                instruments[row.symbol] = instrument
            bars.append(
                Bar(
                    instrument=instrument,
                    timestamp=row.timestamp.to_pydatetime(),
                    open=Decimal(str(row.open)),
                    high=Decimal(str(row.high)),
                    low=Decimal(str(row.low)),
                    close=Decimal(str(row.close)),
                    volume=Decimal(str(row.volume)),
                )
            )
        return bars


def _safe(symbol: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(symbol).upper())


def ingest_csv(
    store: BarStore,
    dataset: str,
    paths: list[Path | str],
    *,
    provider: str,
    adjustment: Adjustment,
    currency: str,
    bar_size: str = "1 day",
    tz: str = "UTC",
    includes_delisted: bool = False,
    overwrite: bool = False,
    notes: str = "",
) -> BarSetMetadata:
    """Ingest local CSV files into a dataset.

    `adjustment` and `includes_delisted` are required arguments with no
    defaults that guess. They are the two facts that determine whether a
    backtest built on this data means anything, and a caller who does not know
    them should find out before proceeding rather than accept a default.
    """
    frames = []
    for path in paths:
        path = Path(path)
        raw = pd.read_csv(path)
        symbol = path.stem.upper()
        frames.append(normalise_bars(raw, symbol=symbol, tz=tz))
    if not frames:
        raise ValueError("no CSV paths supplied")

    combined = pd.concat(frames, ignore_index=True)
    validate_bars(combined)
    metadata = BarSetMetadata(
        provider=provider,
        adjustment=adjustment,
        bar_size=bar_size,
        currency=currency.upper(),
        fetched_at=datetime.now(UTC),
        symbols=tuple(sorted(combined["symbol"].unique())),
        start=combined["timestamp"].min().to_pydatetime(),
        end=combined["timestamp"].max().to_pydatetime(),
        includes_delisted=includes_delisted,
        notes=notes,
    )
    store.write(dataset, combined, metadata, overwrite=overwrite)
    return metadata
