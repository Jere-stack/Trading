# tradelab

A stocks-only automated trading system for a small (~€10,000) account, built
around one uncomfortable conclusion: **at this size, transaction costs — not
signal quality — decide whether a strategy is viable.**

Round-trip cost on a €1,000 position is 25–40 bps. Most published retail
strategies are unprofitable before they are tested. So the system is optimised
for **rejecting strategies cheaply and not fooling yourself**, rather than for
throughput or feature breadth.

**Current status: infrastructure complete and tested. Zero validated
strategies.** That is the intended state — see
[`docs/06-strategy-hypotheses.md`](docs/06-strategy-hypotheses.md).

---

## Quick start

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"

.venv/bin/python -m pytest tests/ -q          # 171 tests
.venv/bin/python scripts/cost_report.py       # why costs dominate at €10k
.venv/bin/python scripts/hypothesis_screen.py # reject hypotheses before any data
.venv/bin/tradelab config --mode PAPER        # validate configuration
```

For IBKR execution: `uv pip install -e ".[ibkr]"` (backtesting and research do
not need it).

---

## The three findings that shaped everything

Reproduce all of them with `scripts/cost_report.py`.

**1. Commission forces long holding periods.** At 30 bps round trip:

| Holding period | Annual cost drag |
|---|---|
| 1 day | **75.6%** |
| 1 week | 15.1% |
| 1 month | 3.6% |
| 1 quarter | 1.2% |

Intraday and daily strategies are arithmetically impossible. Holding periods
must be weeks to months.

**2. Per-trade FX conversion costs 3.76% of the account per year.** IBKR's
USD 2.00 minimum is 40 bps on a €500 conversion. Converting per trade costs
€400/year on €100k of turnover versus €24 blocked monthly. Hold a standing USD
balance; convert in blocks.

**3. Illiquid small caps are a trap, not a frontier.** A Helsinki micro-cap
costs **~322 bps round trip**. Institutions are absent because the spread is
uneconomic *for everyone* — not because retail size confers an advantage. This
closes off the most commonly recommended "retail edge".

### And the finding that shaped the research protocol

The expected maximum Sharpe of **N worthless strategies** over 3 years of daily
data:

| Variants tested | Expected best Sharpe (pure luck) |
|---|---|
| 10 | 0.91 |
| 200 | **1.60** |
| 10,000 | 2.23 |

Test 200 combinations and report the best, and you should expect Sharpe ≈1.6
**from nothing**. `tests/test_validation.py` constructs the best of 200 random
series (Sharpe 1.39) and asserts the protocol rejects it (DSR 0.36) — while the
same series declared as one trial would pass at DSR 0.99.

---

## Design principles, enforced structurally

Not by convention — by the type system, the import graph, and the control flow.

| Principle | Mechanism |
|---|---|
| Ledger reconciles exactly to broker statements | `Decimal` everywhere in accounting; floats only in analytics |
| Backtest and live share one code path | All time via injected `Clock`; nothing outside `LiveClock` reads the wall clock |
| Risk gate is unbypassable | `strategy/` cannot import `execution/`; the engine routes every intent through `RiskEngine` |
| Lookahead requires deliberate circumvention | Orders fill on the bar *after* the signal; `ctx.history()` cannot return unclosed bars |
| Unknown means no | Any risk check that raises is a REJECT, not a skip |
| Cost is never optimistic | Limits require the price to trade *through*; adverse gaps fill at the open; spread inflated 1.25× |

Each is pinned by a test in `tests/test_engine_integrity.py` or
`tests/test_risk_engine.py`.

---

## Layout

```
src/tradelab/
├── core/        Domain primitives (money, clock, types). Depends on nothing.
├── costs/       Commission, slippage, FX. The economic heart.
├── portfolio/   Multi-currency accounting. Source of truth.
├── risk/        13-check fail-closed pre-trade gate, kill switch.
├── execution/   Broker protocol, pessimistic simulator, IBKR adapter.
├── strategy/    Strategy interface. Sandboxed by construction.
├── engine/      Backtest and live loops.
├── data/        Schema, quality audit, calibration, providers, Parquet store.
├── engine/      Backtest replay and the live/paper runner.
└── research/    Metrics, feasibility screening, statistical validation.

