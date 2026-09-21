"""Dividend cut detection from declaration records.

Hypothesis N2 in the register: a company cutting its dividend has told the
market something management works hard to avoid saying, and dividend-mandated
holders -- income funds, retirees drawing an income -- are forced to sell
regardless of price. Forced sellers who cannot wait are the only reliable
source of an edge at retail size.

**This is a short signal and the mandate is long-only.** A confirmed persistent
negative drift is not a strategy here; it is an *exclusion filter* usable by
every other strategy. That is still worth establishing.

## Three ways this detection goes wrong, and what is done about each

**1. Special dividends masquerade as cuts.** GE paid a $0.055 special in June
2012 between two regular $0.170 quarterlies. A naive comparison of consecutive
payments reads -67.6% and then +209%; GE did not cut its dividend in 2012. One
false positive in GE's four detections. Specials carry a null `period`, so
regular payments are compared only against regular payments.

**2. Frequency changes are cuts that do not look like cuts.** A company moving
from quarterly $0.20 to semi-annual $0.20 has halved its annual rate while the
per-payment amount is unchanged. Comparison is therefore on the **annualised
rate**, not the payment.

**3. Split adjustment.** `value` is adjusted for later splits and `period`
is not, so a 2:1 split halves the unadjusted payment without anything being
cut. Comparing `value` -- consistently adjusted across the whole series --
avoids it. Verified on GE: the adjustment ratio is constant at 4.7924 across
2010-2013, so the adjustment itself introduces no spurious changes.

## The bias this module cannot remove, stated plainly

**A dividend omission produces no record at all.** A company that stops paying
simply stops appearing, so the most severe form of the event -- the one with
the largest expected drift -- is invisible to a detector that reads rows.
Studying only *reductions* selects against the worst cases and biases the
measured drift toward zero, which is the flattering direction.

`detect_omissions` covers them with a gap rule, but its event date is an
inference rather than an observation: the endpoint never reports the
announcement of a dividend that did not happen. Those events carry
`date_is_inferred=True` and **must be analysed separately**, never pooled with
declared cuts into one headline number. Pooling a precisely-dated sample with
an imprecisely-dated one produces a result whose error bars nobody can state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

PERIODS_PER_YEAR = {
    "monthly": 12,
    "quarterly": 4,
    "semiannual": 2,
    "semi-annual": 2,
    "annual": 1,
    "yearly": 1,
}
"""Payment frequency by EODHD's `period` label, for annualising a rate.

