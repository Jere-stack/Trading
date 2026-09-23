#!/usr/bin/env python
"""Record N13 run 1 -- SEEN, and defective -- before the corrected run.

Run 1 finished and its output was read. It stopped at gate 1. Its diagnostics
then showed seven broken month-ends: removing the foreign listings left 105
panel rows that are not US trading sessions (102 US holidays with no price at
all, three with a single stray price). Where the 12-1 momentum lookback landed
on one, every stock's momentum was missing; where a month-end landed on one
(Memorial Day 2021), the universe was empty. Books then held cash for a month.

The fix has no free parameter: keep only the dates the S&P 500 ETF traded.
Because run 1 was seen, it is recorded as a trial -- its 45 configurations
count toward every later deflation bar -- and the corrected run is the verdict
run. Both results are reported.

Run:  .venv/bin/python -m scripts.record_round10_run1
"""

from __future__ import annotations

from pathlib import Path

from tradelab.research.ledger import ResearchLedger

AUTHOR = "claude-opus-5-5"
HYP = "N13-quality-profitability-momentum"


def main() -> None:
    ledger = ResearchLedger(Path("research/ledger.jsonl"), author=AUTHOR)
    print(f"trial count before: {ledger.trial_count()}")
    ledger.record_trial(
        HYP,
        summary="Run 1 on the SEC-cleaned universe: killed at gate 1; later found defective (holiday rows)",
        n_configs=45,
        n_observations=53,
        metrics={
            "ic": 0.0390,
            "ic_t_stat": 1.47,
            "ic_n_eff": 39.0,
            "hit_rate": 0.62,
            "cagr": 0.1428,
            "sharpe": 0.75,
            "spy_cagr": 0.1462,
            "spy_sharpe": 0.91,
            "baseline_cagr_h1": 0.1492,
            "baseline_cagr_h2": 0.2291,
            "surface_share_beating_baseline": 0.0,
            "eligible_mean": 54.0,
            "months_with_no_eligible_name": 7.0,
        },
        universe="SEC-identified top 100 US-listed by trailing dollar volume, monthly",
        period="2012-09 to 2026-09",
        config={"hold": 20, "band": 10, "rebalance": "monthly", "cost_bps_round_trip": 33.1},
    )
    ledger.append(
        "note",
        HYP,
        "Run 1 defect: 105 non-session rows broke 7 month-ends; fix fixed before run 2, no free parameter",
        detail={
            "defect": (
                "the clean panel kept rows for dates on which only the removed foreign listings traded: "
                "102 US holidays with no price, 3 with one stray price. 6 month-ends lost momentum for "
                "every stock (lookback row empty) and 2021-05-31 had an empty universe; ranked books "
                "held cash for those months, and gate 1 lost 3 of 56 quarterly observations"
            ),
            "fix": "keep only dates on which SPY traded (US sessions); applied in the clean-universe build",
            "run_1_seen": True,
            "run_1_verdict": "killed at gate 1 (IC +0.039, t +1.47); gates 5-9 also failed",
            "commitment": (
                "run 2, after the fix, is the verdict run whatever it shows; run 1 is reported beside it "
                "and its 45 configurations count toward the deflation bar"
            ),
        },
    )
    print(f"recorded; chain verified: {ledger.verify()} entries, trials {ledger.trial_count()}")


if __name__ == "__main__":
    main()
