# Paper trading: what the first run actually established

`scripts/paper_session.py` replays two years of real daily US bars through the
full live path — signal, FX funding, risk gate, order, simulated fill, ledger,
persisted state, session rollover. It is a **plumbing test**. The strategy it
runs (`MonthlyEqualWeight`) has no edge and is not supposed to; its value is
that the correct answer can be computed by hand, so any deviation is a bug in
the system rather than a quirk of a signal.

It does **not** validate IBKR API integration, real fill prices or real
commissions. Those need IB Gateway on a real machine and are the second half of
Step 1.

## Result of the current run

| | |
|---|---|
| Period | 2024-09-19 → 2026-09-18 (501 sessions) |
| Starting capital | EUR 10,000 |
| Final equity | EUR 12,320.68 |
| Gross exposure | EUR 9,876 (0.80x) |
| Open positions | 10 / 10 |
| Risk rejections | 0 |
| FX conversions | 1 |

Zero rejections is the number that matters. Every order the strategy emitted
cleared the gate, which means the strategy and the limits now agree about what
is tradable. Getting there took four defects.

## Four defects the run surfaced

None of these would have appeared in a single-currency backtest. This is the
argument for running the paper path before committing capital, and for running
it against a strategy whose intended positions you can check by hand.

### 1. The FX rate was inverted

`Portfolio.fx_rate` wants base-currency-per-unit-of-foreign — EUR per USD, the
*inverse* of the quoted EUR/USD. Passing 1.1481 instead of 1/1.1481 overvalues
every USD holding by 32% and silently breaks every limit derived from equity.

### 2. Funding ran after the risk gate

`CashSufficiencyCheck` correctly refuses an order that would drive a currency
balance negative, so with funding downstream of the gate, nothing ever
converted and nothing ever traded. Conversion now happens in `_fund_batch`,
before the evaluation loop.

### 3. The currency budget was double-counted

`exposure_by_currency` counts cash **and** positions. Buying a USD stock with
USD cash therefore does not change USD exposure — it moves value from one line
to the other. Charging the purchase against `max_currency_exposure` counted it
twice: the EUR 9,500 conversion that funded the account had already spent the
whole budget, so gross exposure stalled at 0.51x.

Foreign exposure moves at **conversion**, and that is where it is bounded
(`BlockFxPolicy.max_foreign_share`). The gate now sizes against free cash in
the currency plus whatever headroom remains under the cap; once over the cap,
cash already held can still be deployed. Refusing to invest USD you already
hold does not reduce USD risk by one cent — it only converts an invested dollar
into an idle one.

### 4. The strategy sized a USD position from EUR equity

`ctx.equity` is base currency; `ctx.last_price` is the instrument's. Dividing
one by the other is a currency error, not an approximation, and it sized every
US name short by the EUR/USD rate — 13% at 1.148. `StrategyContext` now carries
the FX table and exposes `budget_in`, `to_base` and `in_currency`; an unknown
currency raises rather than assuming parity.

This is the defect worth generalising: it does not look like a bug in a live
run. It looks like the risk gate refusing to fill the book.

## Why gross exposure is 0.80x, not the 0.90x target

This is not a defect, and it will not be fixed by loosening a limit.

At EUR 12,321 equity with a 0.90 target across 10 names, each name gets USD
1,257. Shares are indivisible, so each position rounds **down**:

| | price | ideal | whole shares | shortfall |
|---|---|---|---|---|
| AAPL | 336.13 | 3.74 | 3 | USD 249 |
| MSFT | 493.78 | 2.55 | 2 | USD 269 |
| UNH | 376.90 | 3.34 | 3 | USD 127 |
| JPM | 349.67 | 3.60 | 3 | USD 208 |
| … | | | | |

Summed across the basket, whole-share rounding alone caps achievable gross at
**0.806x**. The run reaches 0.80x, i.e. essentially the ceiling.

The general result: with `N` names at average price `p` and equity `E`, rounding
down loses about `N·p/2` of deployment, or `N·p/(2E)` of gross. Ten US
mega-caps averaging USD 250 against EUR 10,000 is ~11% — which is exactly what
is observed.

**This is a real constraint on a EUR 10,000 account, not an artefact.** Three
ways out, in order of preference:

1. **Fewer, cheaper names.** Halving the basket halves the granularity cost.
2. **Fractional shares.** IBKR supports them for US equities. This removes the
   constraint outright and is the reason to prefer IBKR over a broker that
   does not.
3. **Accept it.** 0.80x gross against a 0.90x target is a 11% drag on gross
   return, which matters, but it is a known and bounded drag.

It is worth carrying into strategy design: a signal that needs 20 concurrent
positions is not implementable at this account size without fractional shares,
regardless of how well it backtests.

## Why a rejected order must change the strategy's behaviour

The first clean run still logged 39 rejections, all `cost_efficiency`, and all
duplicates: the same one-share top-up in the same three names, month after
month. A rejected order never changes the position that produced it, so the
strategy asks again next month, and the month after that.

`MonthlyEqualWeight` now carries its own `min_order_value` and lets drift
accumulate until the trade is worth its commission. The risk gate remains the
authority — this is the strategy declining to ask, not the strategy enforcing
a limit. The distinction matters architecturally: `strategy/` must not import
`risk/`, or the gate becomes bypassable.

The general lesson, which applies to every strategy added later: **a strategy
that ignores rejections will re-submit the same rejected order forever.** Either
the strategy models the constraint, or the log fills with noise and a real
rejection goes unnoticed among the duplicates.

## Running it

```bash
EODHD_API_TOKEN=... .venv/bin/python scripts/paper_session.py
```

The run is resumable: it replays only sessions newer than the last recorded, so
running it twice in one day is a no-op rather than a double-trade. State lives
in `state/paper/` and is committed, because the container is wiped between
sessions and because a track record in version history cannot be quietly
revised later.

To try a change without writing into the committed record:

```bash
.venv/bin/python scripts/paper_session.py --state-dir /tmp/scratch-run
```
