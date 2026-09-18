# Architecture

## Design premise

The system is built around one uncomfortable conclusion from the cost analysis
(`docs/01-broker-selection.md`): **at €10,000, transaction costs — not signal
quality — decide whether a strategy is viable.** A 25–40 bps round-trip cost
means most published retail strategies are unprofitable before they are even
tested.

So the architecture optimises for a specific thing: **making it cheap to reject
strategies, and hard to fool yourself.** Every structural decision below serves
that, rather than serving throughput or feature breadth.

Two properties are treated as non-negotiable and enforced structurally rather
than by convention:

1. **Backtest, paper and live run the same strategy code**, through the same
   risk gate, against the same interfaces. Anything less means the thing you
   validated is not the thing you deployed.
2. **Lookahead bias must require deliberate circumvention**, not merely an
   off-by-one error.

---

## Technology stack, and why

| Concern | Choice | Reasoning |
|---|---|---|
| Language | **Python 3.11+** | The quant ecosystem is unmatched. Latency is irrelevant here — holding periods are weeks, not microseconds. |
| Packaging | **uv** + `pyproject.toml` | Fast, reproducible, lockfile-based. |
| Money | **`decimal.Decimal`** | The ledger must reconcile exactly against broker statements. Float drift over thousands of fills makes that impossible. |
| Analytics | **numpy / pandas / scipy** | Floats are correct here — analytics is a lossy read-model, not the ledger. |
| Broker API | **`ib_async`** | Maintained successor to `ib_insync`, which has been unmaintained since 2021 and mishandles modern asyncio loops. |
| Config | **pydantic-settings** + YAML | Limits arrive as human-written YAML; a typo must fail at load, not at 03:00 live. |
| Market data | **Parquet** + DuckDB | Columnar, compressed, zero-ops. No database server to operate. |
| State | **SQLite** | ACID order/fill/position state that survives restarts. One file, no server. |
| Logging | **structlog** (JSON) | Machine-queryable post-mortems. |
| Testing | **pytest** + hypothesis | Property tests for accounting invariants. |
| Backtest engine | **custom** | Deliberate. See below. |

### Why a custom backtest engine

The requirement that paper and live run *the same code* is only achievable if
the engine's strategy-facing contract is identical across modes — which means
owning that contract. Existing frameworks each break it: `backtrader` and
`zipline` are unmaintained, `vectorbt` is vectorised in a way that makes
event-driven live execution a separate code path, and all of them abstract the
fill model away precisely where this project needs exact control. The engine is
~350 lines; the coupling cost of a framework is higher.

---

## Module layout

```
src/tradelab/
├── core/          Domain primitives. Depends on nothing.
│   ├── money.py       Decimal helpers, lot rounding, bps conversion
│   ├── clock.py       Clock / LiveClock / SimulationClock
│   ├── enums.py       Side, OrderType, OrderStatus, RunMode, ...
│   ├── types.py       Instrument, Bar, Quote, OrderRequest, Order, Fill, Position
│   └── ids.py         Deterministic id generation
├── costs/         Transaction cost models. The economic heart.
│   ├── commission.py  IBKR Tiered/Fixed, per-currency routing
│   ├── slippage.py    Half-spread + square-root impact
│   └── fx.py          Conversion cost, per-trade vs block policy
├── portfolio/     Accounting. Multi-currency, source of truth.
├── risk/          Pre-trade gate, limits, kill switch.
│   ├── limits.py      Validated limit config
│   ├── engine.py      13 fail-closed checks
│   └── killswitch.py  SOFT/HARD halts, risk state
├── execution/     Broker abstraction and implementations.
│   ├── broker.py      Protocol + reconciliation
│   ├── sim_broker.py  Pessimistic fill simulator
│   └── ibkr_broker.py IBKR adapter (ib_async)
├── strategy/      Strategy interface. Sandboxed by construction.
├── engine/        Backtest and live run loops.
├── data/          Market data storage and providers.
└── research/      Validation. The machinery for saying no.
    ├── metrics.py     Performance statistics
    └── validation.py  DSR, PBO, walk-forward, bootstrap, permutation
```

**Dependencies point inward only.** `core` imports nothing from the project;
`costs` and `portfolio` import only `core`; `risk` imports `core`, `costs`,
`portfolio`; `execution` and `engine` sit on top. A strategy never imports
`execution`. This is what makes the risk gate unbypassable — not discipline, but
the import graph.

---

## The control flow that guarantees correctness

Every timestamp, in this fixed order:

```
1. Clock advances to the bar timestamp
       (SimulationClock in backtest, LiveClock in live — same interface)
2. Bars published to strategy history; positions marked to market
3. Resting orders from PREVIOUS timestamps filled against this bar
4. strategy.on_bar() called — sees this bar as the latest CLOSED bar
5. Emitted OrderRequests pass through RiskEngine.evaluate()
6. Survivors submitted — eligible to fill from the NEXT bar
```

