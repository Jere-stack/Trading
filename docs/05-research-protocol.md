# Research Protocol

## Purpose

This protocol exists to **reject strategies efficiently**. That is the correct
default posture, and the reason is arithmetic rather than pessimism.

Run this repository's validation module and it reports the following: the
expected maximum Sharpe ratio of **N completely worthless strategies** over
three years of daily data is

| Variants tested | Expected best annualised Sharpe (pure luck) |
|---|---|
| 10 | 0.91 |
| 50 | 1.32 |
| 200 | **1.60** |
| 1,000 | 1.88 |
| 10,000 | 2.23 |

If you test 200 parameter combinations and report the best, you should expect a
Sharpe near 1.6 **from nothing at all.** Every retail backtest showing "Sharpe
1.4 over 2015–2024" is consistent with a moderately diligent random search.

Verify with `.venv/bin/python -c` against
`tradelab.research.validation.expected_max_sharpe`, or read
`tests/test_validation.py::TestDeflatedSharpe`, which constructs the best of 200
random return series (observed Sharpe 1.39) and asserts that the protocol
rejects it.

**The rule that follows: a good backtest is not evidence. It is a hypothesis
that has not yet been rejected.**

---

## Stage 0 — Pre-registration (before any data)

Write the hypothesis down *first*, in `docs/06-strategy-hypotheses.md`, and
record it in the append-only ledger so the trial count starts accruing from the
same moment the research does:

```python
ResearchLedger("research/ledger.jsonl").record_hypothesis(
    "N2-dividend-cut-drift", statement=..., rationale=...
)
```

The ledger refuses a hypothesis with no stated mechanism, which is item 1
below. See [11 Research ledger](11-research-ledger.md).

1. **Economic mechanism.** Who is on the other side of this trade, and why do
   they accept a worse price? If there is no answer, there is no edge — only a
   pattern.
2. **Why it has not been arbitraged away.** Capacity limits? Career risk?
   Mandate constraints? Data cost? "Nobody has noticed" is not an answer for any
   liquid market.
3. **A prior on effect size, stated as a number, before testing.** A prior chosen
   after seeing results is not a prior.
4. **Parameters you will test, and the count.** This is the `n_trials` that the
   Deflated Sharpe Ratio needs. Understating it invalidates everything
   downstream — the honesty of this number *is* the protocol. It is no longer
   kept in anyone's head: `ledger.record_trial(..., n_configs=36)` on every run,
   including the failed ones, and the count is whatever the record sums to.
5. **Kill criteria, defined in advance.** What result makes you abandon this?
   Deciding afterwards is how a failed test becomes "needs more tuning".

## Stage 1 — Feasibility screening (arithmetic, no data)

Run `scripts/hypothesis_screen.py`. Two constraints reject most candidates
before any data is acquired:

**Cost.** Turnover × round-trip cost is a hard annual drag. At 30 bps round trip:

| Holding period | Annual cost drag | Verdict |
|---|---|---|
| 1 day | 75.6% | Impossible |
| 1 week | 15.1% | Almost certainly impossible |
| 1 month | 3.6% | Feasible |
| 1 quarter | 1.2% | Comfortable |

The screen requires the prior gross edge to be **at least 2× the round-trip
cost** — not 1.1×, because the cost model is itself uncertain and a live edge is
almost always smaller than a backtested one. A strategy that works only if
costs come in exactly as modelled does not work.

**Statistical power.** Events needed to detect an effect against per-event
noise, at 80% power:

| Effect | Noise | Events needed |
|---|---|---|
| 50 bps | 300 bps | 223 |
| 100 bps | 300 bps | 56 |
| 200 bps | 300 bps | 14 |

Over a 45-day horizon, per-event noise is ~1,000 bps against a plausible effect
of ~100 bps. This is why event studies need large samples, and why compelling
economic stories often cannot be *validated* even when they may be true.

**These two constraints are in tension, and that tension is the central bind of
this project.** Uncrowded effects are uncrowded largely because they are rare;
rare effects cannot be validated with retail-accessible data. A candidate must
thread both, and most do not.

## Stage 2 — Single in-sample fit (deliberately quick)

Fit on the earliest ~50% of data. **Spend as little time here as possible.**

The only purposes are to confirm the signal computes as intended and that the
sign is not backwards. A beautiful in-sample result means nothing; it is the
expected outcome of fitting. Resist the urge to improve it — every improvement
here is an extra trial that must be declared in Stage 4.

## Stage 3 — Walk-forward, out-of-sample

Use `purged_walk_forward_splits` with an **embargo of at least the longer of
(feature lookback, holding period)**.

The embargo is not ceremony. A 20-day-lookback feature at a split boundary
contains information from both sides, so without a gap up to 20 days of test
data leaks into training. Run **both** anchored (expanding) and rolling
(fixed-window) variants: a strategy that only works anchored is usually relying
on one historical episode.

Requirements to pass:
- Positive net-of-cost return in **at least 60% of out-of-sample windows**.
- No single window contributing more than ~40% of total profit. One good episode
  is not an edge.
- Out-of-sample Sharpe at least 50% of in-sample Sharpe. Larger decay means the
  in-sample fit captured noise.

## Stage 4 — Multiple-testing correction

Apply `deflated_sharpe_ratio` with the **honest** trial count: every parameter
combination, every universe variant, every signal definition you tried,
including the ones you discarded.

