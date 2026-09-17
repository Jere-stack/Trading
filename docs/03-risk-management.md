# Risk Management

Implemented in `src/tradelab/risk/`, tested in `tests/test_risk_engine.py`.
Every order in the system — backtest, paper and live — passes through
`RiskEngine.evaluate`. There is no route from a strategy to a broker that
bypasses it, and that is enforced by the import graph rather than by discipline:
`strategy/` cannot import `execution/`.

## Two properties that matter more than any individual limit

**1. Fail-closed.** A check that raises an exception is a REJECT, never a skip.

```python
except Exception as exc:
    verdict = RiskVerdict.reject(check.name, f"check raised {type(exc).__name__}: {exc}. "
                                 "Failing closed: an unevaluable limit is treated as a "
                                 "breached limit.")
```

The common triggers are a missing FX rate or absent market data. The safe answer
to "I don't know" is "no". Tested by
`TestFailClosed::test_raising_check_rejects`.

**2. Resize is a floor, not a negotiation.** When several checks want to shrink
an order, the smallest wins — and the **cost gate then re-runs on the resized
order**. This second part is easy to miss and important: a sizing limit can
shrink an order until commission dominates, and approving that stub defeats the
purpose of having a cost gate at all. Tested by
`TestCostEfficiency::test_resize_that_becomes_uneconomic_is_rejected`.

**Risk-reducing orders are exempted from sizing limits.** Without this, a book
that has breached a limit becomes un-exitable — the gate would block the very
orders that restore compliance. A control that prevents you selling is a
liability.

## The thirteen checks

Ordered cheapest-first, so the common rejection path is fast.

| Check | Prevents |
|---|---|
| `HaltCheck` | Trading while a kill switch is engaged |
| `MandateCheck` | Non-equity instruments; unintended short positions |
| `SymbolPermissionCheck` | Trading outside an approved universe |
| `MarketHoursCheck` | Executing at outside-RTH spreads the cost model never validated |
| `OrderRateCheck` | A runaway loop firing hundreds of orders |
| `DuplicateOrderCheck` | Resubmission after a restart or a repeated signal |
| `PriceSanityCheck` | Fat-finger limits, stale feeds, sub-€3 stocks |
| `LossLimitCheck` | A bad day compounding; unreviewed resumption after a drawdown |
| `PositionSizeCheck` | One name dominating a small account |
| `CapacityCheck` | A position that cannot be exited in one day |
| `PortfolioExposureCheck` | Leverage, over-diversification, unhedged FX concentration |
| `StrategyBudgetCheck` | One strategy consuming another's allocated capacity |
| `CostEfficiencyCheck` | Orders whose modelled cost exceeds the plausible edge |

### CostEfficiencyCheck — the distinctive one

Retail systems almost never have this, and at €10k it is the most valuable
control in the list. Two limits:

- `min_order_notional` (default €400). Below this, the €1.25 commission floor
  alone exceeds any plausible per-trade edge.
- `max_cost_bps_of_notional` (default 35 bps). Rejects an order whose modelled
  one-way cost — commission plus spread plus impact — exceeds this share of
  notional.

This is what makes the system refuse to trade Helsinki micro-caps (~161 bps one
way) without needing a hand-maintained blacklist. The economics do the
screening.

Applied only to risk-*increasing* orders. An exit must always be permitted,
however expensive: refusing to sell is not a cost control.

### CapacityCheck — bounding the exit, not the entry

`max_participation_of_adv` (default 1%) bounds market impact, but its more
important function is bounding **liquidation**. A position you cannot exit in a
day is not a position; it is a hostage. An instrument with no ADV data is
rejected outright for new risk — sizing blind in unknown liquidity is how
small accounts get trapped.

## Kill switch: two severities

| | SOFT | HARD |
|---|---|---|
| Triggers | Daily loss limit, rate limit, consecutive losing days | Max drawdown, reconciliation break, zero/negative equity |
| Blocks new risk | Yes | Yes |
| Blocks exits | **No** | Yes |
| Clears | Automatically at next session | **Manual re-arm with a named operator** |