The ordering is the entire anti-lookahead argument:

- **Step 3 before step 4** makes "orders fill after the signal" structural. A
  signal computed from a bar's close cannot fill at that close, because the
  close was not knowable until the bar ended. Filling at the signal bar's close
  is the single most common source of fake backtest edge.
- **Step 5 between 4 and 6** makes the risk gate unbypassable — a strategy has
  no route to the broker.
- **A strategy cannot reorder any of this**, because it does not drive the loop.

These are verified by tests, not just documented:
`tests/test_engine_integrity.py` pins each specific route to a lookahead or
cost-optimism bug.

---

## Paper → live switching

Selected by config, not by code path:

```
RunMode.BACKTEST → SimulatedBroker over historical bars
RunMode.PAPER    → SimulatedBroker over live data, OR IbkrBroker on port 7497
RunMode.LIVE     → IbkrBroker on port 7496
```

**Run both paper routes — they catch disjoint classes of bug:**

| Route | Validates | Catches |
|---|---|---|
| `SimulatedBroker` + live data | The **strategy** | Whether the edge survives realistic modelled costs; lets you log counterfactuals |
| `IbkrBroker` + paper account | The **integration** | Contract ambiguity, tick-size rejections, pacing violations, reconnects, partial fills |

A system that only ran against its own simulator will break on its first live
order, usually on something mundane. A system that only ran against IBKR paper
has no cost attribution and a fill model it does not control.

### Promotion gate to live capital

Ordered, and each is a hard gate:

1. Strategy survives the research protocol (`docs/05-research-protocol.md`).
2. ≥1 month on `SimulatedBroker` with live data, cost model **calibrated against
   real IBKR statements**.
3. ≥1 month on the IBKR paper account with zero unexplained reconciliation
   breaks.
4. Live P&L attribution matches simulated attribution within a stated tolerance.
5. Deploy at **25% of target size** for the first month. Size up only after live
   costs match modelled costs.

**Honest caveat on the 1–2 month paper plan.** Per
`minimum_track_record_length`, a Sharpe-1.0 strategy needs roughly **2.7 years**
of daily data to be statistically distinguished from zero at 95% confidence. One
to two months of paper trading therefore **cannot validate an edge.** What it
can do — and what it is genuinely necessary for — is catch implementation bugs,
verify the cost model, and confirm operational stability. Both matter; only one
is statistical evidence. Plan accordingly, and do not interpret a profitable
paper month as confirmation.

---

## Risk management

Layered, with every order passing through all layers. Full detail in
`docs/03-risk-management.md`.

| Layer | Controls |
|---|---|
| **Mandate** | Equity/ETF only; long-only by default; no leverage |
| **Order** | Price sanity, fat-finger limit deviation, stale data, duplicates, rate limits |
| **Cost** | `min_order_notional`, `max_cost_bps_of_notional` — the distinctive small-account control |
| **Position** | Max weight, absolute notional cap, ADV participation |
| **Portfolio** | Gross/net exposure, position count, per-currency concentration |
| **Strategy** | Per-strategy gross exposure budgets |
| **Loss** | Daily loss (SOFT halt), max drawdown (HARD halt), consecutive losing days |

Two properties matter more than the specific limits:

**Fail-closed.** A check that raises an exception is a REJECT, never a skip. The
common cause is a missing FX rate or absent market data, and the safe answer to
"I don't know" is "no".

**Resize is a floor, not a negotiation.** When several checks want to shrink an
order, the smallest wins — and the cost gate then **re-runs on the resized
order**, because a resize can turn an economic order into an uneconomic stub
that pays full commission for a token position.

**Risk-reducing orders are exempted from sizing limits.** Without this, a book
that has breached a limit becomes un-exitable: the gate would block the very
orders that restore compliance. A halt that prevents you selling is a liability,
not a control.

### Why concentration, not diversification

At a €1.25 per-order floor, 30 positions of €330 pay ~45 bps round trip while 10
positions of €1,000 pay ~25 bps. **Wide diversification is unaffordable at this
account size.** The book is therefore concentrated (8–12 names) by economic
necessity, and risk control shifts onto position-level stops and the portfolio
drawdown kill switch. This is a genuine constraint of small-account trading, not
a preference.

---

## Multiple strategies

Supported from the start because retro-fitting attribution is painful:

- Every `OrderRequest` and `Fill` carries a `strategy_id`.
- `Portfolio.strategy_pnl()` and `exposure_by_strategy()` give per-strategy
  attribution.
- `strategy_budgets` allocates gross exposure as a fraction of equity.
  Over-allocation is rejected **at config load** — otherwise the first strategy
  to fire consumes shared capacity and realised allocation depends on arrival
  order rather than intent.

