#!/usr/bin/env python
"""Record amendment 4 of round 10 -- the name tie-break -- BEFORE the final N13 run.

Found in the clean-universe audit output, which reports identification only.
Recorded separately from amendments 1-3 because it was found after they were.

Run:  .venv/bin/python -m scripts.record_round10_amend4
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
        "Round-10 amendment 4: name ties broken by legal name then dates, audited before results",
        detail={
            "found_by": (
                "the final clean-universe audit sample (identification output, no strategy result): "
                "Kraft Foods Group was matched to Mondelez, formerly 'Kraft Foods Inc'"
            ),
            "scope": (
                "31 of 238 name matches had several filers at the top normalised score; CIK order "
                "picked wrongly in about 10 (Allergan plc, Raytheon, Viacom, Discovery, Express "
                "Scripts Holding, Dell Technologies, DirecTV, Zayo, Alpha Natural Resources, Kraft)"
            ),
            "rule": (
                "among tied filers: (1) highest similarity of the full legal name with corporate "
                "words kept and abbreviations expanded, word order ignored; (2) then the overlap "
                "between the dates the symbol has prices and the dates the filer filed; (3) then CIK "
                "order. No price level or return is used"
            ),
            "audit": (
                "all 31 decisions printed and checked by eye; BBBY and BBBY_OLD both hold Overstock's "
                "prices (2020 close equals OSTK's) and correctly map to Overstock's successor filer"
            ),
            "gate_0_after_amendments": "survivors 99.6%, delisted 90.6%, gap +9.0 points vs limit 6: FAILS",
            "no_results_seen": True,
        },
    )
    print(f"recorded; chain verified: {ledger.verify()} entries, trials {ledger.trial_count()}")


if __name__ == "__main__":
    main()