docs/            Broker selection, architecture, risk, protocol, hypotheses
scripts/         Reproducible cost and hypothesis reports
tests/           171 tests
```

Dependencies point inward only.

---

## Documentation

| Document | Contents |
|---|---|
| [01 Broker selection](docs/01-broker-selection.md) | Why IBKR Ireland; full cost analysis; why Alpaca's EU entity doesn't apply |
| [02 Architecture](docs/02-architecture.md) | Control flow, tech stack, paper→live switching, promotion gates |
| [03 Risk management](docs/03-risk-management.md) | The 13 checks, kill-switch semantics, known gaps |
| [04 Market data](docs/04-data.md) | Sources, quality audit, spread calibration, survivorship bias |
| [07 EODHD setup](docs/07-eodhd-setup.md) | Step-by-step data acquisition; what needs a computer vs an iPad |
| [05 Research protocol](docs/05-research-protocol.md) | The 10-stage validation gauntlet and its hard gates |
| [06 Strategy hypotheses](docs/06-strategy-hypotheses.md) | 10 candidates, 6 rejected pre-data, 4 pending |

---

## Honest limitations

- **1–2 months of paper trading cannot validate an edge.** A Sharpe-1.0
  strategy needs ~2.7 years to be distinguished from zero. The paper period
  catches implementation bugs and calibrates the cost model — necessary, but not
  statistical evidence.
- **Cost figures are modelled defaults until calibrated.** `tradelab data
  calibrate` measures ADV, volatility and spread from bars, but the final check
  is diffing modelled cost against real IBKR statements. A cost model never
  diffed against a statement is a guess, however precise it looks.
- **IBKR cannot serve delisted contracts**, so an IBKR-built universe is
  survivorship-biased — typically worth 1–4%/yr of spurious return, comparable
  to any edge under investigation. The quality auditor refuses such data as
  CRITICAL. Resolving this needs a paid dataset (EODHD, ~€20 for one month).
- **TradingView cannot supply data for this system.** It has no data API, and
  its market data is licensed display-only — explicitly excluding algorithmic
  decision-making, so even manual chart exports may not be fed into a backtest.
  It also has no delisted tickers, so it would not fix survivorship bias
  anyway. See [docs/04-data.md](docs/04-data.md).
- **`max_sector_weight` is in the config schema but not wired into a check.**
  Known gap; manage sector concentration via universe construction until closed.
- **Most likely outcome is zero validated strategies**, which is better than
  deploying an overfitted one. At ~€100k the cost constraint relaxes and several
  rejected candidates re-enter feasibility.

---

## Mandate

Stocks and ETFs only. No options, futures, crypto, CFDs or leverage — enforced
by the `AssetClass` enum and `MandateCheck`, so adding them requires a
reviewable change rather than passing a different contract type through the
stack. Long-only by default.

---

## Restart safety

An automated system restarts — on crashes, deploys, IB Gateway's nightly
re-authentication. `LiveRunner` runs a startup sequence whose order is
safety-critical: connect → restore risk state → reconcile against the broker →
seed the ledger → begin the loop. A failure at any step halts rather than
continuing degraded, because not trading costs an opportunity while trading on
wrong state costs capital.

**A restored HARD halt stays a HARD halt.** A system that trips a drawdown kill
switch and then restarts must not come back up trading:

```
SESSION 1  order submitted; HARD halt tripped; process crashes
SESSION 2  CRITICAL startup: restored a HARD halt: drawdown 15% breached.
                     Restarting is not a re-arm.
           WARNING  risk_reject: buyer BUY 10 TEST: HARD halt active
           orders submitted after restart: 0