**Netting is deliberately not implemented.** If strategy A buys 100 AAPL and
strategy B sells 100 AAPL, the system sends both orders and pays both
commissions. Netting would be cheaper but would destroy per-strategy
attribution, making it impossible to tell which strategy to cut. At 10–40 round
trips per year the saving is negligible and the information loss is not. Revisit
only if strategies genuinely overlap in universe and timing.

---

## Operations

**IB Gateway, not TWS**, for unattended running: lighter and more stable. It
requires **daily re-authentication** — plan for it rather than discovering it.

Startup sequence, in order, because each step depends on the previous:

1. Connect to IB Gateway; fail loudly if unavailable.
2. **Reconcile** local positions against broker positions. Any break → HARD
   halt. The broker is authoritative; a restart that skips reconciliation will
   open a second position in a name it already holds.
3. Restore `RiskState` from SQLite — otherwise the daily loss limit silently
   resets to zero used.
4. Verify market data is live and not stale.
5. Begin the event loop.

**Kill switch semantics:**

- **SOFT** (daily loss, rate limit): blocks new risk, allows exits, clears at
  the next session.
- **HARD** (drawdown, reconciliation break): blocks everything, **requires
  manual re-arming with a named operator**. An automated system that resumes on
  its own after a 15% drawdown does not have a kill switch; it has a pause
  button. The whole value of the control is that a human looks at what happened
  before capital is risked again.

**Monitoring worth having:** daily equity/exposure snapshot, modelled-vs-actual
cost per fill (the leading indicator that a strategy is decaying), rejection
counts by reason, and reconciliation status. `BacktestResult.rejections` is a
primary result, not diagnostics — a strategy whose orders are 80% rejected on
cost grounds is not a strategy with a small edge, it is one that cannot be
traded at this size, and the equity curve alone will not tell you that.

### Restart safety

Implemented in `LiveRunner` (`src/tradelab/engine/live.py`) and `StateStore`
(`src/tradelab/portfolio/state.py`), pinned by `tests/test_live_runner.py`.

An automated system restarts — on a crash, a deploy, IB Gateway's nightly
re-authentication. Two things must survive, and losing either has a concrete
consequence:

| Lost across restart | Consequence |
|---|---|
| Risk state | `day_start_equity` resets, so a system already 3% down starts with a fresh 3% of rope. **The daily loss limit becomes unenforceable by crashing.** |
| Order/fill history | Reconciliation has nothing to compare against, so a restart cannot detect a fill that arrived while it was down. |

**A restored HARD halt stays a HARD halt.** Restarting is not a re-arm — a
system that trips a drawdown kill switch and then restarts must not come back up
trading. Clearing it requires `tradelab rearm --operator <name>`, which prompts
for confirmation and records who did it.

State is SQLite with `journal_mode=WAL` and `synchronous=FULL`: durability
matters far more than write throughput here, and losing the last committed fill
to an OS buffer is exactly the failure this prevents. Decimals are stored as
TEXT, because SQLite's REAL is a float and routing the ledger through one
reintroduces the representation error `Decimal` exists to avoid.

Positions are **derived from the fill log** rather than stored, so the local
view cannot drift from the fills that justify it. The broker remains
authoritative; this is what reconciliation compares against.

### Operational commands

```bash
tradelab status --mode PAPER    # persisted risk state, halts, recent events
tradelab rearm --operator jere  # clear a HARD halt, with confirmation
tradelab check-broker           # read-only connection and position check
```

Run `tradelab status` before starting a session and after any unexpected stop.

---

## Deliberate limitations

Stated so they are choices rather than oversights:

- **No intraday data pipeline.** The cost structure rules out intraday holding
  periods, so daily bars are sufficient. Adding minute bars would invite
  strategies the account cannot afford.
- **No short selling.** Requires margin, incurs borrow fees and recall risk, and
  falls outside the stated cash-equity mandate. `allow_short` exists but
  defaults off.
- **No options, futures, crypto, CFDs, leverage.** Enforced by the
  `AssetClass` enum and `MandateCheck`, so adding them requires a reviewable
  change rather than passing a different contract type through the stack.
- **Single-threaded event loop.** Correctness and reproducibility over
  throughput. A €10k account rebalancing weekly has no throughput problem.
- **No ML.** Not on principle, but because ~40 bps of cost per round trip means
  the achievable signal-to-noise does not support a high-variance estimator on
  the data volume available. Revisit above ~€100k.

---

## Reading order

1. `docs/01-broker-selection.md` — broker choice and the cost analysis
2. `docs/02-architecture.md` — this document
3. `docs/03-risk-management.md` — risk layers in detail
4. `docs/05-research-protocol.md` — how strategies are validated and rejected
5. `docs/06-strategy-hypotheses.md` — candidate edges and their status