A label outside this map (or null) marks an irregular payment -- a special, or
a record EODHD could not classify -- and is excluded from rate comparison
rather than guessed at.
"""


@dataclass(frozen=True)
class DividendCut:
    """A reduction in the annualised dividend rate, dated by declaration."""

    symbol: str
    event_date: datetime
    """When the market learned. The declaration date, not the ex-date."""
    ex_date: datetime
    previous_rate: float
    """Annualised rate before the cut, split-adjusted."""
    new_rate: float
    previous_declaration: datetime | None
    date_is_inferred: bool = False
    """True for omissions, where no declaration of the non-payment exists."""

    @property
    def cut_fraction(self) -> float:
        """How much of the rate was removed, in [0, 1]."""
        if self.previous_rate <= 0:
            return 0.0
        return max(0.0, 1.0 - self.new_rate / self.previous_rate)

    @property
    def is_omission(self) -> bool:
        return self.new_rate <= 0


def _annualised(frame: pd.DataFrame) -> pd.Series:
    freq = (
        frame["period"]
        .astype("string")
        .str.strip()
        .str.lower()
        .map(PERIODS_PER_YEAR)
        .astype("Float64")
    )
    return frame["value"].astype("Float64") * freq


def detect_dividend_cuts(
    frame: pd.DataFrame,
    *,
    min_cut: float = 0.25,
    require_declaration: bool = True,
) -> list[DividendCut]:
    """Find reductions of at least `min_cut` in the annualised dividend rate.

    `require_declaration=True` drops payments with no declaration date, because
    an event study anchored on the ex-date measures a drift that began when the
    market learned -- typically 12 to 43 days earlier, and up to 69 in the
    sample checked -- and reports the part that had already happened as though
    it were tradable.

    That drop is not free: declaration dates are missing more often in older
    records (27% of GE's history, concentrated pre-2009), so requiring them
    tilts the sample toward recent years. Prefer it anyway -- a precisely dated
    smaller sample beats a larger one whose returns start before the entry --
    but report the retention rate alongside any result.
    """
    cuts: list[DividendCut] = []
    for symbol, group in frame.groupby("symbol", observed=True):
        group = group.sort_values("ex_date")
        rate = _annualised(group)
        # Irregular payments (specials, unclassifiable records) are excluded
        # from the comparison entirely rather than compared against: they are
        # neither a cut nor the baseline for one.
        regular = group[rate.notna()].copy()
        if len(regular) < 2:
            continue
        regular["rate"] = _annualised(regular)

        previous = regular.iloc[0]
        for _, row in regular.iloc[1:].iterrows():
            prior_rate = float(previous["rate"])
            new_rate = float(row["rate"])
            if prior_rate > 0 and new_rate <= prior_rate * (1.0 - min_cut):
                declaration = row["declaration_date"]
                has_declaration = pd.notna(declaration)
                if has_declaration or not require_declaration:
                    prior_declaration = previous["declaration_date"]
                    cuts.append(
                        DividendCut(
                            symbol=str(symbol),
                            event_date=pd.Timestamp(
                                declaration if has_declaration else row["ex_date"]
                            ).to_pydatetime(),
                            ex_date=pd.Timestamp(row["ex_date"]).to_pydatetime(),
                            previous_rate=prior_rate,
                            new_rate=new_rate,
                            previous_declaration=(
                                pd.Timestamp(prior_declaration).to_pydatetime()
                                if pd.notna(prior_declaration)
                                else None
                            ),
                            date_is_inferred=not has_declaration,
                        )
                    )
            previous = row
    return cuts


def detect_omissions(
    frame: pd.DataFrame,
    *,
    gap_multiple: float = 2.0,
    universe_end: datetime | None = None,
    min_history: int = 4,
) -> list[DividendCut]:
    """Find dividends that stopped, via a gap in an established payment schedule.

    An omission is the most severe form of the event and the endpoint cannot
    report it: a dividend that was never declared produces no row. It is
    visible only as an absence -- a company paying quarterly for years, then
    nothing.

    **The event date here is inferred, not observed.** It is taken as the date
    the next payment was due, which is when the market could first have known
    for certain; the actual announcement usually came earlier. Every result
    carries `date_is_inferred=True` and belongs in its own bucket. Pooling it
    with declared cuts produces a headline nobody can put error bars on.

    `universe_end` must be the dataset's last date. Without it, every company
    still paying dividends at the end of the sample looks like it stopped --
    which would fill the sample with false omissions drawn entirely from
    healthy companies.
    """
    if universe_end is None:
        raise ValueError(
            "universe_end is required: without it every company still paying at "
            "the end of the sample registers as an omission, and the false ones "
            "are drawn entirely from healthy companies"
        )
    end = pd.Timestamp(universe_end)
    out: list[DividendCut] = []

    for symbol, group in frame.groupby("symbol", observed=True):
        group = group.sort_values("ex_date")
        rate = _annualised(group)
        regular = group[rate.notna()]
        if len(regular) < min_history:
            continue
        regular = regular.assign(rate=_annualised(regular))

        gaps = regular["ex_date"].diff().dt.days.dropna()
        if gaps.empty:
            continue
        typical = float(gaps.median())
        if typical <= 0:
            continue

        last = regular.iloc[-1]
        silence = (end - pd.Timestamp(last["ex_date"])).days
        if silence <= typical * gap_multiple:
            continue  # still paying, or not yet overdue

        due = pd.Timestamp(last["ex_date"]) + pd.Timedelta(days=typical)
        out.append(
            DividendCut(
                symbol=str(symbol),
                event_date=due.to_pydatetime(),
                ex_date=due.to_pydatetime(),
                previous_rate=float(last["rate"]),
                new_rate=0.0,
                previous_declaration=(
                    pd.Timestamp(last["declaration_date"]).to_pydatetime()
                    if pd.notna(last["declaration_date"])
                    else None
                ),
                date_is_inferred=True,
            )
        )
    return out


@dataclass(frozen=True)
class DividendInitiation:
    """A company's first dividend, dated by declaration.

    The mirror image of a cut, and the more useful one here. A cut is a
    forced-*seller* story that a long-only mandate can act on only by not
    holding. An initiation is a forced-*buyer* story: funds with an income
    mandate may hold only dividend payers, so a company becoming one is
    mechanically bought by a class of investor that was previously barred from
    owning it -- and they buy on the calendar, not on the price.

    It is also a costly signal in the same sense a cut is. Management binds
    itself to a recurring payment it will be punished for withdrawing, which is
    not something a board does lightly about earnings it does not expect to
    persist.
    """

    symbol: str
    event_date: datetime
    ex_date: datetime
    annual_rate: float
    yield_estimate: float
    """Annualised rate over the price at declaration, where price is known."""
    date_is_inferred: bool = False


def detect_initiations(
    frame: pd.DataFrame,
    *,
    min_history_days: int = 730,
    first_bar: dict[str, datetime] | None = None,
    require_declaration: bool = True,
) -> list[DividendInitiation]:
    """Find first-ever dividends, excluding companies that were already paying.

    **`first_bar` is what makes this honest.** A company whose price history
    starts in 2005 and whose first dividend record is also 2005 may have been
    paying since 1990 -- the data simply begins mid-stream. Counting that as an
    initiation fills the sample with established payers misclassified as new
    ones, which is a silent contamination in the direction of "no effect",
    because established payers have no reason to drift.

    Passing `first_bar` (symbol -> first price bar) requires a real gap of
    `min_history_days` between a company appearing in the data and its first
    dividend, so the initiation is observed rather than assumed. Without it,
    the function still runs but the caller is told nothing about how many
    events are actually resumptions of a stream that predates the data.
    """
    out: list[DividendInitiation] = []
    for symbol, group in frame.groupby("symbol", observed=True):
        group = group.sort_values("ex_date")
        rate = _annualised(group)
        regular = group[rate.notna()]
        if regular.empty:
            continue
        regular = regular.assign(rate=_annualised(regular))
        first = regular.iloc[0]

        if first_bar is not None:
            started = first_bar.get(str(symbol))
            if started is None:
                continue
            gap = (pd.Timestamp(first["ex_date"]) - pd.Timestamp(started)).days
            if gap < min_history_days:
                continue  # may have been paying before the data begins

        declaration = first["declaration_date"]
        has_declaration = pd.notna(declaration)
        if require_declaration and not has_declaration:
            continue

        out.append(
            DividendInitiation(
                symbol=str(symbol),
                event_date=pd.Timestamp(
                    declaration if has_declaration else first["ex_date"]
                ).to_pydatetime(),
                ex_date=pd.Timestamp(first["ex_date"]).to_pydatetime(),
                annual_rate=float(first["rate"]),
                yield_estimate=0.0,
                date_is_inferred=not has_declaration,
            )
        )
    return out