```

Without persisted risk state, `day_start_equity` resets on restart — making the
daily loss limit unenforceable by the simple expedient of crashing.

```bash
tradelab status --mode PAPER    # risk state, halts, recent events
tradelab rearm --operator jere  # clear a HARD halt, with confirmation
```

---

## Data layer

```bash
.venv/bin/tradelab data fetch --dataset us-liquid --symbols AAPL,MSFT,NVDA
.venv/bin/tradelab data audit --dataset us-liquid       # refuses on CRITICAL
.venv/bin/tradelab data calibrate --dataset us-liquid   # measures the cost model
.venv/bin/tradelab data fx --base EUR --quote USD       # real ECB rates
```

**The estimator choice turned out to be load-bearing.** Spread cannot be
observed in OHLCV data, so it must be estimated. Validated against simulated
bars with a *known* injected spread
(`scripts/validate_spread_estimators.py`):

| True spread | Corwin-Schultz | Abdi-Ranaldo |
|---|---|---|
| 5 bps | **60.9** | 7.8 |
| 25 bps | **71.5** | 26.8 |
| 200 bps | 204.2 | 201.3 |

Corwin-Schultz — the better-known estimator — carries a **~60 bps bias floor**.
Applied to liquid names it reports 60 bps where the truth is 5, which exceeds
the 35 bps cost budget and would reject the entire liquid universe as
untradable. Silently, and with the appearance of rigour. Abdi-Ranaldo is the
default.

The generalisable lesson: *"take the more conservative of two estimates" is
sound only when both are unbiased.* When one has a systematic floor, the maximum
inherits the bias rather than the caution.

Measured on real ECB data (2023-09 → 2026-09): **EUR/USD annualised volatility
is 6.70%, peak-to-trough 17.4%**. A strategy earning 30 bps over 12 round trips
a year makes ~3.6% gross — unhedged USD exposure carries nearly twice that in
uncompensated volatility.

### What data costs, and why it changes the arithmetic

A subscription is charged per year whether or not the strategy trades, so on a
small account it is a large fixed drag that per-trade cost models miss entirely.
EODHD at €199/yr is **1.99% of a €10,000 account**:

| Hypothesis | Gross/yr | Data cost | **Net/yr** | Net € | Break-even account |
|---|---|---|---|---|---|
| H5 index deletions | 4.20% | 1.99% | **1.31%** | €131 | €6,030 |
| H7 spin-offs | 6.00% | 1.99% | **3.56%** | €356 | €3,586 |
| H9 fire sales | 5.00% | 1.99% | **2.26%** | €226 | €4,682 |

Break-even sizes sit below €10,000, so the subscription is justified —
conditional on the edges being real, which none is. But the net figures set
expectations honestly: **one to three hundred euros a year on €10,000, with data
consuming a third to half of gross profit.**

**Practical route: buy one month (€19.99), not a year.** A validation sprint
needs the history, not a live feed — download the universe once, store it with
provenance, cancel, and run the protocol against the local copy.

The EODHD provider is implemented and verified against the live API:

```bash
export EODHD_API_TOKEN=...
tradelab data eodhd --dataset us-research --exchange US --years 10 --max-symbols 500
tradelab data audit --dataset us-research
tradelab data calibrate --dataset us-research --notional 1000
```

Omitting `--symbols` builds the universe from **active and delisted** tickers,
which is the entire reason to pay. Full setup, including which phases need a
real computer, is in [docs/07-eodhd-setup.md](docs/07-eodhd-setup.md).

---

## Pipeline validation

`scripts/pipeline_demo.py` validates the whole system end to end against
synthetic data with a **known injected effect**. This matters because
validating a research pipeline on real market data confounds two unknowns:
whether the pipeline works, and whether the effect exists.

Results:

| Case | Gross return | Net return | Verdict |
|---|---|---|---|
| No effect | +12.31% | **−0.73%** | Beta, destroyed by 940 bps cost drag |
| 300 bps effect | +382.69% | +345.28% | Detected |

Two findings worth stating:

**A long-only strategy with no edge does not merely fail to profit at €10k — it
loses money by trading at all.** The null case earns +12.31% gross purely from
market exposure, and ~940 bps of annual cost drag turns that into a net loss.

**The permutation test correctly separates skill from exposure** (p=0.71 with no
effect, p=0.000 with one), which a Sharpe ratio cannot do. The null case's
Sharpe of 0.38 looks like a weak edge; it is entirely beta.

Break-even in that setup is ~100 bps per event — and that is a **lower bound**,
because the synthetic effect is injected into all names simultaneously and
compounds over 739 fills. Against it, the pre-registered priors are sobering:
PEAD at 90 bps sits below the floor before crowding is even considered.
