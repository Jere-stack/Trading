# Market Data

Bad data produces confident, wrong backtests. Unlike a code bug it does not
crash — it quietly changes the answer, and the direction is almost always
flattering. This layer exists to make data problems loud.

Implemented in `src/tradelab/data/`, tested in `tests/test_data.py`.

---

## Source: use IBKR itself

**IBKR is the primary source, and the reason is alignment rather than
convenience.** The contract object that returns a bar is the same one that will
carry an order.

Any external vendor introduces a symbol-mapping problem — vendor `NOKIA` versus
IBKR's Helsinki listing versus the New York ADR — and a mapping error means
**researching one instrument and trading another**, which looks correct at every
step. It also avoids paying a second vendor for data the account already
entitles you to.

`IbkrBarProvider` handles three IBKR-specific constraints that otherwise produce
confusing failures: request pacing (exceeding it drops the connection rather
than returning an error), per-request duration limits (long histories must be
chunked), and `whatToShow` semantics (`MIDPOINT` bars carry **no volume**, which
silently disables the capacity check).

### IBKR's one serious weakness

**IBKR generally cannot serve data for delisted contracts**, so an
IBKR-built universe is survivorship-biased. The provider declares
`includes_delisted = False` honestly, and the quality auditor escalates it to
CRITICAL rather than letting it pass.

This is not a small caveat. Survivorship bias on US equities is typically worth
**1–4% per year of spurious return** — comparable to or larger than any edge in
`docs/06-strategy-hypotheses.md`. Options, in order of preference:

1. **Buy a point-in-time dataset** (CRSP, Norgate, Sharadar). Costs money;
   solves the problem properly.
2. **Restrict to hypotheses where it matters less.** H5 (index deletions) is
   partly self-correcting, since the event *is* a universe change and the
   matched-sample control must be built explicitly anyway.
3. **Bound the bias** by measuring the same strategy on a universe you know to
   be biased and quantifying the gap. Weak, but better than ignoring it.

**Do not proceed to a headline result on a survivorship-biased universe.** The
auditor is configured to refuse, which is the intended behaviour.

### Other sources

| Source | Status | Notes |
|---|---|---|
| **ECB reference rates** | **Working, free, keyless** | `EcbFxProvider`. 1999→present, 41 currencies. Required: a EUR account holding USD stocks cannot compute equity — and therefore cannot evaluate any risk limit — without FX. |
| Alpaca | Needs a free paper key | US only. Useful for research breadth. |
| Stooq | Now behind a JS proof-of-work challenge | Not used. |
| Yahoo Finance | Rate-limited/blocked from many IPs | Unreliable for a pipeline. |

---

## Schema: one canonical form, enforced at the edge

Providers disagree about column names, timezones, adjustment policy and how
missing data is signalled. `normalise_bars` converts at the boundary and
validates strictly; nothing downstream needs to know the source.

Two conventions are load-bearing:

**`timestamp` is the bar's CLOSE time, always UTC, always tz-aware.** That makes
`timestamp <= now` a safe comparison, which is the anti-lookahead property the
engine relies on. Naive timestamps are rejected outright — a vendor returning
exchange-local times read as UTC shifts every bar by hours, moving signals
across session boundaries in a way that is nearly invisible in a plot.

**`adjustment` is a required flag, not an assumption.** `Adjustment.NONE` is the
only value safe for computing realistic fill prices, because it is what actually
traded; `SPLIT_AND_DIVIDEND` is what you want for returns. Back-adjusted prices
from years ago can be a small fraction of the real traded price, which silently
breaks per-share commission models and minimum-price filters.

Rejected loudly, never repaired silently: NaN prices, non-positive prices, OHLC
violations, duplicate `(symbol, timestamp)` rows, unparseable timestamps, and a
missing `symbol` with nothing to infer it from.

---

## Quality audit: five ways data lies

`audit()` reports rather than repairs. Automatic repair hides the problem, and
the right response differs by cause: a split needs adjustment, a bad tick needs
removal, a halt needs exclusion from the universe.

| Check | Severity | Detects |
|---|---|---|
| `survivorship` | **CRITICAL** | Every symbol ending on the same recent date — the signature of a universe built from *current* constituents |
| `unadjusted_split` | **CRITICAL** if unadjusted | Near-exact split ratios (½, ⅓, ¼…) with a reciprocal volume jump |
| `zero_volume` | CRITICAL if >10% | Bars where no trade occurred, so no fill was possible |
| `calendar_gap` | CRITICAL if >5% | Missing trading days, which shift lookback windows |
| `outlier_return` | WARNING | Moves beyond a threshold — bad ticks create exactly the moves event strategies trigger on |
| `stale_price` | WARNING | Repeated closes — usually a halt or delisting, not a flat market |
| `short_history` | WARNING | Too few bars for a stable volatility estimate |

`report.raise_if_unusable()` refuses to proceed on any CRITICAL issue.

---

## Calibration: measuring what the cost model assumes

This is what removes the standing caveat that *every cost figure is a modelled
default*. ADV and volatility are directly measurable. **Spread is not** — OHLCV
data contains no quotes — so it must be estimated.

