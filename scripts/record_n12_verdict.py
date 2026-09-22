#!/usr/bin/env python
"""Record the N12 result against the criteria fixed in ledger entry 86.

Trial accounting, stated so it can be checked rather than trusted:

    gate A/B   default parameterisation                      1
    gate C     placebos, 3 volume + 3 direction              6
    gate D     momentum alone, and neutralised               2
    post 1     parameter surface                            48
    post 2     component decomposition                       3
    post 2b    attacks on the surviving component            5
    post 3     horizon sweep                                 4
    permutation  three statistics positioned in a null       3
                                                            --
                                                            72

The 150 permutation DRAWS are not counted as trials. They are null
calibration, not configurations searched for a winner; counting them would
inflate the deflation bar in the flattering direction by pretending the
search was wider than it was.

Run:  .venv/bin/python -m scripts.record_n12_verdict
"""

from __future__ import annotations

from pathlib import Path

from tradelab.research.ledger import ResearchLedger

AUTHOR = "claude-opus-5"


def main() -> None:
    ledger = ResearchLedger(Path("research/ledger.jsonl"), author=AUTHOR)
    before = ledger.trial_count()
    print(f"trial count before: {before}")

    ledger.record_trial(
        "N12-accumulation-footprint",
        summary="Gates A-D on the pre-registered parameterisation, in order, stopping at B.",
        n_configs=9,
        n_observations=167,
        metrics={
            "ic": 0.0029,
            "ic_t_stat": 0.23,
            "ic_n_eff": 118.3,
            "hit_rate": 0.49,
            "ic_autocorr": 0.171,
            "names_per_date": 98.0,
        },
        universe="top 100 US by trailing dollar volume, point-in-time",
        period="2012-09 to 2026-09",
        config={"params": "k1.25/w21/b120/l21", "pool": 100, "horizon": "1 month"},
    )

    ledger.record_trial(
        "N12-accumulation-footprint",
        summary=(
            "Post-mortem: 48-cell parameter surface, component decomposition, attacks on "
            "the one live component, horizon sweep, and three permutation tests."
        ),
        n_configs=63,
        n_observations=167,
        metrics={
            "surface_cells": 48.0,
            "surface_positive_share": 0.54,
            "surface_cells_t_over_2": 2.0,
            "surface_best_abs_t": 2.08,
            "persistence_only_t": 0.27,
            "direction_only_t": 2.74,
            "footprint_empirical_p": 1.0,
            "footprint_z_vs_null": -2.82,
            "direction_cross_sectional_z": 3.63,
            "direction_empirical_p": 0.02,
        },
        universe="top 100 US by trailing dollar volume, point-in-time",
        period="2012-09 to 2026-09",
    )

    ledger.record_verdict(
        "N12-accumulation-footprint",
        "rejected",
        reason=(
            "REJECTED at gate B, the first gate that could kill it, and the post-mortem "
            "makes the rejection stronger rather than ambiguous.\n"
            "GATE B: information coefficient +0.0029, t +0.23 on 118.3 effective "
            "observations, hit rate 49%. A coin flip. The pre-registered bar was |t| >= 2.\n"
            "PARAMETER SURFACE (criterion 3): 48 cells, 54% with positive IC, exactly 2 "
            "cells above |t| = 2 with a maximum of 2.08 -- against a null expectation, "
            "written down BEFORE the run, of ~2.2 cells and a maximum around 2.0-2.3. The "
            "surface is indistinguishable from chance, and both 'significant' cells carry "
            "the WRONG SIGN at a 10-day window, which is short-term reversal, not "
            "accumulation.\n"
            "THE DECISIVE RESULT is the permutation test. Against 50 volume shuffles the "
            "footprint's real IC of +0.0029 sits 2.82 standard deviations BELOW its own "
            "null mean of +0.0217, empirical p = 1.000. A RANDOMLY SHUFFLED VOLUME SERIES "
            "PRODUCES A BETTER SIGNAL THAN THE REAL ONE. The mechanism is clear in "
            "hindsight: shuffling makes the elevated-day set effectively random, so the "
            "placebo degenerates into a noisy mean close-location, and mean close-location "
            "carries more information than close-location conditioned on high volume. "
            "Conditioning on elevated volume does not merely fail to add; it DESTROYS "
            "information that was already there. That is the hypothesis refuted in its own "
            "terms.\n"
            "COMPONENT DECOMPOSITION confirms it: persistence alone -- the novel part -- "
            "scores t +0.27. The elevated-day volume share scores t -0.21.\n"
            "A BY-PRODUCT, recorded as an observation and explicitly NOT as a finding: the "
            "mean close-location value over 21 sessions scores IC +0.0309, and against a "
            "CROSS-SECTIONAL permutation null it sits 3.63 sd above a null mean of -0.0010 "
            "(empirical p = 0.020, which is the floor 50 draws can resolve). Four reasons "
            "it is not treated as a result: it is POST-HOC, emerging from the autopsy of a "
            "failed hypothesis after 72 configurations in this round and 322 in the "
            "project; it is NOT NOVEL, being the Accumulation/Distribution and Chaikin "
            "Money Flow family from 1980s technical analysis; an IC of 0.03 is weak; and it "
            "has faced none of the gates -- no out-of-sample split, no earnings control, no "
            "cost-aware backtest, no risk check. N7 momentum also had a genuinely positive "
            "mean and died on variance.\n"
            "A METHODOLOGICAL DEFECT FOUND IN MY OWN TEST DESIGN, which generalises beyond "
            "this hypothesis: a WITHIN-STOCK TIME SHUFFLE PRESERVES EACH STOCK'S LONG-RUN "
            "MEAN. For any signal that is a trailing average, that placebo destroys only "
            "the timing and leaves the level, so it tests far less than it appears to and "
            "will flatter the signal. Two of three such shuffles reproduced the "
            "close-location result at t +2.01 and +2.61 against a real t of +2.74. The "
            "correct null for a cross-sectional characteristic is a CROSS-SECTIONAL "
            "permutation -- keep each series intact, permute which stock's signal maps to "
            "which stock's forward return. Both nulls are now implemented and every future "
            "hypothesis is tested against the appropriate one.\n"
            "COST: no backtest was ever run, no portfolio was ever constructed, and the "
            "hypothesis died in one session. That is what pre-registering the test ORDER "
            "buys."
        ),
    )

    after = ledger.trial_count()
    print(f"trial count after:  {after}  (+{after - before})")
    print(f"chain verified: {ledger.verify()} entries")


if __name__ == "__main__":
    main()
