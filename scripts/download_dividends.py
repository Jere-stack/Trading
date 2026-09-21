#!/usr/bin/env python
"""Download dividend declaration history for the survivorship-free US universe.

One call per symbol against EODHD's `/div` endpoint. Against a 100,000/day
allowance and a 1,000/minute ceiling, 16,737 symbols is roughly twenty minutes
and a sixth of a day's quota.

**Symbols come from the bar dataset on disk, not from a fresh symbol list.**
That includes delisted tickers, which is the whole point: a company that cuts
its dividend and then fails is the single most informative observation in the
sample, and a universe of survivors excludes exactly those. The same mistake
made cash merger arbitrage look profitable until the broken deals were put
back in.

    EODHD_API_TOKEN=... .venv/bin/python scripts/download_dividends.py

Resumable, and the resumption is real rather than nominal: results are flushed
to disk every `--checkpoint` symbols, so a run killed at 5,000 symbols resumes
from 5,000 rather than from zero.

That is not hypothetical. The first attempt accumulated everything in memory
and wrote once at the end; the container it ran in was recycled after 5,000
symbols and the entire run was lost, having written nothing. A long job whose
output exists only in memory has no partial success -- only total success or
total loss.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from tradelab.data.providers.eodhd import EodhdProvider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--out", type=Path, default=Path("data/dividends/us-universe"))
    parser.add_argument("--start", default="2005-01-01")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="Stop after N symbols (for testing)")
    parser.add_argument(
        "--checkpoint",
        type=int,
        default=500,
        help="Flush to disk every N symbols. Lower is safer, slightly slower.",
    )
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    symbols = sorted(p.stem for p in args.bars.glob("*.parquet"))
    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        raise SystemExit(f"no bar files in {args.bars}; download the universe first")

    args.out.mkdir(parents=True, exist_ok=True)
    shard_path = args.out / "dividends.parquet"
    done: set[str] = set()
    if shard_path.exists():
        done = set(pd.read_parquet(shard_path, columns=["symbol"])["symbol"].unique())
        print(f"resuming: {len(done):,} symbols already downloaded")

    pending = [s for s in symbols if s not in done]
    print(f"{len(symbols):,} symbols in universe, {len(pending):,} to fetch")
    if not pending:
        print("nothing to do")
        return

    provider = EodhdProvider(api_token=args.token, exchange="US", max_workers=args.workers)
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.now(UTC)

    def flush(buffer: list[pd.DataFrame]) -> None:
        """Append `buffer` to the parquet on disk, atomically.

        Written to a temporary file and renamed, so a process killed mid-write
        leaves the previous good file rather than a truncated one.
        """
        if not buffer:
            return
        merged = pd.concat(buffer, ignore_index=True)
        if shard_path.exists():
            merged = pd.concat([pd.read_parquet(shard_path), merged], ignore_index=True)
        merged = merged.sort_values(["symbol", "ex_date"]).reset_index(drop=True)
        tmp = shard_path.with_suffix(".parquet.tmp")
        merged.to_parquet(tmp, index=False)
        tmp.replace(shard_path)

    buffer: list[pd.DataFrame] = []
    began = time.time()
    with_dividends = 0
    processed = 0
    for index, frame in enumerate(provider.iter_dividends(pending, start, end), start=1):
        processed = index
        if not frame.empty:
            buffer.append(frame)
            with_dividends += 1
        if index % args.checkpoint == 0:
            flush(buffer)
            buffer = []
            rate = index / max(time.time() - began, 1e-9)
            remaining = (len(pending) - index) / max(rate, 1e-9)
            print(
                f"  {index:>6,}/{len(pending):,}  {with_dividends:,} paying  "
                f"{rate:.0f}/s  ~{remaining / 60:.0f} min left  "
                f"{len(provider.failures):,} failed  [saved]",
                flush=True,
            )
    flush(buffer)

    if not shard_path.exists():
        print("no dividend records returned")
        return
    fresh = pd.read_parquet(shard_path)
    print(f"\nprocessed {processed:,} symbols this run")

    have_declaration = int(fresh["declaration_date"].notna().sum())
    metadata = {
        "downloaded": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "eodhd /div",
        "symbols_requested": len(symbols),
        "symbols_paying": int(fresh["symbol"].nunique()),
        "records": len(fresh),
        "with_declaration_date": have_declaration,
        "declaration_coverage": round(have_declaration / max(len(fresh), 1), 4),
        "failures": len(provider.failures),
        "range": [str(fresh["ex_date"].min()), str(fresh["ex_date"].max())],
        "note": (
            "Includes delisted tickers. Declaration coverage matters: an event "
            "study anchored on the ex-date starts after the market already "
            "knew, so records without a declaration date are not usable for "
            "announcement studies."
        ),
    }
    (args.out / "_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"\nwrote {len(fresh):,} records for {metadata['symbols_paying']:,} paying symbols")
    print(f"declaration date present on {metadata['declaration_coverage']:.1%}")
    print(f"failures: {len(provider.failures):,}")


if __name__ == "__main__":
    main()
