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
| **EODHD** | **Paid, €199/yr** | End-of-day all-world *including delisted tickers* and Nasdaq Helsinki. The realistic fix for survivorship bias here. |
| Norgate Data | Paid | Survivorship-bias-free, but US/AU/CA only — no Nordic coverage. |
| Sharadar | Paid | Point-in-time, survivorship-bias-free, US only. |
| Alpaca | Needs a free paper key | US only. Useful for research breadth. |
| **TradingView** | **Not usable — see below** | No data API, and the licence forbids this use case. |
| Stooq | Now behind a JS proof-of-work challenge | Not used. |
| Yahoo Finance | Rate-limited/blocked from many IPs | Unreliable for a pipeline. |

### TradingView: not a viable source, on two independent grounds

Worth stating explicitly because it is the most common suggestion.

**1. There is no data API.** TradingView publishes three APIs — Charting
Library, Datafeed (UDF), and Broker REST — and none of them retrieves prices.
Charting Library and Datafeed are for developers who *supply* data to a
TradingView chart; Broker REST is for brokerages integrating *into* TradingView
for order routing. There is no public endpoint for pulling historical bars out,
and this has been the position for years.

**2. The licence forbids this use case, however the data is obtained.** This is
the decisive point, and it is stronger than a simple anti-scraping rule.
TradingView's terms license market data for **display-only** use and explicitly
prohibit "non-display" usage — naming *algorithmic decision-making, algorithmic
trading, price referencing, and any machine-driven processes that do not involve
direct, human-readable display*.

A backtest is machine-driven, non-display use by definition. So even the
legitimate, manual "Export chart data" button on a paid plan produces data that
**may not be fed into this system.** The restriction comes from the exchanges
that license data to TradingView, not from TradingView's own preferences, which
is why no workaround exists at the user's discretion.

Their terms separately prohibit automated collection by "scripts, APIs, screen
scraping, data mining, robots or other data gathering and extraction tools",
and enforcement is real — accounts are banned for detected automated access.
**No scraping path is implemented in this repository, and none should be.**

**3. It would not solve the actual problem anyway.** The blocker is survivorship
bias — needing delisted and acquired names. TradingView does not serve delisted
tickers either, so even setting aside points 1 and 2, it would deliver the same
biased universe as IBKR with added licensing and reliability risk.

TradingView remains genuinely useful for what it is designed for: **manual chart
inspection**. Eyeballing a candidate's price history around an event, or sanity-
checking an odd bar the quality auditor flagged, is display use and entirely
appropriate.

---

## What data actually costs, and why it matters at €10k

A subscription is charged **per year regardless of whether the strategy
trades**, so on a small account it behaves like a large fixed drag. Per-trade
cost models miss this completely, which is how a strategy that looks marginally
profitable becomes a guaranteed loss once the tooling is paid for.

`tradelab.research.feasibility.annual_economics` models it. At EODHD's €199/year
on a €10,000 account (**1.99% of the account annually**):

| Hypothesis | Gross/yr | Trading cost | Data cost | **Net/yr** | Net € | Break-even account |
|---|---|---|---|---|---|---|
| H4 PEAD | 5.04% | 1.68% | 1.99% | **1.37%** | €137 | €5,923 |
| H5 index deletions | 4.20% | 0.90% | 1.99% | **1.31%** | €131 | €6,030 |
| H7 spin-offs | 6.00% | 0.45% | 1.99% | **3.56%** | €356 | €3,586 |
| H9 fire sales | 5.00% | 0.75% | 1.99% | **2.26%** | €226 | €4,682 |

Reproduce with `.venv/bin/python scripts/hypothesis_screen.py`.

**This cuts both ways, and both directions matter.**

The break-even account sizes all sit **below €10,000**, so paying for data is
justified at this account size. But it is a bet on the research succeeding, not
on a known return — none of these edges is validated, and the priors are
deliberately generous.

The net figures are the sobering part: **the best candidates net on the order of
one to three hundred euros a year on €10,000, with the subscription consuming a
third to half of gross profit.** That is the honest scale of what a €10k
systematic equity account can expect *even when a real edge is found*.

Both improve sharply with size, because the edge scales with equity while the
subscription does not. At €50,000 the same data cost is 0.40% instead of 1.99%.

### The practical recommendation

**Buy one month, not a year.** EODHD is €19.99 monthly with no commitment. A
validation sprint needs the *history*, not a live feed — download the universe
once, store it in the Parquet store with its provenance metadata, cancel, and
run the research protocol against the local copy for as long as it takes.

That turns a €199/year commitment into a **€20 one-off**, which is 0.2% of the
account and small enough that the decision does not need to be agonised over.
Re-subscribe for a month when the universe needs refreshing.

**This is licence-compliant, not a loophole.** EODHD's terms state that
"Non-Professional Users are permitted to store, manipulate, and analyze the data
for private, non-commercial purposes", and that the minimum commitment is one
month with cancellation at any time. No clause requires deletion on
cancellation. Redistribution remains prohibited, which is why the entire
`data/` tree is gitignored.

This is also the honest sequencing: the research protocol is designed to
*reject* most candidates, so the expected outcome of the first sprint is a set
of rejections. Paying €20 to find that out is good value; paying €199 up front
for a live feed you will not use is not.

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
