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

.venv/bin/python -m pytest tests/ -q          # 87 tests
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
└── research/    Metrics, feasibility screening, statistical validation.

docs/            Broker selection, architecture, risk, protocol, hypotheses
scripts/         Reproducible cost and hypothesis reports
tests/           87 tests
```

Dependencies point inward only.

---

## Documentation

| Document | Contents |
|---|---|
| [01 Broker selection](docs/01-broker-selection.md) | Why IBKR Ireland; full cost analysis; why Alpaca's EU entity doesn't apply |
| [02 Architecture](docs/02-architecture.md) | Control flow, tech stack, paper→live switching, promotion gates |
| [03 Risk management](docs/03-risk-management.md) | The 13 checks, kill-switch semantics, known gaps |
| [05 Research protocol](docs/05-research-protocol.md) | The 10-stage validation gauntlet and its hard gates |
| [06 Strategy hypotheses](docs/06-strategy-hypotheses.md) | 10 candidates, 6 rejected pre-data, 4 pending |

---

## Honest limitations

- **1–2 months of paper trading cannot validate an edge.** A Sharpe-1.0
  strategy needs ~2.7 years to be distinguished from zero. The paper period
  catches implementation bugs and calibrates the cost model — necessary, but not
  statistical evidence.
- **All cost figures are modelled defaults** until calibrated against real IBKR
  statements. A cost model never diffed against a statement is a guess, however
  precise it looks.
- **`max_sector_weight` is in the config schema but not wired into a check.**
  Known gap; manage sector concentration via universe construction until closed.
- **No data pipeline yet.** Strategy validation is blocked on acquiring daily
  bars with delisted names included.
- **Most likely outcome is zero validated strategies**, which is better than
  deploying an overfitted one. At ~€100k the cost constraint relaxes and several
  rejected candidates re-enter feasibility.

---

## Mandate

Stocks and ETFs only. No options, futures, crypto, CFDs or leverage — enforced
by the `AssetClass` enum and `MandateCheck`, so adding them requires a
reviewable change rather than passing a different contract type through the
stack. Long-only by default.