Call it through `deflate_from_ledger`, so `n_trials` is read from the
append-only record rather than chosen at the point of reporting:

```python
result = deflate_from_ledger(returns, ledger, "N2-dividend-cut-drift")
```

Use `project_wide=True` when the claim is "this is the best thing we found",
because that is the selection that actually occurred.

Requirement: **DSR > 0.95.**

A worked example from the test suite: the best of 200 random return series shows
an annualised Sharpe of 1.39 and a DSR of **0.36** — correctly rejected. The
same series reported as a single trial gives DSR **0.99** — accepted. The
difference is entirely the declared trial count. This is the mechanism by which
selection bias hides, and it is defeated only by honesty.

## Stage 5 — Overfitting probability

Apply `probability_of_backtest_overfitting` (CSCV) across **all** configurations
tried, including losers — PBO measures whether the *selection procedure*
generalises, so omitting failures makes it meaningless.

Requirement: **PBO < 0.30.** Above 0.50, the in-sample winner underperforms out
of sample and the selection process is producing no information.

## Stage 6 — Parameter stability

Apply `parameter_stability`. A real effect has a **plateau**; a curve fit has a
**spike**.

Requirement: neighbouring parameter values retain >50% of the optimum's
performance. If performance collapses one step away, the parameter was selected
to match noise, because no economic mechanism changes discontinuously at an
arbitrary threshold.

## Stage 7 — Regime and robustness testing

The strategy must survive, without re-fitting:

- **Distinct regimes:** 2008–09 (crisis), 2010–15 (QE/low-vol), 2020 (COVID
  crash and recovery), 2022 (inflation/rate shock).
- **Cost stress:** 1.5× and 2× the modelled cost. If the edge dies at 1.5×, it
  will die live, because live costs exceed modelled costs.
- **Universe perturbation:** drop a random 20% of the universe and re-run. A
  strategy dependent on specific names is fitted to those names.
- **Timing perturbation:** shift all entries by ±1 day. A real effect is not
  destroyed by one day of slippage; an artefact often is.
- **Survivorship check:** the universe must include delisted and acquired names.
  Backtests on current index members overstate returns substantially.

## Stage 8 — Attribution and null tests

- `permutation_test` — shuffle the position series while keeping asset returns
  fixed. This destroys timing while preserving exposure. If shuffled timing
  performs as well, the result came from market exposure, not signal — a
  distinction a Sharpe ratio cannot make.
- `block_bootstrap_sharpe` — block resampling, not iid, because volatility
  clustering means iid bootstrap understates interval width and flatters the
  strategy. Requirement: 5th percentile Sharpe > 0.
- **Cost attribution:** report gross return, commission, spread and impact
  separately. If the strategy is only profitable gross, say so plainly.

## Stage 9 — Capacity

Confirm the strategy works at €10k **and** would still work at €50k. A
strategy whose edge vanishes at 5× size is not scalable, which matters if the
account grows — and often signals dependence on illiquid names where the cost
model is least reliable.

## Stage 10 — Paper trading

**Minimum 1 month simulated + 1 month IBKR paper**, per
`docs/02-architecture.md`.

**What paper trading can and cannot establish.** Run
`minimum_track_record_length`:

| True annualised Sharpe | Days to distinguish from zero (95%) |
|---|---|
| 0.5 | ~2,730 (10.8 years) |
| 1.0 | ~683 (2.7 years) |
| 1.5 | ~303 (1.2 years) |
| 2.0 | ~172 (0.7 years) |

**One to two months of paper trading cannot validate an edge.** Any realistic
strategy needs years. What the paper period *can* do, and is genuinely necessary
for:

- Catch implementation bugs (the most common cause of live losses).
- **Calibrate the cost model against real IBKR statements.** This is the single
  most valuable output of the paper period.
- Verify operational stability (reconnects, daily re-auth, reconciliation).
- Confirm modelled fills resemble real fills.

Do not interpret a profitable paper month as confirmation. Do interpret an
unprofitable one, or a cost model that proves optimistic, as a reason to stop.

---

## Summary of hard gates

| Stage | Requirement |
|---|---|
| 1 | Prior gross edge ≥ 2× round-trip cost; required sample ≤ 10 years |
| 3 | >60% of OOS windows profitable net; OOS Sharpe ≥ 50% of IS |
| 4 | Deflated Sharpe Ratio > 0.95 at honest trial count |
| 5 | PBO < 0.30 |
| 6 | Neighbouring parameters retain >50% of performance |
| 7 | Survives 2× cost stress and all regimes without re-fitting |
| 8 | Bootstrap 5th-percentile Sharpe > 0; permutation p < 0.05 |
| 9 | Works at €10k and €50k |
| 10 | Clean paper month; cost model calibrated to statements |

**Failing any gate means rejection, not re-tuning.** Re-tuning after a failure
adds a trial, which raises the bar at Stage 4 — and the honest bookkeeping of
that is what separates research from rationalisation.

## The expected outcome

**Most hypotheses will be rejected, and zero surviving strategies is an
acceptable result.** It is strictly better than deploying an overfitted one: a
rejected strategy costs research time, while a deployed overfitted strategy
costs capital and the months spent discovering it does not work.

If nothing survives, the rational alternatives are a low-cost index holding, or
waiting until the account is large enough that the cost constraint relaxes
(~€100k, where per-order minimums stop binding). Neither is a failure of the
research; both are what the evidence supports.
