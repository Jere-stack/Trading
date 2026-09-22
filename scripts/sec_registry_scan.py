#!/usr/bin/env python
"""Build a security master from the SEC: every XBRL filer since 2009, with
current tickers, former names, industry code and domicile.

Round 9 matched symbols to companies by NAME, because the SEC's ticker file
sits on www.sec.gov and this project does not send a personal email to reach
it. Name matching left ~25% of delisted symbols unidentified. The fix is to
stop guessing from the symbol side and enumerate the registry side instead:

  1. Every company that filed an XBRL balance sheet -- US GAAP or IFRS, in any
     of the common reporting currencies -- appears in the SEC `frames` for
     total assets. That gives the complete list of CIKs, including companies
     since delisted, acquired or renamed.
  2. For each CIK, the `submissions` record gives its CURRENT tickers (so a
     listed symbol is identified by ticker, authoritatively), its FORMER NAMES
     (so "Michael Kors" is found under Capri Holdings), its SIC code, and its
     business address (so domicile is known rather than inferred from a
     vendor label).

~17,000 requests at under 8/second. Every response is cached, so an
interrupted run resumes where it stopped and a re-run costs nothing.

Run:  .venv/bin/python -m scripts.sec_registry_scan
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from tradelab.data.sec import SecClient

OUT = Path("data/sec/registry.parquet")
CURRENCIES = (
    "USD", "EUR", "GBP", "CHF", "DKK", "SEK", "NOK", "JPY", "CNY", "TWD",
    "KRW", "INR", "BRL", "CAD", "AUD", "HKD", "ILS", "MXN", "ZAR",
)


def frame_ciks(client: SecClient) -> dict[int, str]:
    ciks: dict[int, str] = {}
    for year in range(2009, 2026):
        for period in (f"CY{year}Q4I", f"CY{year}Q2I"):
            for taxonomy in ("us-gaap", "ifrs-full"):
                for unit in CURRENCIES:
                    payload = client.get_json(
                        f"/api/xbrl/frames/{taxonomy}/Assets/{unit}/{period}.json"
                    )
                    for row in (payload or {}).get("data", []):
                        ciks.setdefault(int(row["cik"]), row["entityName"])
    return ciks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shard", default=None,
        help="k/n: fetch only every n-th CIK starting at k, to fill the cache in parallel. "
             "Run n shards with the per-process interval raised so their SUM stays under the "
             "SEC's 10 requests/second, then run once without --shard to write the registry.",
    )
    parser.add_argument("--interval", type=float, default=0.13)
    args = parser.parse_args()
    client = SecClient(min_interval=args.interval)
    print("collecting CIKs from frames (us-gaap + ifrs-full, 19 currencies, 2009-2025)...", flush=True)
    ciks = frame_ciks(client)
    print(f"  {len(ciks):,} distinct XBRL filers; requests so far {client.requests_made:,}", flush=True)

    ordered = sorted(ciks)
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        for cik in ordered[k::n]:
            client.submissions(cik)
        print(f"shard {args.shard} done; network requests {client.requests_made:,}", flush=True)
        return

    rows = []
    for i, cik in enumerate(ordered, 1):
        sub = client.submissions(cik) or {}
        address = (sub.get("addresses") or {}).get("business") or {}
        rows.append({
            "cik": cik,
            "name": sub.get("name") or ciks[cik],
            "frame_name": ciks[cik],
            "tickers": "|".join(t.upper() for t in (sub.get("tickers") or [])),
            "exchanges": "|".join(str(e) for e in (sub.get("exchanges") or []) if e),
            "former_names": "|".join(f.get("name", "") for f in (sub.get("formerNames") or [])),
            "sic": sub.get("sic"),
            "category": sub.get("category"),
            "state_of_inc": sub.get("stateOfIncorporation"),
            "country": address.get("stateOrCountryDescription") or address.get("stateOrCountry"),
            "entity_type": sub.get("entityType"),
        })
        if i % 1000 == 0:
            print(f"  {i:,}/{len(ciks):,} submissions  (network requests {client.requests_made:,})", flush=True)

    frame = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUT, index=False)
    print(f"wrote {OUT}: {len(frame):,} filers, "
          f"{(frame['tickers'] != '').sum():,} with a current ticker", flush=True)


if __name__ == "__main__":
    main()
