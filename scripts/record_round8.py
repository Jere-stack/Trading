#!/usr/bin/env python
"""Record round 8: the wide search, M4 (post-publication factor survival), and N13.

Trial accounting for M4: the 153 JKP factors are other people's hypotheses, but
this round LOOKED at all 153 and at 13 themes in order to choose what to build
next. That choice is a selection, and selections are what the deflation bar
exists to price. All 153 are counted. Counting 13 (the themes) would be
defensible -- they are the unit of decision -- but the conservative number is
the honest default, and moving from 322 to 475 trials raises the expected
maximum Sharpe of a worthless strategy only slightly, because it grows with the
square root of the log of the count.

Run:  .venv/bin/python -m scripts.record_round8
"""

from __future__ import annotations

from pathlib import Path

from tradelab.research.ledger import ResearchLedger

AUTHOR = "claude-opus-5-5"


def main() -> None:
    ledger = ResearchLedger(Path("research/ledger.jsonl"), author=AUTHOR)
    before = ledger.trial_count()
    print(f"trial count before: {before}")

    # ----------------------------------------------------------------- M4
    ledger.record_hypothesis(
        "M4-post-publication-factor-survival",
        statement=(
            "Of the 153 US equity anomalies in Jensen, Kelly and Pedersen (2023), which themes "
            "still earn a premium AFTER publication, in the last decade, in mega and large caps, "
            "and in the LONG leg a long-only investor can actually hold?"
        ),
        rationale=(
            "Not a strategy. A measurement to decide where the next one should be built. "
            "Post-publication returns are returns the original authors could not have fitted, "
            "so they are an out-of-sample test run by the passage of time across 153 hypotheses "
            "on the whole US market, using professionally constructed survivorship-free data. "
            "Fifteen price-and-volume hypotheses in this project all died; this asks which "
            "families of idea survive when someone else's clean data is the judge."
        ),
        data_required="JKP factor and tercile-portfolio returns (free, jkpfactors.com, through 2025-12)",
    )
    ledger.record_trial(
        "M4-post-publication-factor-survival",
        summary=(
            "153 factors: in-sample vs post-publication decay, last-decade significance against "
            "chance, long leg vs market and vs own tercile average, mega- and large-cap "
            "long-short, aggregated to 13 themes."
        ),
        n_configs=153,
        n_observations=1200,
        metrics={
            "factors": 153.0,
            "post_over_insample": 0.38,
            "positive_post_publication": 0.79,
            "recent_ls_t_over_2": 5.0,
            "recent_long_bias_free_t_over_2": 4.0,
            "chance_expectation_t_over_2": 3.5,
            "tercile_construction_offset_vs_mkt": 0.0042,
            "momentum_long_bias_free_recent": 0.0153,
            "quality_long_bias_free_recent": 0.0122,
            "profitability_long_bias_free_recent": 0.0108,
            "low_risk_long_bias_free_recent": -0.0003,
            "seasonality_long_bias_free_recent": -0.0012,
        },
        universe="US, all stocks capped value weight; plus mega- and large-cap subsets",
        period="1926-01 to 2025-12; 'recent' = 2015-01 to 2025-12",
    )
    ledger.record_verdict(
        "M4-post-publication-factor-survival",
        "shelved",
        reason=(
            "MEASURED. Four results, one of them a correction to my own first reading.\n"
            "1 DECAY. Of 121 factors with a positive in-sample premium, the post-publication "
            "premium averages +1.54%/yr against +4.05% in-sample -- 38% survives, close to "
            "McLean-Pontiff's ~42% -- and 79% of factors remain positive after publication. "
            "Published anomalies are shrunk to about a third, not dead.\n"
            "2 INDIVIDUAL FACTORS IN THE LAST DECADE ARE NOISE. 2015-2025 long-short: 5 factors "
            "at t >= 2 against ~3.5 expected by chance. Bias-free long legs: 4. Picking the best "
            "single factor from this table would be data mining with extra steps.\n"
            "3 A CONSTRUCTION BIAS I NEARLY REPORTED AS SIGNAL. My first pass showed 126 of 153 "
            "long legs beating the market and 16 at t >= 2. It is not evidence: in 2015-2025 the "
            "AVERAGE of each factor's three terciles beat the market by +0.42%/yr, and even the "
            "MIDDLE tercile beat it 76% of the time, while short legs sat at -0.16%. JKP's "
            "terciles carry an offset against their own market series. Caught by checking "
            "whether the short legs ALSO beat the market. The corrected measure differences "
            "each long tercile against its own factor's tercile average, which cancels the "
            "offset exactly; the count falls from 16 to 4.\n"
            "4 AT THE THEME LEVEL, THREE ARE CONSISTENT THROUGH EVERY LENS -- post-publication, "
            "last decade, bias-free long leg, mega caps, large caps:\n"
            "   Momentum       ls post +2.85%  recent +3.55%  long* +1.53%  mega +2.42%\n"
            "   Quality        ls post +2.47%  recent +2.51%  long* +1.22%  mega +2.41%\n"
            "   Profitability  ls post +2.35%  recent +2.44%  long* +1.08%  mega +1.14%\n"
            "with 82-91% of each theme's factors positive in the bias-free long leg.\n"
            "CROSS-VALIDATION OF THIS PROJECT'S OWN WORK, and the most reassuring number in the "
            "round: the themes this project independently tested and rejected are exactly the "
            "ones JKP's data shows dead or weak for a long-only book -- Low Risk (N5, N6) at "
            "-0.03%, Seasonality -0.12%, Size -0.77%, Short-Term Reversal +0.25%. And price "
            "momentum (N7), rejected here on variance with 17 of 25 cells positive, sits in the "
            "theme JKP finds strongest. Two independent pipelines on different data agree. The "
            "infrastructure is not the reason fifteen hypotheses died.\n"
            "REALISM: +1.1% to +1.5%/yr is the GROSS, bias-free, theme-level long-leg premium "
            "for portfolios of hundreds of stocks, before costs. A 20-30 stock book captures it "
            "with far more noise. This is a modest edge, not a large one, and any strategy built "
            "on it should be expected to struggle against the deflation bar on a 14-year sample.\n"
            "WHAT IT CHANGES: two of the three surviving themes need company fundamentals, which "
            "this project had deferred on cost grounds (EODHD fundamentals at ~EUR 60/month). "
            "The SEC serves XBRL fundamentals free at data.sec.gov -- verified reachable, 503 "
            "us-gaap tags for Apple back to 2007, every row carrying its filing date so that "
            "point-in-time reconstruction is possible. The cost objection to fundamentals was "
            "wrong. Recorded as a correction to the standing recommendation."
        ),
    )

    # ----------------------------------------------------------------- N13
    ledger.record_hypothesis(
        "N13-quality-profitability-momentum",
        statement=(
            "Among the top 100 US stocks by trailing dollar volume, a long-only book ranked on an "
            "equal-weighted composite of profitability, quality and 12-1 momentum, built from "
            "free point-in-time SEC fundamentals, beats both SPY and a size-matched naive book "
            "after costs, without worse risk-adjusted return."
        ),
        rationale=(
            "MECHANISM, from the literature rather than invented here: markets underprice "
            "persistent profitability and quality (Novy-Marx 2013; Asness, Frazzini and Pedersen "
            "'Quality Minus Junk'), and underreact to news, which momentum harvests. WHO IS ON "
            "THE OTHER SIDE: investors anchored on price and narrative rather than on the "
            "durability of cash flows, and slow-moving capital that updates late.\n"
            "WHY THIS AND NOT ANOTHER GUESS: M4 is the selection. These are the three themes "
            "that survived publication, the last decade, the bias-free long leg, and the "
            "mega-cap universe simultaneously -- and the themes this project rejected on its "
            "own are the ones M4 shows dead, which says the selection is not an artefact of "
            "the pipeline.\n"
            "WHY IT MIGHT STILL FAIL, stated in advance: the theme premium is ~+1-1.5%/yr gross "
            "over hundreds of names; 20-30 names over 14 years may not resolve it; much of the "
            "last decade's quality and momentum return was the same mega-cap concentration M3 "
            "measured, which is why the size-matched baseline below is a kill criterion and not "
            "a footnote.\n"
            "PRIOR: higher than any of the fifteen before it, and still low in absolute terms."
        ),
        data_required=(
            "SEC XBRL companyfacts (free) with ticker->CIK mapping including delisted firms; "
            "daily prices already held"
        ),
    )
    ledger.append(
        "note",
        "N13-quality-profitability-momentum",
        "Design and kill criteria, fixed before any fundamentals were downloaded",
        detail={
            "universe": "top 100 US common stocks by trailing 60-session median dollar volume, point-in-time (per M3)",
            "components": (
                "PROFITABILITY gross profit / total assets (Novy-Marx). QUALITY operating income "
                "/ book equity. MOMENTUM 12-1 month return. Each ranked cross-sectionally to a "
                "percentile; composite = equal-weighted mean. Weights are FIXED at 1/3 and are "
                "not a tuning parameter."
            ),
            "point_in_time": (
                "A fundamental value is usable only from the first session AFTER its SEC "
                "'filed' date. Restatements (10-K/A) are used only from their own filed date, "
                "never back-filled. Unit test required: no value observable before filing."
            ),
            "portfolio": "long-only, top 20 by composite, equal weight, quarterly rebalance, hold band 10, 33 bps round trip",
            "0_data_audit": (
                "CIK mapping coverage must not differ by more than 6 points between names that "
                "later delisted and names that survived (the survivorship_check rule). Fail "
                "means the test is not run."
            ),
            "1_ic": "composite IC vs next-quarter return, t >= 2 on effective sample",
            "2_permutation": "cross-sectional permutation null (the corrected placebo from round 7), empirical p < 0.05",
            "3_components": "at least 2 of 3 components with positive IC; a composite that works only through one component IS that component",
            "4_fundamentals_add_value": (
                "profitability+quality sub-composite neutralised to momentum keeps |t| >= 2 -- "
                "otherwise the fundamentals add nothing and this is N7 again, already rejected"
            ),
            "5_size_matched_baseline": (
                "must beat an equal-weighted book of the 20 LARGEST names by dollar volume from "
                "the same universe, in BOTH halves. M3 showed that baseline alone returned "
                "18.62%; beating SPY is not enough, because concentration alone does that"
            ),
            "6_risk": "Sharpe after costs not below SPY's over the same window",
            "7_surface": "hold {15,20,30} x rebalance {monthly,quarterly} x band {0,10}: >= 50% of cells beat the size-matched baseline",
            "8_out_of_sample": "discovery 2012-2019, confirmation 2019-2026, with the surface re-run in BOTH halves",
            "9_deflation": "realised Sharpe against the deflation bar at the ledger's trial count on the day of the test",
        },
    )

    after = ledger.trial_count()
    print(f"trial count after:  {after}  (+{after - before})")
    print(f"chain verified: {ledger.verify()} entries")


if __name__ == "__main__":
    main()
