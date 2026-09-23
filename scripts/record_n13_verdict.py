#!/usr/bin/env python
"""Record the N13 verdict run (round 10, run 2) and close the hypothesis.

Trial accounting: run 2 evaluates the same 45 configurations as run 1
(12-cell surface x 3 windows, 5 books, 4 IC gates). Run 1's 45 are already
in the ledger; both count, because both were seen.

Run:  .venv/bin/python -m scripts.record_n13_verdict
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
        summary="Run 2 (verdict run) on the SEC-cleaned universe, US sessions only: killed at gate 1",
        n_configs=45,
        n_observations=56,
        metrics={
            "ic": 0.0358,
            "ic_t_stat": 1.38,
            "ic_n_eff": 42.2,
            "hit_rate": 0.62,
            "cagr": 0.1667,
            "sharpe": 0.83,
            "maxdd": -0.379,
            "ir_vs_spy": 0.27,
            "spy_cagr": 0.1462,
            "spy_sharpe": 0.90,
            "qqq_cagr": 0.1936,
            "baseline_cagr": 0.2257,
            "baseline_cagr_h1": 0.1758,
            "baseline_cagr_h2": 0.2725,
            "top20_largest_cagr": 0.2208,
            "top20_largest_sharpe": 0.94,
            "top20_largest_maxdd": -0.462,
            "surface_share_beating_baseline": 0.0,
            "deflation_bar": 0.83,
            "eligible_mean": 56.0,
            "gate0_gap_points": 9.0,
        },
        universe="SEC-identified top 100 US-listed by trailing dollar volume, monthly",
        period="2012-09 to 2026-09",
        config={"hold": 20, "band": 10, "rebalance": "monthly", "cost_bps_round_trip": 33.1},
    )
    ledger.record_verdict(
        HYP,
        "rejected",
        reason=(
            "Killed at gate 1 on the verdict run: composite IC +0.036, t +1.38 on 42 effective "
            "quarters (run 1, defective, gave t +1.47). Every later gate but the raw-Sharpe reading "
            "of gate 9 also fails: 0 of 12 parameter cells beat the 20 largest eligible stocks in "
            "either half, and N13's Sharpe (0.83) is below SPY's (0.90). The data carry a residual "
            "survivorship gap of 9.0 points against a pre-registered limit of 6; with 0 of 12 cells "
            "beating a naive size book, no plausible correction of that gap reverses the verdict."
        ),
    )
    print(f"recorded; chain verified: {ledger.verify()} entries, trials {ledger.trial_count()}")


if __name__ == "__main__":
    main()