### The estimator choice is load-bearing

Two published high-low estimators were validated against simulated OHLC with a
**known injected spread** (`scripts/validate_spread_estimators.py`):

| True spread | Corwin-Schultz | Abdi-Ranaldo |
|---|---|---|
| 5 bps | **60.9** | 7.8 |
| 10 bps | **63.0** | 12.3 |
| 25 bps | **71.5** | 26.8 |
| 50 bps | 87.2 | 51.5 |
| 100 bps | 122.3 | 101.4 |
| 200 bps | 204.2 | 201.3 |

**Abdi-Ranaldo (2017) is the default.** It tracks the truth across the range
with a small, slightly conservative bias of +1.3 to +2.8 bps.

**Corwin-Schultz (2012), despite being far better known, carries a ~60 bps bias
floor.** Applied to liquid names it reports ~60 bps where the truth is 5 —
*above* the 35 bps one-way cost budget. A Corwin-Schultz calibration would
therefore reject the entire liquid universe as untradable, silently, and with
the appearance of rigour.

**The generalisable lesson:** "take the more conservative of two estimates" is
sound only when both are unbiased. When one has a systematic floor, the maximum
inherits the bias rather than the caution. `MAX_OF_BOTH` was the original
default here and was wrong.

### The opposite bias bounds where this works

Abdi-Ranaldo needs enough intraday trades for the high-low mid to approximate
the efficient price:

| True spread | ~200 trades/day | ~390 trades/day |
|---|---|---|
| 10 bps | **0.0** | 15.4 |
| 25 bps | **9.8** | 28.6 |
| 50 bps | 44.2 | 52.9 |

For thinly-traded names the estimator **understates** the spread, collapsing
toward zero at small true spreads. That is the dangerous direction: it makes an
illiquid instrument look cheap to trade precisely where the real cost is worst.

**Rule: trust a calibrated spread only for actively traded names, and never let
a low estimated spread alone qualify a thin instrument.** `screen_universe`
rejects on ADV participation independently of the spread, so a thin name cannot
pass on an optimistic spread alone.

**Better still, measure it.** `sample_spread_from_quotes` takes observed IBKR
quotes and returns the *median* spread per symbol — median, not mean, because
quoted spreads widen dramatically at the open, the close and around news, and a
mean would be dominated by episodes that are a small fraction of the session.

---

## Universe screening

`screen_universe` applies the cost model to measured statistics and answers
"which instruments can this account afford to trade at all?" — before any signal
is written. It typically removes a large fraction of a broad universe, and
running it first avoids researching signals on names that were never tradable.

It is the data-driven counterpart to `CostEfficiencyCheck`, rejecting on price
floor, ADV participation, and modelled one-way cost.

---

## FX is not optional

`EcbFxProvider` supplies real daily rates back to 1999. A EUR-based portfolio
holding USD stocks cannot compute equity without them, and every risk limit is
derived from equity.

Measured on real ECB data over 2024-01 → 2026-09:

- **EUR/USD annualised volatility: 6.66%**
- **Peak-to-trough range: 17.4%** (1.0198 → 1.1974)

Set that against the strategy economics. A candidate earning 30 bps per round
trip over 12 round trips a year makes ~3.6% gross. **Unhedged USD exposure
carries nearly twice that in annual volatility** — uncompensated, because you
are not paid to bear EUR/USD risk.

This is the data behind the 70% `max_currency_exposure` default, and it is a
strong argument for preferring EUR-denominated instruments where an equivalent
edge exists.

Two caveats on ECB rates: they are **reference rates fixed once daily around
16:00 CET**, not tradable rates and not what IBKR would convert at (use
`tradelab.costs.fx` for conversion cost — the reference rate contains no
spread); and the published rate is **same-day**, so using it to price a
conversion decided at the open is lookahead.

---

## Storage

Parquet, per-symbol, under `<root>/bars/<dataset>/`. No database server — the
access pattern is "read a few years of daily bars for 10–50 symbols,
repeatedly", which columnar files serve well.

**Metadata is required, not optional.** Provider, adjustment policy, fetch time,
and whether delisted names are included are persisted in a sidecar JSON, because
those questions cannot be reconstructed from prices months later and results
computed without them cannot be trusted. `BarStore.metadata()` raises rather
than defaulting.

`write()` refuses to overwrite without an explicit opt-in: a dataset directory
may hold a differently-adjusted series, and splicing produces a phantom return
at the join.

---

## Workflow

```bash
# 1. Fetch (requires IB Gateway running and ib_async installed)
.venv/bin/tradelab data fetch --dataset us-liquid --symbols AAPL,MSFT,NVDA

# 2. Audit — refuses on CRITICAL issues
.venv/bin/tradelab data audit --dataset us-liquid

# 3. Calibrate the cost model from the data
.venv/bin/tradelab data calibrate --dataset us-liquid --notional 1000

# 4. Only now is strategy research defensible
```

Then set `require_calibrated_spread=True` for final validation runs, so a
guessed spread cannot pass silently into a headline result.
