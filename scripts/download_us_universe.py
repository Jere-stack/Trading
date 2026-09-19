#!/usr/bin/env python
"""Download a survivorship-free US common-stock universe from EODHD.

Run:  .venv/bin/python scripts/download_us_universe.py --years 15

Universe definition, and why each filter is there:

* **Type == "Common Stock".** Excludes funds, ETFs, mutual funds, preferred
  stock, warrants and units. Of 51,104 active US tickers only 17,822 are
  common stock; most of the rest are fund share classes on NMFQS.
* **Major exchanges only** (NASDAQ, NYSE, NYSE MKT/AMEX). Excludes PINK,
  OTCGREY, OTCQB and OTCMKTS, whose spreads put them far outside the cost
  budget of a EUR 10k account -- the same reasoning that ruled out Helsinki
  micro-caps.
* **Active AND delisted.** This is the point. Delisted common stock on these
  exchanges outnumbers active by roughly 2.7 to 1, so a universe of current
  listings alone is missing the majority of the companies that ever traded.

Written incrementally: each symbol lands on disk as it arrives, so an
interrupted run loses nothing but the tail, and the dataset is only marked
complete when its provenance metadata is written at the end.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradelab.data.providers.eodhd import EodhdProvider
from tradelab.data.schema import Adjustment, BarSetMetadata, SchemaError, normalise_bars
from tradelab.data.store import BarStore

MAJOR_EXCHANGES = {"NASDAQ", "NYSE", "NYSE MKT", "AMEX"}


def build_universe(provider: EodhdProvider, limit: int | None) -> list[dict]:
    """Active plus delisted common stock on the major US exchanges."""
    rows: list[dict] = []
    for delisted in (False, True):
        frame = provider.symbols("US", delisted=delisted)
        subset = frame[
            (frame["Type"] == "Common Stock")
            & (frame["Exchange"].isin(MAJOR_EXCHANGES))
            & (frame["Currency"] == "USD")
        ]
        for record in subset.to_dict("records"):
            record["delisted"] = delisted
            rows.append(record)

    seen: set[str] = set()
    unique: list[dict] = []
    for record in rows:
        code = str(record["Code"]).upper()
        if code in seen:
            continue
        seen.add(code)
        unique.append(record)

    if not limit or limit >= len(unique):
        return unique

    # Preserve the active/delisted mix when sampling. Taking the first N would
    # return only active names, since active tickers are collected first --
    # which would quietly reproduce the survivorship bias this universe exists
    # to eliminate.
    active = [r for r in unique if not r["delisted"]]
    dead = [r for r in unique if r["delisted"]]
    dead_share = len(dead) / len(unique)
    n_dead = min(len(dead), round(limit * dead_share))
    n_active = min(len(active), limit - n_dead)
    return active[:n_active] + dead[:n_dead]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="us-universe")
    parser.add_argument("--root", default="data")
    parser.add_argument("--years", type=int, default=15)
    parser.add_argument("--limit", type=int, default=None, help="Cap symbol count")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--requests-per-minute", type=int, default=900)
    parser.add_argument("--max-failure-rate", type=float, default=0.15)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    provider = EodhdProvider(
        exchange="US",
        adjustment=Adjustment.SPLIT_AND_DIVIDEND,
        max_workers=args.workers,
        requests_per_minute=args.requests_per_minute,
    )
    provider.max_failure_rate = args.max_failure_rate

    print("building survivorship-free US common-stock universe...")
    universe = build_universe(provider, args.limit)
    dead = sum(1 for r in universe if r["delisted"])
    print(
        f"  {len(universe):,} symbols: {len(universe) - dead:,} active, "
        f"{dead:,} delisted ({dead / max(len(universe), 1):.1%})"
    )

    symbols = [str(r["Code"]).upper() for r in universe]
    end = datetime.now(UTC)
    start = end - timedelta(days=365 * args.years)

    store = BarStore(Path(args.root))
    store.open_dataset(args.dataset, overwrite=args.overwrite)

    print(
        f"fetching {args.years}y with {args.workers} workers at "
        f"{args.requests_per_minute}/min (~{len(symbols) / args.requests_per_minute:.0f} min)..."
    )
    written = 0
    rows = 0
    rejected: dict[str, str] = {}
    began = time.monotonic()
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    stored: list[str] = []

    for frame in provider.iter_fetch(symbols, start, end):
        symbol = str(frame["symbol"].iloc[0]) if "symbol" in frame.columns else "?"
        try:
            canonical = normalise_bars(frame, tz="UTC")
            store.write_symbol(args.dataset, canonical)
        except SchemaError as exc:
            # A symbol that fails validation is recorded, never silently kept.
            rejected[symbol] = str(exc).split("\n")[0]
            continue
        stored.append(str(canonical["symbol"].iloc[0]))
        written += 1
        rows += len(canonical)
        lo = canonical["timestamp"].min().to_pydatetime()
        hi = canonical["timestamp"].max().to_pydatetime()
        first_ts = lo if first_ts is None else min(first_ts, lo)
        last_ts = hi if last_ts is None else max(last_ts, hi)
        if written % 500 == 0:
            rate = written / max(time.monotonic() - began, 1e-9) * 60
            remaining = (len(symbols) - written) / max(rate, 1e-9)
            print(
                f"  {written:>6,}/{len(symbols):,}  {rows:>12,} rows  "
                f"{rate:>5.0f}/min  ~{remaining:.0f} min left"
            )

    elapsed = (time.monotonic() - began) / 60
    print(f"\nfetched {written:,} symbols, {rows:,} rows in {elapsed:.1f} min")
    print(f"  fetch failures: {len(provider.failures):,}")
    print(f"  schema rejects: {len(rejected):,}")

    if written == 0:
        print("nothing stored; aborting without writing metadata")
        return

    metadata = BarSetMetadata(
        provider=provider.name,
        adjustment=Adjustment.SPLIT_AND_DIVIDEND,
        bar_size="1 day",
        currency="USD",
        fetched_at=datetime.now(UTC),
        symbols=tuple(sorted(stored)),
        start=first_ts,
        end=last_ts,
        includes_delisted=dead > 0,
        notes=(
            f"US common stock on {sorted(MAJOR_EXCHANGES)}; {args.years}y; "
            f"{len(provider.failures)} fetch failures, {len(rejected)} schema rejects"
        ),
    )
    store.finalize(args.dataset, metadata)
    print(f"finalised dataset '{args.dataset}' with {len(stored):,} symbols")

    diagnostics = Path(args.root) / "bars" / args.dataset / "_diagnostics.json"
    diagnostics.write_text(
        json.dumps(
            {
                "fetch_failures": provider.failures,
                "schema_rejects": rejected,
                "dropped_bars": provider.dropped_bars,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"diagnostics written to {diagnostics}")


if __name__ == "__main__":
    main()
