# The research ledger

Every statistical test in `research/validation.py` needs one input that no
backtest can supply: **how many configurations were evaluated before this one
was reported as the winner.**

That number decides whether a result means anything. Expected maximum
*annualised* Sharpe of **worthless** strategies over three years of daily data
(756 observations, Sharpe variance under the null):

| Trials | Expected max Sharpe of pure noise |
|---|---|
| 1 | 0.00 |
| 10 | 0.91 |
| 50 | 1.32 |
| 200 | 1.60 |
| 1,000 | 1.88 |

Reproduce with `expected_max_sharpe(n, 1/755) * sqrt(252)`.

This table is the single most important thing in the project. A Sharpe of 1.3
is a respectable result from one pre-registered test, and is *precisely* what
fifty worthless strategies produce by chance. The difference lives entirely in
a count held nowhere except the researcher's memory — which, across many
sessions, several models and months of elapsed time, is not a place at all.

`research/ledger.jsonl` is that place.

```bash
.venv/bin/python -m tradelab.cli ledger
.venv/bin/python -m tradelab.cli ledger --hypothesis N1-cash-merger-arbitrage
```

## Append-only, with a hash chain

A trial count that can be revised downward is not a trial count.

The failure mode is not fraud. It is the entirely reasonable-sounding thought
*"that run had a bug, so it shouldn't count."* Sometimes true. But applied
freely it deletes exactly the failed trials whose presence makes the count
honest, and it always deletes them in the direction that flatters the result.

So each entry carries the hash of the one before it. Editing an old entry
breaks the chain from that point forward, and the CLI names the first entry
that fails:

```
LEDGER INTEGRITY FAILURE
  entry 34 hashes to e8790f4c…, not the recorded 62d09ef3…; its own content was edited
```

Appending to a broken chain is also refused — otherwise an edit could be
laundered by burying it under valid entries.

Superseding a trial is allowed and requires a written reason, but the
superseded trial **still counts by default**. The work was done; the result
could still have influenced a decision.

## What counts as a trial

> Any evaluation of a configuration against data, where the result could have
> changed a decision.

That includes every point of a parameter grid — not just the point reported —
every re-run on a different period or universe, and every variant tried and
abandoned.

The one exclusion is a run that produced no interpretable result at all: a
crash, an empty sample. Those are recorded with `n_observations=0` and excluded
automatically, so the exclusion is **visible in the record** rather than
assumed.

A grid search records as one entry carrying `n_configs`, which the count sums.
Writing 200 rows for a 200-point grid is correct and nobody does it; this makes
the honest thing the easy thing.

## Why JSONL and not SQLite

The opposite choice from `portfolio/state.py`, deliberately.

| | Paper state | Research ledger |
|---|---|---|
| Written | constantly | rarely |
| Read by | machines | humans |
| Value depends on | durability under crash | being auditable |
| Format | SQLite (WAL, `synchronous=FULL`) | JSONL |

A text file diffs in review. A binary database hides an edit inside an opaque
blob. The ledger will hold a few hundred lines over several years.

## Using it

```python
from tradelab.research.ledger import ResearchLedger, deflate_from_ledger

ledger = ResearchLedger("research/ledger.jsonl", author="claude-opus-5")

# Before testing: register the mechanism. A rationale is mandatory --
# a hypothesis with no stated mechanism is data mining with extra steps.
ledger.record_hypothesis(
    "N2-dividend-cut-drift",
    statement="Short-horizon drift after an announced dividend cut",
    rationale="Income mandates force selling regardless of price; the seller "
              "is not trading on value and cannot wait",
    data_required="dividend declarations with declarationDate",
)

# During: record every evaluation, including the ones that failed.
ledger.record_trial(
    "N2-dividend-cut-drift",
    summary="Holding-period grid, 5-60 days, across 715 cut events",
    n_configs=36,
    n_observations=715,
    metrics={"mean_return": -0.004, "sharpe": 0.12},
    universe="US survivorship-free",
    period="2010-2025",
)

# After: the trial count is read from the record, not typed in.
result = deflate_from_ledger(returns, ledger, "N2-dividend-cut-drift")
```

`deflate_from_ledger` exists so that `n_trials` stops being an argument someone
chooses. It is whatever the record says.

`project_wide=True` deflates against **every** configuration ever evaluated,
which is the right bar for "this is the best thing we found". Deflating a
single pre-registered hypothesis against its own trials is right only when it
was genuinely tested in isolation — and once several candidates have been run,
it rarely is.

## Current state of the record

Seeded from work done before the ledger existed (`scripts/seed_ledger.py`),
reconstructed conservatively: where the written record is ambiguous, the higher
count is taken, because understating is the failure mode that matters.

| Hypothesis | Trials | Verdict |
|---|---|---|
| H1 Overnight risk premium | 1 | rejected — 3 bps against ~25 bps cost |
| H2 Short-term reversal | 1 | rejected — 10 bps net, most crowded retail effect |
| H3 Turn-of-month flow | 1 | rejected — 25 bps gross against 25 bps cost |
| H4 Post-earnings drift | 1 | **pending** — needs the €60 Fundamentals tier |
| H5 Index deletion | 1 | **pending** — needs point-in-time index membership |
| H6 Index addition | 1 | rejected — decayed to near zero since 2000 |
| H7 Spin-off selling | 1 | **pending** — needs corporate action data |
| H8 Tax-loss reversal | 1 | rejected — n=20 over 20 years; no power |
| H9 Fund fire-sales | 1 | **pending** — needs fund holdings data |
| H10 Cross-sectional momentum | 1 | rejected — 45 bps on the most crowded factor |
| N1 Cash merger arbitrage | 25 | **rejected** — −2.45% EV per resolved event |
| M1 Survivorship bias | 2 | shelved — measured: US +2.78%/yr, HEL +1.05%/yr |

**37 configurations evaluated.** Any result reported from here on deflates
against that number and rising.

N1 is the one that matters methodologically. Its headline was positive at every
threshold tested (+1.43% to +6.59%) while the honest resolved-events subset was
negative at every one (−1.39% to −6.34%). Parameter robustness gave false
reassurance; only splitting resolved from unresolved events exposed it. Those
25 trials stay in the record permanently, because they deflate everything
tested after them.

## What it does not do

- **It cannot detect a trial you never recorded.** The chain proves nothing was
  removed; it cannot prove everything was added. That part is discipline, and
  the reason `record_trial` is called from the analysis script rather than
  written up afterwards.
- **It does not make the count the right one.** Whether to deflate against one
  hypothesis or the project total is a judgement about what selection actually
  occurred. The ledger makes both numbers available and neither automatic.
