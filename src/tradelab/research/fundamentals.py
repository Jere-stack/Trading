"""Point-in-time company fundamentals from SEC XBRL `companyfacts`.

THE ONE RULE
------------
A value is usable only from the first trading session AFTER the date its
filing reached the SEC. Every other design choice here serves that rule.

Two things make it harder than it sounds:

  * The same fiscal-year number appears in several filings: the year's own
    annual report, as a comparative column in the next year's, and in any
    amendment. They can differ (restatements). The value known on a given day
    is the one from the most recent filing made BEFORE that day -- never a
    later restatement, however much more accurate it is.
  * Ratios need a numerator and a denominator that were known together. They
    are therefore formed WITHIN a single filing (same accession number, same
    fiscal-period end), and the ratio row inherits that filing's date. Mixing a
    revenue figure from March with an asset figure restated in August would be
    look-ahead by stealth.

WHAT IS MEASURED
----------------
Two characteristics, from the N13 pre-registration:

  profitability = gross profit / total assets            (Novy-Marx 2013)
  quality       = operating income / book equity         (Fama-French RMW)

Gross profit comes from the GrossProfit tag, or revenue minus cost of revenue
reported in the same filing. Banks and insurers report neither and are left
undefined rather than forced -- the academic constructions exclude them too.

US GAAP and IFRS filers are both handled. Foreign private issuers (20-F)
report in their home currency, but a ratio of two amounts from the same
statement is currency-free, so TSMC in New Taiwan dollars and Apple in US
dollars rank on the same scale.

Annual reports only (10-K, 20-F, 40-F and amendments). Annual data updated as
reports arrive is the standard academic construction and avoids the
quarterly year-to-date arithmetic that is a common source of silent errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "ANNUAL_FORMS",
    "CONCEPTS",
    "FundamentalRows",
    "annual_ratio_rows",
    "as_of",
    "with_predecessors",
]

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A"})
FLOW_DAYS = (330, 400)
"""A flow fact counts as annual if its period spans 330-400 days."""
MAX_STALENESS_DAYS = 548
"""A fiscal year older than ~18 months at the decision date is treated as
missing: a company that stopped filing must not rank on its last report."""

# (taxonomy, tag) chains in priority order. Tags changed over the sample --
# ASC 606 replaced `Revenues` with `RevenueFromContractWith...` in 2018 -- so
# each concept is the union of its tags, with earlier entries preferred when
# one filing reports several.
CONCEPTS: dict[str, dict[str, Any]] = {
    "revenue": {"kind": "flow", "tags": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("us-gaap", "SalesRevenueGoodsNet"),
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
    ]},
    "cost_of_revenue": {"kind": "flow", "tags": [
        ("us-gaap", "CostOfRevenue"),
        ("us-gaap", "CostOfGoodsAndServicesSold"),
        ("us-gaap", "CostOfGoodsSold"),
        ("us-gaap", "CostOfServices"),
        ("us-gaap", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"),
        ("ifrs-full", "CostOfSales"),
    ]},
    "gross_profit": {"kind": "flow", "tags": [
        ("us-gaap", "GrossProfit"),
        ("ifrs-full", "GrossProfit"),
    ]},
    "operating_income": {"kind": "flow", "tags": [
        ("us-gaap", "OperatingIncomeLoss"),
        ("ifrs-full", "ProfitLossFromOperatingActivities"),
    ]},
    "assets": {"kind": "stock", "tags": [
        ("us-gaap", "Assets"),
        ("ifrs-full", "Assets"),
    ]},
    "equity": {"kind": "stock", "tags": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ("ifrs-full", "EquityAttributableToOwnersOfParent"),
        ("ifrs-full", "Equity"),
    ]},
}


@dataclass(frozen=True)
class FundamentalRows:
    """One row per (filing, fiscal-period end): the ratios as that filing stated them."""

    frame: pd.DataFrame
    """Columns: end, filed, accn, profitability, quality."""
    unit: str | None


def _primary_unit(facts: dict[str, Any]) -> str | None:
    """The currency the company reports its balance sheet in.

    Chosen from total assets because every operating company reports it. All
    concepts are then read in that unit only, so a ratio never divides dollars
    by euros.
    """
    counts: dict[str, int] = {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        units = facts.get("facts", {}).get(taxonomy, {}).get("Assets", {}).get("units", {})
        for unit, rows in units.items():
            counts[unit] = counts.get(unit, 0) + len(rows)
    return max(counts, key=counts.get) if counts else None


def _concept_rows(facts: dict[str, Any], concept: str, unit: str) -> pd.DataFrame:
    spec = CONCEPTS[concept]
    out = []
    for priority, (taxonomy, tag) in enumerate(spec["tags"]):
        rows = facts.get("facts", {}).get(taxonomy, {}).get(tag, {}).get("units", {}).get(unit, [])
        for r in rows:
            if r.get("form") not in ANNUAL_FORMS or "filed" not in r or "end" not in r:
                continue
            if spec["kind"] == "flow":
                if "start" not in r:
                    continue
                days = (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])).days
                if not FLOW_DAYS[0] <= days <= FLOW_DAYS[1]:
                    continue
            elif "start" in r:
                continue
            out.append({
                "accn": r["accn"], "end": r["end"], "filed": r["filed"],
                "val": float(r["val"]), "priority": priority,
            })
    if not out:
        return pd.DataFrame(columns=["accn", "end", "filed", "val"])
    frame = pd.DataFrame(out).sort_values("priority")
    # One value per (filing, period end): the highest-priority tag that filing used.
    frame = frame.drop_duplicates(["accn", "end"], keep="first").drop(columns="priority")
    return frame


def annual_ratio_rows(facts: dict[str, Any]) -> FundamentalRows:
    """Profitability and quality per (filing, fiscal-period end).

    Every input to a ratio comes from the SAME filing, so the ratio row carries
    a single, honest filing date.
    """
    unit = _primary_unit(facts)
    empty = pd.DataFrame(columns=["end", "filed", "accn", "profitability", "quality"])
    if unit is None:
        return FundamentalRows(empty, None)

    parts = {c: _concept_rows(facts, c, unit) for c in CONCEPTS}
    present = [p[["accn", "end", "filed"]] for p in parts.values() if len(p)]
    # A filer can report a balance sheet yet have no ANNUAL rows at all -- a
    # company that has so far filed only quarterly reports. That is an empty
    # history, not an error; the smoke test found it as a crash.
    if not present:
        return FundamentalRows(empty, unit)
    keys = pd.concat(present, ignore_index=True)
    table = keys.drop_duplicates(["accn", "end"]).set_index(["accn", "end"])
    for concept, frame in parts.items():
        table[concept] = frame.set_index(["accn", "end"])["val"] if len(frame) else np.nan

    gp = table["gross_profit"].where(
        table["gross_profit"].notna(), table["revenue"] - table["cost_of_revenue"]
    )
    assets = table["assets"].where(table["assets"] > 0)
    equity = table["equity"].where(table["equity"] > 0)
    table["profitability"] = gp / assets
    table["quality"] = table["operating_income"] / equity

    out = table.reset_index()[["end", "filed", "accn", "profitability", "quality"]]
    out["end"] = pd.to_datetime(out["end"])
    out["filed"] = pd.to_datetime(out["filed"])
    out = out.dropna(subset=["profitability", "quality"], how="all")
    return FundamentalRows(out.sort_values(["end", "filed"]).reset_index(drop=True), unit)


def as_of(rows: pd.DataFrame, date: pd.Timestamp, metric: str) -> float:
    """The value of `metric` a market participant could have known on `date`.

    Only filings made STRICTLY BEFORE `date` are visible -- a report filed
    today is usable from the next session. Among visible filings, the latest
    fiscal period wins, and within that period the latest filing wins (a
    restatement that has already happened is known; one that has not, is not).
    """
    if rows is None or len(rows) == 0:
        return float("nan")
    date = pd.Timestamp(date)
    visible = rows[(rows["filed"] < date) & rows[metric].notna()]
    if visible.empty:
        return float("nan")
    latest_end = visible["end"].max()
    if (date - latest_end).days > MAX_STALENESS_DAYS:
        return float("nan")
    same = visible[visible["end"] == latest_end]
    return float(same.sort_values("filed")[metric].iloc[-1])


def with_predecessors(funds: dict[int, pd.DataFrame], links: dict[int, int]) -> dict[int, pd.DataFrame]:
    """Give each reorganised company the financial history of the entity it replaced.

    A holding-company reorganisation or redomicile issues a NEW CIK to a stock
    that never stopped trading (Disney 2019, Cigna 2018, BlackRock 2024). Without
    this, such a stock has no fundamentals until its new registrant files its
    first annual report, and drops out of the ranking for up to a year.

    The histories are simply pooled: `as_of` already takes the latest fiscal
    period known on the date, so the successor's own reports take over the moment
    they exist. Chains (A replaced by B replaced by C) are followed to the end.
    """
    out = dict(funds)
    for successor in links:
        parts, seen, cik = [], set(), successor
        while cik is not None and cik not in seen:
            seen.add(cik)
            if cik in funds and len(funds[cik]):
                parts.append(funds[cik])
            cik = links.get(cik)
        if len(parts) > 1:
            out[successor] = (
                pd.concat(parts, ignore_index=True)
                .sort_values(["end", "filed"])
                .reset_index(drop=True)
            )
        elif parts:
            out[successor] = parts[0]
    return out
