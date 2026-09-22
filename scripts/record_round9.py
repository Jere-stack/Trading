#!/usr/bin/env python
"""Record round 9: N13 stopped at gate 0, the universe contamination it exposed,
and the consequences for M3 and N12.

No trial is recorded for N13. No return was computed, no configuration was
evaluated, and the deflation count must not grow for a test that never ran --
nor shrink by pretending it did.

Run:  .venv/bin/python -m scripts.record_round9
"""

from __future__ import annotations

from pathlib import Path

from tradelab.research.ledger import ResearchLedger

AUTHOR = "claude-opus-5-5"


def main() -> None:
    ledger = ResearchLedger(Path("research/ledger.jsonl"), author=AUTHOR)
    before = ledger.trial_count()
    print(f"trial count before: {before}")

    # ---------------------------------------------------------- N13 stopped
    ledger.append(
        "note",
        "N13-quality-profitability-momentum",
        "Gate 0 failed first pass (+29.0 pts); rule-based fixes fixed in advance; one re-run allowed",
        detail={
            "first_pass": "survivors mapped 92.9% (260/280), delisted 63.9% (76/119), gap +29.0 pts",
            "fixes_decided_before_rerun": (
                "(a) name normalisation: delete apostrophes incl. curly, word-order and "
                "space-insensitive similarity, all historical SEC names per CIK; (b) liberal "
                "candidate generation with UNCHANGED acceptance thresholds; (c) remove a symbol "
                "as a vendor mislabel only if it is unmatched AND its base ticker exceeds 5 "
                "characters or its name carries a fixed non-US legal-form token (PJSC, OJSC, "
                "PAO, OAO, KSC, KSCC, KPSC, SAK, Public Co, SA Esp). A symbol that merely fails "
                "to match is NOT removed and still counts against the gate."
            ),
            "commitment": "one re-run at the same 6-point threshold; a second failure stops N13",
            "no_returns_examined": True,
        },
    )
    ledger.record_verdict(
        "N13-quality-profitability-momentum",
        "shelved",
        reason=(
            "STOPPED AT GATE 0, as pre-committed. Not rejected: the hypothesis was never tested, "
            "and no return was computed.\n"
            "FINAL PASS: survivors mapped 96.8% (271/280), delisted 71.2% (79/111), gap +25.6 "
            "points against a 6-point threshold. Eight vendor mislabels removed by the fixed rule "
            "(Boubyan Bank, Celsia, Federal Grid, HumanSoft, Mabanee, National Industries, "
            "Sberbank, TMB Bank).\n"
            "WHY IT FAILED IS THE FINDING. Of the 32 delisted symbols still unmatched, only ~7 are "
            "genuine US firms the matcher missed through renames (DowDuPont, Green Mountain "
            "Coffee, Michael Kors -> Capri, Overstock -> Beyond, Altaba, InfraREIT, Virgin "
            "Media). Matching all seven perfectly would still leave a ~19-point gap. The rest "
            "are not identifiable as US companies: ~13 foreign listings and IFRS filers (ASM "
            "International, BDO Unibank, Boozt, Goldcorp, JDE Peet's, NLMK, Novatek, Polyus, "
            "UniCredit, VimpelCom, Seadrill, Equal Energy, Potash); 5 codes with no name at all "
            "(AIP_OLD, ASPI_OLD, GC, SK, SQ); and codes whose names belong to companies that "
            "ceased to exist before this panel starts -- CompuCom (private 2004), Vintage "
            "Petroleum (acquired 2006), Longs Drug Stores (acquired 2008), Hampton Industries, "
            "Knowlton Development, Highland Distressed Opportunities -- yet which sat in the "
            "2012-2026 top 100 by dollar volume. Those codes carry stale names on some other "
            "security's prices.\n"
            "A survivorship-clean test needs to know what the delisted securities ARE. With this "
            "vendor's metadata, a quarter of them cannot be identified, so no matcher can pass "
            "this gate and the stop is correct on the merits, not merely on procedure.\n"
            "The case for the underlying idea is unaffected: M4, on professionally built "
            "CRSP/Compustat-based data, still shows Momentum, Quality and Profitability with a "
            "modest post-publication long-leg premium. What is blocked is THIS project's ability "
            "to replicate it on its own universe."
        ),
    )

    # ---------------------------------------------------------- M5 contamination
    ledger.record_hypothesis(
        "M5-universe-contamination",
        statement=(
            "How much of the point-in-time top-100 and top-20 by dollar volume consists of "
            "securities that cannot be identified as SEC-registered US companies?"
        ),
        rationale=(
            "Not a strategy. The N13 gate-0 audit showed the vendor labels Moscow, Kuwaiti, Thai "
            "and Colombian securities 'NYSE / USD / Common Stock', and attaches names of long-"
            "defunct firms to codes that trade in 2012-2026. Local-currency prices inflate dollar "
            "volume, so such securities concentrate exactly at the top of a dollar-volume ranking "
            "-- the universe M3 and N12 used."
        ),
        data_required="the N12 panel and the SEC CIK map from N13 step 1",
    )
    ledger.record_trial(
        "M5-universe-contamination",
        summary="Count unmatched securities in the monthly top 100 and top 20 by trailing dollar volume",
        n_configs=1,
        n_observations=169,
        metrics={
            "top100_unmatched_mean": 11.8,
            "top100_unmatched_max": 23.0,
            "top20_unmatched_mean": 4.62,
            "top20_months_with_any": 168.0,
            "months": 169.0,
            "sber_months_in_top20": 103.0,
            "hai_months_in_top20": 72.0,
            "ldg_months_in_top20": 67.0,
        },
        universe="N12 panel: symbols ever in top 200 by trailing dollar volume",
        period="2012-09 to 2026-09",
    )
    ledger.record_verdict(
        "M5-universe-contamination",
        "shelved",
        reason=(
            "MEASURED, and it compromises earlier work. The top 100 carried 11.8 unidentifiable "
            "securities per month on average (max 23). The top 20 carried 4.62, in 168 of 169 "
            "months. Sberbank -- a Moscow listing priced in roubles -- sat in the 'US top 20' for "
            "103 months; 'Hampton Industries' for 72, 'Longs Drug Stores' for 67, 'Vintage "
            "Petroleum' for 44. Some unmatched names are legitimate US-listed foreign issuers "
            "(Alibaba, TSMC, ASML) and would belong in a US universe; the rest do not.\n"
            "Round 2 fixed a version of this (TMB Bank at $24,100 outranking Microsoft) with a "
            "metadata filter. The filter trusted the same vendor labels that are wrong, so the "
            "defect survived it. The lesson is general: a universe filter built on the vendor's "
            "own metadata cannot catch the vendor's metadata errors. An EXTERNAL registry -- here "
            "the SEC -- is what exposed it."
        ),
    )

    # ------------------------------------------------------ consequences
    ledger.record_verdict(
        "M3-concentration-not-weighting",
        "shelved",
        reason=(
            "NUMBERS WITHDRAWN. M5 shows the top-20 and top-30 dollar-volume books behind M3 held "
            "~4.6 unidentifiable securities a month -- about a quarter of a 20-name book -- "
            "including local-currency foreign listings and codes carrying the names of defunct "
            "firms. The specific figures (top 20 at -0.39% vs SPY in the sweep; 18.62% CAGR held; "
            "Sharpe 0.85) cannot be relied on, and the sign of the contamination's effect cannot "
            "be determined, so they are not merely adjusted but withdrawn. The qualitative claim "
            "-- that narrowing the pool toward the largest US companies shrinks the equal-weight "
            "deficit -- is UNVERIFIED rather than refuted. M3 must not be cited as evidence until "
            "re-run on a universe whose members are identified against an external registry."
        ),
    )
    ledger.append(
        "note",
        "N12-accumulation-footprint",
        "Universe contaminated (M5): ~11.8 of the top 100 per month unidentifiable; verdict stands",
        detail={
            "effect": (
                "Contamination adds noise to the IC and could in principle mask a weak signal. The "
                "rejection does not rest on the IC alone: against 50 volume shuffles the real IC sat "
                "2.82 sd BELOW its own null. A shuffled series beating the real one is not "
                "explained by ~12% of names being noise, so the verdict is unchanged."
            ),
        },
    )

    after = ledger.trial_count()
    print(f"trial count after:  {after}  (+{after - before})")
    print(f"chain verified: {ledger.verify()} entries")


if __name__ == "__main__":
    main()
