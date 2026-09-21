"""Deciding which symbols are actually US common stocks.

A survivorship-free exchange dump is not a universe. EODHD's US list holds
111,260 symbols of which only 46% are even labelled common stock -- the rest
are funds, ETFs, mutual funds, preferred shares, warrants, units and notes --
and **the label itself is unreliable**:

    JPMPRD    JPMorgan Chase & Co        -> labelled Common Stock; a preferred
    MTB-P-J   M&T Bank                   -> labelled Common Stock; a preferred
    BTT       BlackRock Municipal Trust  -> labelled Common Stock; a closed-end fund
    WDI       Western Asset Income Fund  -> labelled Common Stock; a closed-end fund
    TTB       TMB Bank (Thailand)        -> labelled USA / NYSE; priced in baht
    NIND      National Industries (KW)   -> labelled USA / NYSE
    ACL       Alcon (Switzerland)        -> showing $38,500 a share

Left in, these dominate any liquidity-ranked universe, because a foreign price
in a foreign currency multiplied by a share count produces an enormous
"dollar volume". A top-500-by-dollar-volume screen on the raw dump returned
TTB at $4.5bn/day at $24,100 a share -- ahead of Microsoft. A random 20 drawn
from that "large cap" universe returned 2.31%/yr against SPY's 14.87%, which
is not a market fact, it is a data fact.

## The ISIN rule that had to be thrown away

Every contaminant above carries a null or non-US ISIN, so requiring one
beginning `US` looked like the answer. **It is survivorship bias in a new
costume.** Among common stocks on tradable exchanges, ISIN coverage is 73.3%
for live names and 24.9% for delisted ones -- EODHD simply has less reference
data on companies that stopped existing. Requiring an ISIN would have
preferentially deleted the failures, which is precisely the bias this project
spent a full universe download removing.

The rule is gone. What remains -- instrument type, exchange, and ticker and
name patterns -- is checked for the same bias by `survivorship_check`, which
is not optional: any future rule added here must be run through it before it
is trusted.

## What is deliberately NOT filtered

Companies that collapsed and reverse-split, leaving a high adjusted price --
HEXO, CERC, ACOR, PTN and the rest -- **stay in**. They are real US common
stocks and real catastrophic losses, and excluding them because their prices
look strange is exactly the survivorship bias this project spent a download
removing. A high adjusted price is a return, not an error.
"""

from __future__ import annotations

import re

import pandas as pd

TRADABLE_EXCHANGES = frozenset({"NASDAQ", "NYSE", "BATS", "NYSE MKT", "AMEX"})
"""Exchanges where a retail order actually executes at a modelled spread.

OTC tiers (PINK, OTCGREY, OTCQB) are excluded: their spreads are far outside
anything the cost model was calibrated on, so a backtest there measures a
trade nobody could place. NMFQS is the mutual-fund quotation service -- 47,722
symbols that are not stocks at all.
"""

_NON_COMMON_NAME = re.compile(
    r"\b(?:fund|trust|etf|etn|index|portfolio|depositary|preferred|warrant|"
    r"unit[s]?|right[s]?|notes?|bond|debenture|spac)\b",
    re.IGNORECASE,
)
"""Name patterns for instruments mislabelled as common stock.

Closed-end funds are the main catch: BTT and WDI both carry US ISINs and the
Common Stock label, and both are funds.
"""

_SHARE_CLASS_TICKER = re.compile(r"(?:-P-|-PR|-WT|-WS|-U$|-UN$|-CL$|-RT$|\.P$|PR[A-Z]$)")
"""Ticker patterns for preferred series, warrants, units and rights."""


def is_us_common_stock(row: pd.Series) -> bool:
    """One row of an EODHD exchange symbol list."""
    if str(row.get("Type", "")).strip() != "Common Stock":
        return False
    if str(row.get("Exchange", "")).strip().upper() not in TRADABLE_EXCHANGES:
        return False
    if _SHARE_CLASS_TICKER.search(str(row.get("Code", ""))):
        return False
    return not _NON_COMMON_NAME.search(str(row.get("Name", "")))


def us_common_stocks(symbols: pd.DataFrame) -> set[str]:
    """Ticker set for the tradable US common-stock universe."""
    keep = symbols[symbols.apply(is_us_common_stock, axis=1)]
    return {str(code).upper() for code in keep["Code"]}


def filter_report(symbols: pd.DataFrame) -> pd.DataFrame:
    """How many symbols each rule removes, for the record.

    A universe filter that silently discards 90% of the input is as dangerous
    as no filter at all, so the damage is always reported.
    """
    total = len(symbols)
    by_type = symbols["Type"].astype(str).str.strip() == "Common Stock"
    by_exchange = by_type & symbols["Exchange"].astype(str).str.upper().str.strip().isin(
        TRADABLE_EXCHANGES
    )
    by_ticker = by_exchange & ~symbols["Code"].astype(str).str.contains(
        _SHARE_CLASS_TICKER, regex=True, na=False
    )
    by_name = by_ticker & ~symbols["Name"].astype(str).str.contains(
        _NON_COMMON_NAME, regex=True, na=False
    )
    stages = [
        ("all symbols", total),
        ("Type == Common Stock", int(by_type.sum())),
        ("on a tradable exchange", int(by_exchange.sum())),
        ("not a preferred/warrant/unit ticker", int(by_ticker.sum())),
        ("not a fund/trust by name", int(by_name.sum())),
    ]
    return pd.DataFrame(
        [
            {"stage": name, "remaining": n, "removed": stages[i - 1][1] - n if i else 0}
            for i, (name, n) in enumerate(stages)
        ]
    )


def survivorship_check(symbols: pd.DataFrame) -> pd.DataFrame:
    """Does the filter delete failures more often than survivors?

    **Run this on every rule before trusting it.** A universe filter that
    removes delisted names at a higher rate than live ones is survivorship bias
    wearing the clothes of data hygiene, and it is invisible in every downstream
    number. The ISIN rule this module used to carry failed exactly here: 73.3%
    coverage on live names against 24.9% on delisted ones.

    A gap of more than a few points means the rule is choosing on survival.
    """
    if "delisted" not in symbols.columns:
        raise ValueError(
            "the symbol list must carry a `delisted` column; without it this "
            "check silently passes and the bias it exists to catch goes unseen"
        )
    # Condition on symbols that could plausibly be in the universe at all.
    # Measuring against the raw dump compares against 39,840 FUND entries that
    # are almost all live, which makes the live keep-rate look artificially low
    # and hides what the rules actually do to common stocks.
    frame = symbols[
        (symbols["Type"].astype(str).str.strip() == "Common Stock")
        & (symbols["Exchange"].astype(str).str.upper().str.strip().isin(TRADABLE_EXCHANGES))
    ].copy()
    frame["kept"] = frame.apply(is_us_common_stock, axis=1)
    grouped = frame.groupby("delisted")["kept"].agg(["size", "sum", "mean"])
    grouped.columns = ["symbols", "kept", "keep_rate"]
    grouped.index = grouped.index.map({False: "live", True: "delisted"})
    return grouped
