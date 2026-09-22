#!/usr/bin/env python
"""N13 step 1: map every symbol that ever entered the top 100 to its SEC CIK.

Then run GATE 0, the data audit fixed in the ledger before any fundamental was
downloaded: CIK coverage for names that later DELISTED must be within 6
percentage points of coverage for names that SURVIVED. If it is not, the test
does not run -- a fundamentals strategy tested on a universe that silently
drops the failures is testing survivorship bias, not quality.

MATCHING RULES, fixed before the run:

  1. Candidates come from the SEC `frames` universe (every company reporting
     total assets at any year-end 2009-2025, including since-delisted ones),
     matched on normalised name: exact, then unique prefix (EODHD truncates
     long names -- "Applied Opt"), then best fuzzy match >= 0.80.
  2. TICKER CONFIRMATION: a candidate whose current SEC ticker list contains
     the symbol is accepted -- but for a DELISTED symbol only if the names also
     agree (similarity >= 0.80). A delisted company's old ticker is frequently
     reissued; the current holder of "ABX" is not the company that traded as
     ABX in 2014, and accepting it would graft one firm's accounts onto
     another's prices.
  3. NAME-ONLY: a delisted symbol with no ticker confirmation is accepted at
     name similarity >= 0.92. Delisted firms have no current ticker to confirm
     against, so this is the only route, and it is set strict on purpose.
  4. Anything else is left unmatched and counted, never guessed.

Run:  .venv/bin/python -m scripts.n13_map_ciks
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.data.sec import SecClient, name_similarity, normalise_name
from tradelab.research.cross_section import month_end_dates

PANEL = Path("data/signals/n12")
OUT = Path("data/sec/cik_map.csv")
POOL = 100
MIN_DOLLAR_VOLUME = 2_000_000
LIQUIDITY_WINDOW = 60
LOOKBACK = 252
GATE0_MAX_GAP = 0.06


def ever_top_pool() -> list[str]:
    closes = pd.read_parquet(PANEL / "close.parquet")
    volumes = pd.read_parquet(PANEL / "dollar_volume.parquet")
    dates = [d for d in month_end_dates(closes.index) if d >= closes.index[LOOKBACK]]
    keep: set[str] = set()
    for date in dates:
        position = closes.index.get_loc(date)
        med = volumes.iloc[max(0, position - LIQUIDITY_WINDOW) : position + 1].median(skipna=True)
        med = med[(med >= MIN_DOLLAR_VOLUME) & closes.loc[date].notna()].dropna()
        keep.update(med.nlargest(POOL).index)
    return sorted(keep)


def base_ticker(symbol: str) -> str:
    """ABX_OLD -> ABX; BRK-B -> BRK-B. EODHD suffixes reissued tickers."""
    return re.sub(r"_OLD\d*$", "", symbol, flags=re.IGNORECASE).upper()


def target_frame(symbols: list[str]) -> pd.DataFrame:
    ref = pd.read_parquet("data/reference/us_symbols.parquet")
    ref = ref.assign(key=ref["Code"].str.upper()).drop_duplicates("key").set_index("key")
    rows = []
    for sym in symbols:
        hit = ref.loc[sym.upper()] if sym.upper() in ref.index else None
        rows.append({
            "symbol": sym,
            "ticker": base_ticker(sym),
            "name": None if hit is None else hit["Name"],
            # A symbol absent from the reference is treated as delisted: the
            # reference covers every live listing, so absence is itself evidence.
            "delisted": True if hit is None else bool(hit["delisted"]),
        })
    return pd.DataFrame(rows)


# Fixed before the second (final) gate-0 pass. Applied ONLY to symbols that
# remain unmatched, so a genuine US company the matcher misses is still counted
# against the gate rather than quietly removed. EODHD labels these Moscow,
# Kuwaiti, Thai and Colombian securities "NYSE / USD / Common Stock"; they are
# not exchange-listed in the US (any NYSE or NASDAQ listing must be SEC
# registered), and their prices are in local currency.
_FOREIGN_FORM = re.compile(
    r"\b(PJSC|OJSC|PAO|OAO|KSC|KSCC|KPSC|SAK|PUBLIC CO|PUBLIC COMPANY|SA ESP)\b",
    re.IGNORECASE,
)


def vendor_mislabel(ticker: str, name: str | None) -> bool:
    return len(ticker.replace("-", "").replace(".", "")) > 5 or bool(_FOREIGN_FORM.search(name or ""))


def sec_universe(client: SecClient) -> pd.DataFrame:
    """Every (cik, name) pair seen in the frames, ALL historical names kept.

    The first pass kept only the latest name per CIK, which broke renamed
    firms: EODHD still calls FCX "Freeport-McMoran Copper & Gold", the name
    the SEC used a decade ago. Keeping every spelling lets the old name match.
    """
    pairs: set[tuple[int, str]] = set()
    for year in range(2009, 2026):
        for period in (f"CY{year}Q4I", f"CY{year}Q2I"):
            for row in client.frame("Assets", period):
                pairs.add((int(row["cik"]), row["entityName"]))
    frame = pd.DataFrame(sorted(pairs), columns=["cik", "sec_name"])
    frame["norm"] = frame["sec_name"].map(normalise_name)
    return frame


def build_index(universe: pd.DataFrame):
    exact: dict[str, set[int]] = {}
    tokens: dict[str, set[int]] = {}
    names: dict[int, set[str]] = {}
    for r in universe.itertuples():
        if not r.norm:
            continue
        names.setdefault(r.cik, set()).add(r.norm)
        for key in (r.norm, r.norm.replace(" ", ""), " ".join(sorted(r.norm.split()))):
            exact.setdefault(key, set()).add(r.cik)
        for tok in r.norm.split():
            if len(tok) >= 3:
                tokens.setdefault(tok, set()).add(r.cik)
    return exact, tokens, names


def candidates(norm: str, index, k: int = 8) -> list[tuple[int, float]]:
    """Liberal candidate generation. Acceptance, not generation, is strict."""
    exact, tokens, names = index
    if not norm:
        return []
    for key in (norm, norm.replace(" ", ""), " ".join(sorted(norm.split()))):
        if key in exact:
            return [(cik, 1.0) for cik in sorted(exact[key])][:k]
    pool: set[int] = set()
    for tok in norm.split():
        if len(tok) >= 3:
            pool |= tokens.get(tok, set())
    if not pool and norm:
        pool = {c for t, cs in tokens.items() if t.startswith(norm[:4]) for c in cs}
    scored = sorted(
        ((cik, max(name_similarity(norm, n) for n in names[cik])) for cik in pool),
        key=lambda t: -t[1],
    )
    return [c for c in scored[:k] if c[1] >= 0.60]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    del args
    client = SecClient()

    print("=" * 88, flush=True)
    print("N13 STEP 1  --  TICKER -> CIK MAPPING, AND GATE 0", flush=True)
    print("=" * 88, flush=True)

    symbols = ever_top_pool()
    targets = target_frame(symbols)
    print(f"  symbols ever in the point-in-time top {POOL}: {len(targets)} "
          f"({int(targets['delisted'].sum())} delisted)", flush=True)

    universe = sec_universe(client)
    index = build_index(universe)
    print(f"  SEC filer universe from frames 2009-2025: {universe['cik'].nunique():,} CIKs, "
          f"{len(universe):,} (cik, name) spellings", flush=True)

    out = []
    for row in targets.itertuples():
        norm = normalise_name(row.name)
        cands = candidates(norm, index)
        chosen = None
        for cik, sim in cands:
            sub = client.submissions(cik) or {}
            sec_name = sub.get("name")
            tickers = [t.upper() for t in sub.get("tickers", []) or []]
            former = [normalise_name(f.get("name")) for f in sub.get("formerNames", []) or []]
            sim_best = max([sim] + [name_similarity(norm, f) for f in [*former, normalise_name(sec_name)] if f])
            confirmed = row.ticker in tickers
            if confirmed and (not row.delisted or sim_best >= 0.80):
                chosen = (cik, sec_name, sim_best, "ticker", sub.get("sic"))
                break
            if row.delisted and sim_best >= 0.92 and chosen is None:
                chosen = (cik, sec_name, sim_best, "name", sub.get("sic"))
        rec = {"symbol": row.symbol, "name": row.name, "delisted": row.delisted}
        if chosen:
            cik, sec_name, sim, method, sic = chosen
            rec.update(cik=cik, sec_name=sec_name, similarity=round(sim, 3), method=method,
                       sic=sic, financial=bool(sic and str(sic).isdigit() and 6000 <= int(sic) <= 6999))
        else:
            rec.update(cik=np.nan, sec_name=None, similarity=np.nan, method="unmatched",
                       sic=None, financial=None)
        out.append(rec)

    table = pd.DataFrame(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT, index=False)
    print(f"  SEC requests made this run: {client.requests_made}", flush=True)

    # ---------------------------------------------------------------- GATE 0
    print("\n" + "-" * 88, flush=True)
    print("GATE 0  --  DATA AUDIT (pre-registered)  --  SECOND AND FINAL PASS", flush=True)
    print("-" * 88, flush=True)
    table["mapped"] = table["method"] != "unmatched"
    table["ticker"] = table["symbol"].map(base_ticker)
    table["mislabel"] = (~table["mapped"]) & table.apply(
        lambda r: vendor_mislabel(r["ticker"], r["name"]), axis=1
    )
    removed = table[table["mislabel"]]
    print(f"  vendor-mislabelled foreign listings removed (unmatched AND fixed rule): {len(removed)}")
    for r in removed.itertuples():
        print(f"    {r.symbol:<10} {r.name}")
    table.to_csv(OUT, index=False)
    gate = table[~table["mislabel"]]
    live = gate[~gate["delisted"]]
    dead = gate[gate["delisted"]]
    live_rate, dead_rate = live["mapped"].mean(), dead["mapped"].mean()
    gap = live_rate - dead_rate
    print(f"  survivors mapped   {int(live['mapped'].sum()):>4} / {len(live):<4} {live_rate:.1%}")
    print(f"  delisted mapped    {int(dead['mapped'].sum()):>4} / {len(dead):<4} {dead_rate:.1%}")
    print(f"  gap                {gap:+.1%}   (kill if |gap| > {GATE0_MAX_GAP:.0%})")
    print(f"\n  by method: {table['method'].value_counts().to_dict()}")
    print(f"  financials (SIC 6000-6999) among mapped: {int(table['financial'].fillna(False).sum())}")

    dupes = table[table["mapped"]].groupby("cik")["symbol"].apply(list)
    dupes = dupes[dupes.map(len) > 1]
    print(f"  CIKs mapped from more than one symbol (ticker changes, share classes): {len(dupes)}")
    for cik, syms in dupes.head(10).items():
        print(f"    CIK {int(cik):<9} {syms}")
    unmatched = gate[~gate["mapped"]]
    if len(unmatched):
        print(f"\n  unmatched ({len(unmatched)}):")
        for r in unmatched.itertuples():
            print(f"    {r.symbol:<10} {'D' if r.delisted else 'L'}  {r.name}")
    low = table[(table["method"] == "name")].sort_values("similarity")
    if len(low):
        print("\n  name-only matches, weakest first (audit these by eye):")
        for r in low.head(12).itertuples():
            print(f"    {r.symbol:<10} {str(r.name)[:30]:<31} -> {str(r.sec_name)[:34]:<35} {r.similarity:.2f}")

    verdict = "PASS" if abs(gap) <= GATE0_MAX_GAP else "KILL"
    print(f"\n  -> {verdict}")
    print("=" * 88)


if __name__ == "__main__":
    main()
