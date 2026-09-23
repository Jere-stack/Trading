#!/usr/bin/env python
"""Record the round-10 cleaning refinements BEFORE the final N13 run.

The smoke run on a partial registry (its N13 output deliberately left unread)
exposed three defects in the cleaning rules. They are fixed and recorded here
while no result has been seen, so the record shows they were not tuned to one.

Run:  .venv/bin/python -m scripts.record_round10_amend
"""

from __future__ import annotations

from pathlib import Path

from tradelab.research.ledger import ResearchLedger

AUTHOR = "claude-opus-5-5"


def main() -> None:
    ledger = ResearchLedger(Path("research/ledger.jsonl"), author=AUTHOR)
    ledger.append(
        "note",
        "N13-quality-profitability-momentum",
        "Round-10 cleaning rules amended after a smoke run whose results were not read",
        detail={
            "smoke_run": (
                "run on a partial registry to exercise the pipeline; stdout written to a file that "
                "was not opened; only exit status and stderr were inspected"
            ),
            "amendment_1_window_rule": (
                "rule 3 (blank prices outside the SEC filing window) now applies to NAME-identified "
                "symbols only. Holding-company reorganisations and redomiciles issue a new CIK to a "
                "stock that never stopped trading (Disney 2019, Cigna 2018, BlackRock 2024, Marvell "
                "2021, APA 2021); the rule was blanking years of their genuine prices"
            ),
            "amendment_2_predecessors": (
                "a young CIK is linked to the CIK it replaced when both share a name spelling, the "
                "predecessor filed first, and it stopped filing within 2 years before to 1 year after "
                "the successor's first filing; the closest handover wins. The histories are pooled "
                "so the stock keeps its fundamentals across the reorganisation"
            ),
            "amendment_3_fiscal_year_ends": (
                "the registry now reads all four quarter-end frames, not only December and June, so "
                "foreign filers with March or September year-ends are in the security master"
            ),
            "bug_fixes": (
                "a filer with only quarterly reports crashed the ratio builder (now an empty history); "
                "the output directory was created after its first write"
            ),
            "unchanged": "universe, composite, baseline, gates, costs, verdict rule, kill criteria",
            "no_results_seen": True,
        },
    )
    print(f"recorded; chain verified: {ledger.verify()} entries, trials {ledger.trial_count()}")


if __name__ == "__main__":
    main()
