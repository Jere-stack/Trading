#!/usr/bin/env python
"""Build a CLEAN price panel: every security positively identified against the SEC.

M5 showed the vendor's own metadata cannot be trusted to define a US universe:
Moscow and Kuwaiti listings labelled "NYSE / USD", codes carrying the names of
firms that ceased to exist before 2011, codes with no name at all. A filter
built on that metadata cannot catch its errors. This one is built on an
EXTERNAL registry instead -- the SEC security master from
scripts/sec_registry_scan.py -- and the rules were fixed before any N13 result
was computed:

  1. IDENTIFY. A listed symbol is identified by its CURRENT ticker in the SEC
     registry. A delisted symbol needs a strict name match (>= 0.92) against a
     filer's name or any of its former names, or a ticker match whose names
     also agree (>= 0.80) -- reissued tickers make a bare ticker match unsafe.
  2. REMOVE THE KNOWN CONTAMINATION. Non-US legal forms (PJSC, KSC, SAK...),
     tickers longer than five characters, and codes with no real name are
     never identified, whatever they happen to match.
  3. CHECK THE PRICES AGAINST THE REGISTRY. An identified security's prices
     are kept only between one year before its FIRST SEC filing and ~13 months
     after its LAST. Prices outside that window belong to some other security
     that used the code, and are blanked.
  4. ONE SECURITY PER COMPANY. Share classes and renamed tickers that map to
     one CIK compete for a single slot; the more liquid one wins on each date.

Unidentified symbols are EXCLUDED. That re-introduces some survivorship tilt,
because most of them delisted; the gate-0 audit is re-run on the result so the
size of the tilt is measured, not assumed.

Run:  .venv/bin/python -m scripts.build_clean_universe
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.n13_map_ciks import base_ticker, vendor_mislabel
from tradelab.data.sec import SecClient, name_similarity, normalise_name

PANEL = Path("data/signals/n12")
REGISTRY = Path("data/sec/registry.parquet")
OUT = Path("data/signals/clean")
PRE_FILING_DAYS = 365
POST_FILING_DAYS = 400


def reference_names(symbols: list[str]) -> pd.DataFrame:
    ref = pd.read_parquet("data/reference/us_symbols.parquet")
    ref = ref.assign(key=ref["Code"].str.upper()).drop_duplicates("key").set_index("key")
    rows = []
    for sym in symbols:
        hit = ref.loc[sym.upper()] if sym.upper() in ref.index else None
        name = None if hit is None else hit["Name"]
        rows.append({
            "symbol": sym,
            "ticker": base_ticker(sym),
            "name": name,
            "delisted": True if hit is None else bool(hit["delisted"]),
        })
    return pd.DataFrame(rows)


def registry_index(reg: pd.DataFrame):
    by_ticker: dict[str, list[int]] = {}
    names: dict[int, list[str]] = {}
    token_index: dict[str, set[int]] = {}
    for r in reg.itertuples():
        for t in str(r.tickers or "").split("|"):
            if t:
                by_ticker.setdefault(t.upper(), []).append(int(r.cik))
        spellings = {normalise_name(r.name), normalise_name(r.frame_name)}
        spellings |= {normalise_name(f) for f in str(r.former_names or "").split("|") if f}
        spellings.discard("")
        names[int(r.cik)] = sorted(spellings)
        for s in spellings:
            for tok in s.split():
                if len(tok) >= 3:
                    token_index.setdefault(tok, set()).add(int(r.cik))
    return by_ticker, names, token_index


def best_name(norm: str, cik: int, names) -> float:
    return max((name_similarity(norm, n) for n in names.get(cik, [])), default=0.0)


def identify(row, by_ticker, names, token_index) -> tuple[int | None, str, float]:
    if not row.name or str(row.name).strip().upper() == row.ticker or vendor_mislabel(row.ticker, row.name):
        return None, "contaminant", 0.0
    norm = normalise_name(row.name)
    ticker_hits = by_ticker.get(row.ticker, [])
    if ticker_hits:
        scored = sorted(((c, best_name(norm, c, names)) for c in ticker_hits), key=lambda t: -t[1])
        cik, sim = scored[0]
        if not row.delisted or sim >= 0.80:
            return cik, "ticker", sim
    pool: set[int] = set()
    for tok in norm.split():
        if len(tok) >= 3:
            pool |= token_index.get(tok, set())
    # Ties at the top are broken by CIK order, deterministically. A wrong pick
    # between two same-named filers (a parent and a subsidiary that both file)
    # is then caught only if its filing window disagrees with the prices -- rule 3.
    scored = sorted(((c, best_name(norm, c, names)) for c in pool), key=lambda t: (-t[1], t[0]))
    if scored and scored[0][1] >= 0.92:
        return scored[0][0], "name", scored[0][1]
    return None, "unresolved", scored[0][1] if scored else 0.0


def blank_outside_window(method: str) -> bool:
    """Whether an identified symbol's prices are checked against its filing window.

    Only NAME-identified symbols. A symbol identified by its CURRENT ticker is
    the live listing, and the SEC's ticker assignment is authoritative for it.
    Its CIK may nonetheless be young, because holding-company reorganisations
    and redomiciles issue a NEW CIK to a stock that never stopped trading --
    Disney 2019, Cigna 2018, BlackRock 2024, Marvell 2021, APA 2021. The smoke
    test showed the window rule blanking years of those stocks' genuine prices.
    For name-identified codes the rule stays: it caught LB_OLD1, which carries
    L Brands' prices under the name of LandBridge, a 2024 listing.
    """
    return method != "ticker"


def squash(name: str | None) -> str:
    return normalise_name(name).replace(" ", "")


def link_predecessors(
    successors: dict[int, tuple[pd.Timestamp | None, list[str]]],
    spellings_index: dict[str, set[int]],
    window_of,
    panel_start: pd.Timestamp,
) -> dict[int, int]:
    """Map each young successor CIK to the CIK it replaced, when one is evident.

    `successors`: cik -> (first XBRL filing date, its name spellings, squashed).
    A candidate predecessor shares a spelling with the successor, filed XBRL
    BEFORE the successor did, and stopped filing within [2 years before, 1 year
    after] the successor's first filing -- the signature of a reorganisation,
    not of two unrelated firms that happen to share a name. Among candidates,
    the one whose last filing sits closest to the successor's first wins.
    Successors that were already filing when the panel starts need no link.
    """
    links: dict[int, int] = {}
    for cik, (first, spellings) in successors.items():
        if first is None or first <= panel_start:
            continue
        candidates: set[int] = set()
        for sp in spellings:
            candidates |= spellings_index.get(sp, set())
        candidates.discard(cik)
        best, best_gap = None, None
        for cand in sorted(candidates):
            p_first, p_last = window_of(cand)
            if p_first is None or p_first >= first:
                continue
            if not (first - pd.Timedelta(days=730) <= p_last <= first + pd.Timedelta(days=365)):
                continue
            gap = abs((p_last - first).days)
            if best_gap is None or gap < best_gap:
                best, best_gap = cand, gap
        if best is not None:
            links[cik] = best
    return links


def filing_window(client: SecClient, cik: int) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    facts = client.companyfacts(cik)
    if not facts:
        return None, None
    dates: list[str] = []
    for taxonomy in ("us-gaap", "ifrs-full", "dei"):
        for tag in facts.get("facts", {}).get(taxonomy, {}).values():
            for rows in tag.get("units", {}).values():
                dates.extend(r["filed"] for r in rows if "filed" in r)
    if not dates:
        return None, None
    return pd.Timestamp(min(dates)), pd.Timestamp(max(dates))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.registry.exists():
        raise SystemExit("run scripts/sec_registry_scan.py first")
    reg = pd.read_parquet(args.registry)
    by_ticker, names, token_index = registry_index(reg)
    client = SecClient()

    closes = pd.read_parquet(PANEL / "close.parquet")
    volumes = pd.read_parquet(PANEL / "dollar_volume.parquet")
    highs = pd.read_parquet(PANEL / "high.parquet")
    lows = pd.read_parquet(PANEL / "low.parquet")

    print("=" * 88, flush=True)
    print("CLEAN UNIVERSE  --  identification against the SEC registry", flush=True)
    print("=" * 88, flush=True)
    print(f"  registry: {len(reg):,} filers, {sum(len(v) for v in by_ticker.values()):,} ticker entries")

    targets = reference_names(list(closes.columns))
    ids = [identify(r, by_ticker, names, token_index) for r in targets.itertuples()]
    targets["cik"] = [i[0] for i in ids]
    targets["method"] = [i[1] for i in ids]
    targets["similarity"] = [round(i[2], 3) for i in ids]
    sic = reg.set_index("cik")["sic"]
    country = reg.set_index("cik")["country"]
    targets["sic"] = targets["cik"].map(sic)
    targets["country"] = targets["cik"].map(country)

    print(f"  candidate symbols (ever top 200 by raw dollar volume): {len(targets)}")
    print(f"  identification: {targets['method'].value_counts().to_dict()}")

    # A count says how many were matched, not whether they were matched RIGHT.
    # A random sample of name-only matches is printed for audit by eye, with a
    # fixed seed so the same sample reappears on a re-run.
    sec_name = reg.set_index("cik")["name"]
    by_name = targets[targets["method"] == "name"]
    if len(by_name):
        sample = by_name.sample(min(15, len(by_name)), random_state=7)
        print("\n  audit sample of NAME matches (vendor name -> SEC registrant):")
        for r in sample.itertuples():
            print(f"    {r.symbol:<9}{'D' if r.delisted else 'L'} {str(r.name)[:32]:<33}-> "
                  f"{str(sec_name.get(r.cik, '?'))[:34]:<35}{r.similarity:.2f}")

    # ------------------------------------------------ price-registry consistency
    ok = targets[targets["cik"].notna()].copy()
    windows = {int(c): filing_window(client, int(c)) for c in ok["cik"].unique()}
    masked_points = 0
    keep_cols = []
    for r in ok.itertuples():
        first, last = windows[int(r.cik)]
        if first is None:
            continue
        if not blank_outside_window(r.method):
            keep_cols.append(r.symbol)
            continue
        lo = first - pd.Timedelta(days=PRE_FILING_DAYS)
        hi = last + pd.Timedelta(days=POST_FILING_DAYS)
        outside = (closes.index < lo) | (closes.index > hi)
        n_bad = int(closes.loc[outside, r.symbol].notna().sum())
        if n_bad:
            masked_points += n_bad
            for frame in (closes, volumes, highs, lows):
                frame.loc[outside, r.symbol] = np.nan
        keep_cols.append(r.symbol)
    print(f"  price points blanked outside the registry filing window: {masked_points:,} "
          "(name-identified codes only)")
    print(f"  identified symbols with a filing window: {len(keep_cols)}")

    spell_index: dict[str, set[int]] = {}
    spellings_of: dict[int, list[str]] = {}
    for rr in reg.itertuples():
        sps = {squash(rr.name), squash(rr.frame_name)}
        sps |= {squash(f) for f in str(rr.former_names or "").split("|") if f}
        sps.discard("")
        spellings_of[int(rr.cik)] = sorted(sps)
        for sp in sps:
            spell_index.setdefault(sp, set()).add(int(rr.cik))
    kept_ciks = {int(c) for c, sym in zip(ok["cik"], ok["symbol"], strict=False) if sym in keep_cols}
    succ = {c: (windows[c][0], spellings_of.get(c, [])) for c in kept_ciks}
    cache: dict[int, tuple] = dict(windows)

    def window_of(cik: int):
        if cik not in cache:
            cache[cik] = filing_window(client, cik)
        return cache[cik]

    links = link_predecessors(succ, spell_index, window_of, closes.index[0])
    pd.Series(links, name="predecessor_cik").rename_axis("cik").to_csv(out_dir / "predecessors.csv")
    names_of = reg.set_index("cik")["name"]
    print(f"\n  predecessor links (reorganised companies keep their earlier fundamentals): {len(links)}")
    for s_cik, p_cik in sorted(links.items(), key=lambda kv: str(names_of.get(kv[0])))[:25]:
        print(f"    {str(names_of.get(s_cik))[:30]:<31}{windows[s_cik][0]:%Y-%m}  <-  "
              f"{str(names_of.get(p_cik))[:30]:<31}last filed {cache[p_cik][1]:%Y-%m}")

    clean = {
        "close": closes[keep_cols],
        "dollar_volume": volumes[keep_cols],
        "high": highs[keep_cols],
        "low": lows[keep_cols],
    }
    for key, frame in clean.items():
        frame.to_parquet(out_dir / f"{key}.parquet")
    targets.to_csv(out_dir / "identification.csv", index=False)
    pd.Series({s: int(c) for s, c in zip(ok["symbol"], ok["cik"], strict=False) if s in keep_cols},
              name="cik").to_csv(out_dir / "symbol_cik.csv", header=True)

    # ------------------------------------------------ gate-0 re-audit
    print("\n" + "-" * 88)
    print("SURVIVORSHIP RE-AUDIT on the candidate set (contaminants excluded from both sides)")
    print("-" * 88)
    audit = targets[targets["method"] != "contaminant"]
    for label, g in (("survivors", audit[~audit["delisted"]]), ("delisted", audit[audit["delisted"]])):
        rate = (g["cik"].notna()).mean()
        print(f"  {label:<10} identified {int(g['cik'].notna().sum()):>4} / {len(g):<4} {rate:.1%}")
    gap = (audit[~audit["delisted"]]["cik"].notna().mean() - audit[audit["delisted"]]["cik"].notna().mean())
    print(f"  gap {gap:+.1%}  (the N13 pre-registration's limit was 6 points)")
    unresolved = audit[audit["cik"].isna()]
    print(f"\n  unresolved ({len(unresolved)}), shown for audit by eye:")
    for r in unresolved.head(40).itertuples():
        print(f"    {r.symbol:<10} {'D' if r.delisted else 'L'}  {str(r.name)[:50]}")
    print("=" * 88)


if __name__ == "__main__":
    main()
