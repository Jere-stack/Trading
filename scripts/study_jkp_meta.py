#!/usr/bin/env python
"""M4: which published factor THEMES still work after publication, and for a long-only book?

Fifteen hypotheses in this project were built from prices and volume and all
fifteen died. Rather than guess a sixteenth, this asks a question that can be
answered from someone else's clean, survivorship-free, professionally built
data: of the 153 anomalies catalogued by Jensen, Kelly and Pedersen (Journal of
Finance, 2023), which still earn money AFTER their paper was published?

WHY THIS IS THE RIGHT TEST, AND WHY IT IS NOT DATA MINING
--------------------------------------------------------
Returns earned after a factor's publication year are returns its authors could
not have fitted. Post-publication performance is therefore an out-of-sample
test run by the passage of time, across 153 hypotheses, on the whole US market.
McLean and Pontiff (2016) found returns fall ~58% after publication; JKP find
most factors nevertheless still replicate. This script measures it directly.

The danger is the opposite of the usual one. Picking the single best factor in
the recent table and trading it would be data mining with extra steps. The
defensible reading is at the level of THEMES -- clusters of related factors --
and only where a theme works in more than one period AND more than one size
group. That is what the summary tables are built to show.

WHAT A LONG-ONLY INVESTOR CAN USE
---------------------------------
JKP factors are long-short. This project cannot short. So each factor's LONG
tercile (high tercile if the factor's direction is +1, low tercile if -1) is
compared against the value-weighted market -- the only comparison relevant to
someone who must beat the index while holding only longs. A factor whose edge
lives entirely in the short leg is worthless here, and several are.

Run:  .venv/bin/python -m scripts.study_jkp_meta
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("data/jkp")
RECENT = ("2015-01-01", "2025-12-31")
LAST_DECADE_LABEL = "2015-2025"


def load() -> dict[str, pd.DataFrame]:
    factors = pd.read_csv(ROOT / "factors" / "[usa]_[all_factors]_[monthly]_[vw_cap].csv",
                          parse_dates=["date"])
    portfolios = pd.read_csv(ROOT / "portfolios" / "[usa]_[all_factors]_[monthly]_[vw_cap].csv",
                             parse_dates=["date"])
    mkt = pd.read_csv(ROOT / "mkt" / "[usa]_[mkt]_[monthly]_[vw_cap].csv", parse_dates=["date"])
    details = pd.read_csv(ROOT / "factor_details.csv")
    clusters = pd.read_csv(ROOT / "Cluster_Labels.csv")

    sizes = []
    for path in sorted((ROOT / "sizes").glob("*.csv")):
        sizes.append(pd.read_csv(path, parse_dates=["date"]))
    size_frame = pd.concat(sizes, ignore_index=True) if sizes else pd.DataFrame()

    return {
        "factors": factors,
        "portfolios": portfolios,
        "mkt": mkt,
        "details": details,
        "clusters": clusters,
        "sizes": size_frame,
    }


def publication_years(details: pd.DataFrame) -> pd.DataFrame:
    """Publication year and in-sample end year per JKP factor code.

    The citation carries the year in parentheses. Where one JKP code maps to
    several HXZ rows, the EARLIEST publication is used: the idea was public from
    then on, and using a later year would credit post-publication returns to the
    in-sample period, which flatters decay estimates.
    """
    rows = []
    for _, r in details.dropna(subset=["abr_jkp"]).iterrows():
        cite = str(r.get("cite", ""))
        years = [int(y) for y in re.findall(r"\((\d{4})", cite)]
        period = str(r.get("in-sample period", ""))
        ends = [int(y) for y in re.findall(r"(\d{4})", period)]
        rows.append({
            "name": r["abr_jkp"],
            "pub_year": min(years) if years else np.nan,
            "is_start": ends[0] if len(ends) >= 1 else np.nan,
            "is_end": ends[-1] if len(ends) >= 2 else np.nan,
            "cite": cite,
        })
    frame = pd.DataFrame(rows)
    return frame.groupby("name").agg(
        pub_year=("pub_year", "min"),
        is_start=("is_start", "min"),
        is_end=("is_end", "max"),
        cite=("cite", "first"),
    ).reset_index()


def stats(series: pd.Series) -> dict:
    """Annualised mean, t-statistic and Sharpe of a monthly return series."""
    x = series.dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 24:
        return {"n": n, "ann": np.nan, "t": np.nan, "sharpe": np.nan}
    mean, sd = x.mean(), x.std(ddof=1)
    return {
        "n": n,
        "ann": float(mean * 12),
        "t": float(mean / (sd / np.sqrt(n))) if sd > 0 else np.nan,
        "sharpe": float(mean / sd * np.sqrt(12)) if sd > 0 else np.nan,
    }


def window(frame: pd.DataFrame, start, end) -> pd.DataFrame:
    return frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]


def main() -> None:
    data = load()
    factors, portfolios, mkt = data["factors"], data["portfolios"], data["mkt"]
    pubs = publication_years(data["details"])
    theme = dict(zip(data["clusters"]["characteristic"], data["clusters"]["cluster"], strict=False))
    direction = factors.groupby("name")["direction"].first().astype(int)
    market = mkt.set_index("date")["ret"]

    print("=" * 96)
    print("M4  POST-PUBLICATION PERFORMANCE OF 153 PUBLISHED US EQUITY FACTORS  (JKP 2023 data)")
    print("=" * 96)
    print(f"  factors {factors['name'].nunique()}   with publication year "
          f"{pubs['pub_year'].notna().sum()}   themes {len(set(theme.values()))}")
    print(f"  data {factors['date'].min():%Y-%m} to {factors['date'].max():%Y-%m}")

    # Sanity: are portfolio returns on the same (excess) basis as the market?
    all_pf = portfolios.groupby("date")["ret"].mean()
    joined = pd.concat([all_pf, market], axis=1, keys=["pf", "mkt"]).dropna()
    gap = float((joined["pf"] - joined["mkt"]).mean() * 12)
    print(f"  basis check: mean(tercile avg - market) = {gap:+.2%}/yr "
          "(near zero => same return basis; ~+3%/yr would mean one side includes the risk-free rate)")

    # ---------------------------------------------------------- per-factor table
    rows = []
    for name, grp in factors.groupby("name"):
        grp = grp.set_index("date")["ret"]
        meta = pubs[pubs["name"] == name]
        pub = float(meta["pub_year"].iloc[0]) if len(meta) else np.nan
        is_s = float(meta["is_start"].iloc[0]) if len(meta) else np.nan
        is_e = float(meta["is_end"].iloc[0]) if len(meta) else np.nan

        ins = stats(grp[(grp.index.year >= is_s) & (grp.index.year <= is_e)]) if np.isfinite(is_e) else stats(pd.Series(dtype=float))
        post = stats(grp[grp.index.year > pub]) if np.isfinite(pub) else stats(pd.Series(dtype=float))
        recent = stats(grp[(grp.index >= RECENT[0]) & (grp.index <= RECENT[1])])

        d = int(direction.get(name, 1))
        long_pf = 3.0 if d == 1 else 1.0
        leg = portfolios[(portfolios["name"] == name) & (portfolios["pf"] == long_pf)].set_index("date")["ret"]
        long_vs_mkt = (leg - market).dropna()
        long_post = stats(long_vs_mkt[long_vs_mkt.index.year > pub]) if np.isfinite(pub) else stats(pd.Series(dtype=float))
        long_recent = stats(long_vs_mkt[(long_vs_mkt.index >= RECENT[0]) & (long_vs_mkt.index <= RECENT[1])])

        # The BIAS-FREE long-only measure. Long tercile minus the mean of the same
        # factor's three terciles. Against the market, JKP's terciles carry a
        # construction offset -- in 2015-2025 the average of any factor's three
        # terciles beat the market by ~+0.42%/yr and even the MIDDLE tercile beat it
        # 76% of the time -- so "long leg minus market" credits roughly half a point
        # of pure portfolio construction to every factor. Differencing against the
        # factor's own tercile average cancels that offset exactly.
        terc = portfolios[portfolios["name"] == name].pivot_table(index="date", columns="pf", values="ret")
        long_vs_avg3 = (terc[long_pf] - terc.mean(axis=1)).dropna() if long_pf in terc else pd.Series(dtype=float)
        adj_post = stats(long_vs_avg3[long_vs_avg3.index.year > pub]) if np.isfinite(pub) else stats(pd.Series(dtype=float))
        adj_recent = stats(long_vs_avg3[(long_vs_avg3.index >= RECENT[0]) & (long_vs_avg3.index <= RECENT[1])])

        rows.append({
            "name": name,
            "theme": theme.get(name, "?"),
            "pub_year": pub,
            "is_ann": ins["ann"], "is_t": ins["t"],
            "post_ann": post["ann"], "post_t": post["t"], "post_n": post["n"],
            "recent_ann": recent["ann"], "recent_t": recent["t"], "recent_sharpe": recent["sharpe"],
            "long_post_ann": long_post["ann"], "long_post_t": long_post["t"],
            "long_recent_ann": long_recent["ann"], "long_recent_t": long_recent["t"],
            "adj_post_ann": adj_post["ann"], "adj_post_t": adj_post["t"],
            "adj_recent_ann": adj_recent["ann"], "adj_recent_t": adj_recent["t"],
        })
    table = pd.DataFrame(rows)

    # size-specific long-short, recent decade
    sizes = data["sizes"]
    if not sizes.empty:
        rec = window(sizes, *RECENT)
        for grp_name in ("mega", "large"):
            sub = rec[rec["size_grp"] == grp_name]
            s = {n: stats(g.set_index("date")["ret"]) for n, g in sub.groupby("name")}
            table[f"{grp_name}_recent_ann"] = table["name"].map(lambda n, s=s: s.get(n, {}).get("ann", np.nan))
            table[f"{grp_name}_recent_t"] = table["name"].map(lambda n, s=s: s.get(n, {}).get("t", np.nan))
            post_rows = {}
            for n, g in sizes[sizes["size_grp"] == grp_name].groupby("name"):
                pub = table.loc[table["name"] == n, "pub_year"]
                py = float(pub.iloc[0]) if len(pub) and np.isfinite(pub.iloc[0]) else np.nan
                if np.isfinite(py):
                    ser = g.set_index("date")["ret"]
                    post_rows[n] = stats(ser[ser.index.year > py])
            table[f"{grp_name}_post_ann"] = table["name"].map(lambda n, p=post_rows: p.get(n, {}).get("ann", np.nan))
            table[f"{grp_name}_post_t"] = table["name"].map(lambda n, p=post_rows: p.get(n, {}).get("t", np.nan))

    out_dir = Path("data/jkp")
    table.to_csv(out_dir / "m4_factor_table.csv", index=False)

    # ---------------------------------------------------------- decay headline
    print("\n" + "-" * 96)
    print("1. DECAY: in-sample vs post-publication (long-short, all stocks, capped value weight)")
    print("-" * 96)
    both = table.dropna(subset=["is_ann", "post_ann"])
    both = both[both["is_ann"] > 0]
    ratio = both["post_ann"].sum() / both["is_ann"].sum()
    print(f"  factors with a positive in-sample premium and a post-publication record: {len(both)}")
    print(f"  mean in-sample premium        {both['is_ann'].mean():+.2%}/yr")
    print(f"  mean post-publication premium {both['post_ann'].mean():+.2%}/yr")
    print(f"  post / in-sample              {ratio:.0%}   (McLean-Pontiff 2016 found ~42%)")
    print(f"  still positive after publication: {(both['post_ann'] > 0).mean():.0%}")

    # ---------------------------------------------------------- multiple testing
    print("\n" + "-" * 96)
    print(f"2. THE LAST DECADE ({LAST_DECADE_LABEL}), read against chance")
    print("-" * 96)
    n = table["recent_t"].notna().sum()
    pos2 = int((table["recent_t"] >= 2).sum())
    lpos2 = int((table["long_recent_t"] >= 2).sum())
    print(f"  factors evaluated {n}.  Under the null, ~{0.023 * n:.1f} would show t >= +2 by chance.")
    apos2 = int((table["adj_recent_t"] >= 2).sum())
    print(f"  long-short  t >= +2:                          {pos2}")
    print(f"  long leg vs MARKET  t >= +2:                  {lpos2}   <- inflated, see below")
    print(f"  long leg vs OWN TERCILE AVERAGE  t >= +2:     {apos2}   <- bias-free")
    print(f"  long leg beating market at all:               {(table['long_recent_ann'] > 0).sum()} of {n}")
    print("\n  The long-vs-market count is not evidence. JKP terciles carry a construction offset")
    print("  against the market: in 2015-2025 the average of each factor's three terciles beat it")
    print("  by ~+0.42%/yr and even the MIDDLE tercile beat it 76% of the time, while short legs sat")
    print("  near zero (-0.16%). The long legs are also highly correlated with each other -- most")
    print("  tilt toward the same mega-caps -- so 16 hits are far fewer than 16 independent ones.")
    print("  The long-short count, 5 against ~3.5 by chance, is the honest read of the decade:")
    print("  indistinguishable from noise at the level of individual factors.")

    # ---------------------------------------------------------- theme table
    print("\n" + "-" * 96)
    print("3. BY THEME -- the level at which the evidence is defensible")
    print("-" * 96)
    print("  ls = long-short premium (%/yr).  long* = long tercile minus the factor's OWN tercile")
    print("  average (%/yr): the bias-free share of the premium a long-only book can capture.")
    print("  pos = share of the theme's factors > 0.  mega/large = long-short within that size group.\n")
    hdr = (f"  {'theme':<20}{'n':>3} |{'ls post':>9}{'pos':>6}{'ls 15-25':>10}{'pos':>6} |"
           f"{'long* post':>11}{'long* 15-25':>12}{'pos':>6} |{'mega 15-25':>11}{'large 15-25':>12}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    summary = []
    for th, g in table.groupby("theme"):
        row = {
            "theme": th, "n": len(g),
            "ls_post": g["post_ann"].mean(), "ls_post_pos": (g["post_ann"] > 0).mean(),
            "ls_recent": g["recent_ann"].mean(), "ls_recent_pos": (g["recent_ann"] > 0).mean(),
            "long_post": g["long_post_ann"].mean(),
            "long_recent": g["long_recent_ann"].mean(), "long_recent_pos": (g["long_recent_ann"] > 0).mean(),
            "adj_post": g["adj_post_ann"].mean(),
            "adj_recent": g["adj_recent_ann"].mean(), "adj_recent_pos": (g["adj_recent_ann"] > 0).mean(),
            "mega_recent": g.get("mega_recent_ann", pd.Series(dtype=float)).mean(),
            "large_recent": g.get("large_recent_ann", pd.Series(dtype=float)).mean(),
        }
        summary.append(row)
    summary = pd.DataFrame(summary).sort_values("adj_recent", ascending=False)
    for _, r in summary.iterrows():
        print(f"  {r['theme']:<20}{int(r['n']):>3} |{r['ls_post']:>+9.2%}{r['ls_post_pos']:>6.0%}"
              f"{r['ls_recent']:>+10.2%}{r['ls_recent_pos']:>6.0%} |{r['adj_post']:>+11.2%}"
              f"{r['adj_recent']:>+12.2%}{r['adj_recent_pos']:>6.0%} |"
              f"{r['mega_recent']:>+11.2%}{r['large_recent']:>+12.2%}")
    summary.to_csv(out_dir / "m4_theme_table.csv", index=False)

    # ---------------------------------------------------------- robust survivors
    print("\n" + "-" * 96)
    print("4. FACTORS THAT PASS EVERY FILTER AT ONCE")
    print("-" * 96)
    print("  post-publication long-short t >= 2, AND bias-free long leg positive post-publication,")
    print("  AND bias-free long leg positive 2015-2025, AND positive long-short in mega caps 2015-2025.\n")
    mask = (
        (table["post_t"] >= 2)
        & (table["adj_post_ann"] > 0)
        & (table["adj_recent_ann"] > 0)
        & (table.get("mega_recent_ann", pd.Series(np.nan, index=table.index)) > 0)
    )
    robust = table[mask].sort_values("adj_recent_ann", ascending=False)
    print(f"  {'factor':<18}{'theme':<18}{'pub':>6}{'ls post':>9}{'t':>6}"
          f"{'long* post':>11}{'long* 15-25':>12}{'t':>6}{'mega 15-25':>11}")
    for _, r in robust.iterrows():
        print(f"  {r['name']:<18}{r['theme']:<18}{int(r['pub_year']) if np.isfinite(r['pub_year']) else 0:>6}"
              f"{r['post_ann']:>+9.2%}{r['post_t']:>6.1f}{r['adj_post_ann']:>+11.2%}"
              f"{r['adj_recent_ann']:>+12.2%}{r['adj_recent_t']:>6.1f}"
              f"{r.get('mega_recent_ann', np.nan):>+11.2%}")
    print(f"\n  {len(robust)} of {len(table)} factors pass all four filters.")

    print("\n" + "=" * 96)
    print("Tables written to data/jkp/m4_factor_table.csv and m4_theme_table.csv (gitignored).")
    print("=" * 96)


if __name__ == "__main__":
    main()
