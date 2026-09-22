#!/usr/bin/env python
"""Record round 6: the EUR 25k re-test, the concentration finding, and N12.

Three things happened and all three belong in the append-only record:

  M3  A MEASUREMENT. The universe handicap is a concentration effect, not a
      weighting one. This constrains every future cross-sectional test, so it
      is recorded the way M1 (survivorship) and M2 (data quality) were.

  N7  A RE-TEST at EUR 25,000 of the closest thing to a survivor. Round 3
      predicted capital would not rescue it; the prediction is checked here
      rather than assumed.

  N12 A NEW HYPOTHESIS, pre-registered with its kill criteria BEFORE any
      return is computed. The criteria are the point; recording them after
      seeing a result would make them decoration.

Run:  .venv/bin/python -m scripts.record_round6
"""

from __future__ import annotations

from pathlib import Path

from tradelab.research.ledger import ResearchLedger

AUTHOR = "claude-opus-5"


def main() -> None:
    ledger = ResearchLedger(Path("research/ledger.jsonl"), author=AUTHOR)
    before = ledger.trial_count()
    print(f"trial count before: {before}")

    # ------------------------------------------------------------- N7 re-test
    ledger.record_trial(
        "N7-cross-sectional-momentum",
        summary="Re-test at EUR 25,000. Round 3 predicted capital would not rescue it.",
        n_configs=29,
        n_observations=160,
        metrics={
            "cost_bps_10k": 40.2,
            "cost_bps_25k": 33.1,
            "excess_cagr_25k": 0.0281,
            "excess_after_fixed_costs_25k": -0.0067,
            "sharpe": 0.44,
            "deflation_bar": 0.76,
            "vol": 0.399,
            "maxdd": -0.443,
            "positive_years": 8,
            "worst_year": -0.4191,
        },
        universe="US common stock, point-in-time liquid",
        period="2012-10 to 2026-09",
        config={"n_hold": 20, "hold_band": 10, "grid": "5 holds x 5 bands"},
    )
    ledger.record_verdict(
        "N7-cross-sectional-momentum",
        "rejected",
        reason=(
            "Rejection CONFIRMED at EUR 25,000, and round 3's stated prediction held exactly: "
            "'the edge survives to 150bps, so cost is NOT what kills it... a larger account "
            "would not rescue it.'\n"
            "What capital DID fix: round-trip cost 40.2 -> 33.1 bps, stranded cash 4.05% -> "
            "2.15%, fixed stack 8.70% -> 3.48% of capital. Headline excess rose +2.43% -> "
            "+2.81%. All real, none of it binding.\n"
            "What capital did NOT fix, which is everything that matters: realised Sharpe 0.44 "
            "against a deflation bar of 0.76 at 201 trials -- the strategy scores BELOW what "
            "pure selection noise produces. Volatility 39.9% against SPY's 14.4%. Max drawdown "
            "-44.3% against -23.9%. Eight positive years in fourteen, median +2.47%, worst year "
            "-41.91% which on EUR 25,000 is -10,478 EUR relative to simply holding the index.\n"
            "After the EUR 870/yr research stack the excess is -0.67%: at EUR 25k the best of "
            "fourteen hypotheses, in its most flattering configuration, LOSES to buying an ETF.\n"
            "One honest nuance recorded against my own interest: the parameter surface is NOT a "
            "fragile optimum -- 17 of 25 cells beat SPY. The signal's mean is genuinely "
            "positive. It is the variance that makes it untradable, which is a different and "
            "more permanent objection than overfitting."
        ),
    )

    # ------------------------------------------------- M3 concentration finding
    ledger.record_hypothesis(
        "M3-concentration-not-weighting",
        statement=(
            "The -8.66%/yr universe handicap is a CONCENTRATION effect -- a small draw from a "
            "wide pool misses the mega-caps that carried the index -- rather than a penalty for "
            "equal weighting."
        ),
        rationale=(
            "Not a strategy and no mechanism is claimed. It is a measurement of the baseline "
            "every cross-sectional hypothesis is scored against, and round 3 recorded the "
            "handicap without separating its two candidate causes. If it is weighting, a "
            "different weighting scheme fixes it. If it is concentration, only a narrower "
            "universe does. The fourteen rejections were all scored against whichever it is."
        ),
        data_required="the existing US panel; no new purchase",
    )
    ledger.record_trial(
        "M3-concentration-not-weighting",
        summary=(
            "Four weighting schemes x two universes x 200 random draws, then a pool-narrowing "
            "sweep from 500 names down to 20."
        ),
        n_configs=20,
        n_observations=167,
        metrics={
            "equal_weight_vs_spy": -0.0719,
            "dollar_volume_weight_vs_spy": -0.0812,
            "pool500_vs_spy": -0.0687,
            "pool100_vs_spy": -0.0578,
            "pool50_vs_spy": -0.0474,
            "pool30_vs_spy": -0.0126,
            "pool20_vs_spy": -0.0039,
            "top20_held_cagr": 0.1862,
            "top20_held_sharpe": 0.85,
            "spy_sharpe": 1.05,
            "top20_turnover": 0.16,
        },
        universe="US common stock, point-in-time liquid",
        period="2012-10 to 2026-09",
    )
    ledger.record_verdict(
        "M3-concentration-not-weighting",
        "shelved",
        reason=(
            "CONFIRMED, and it is the most consequential measurement in the project so far.\n"
            "WEIGHTING IS NOT THE CAUSE. Weighting a random 20 by dollar volume made the "
            "deficit WORSE, -7.19% -> -8.12%. Square-root and capped variants moved it by "
            "under a point. The dollar-volume proxy raised the share of draws beating SPY "
            "(2% -> 9%) while LOWERING the mean, which is a variance effect -- more lottery "
            "tickets, not more edge.\n"
            "CONCENTRATION IS THE CAUSE. Narrowing the draw pool collapses the deficit "
            "monotonically: top 500 -6.87%, top 100 -5.78%, top 50 -4.74%, top 30 -1.26%, "
            "top 20 -0.39%. The index's return lived in a handful of names and a draw from a "
            "wide pool almost never held them.\n"
            "CONSEQUENCE FOR ALL FUTURE WORK: a signal selecting from 5,000 names must "
            "overcome roughly 7 points before it is level with the index; the same signal "
            "selecting within the top 30-50 starts within 1-5 points. The alpha bar is a "
            "property of the universe, and it was set unnecessarily high for all fourteen "
            "rejections. This does not resurrect them -- N7 fails on variance, not on the "
            "baseline -- but it changes where the next one should look.\n"
            "WHAT IT IS NOT: holding the top 20 by dollar volume returned 18.62% against SPY's "
            "14.87% at only 16% monthly turnover, but that is NOT a validated strategy and is "
            "not treated as one. Its Sharpe is 0.85 against SPY's 1.05 -- WORSE risk-adjusted "
            "-- with 21.9% volatility against 14.2% and a -40.8% drawdown against -23.9%. The "
            "halves are +1.20% then +6.40%, so it is a concentrated bet on a mega-cap regime "
            "that a fourteen-year window cannot distinguish from a permanent effect. Recorded "
            "as a measured baseline, not as a recommendation.\n"
            "CAVEAT AGAINST MY OWN CONCLUSION: dollar volume is a proxy for market cap and a "
            "poor one, since it also ranks on turnover. A true cap-weighted test would be "
            "cleaner and is not possible with the current data."
        ),
    )

    # -------------------------------------------------- N12 pre-registration
    ledger.record_hypothesis(
        "N12-accumulation-footprint",
        statement=(
            "Among large, liquid US stocks, the PERSISTENCE of directional volume elevation -- "
            "many modest elevated sessions closing near their highs, rather than one large one "
            "-- predicts positive abnormal returns over the following month."
        ),
        rationale=(
            "MECHANISM, textbook rather than speculative: an institution taking a position "
            "large relative to a stock's daily volume cannot do it in one trade. Market impact "
            "forces the order to be split across sessions, and participation algorithms do this "
            "explicitly, targeting 5-20% of each day's volume until filled. Kyle (1985) derives "
            "the same behaviour from theory -- an informed trader spreads trading precisely so "
            "the order does not reveal itself.\n"
            "WHO IS ON THE OTHER SIDE AND WHY THEY LOSE: nobody is trading against their "
            "interest. The claim is that the accumulating institution is deliberately paying "
            "for concealment, and concealment over days is what leaves the footprint. The "
            "counterparty is whoever supplies liquidity into a demand they cannot see the size "
            "of.\n"
            "WHY IT MIGHT NOT BE ARBITRAGED AWAY: the standard volume measures -- turnover, "
            "Amihud, the volume ratio -- all AVERAGE over their window, and averaging is exactly "
            "the operation that destroys the distinction between fifteen modest sessions and "
            "one huge one. Gervais/Kaniel/Mingelgrin's high-volume premium and Lee/Swaminathan's "
            "turnover-conditioned momentum are both built on the LEVEL. This signal is "
            "constructed to discard the one-day spike that drives them.\n"
            "NOVELTY CLAIM, deliberately narrow: not that order splitting is unknown, it is a "
            "textbook mechanism, but that a cross-sectional equity signal built on the "
            "PERSISTENCE of directional volume elevation, computable from daily OHLCV alone, is "
            "not in the standard factor zoo.\n"
            "PRIOR: low. Fourteen hypotheses, zero survivors. The base rate says this dies too, "
            "and the value is in killing it cheaply."
        ),
        data_required="daily OHLCV, already held; earnings dates from EODHD for kill criterion 4",
    )
    ledger.append(
        "note",
        "N12-accumulation-footprint",
        "Kill criteria and test order, fixed before any return was computed",
        detail={
            "test_order": "cheapest and most-likely-fatal first, as in N10",
            "1_placebo_volume": (
                "Permute each stock's volume history in time, preserving its distribution and "
                "destroying only the clustering. KILL if the shuffled panel predicts at >=80% "
                "of the real one -- persistence would then carry nothing."
            ),
            "2_placebo_clv": (
                "Permute each stock's close-location history, keeping real volume clustering. "
                "Isolates whether direction adds anything to elevation."
            ),
            "3_parameter_surface": (
                "k in {1.1,1.25,1.5,2.0} x window in {10,21,42,63} x baseline in {60,120,250}. "
                "KILL if fewer than 50% of cells are positive. This is the N11 lesson applied "
                "in advance: a parameter that is never varied is never tested, and an "
                "out-of-sample split cannot validate a parameter held fixed in both halves."
            ),
            "4_momentum_orthogonality": (
                "Double-sort against 12-1 momentum. KILL if the marginal contribution within "
                "momentum quintiles is not positive -- it would then BE momentum, which is "
                "already rejected."
            ),
            "5_earnings_contamination": (
                "Exclude names with an earnings date inside the formation window. KILL if the "
                "effect only exists with earnings included -- it would then be PEAD, which is "
                "published and decayed."
            ),
            "6_effective_sample": (
                "Volume is strongly autocorrelated. Compute n_eff = n(1-r)/(1+r). KILL if the "
                "t-statistic on the effective sample is below 2. This is what killed "
                "listing_rate in N11 and it is cheap to check first."
            ),
            "7_universe_and_benchmark": (
                "Tested WITHIN the top 50-100 by dollar volume, per M3, so the baseline is "
                "1-5 points from SPY rather than 7. Must beat SPY on absolute return AND not "
                "worsen Sharpe, since M3 showed a concentrated book can buy return with risk."
            ),
            "8_out_of_sample": (
                "Discovery 2012-2019, confirmation 2019-2026, with the parameter surface "
                "re-tested in BOTH halves rather than only the lag."
            ),
        },
    )

    after = ledger.trial_count()
    print(f"trial count after:  {after}  (+{after - before})")
    print(f"chain verified: {ledger.verify()} entries")


if __name__ == "__main__":
    main()