Two design points:

**A SOFT halt must not block exits.** A halt that traps you in a losing position
is a liability. Tested by `TestLossLimits::test_soft_halt_still_allows_exit`.

**A HARD halt requires a human.** `KillSwitch.reset()` demands a non-empty
operator identity for the audit trail. An automated system that resumes on its
own after a 15% drawdown does not have a kill switch; it has a pause button. The
entire value of the control is that a person looks at what happened before
capital is risked again. A SOFT trip can never downgrade an active HARD halt.

## Why concentration rather than diversification

The standard advice is to diversify. At this account size that advice is wrong,
and the reason is arithmetic:

| Book | Position size | Round-trip commission |
|---|---|---|
| 30 positions | €330 | ~45 bps |
| 12 positions | €830 | ~30 bps |
| 10 positions | €1,000 | ~25 bps |

**Wide diversification is unaffordable.** Defaults therefore allow 12 positions
at up to 15% each, and risk control shifts from diversification onto:

- Per-position weight caps
- The portfolio drawdown kill switch
- Currency exposure limits (default 70% non-base)
- Cost-efficiency screening

This is a genuine constraint of small-account trading, not a stylistic
preference. It also means **idiosyncratic risk is high and unavoidable** — a
single-name blow-up in a 15% position costs 15% of the account. Size limits and
the drawdown halt are the only defences, which is why their defaults are
conservative.

## Multi-strategy risk budgets

`strategy_budgets` allocates gross exposure as a fraction of equity.
Over-allocation is rejected **at config load**:

```
strategy budgets sum to 1.2 but max_gross_exposure is 1.0. Over-allocating
guarantees that whichever strategy trades first consumes the shared capacity,
making realised allocation depend on arrival order rather than on intent.
```

Catching this at load rather than at runtime matters because the runtime failure
is silent: everything appears to work, and the allocation is simply not what was
intended.

## Configuration validation

`RiskLimits` is a pydantic model with cross-field checks, because limits arrive
as human-written YAML. Caught at load time:

- `max_position_weight: 50` meaning 50% but parsed as 5000% (bounds check)
- `max_position_weight > max_gross_exposure` (unreachable limit)
- `max_daily_loss_pct >= max_drawdown_pct` (the soft control could never bind
  before the hard one, making it useless)
- A symbol both whitelisted and blacklisted
- Any misspelled key (`extra="forbid"`)

In LIVE mode specifically, `Settings` additionally rejects zero-commission
modelling, the lookahead diagnostic flag, the paper port, and any gross
exposure above 1.0 — configurations whose cost of error is asymmetric when real
orders are being sent.

## Operational risk: startup sequence

Order matters; each step depends on the previous.

1. Connect to IB Gateway. Fail loudly if unavailable.
2. **Reconcile** local positions against broker positions. Any break → HARD
   halt. The broker is authoritative. A restart that skips reconciliation will
   open a second position in a name it already holds.
3. Restore `RiskState` from SQLite. Otherwise the daily loss limit silently
   resets to zero used, and a system that already lost 3% starts the day with a
   fresh 3% of rope.
4. Verify market data is live and not stale.
5. Begin the event loop.

## What this does not protect against

Stated so the gaps are known:

- **A broken signal that trades within all limits.** Risk limits bound loss
  *rate*, not correctness. A strategy can lose 3% a day, every day, inside
  every limit — which is what the consecutive-losing-days SOFT halt is for, and
  it is a blunt instrument.
- **Correlated positions.** 12 positions in one sector is a single bet. The
  `max_sector_weight` limit exists in the config schema but sector data is not
  yet wired into the check; **this is a known gap.** Until it is closed, sector
  concentration must be managed by universe construction.
- **Gap risk.** A stop cannot execute inside a gap. A 15% position that gaps
  down 40% overnight costs 6% of the account, and no pre-trade control prevents
  it. Position sizing is the only defence.
- **Broker or custodian failure.** Mitigated by choosing a regulated entity with
  investor compensation coverage, not by code.
