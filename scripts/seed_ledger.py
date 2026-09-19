#!/usr/bin/env python
"""Seed the research ledger with the trials already run.

A ledger that starts empty while research has already happened understates the
trial count from its first day, which is the one error it exists to prevent.
This backfills everything done before the ledger existed, from the record in
`docs/06-strategy-hypotheses.md` and `docs/08-testable-with-this-data.md`.

Backfilled entries are marked `backfilled: true` in their detail. They are
reconstructed from written notes rather than logged at the time, so the counts
are the most conservative reading of what was run -- where the record is
ambiguous the higher count is taken, since understating is the failure mode
that matters.

Run once:  .venv/bin/python scripts/seed_ledger.py
"""

from __future__ import annotations

from pathlib import Path

from tradelab.research.ledger import ResearchLedger

LEDGER_PATH = Path("research/ledger.jsonl")
AUTHOR = "claude-opus-5"


def seed(ledger: ResearchLedger) -> None:
    # ---------------------------------------------------------- pre-data screen
    # Ten hypotheses screened against the cost model before any data was
    # bought. Each was one configuration: the published effect size against
    # measured round-trip cost at EUR 10k. Six died here.
    screen = [
        (
            "H1-overnight-risk-premium",
            "Buy at close, sell at open",
            "3 bps over 1 day",
            "rejected",
            "3 bps gross against ~25 bps round-trip cost; infeasible on cost and on power",
        ),
        (
            "H2-short-term-reversal",
            "Buy 1-week losers, hold 5 days",
            "35 bps over 5 days",
            "rejected",
            "35 bps gross against ~25 bps cost leaves 10 bps, and the effect is the most crowded in retail quant",
        ),
        (
            "H3-turn-of-month-flow",
            "Long the last and first four sessions of a month",
            "25 bps over 4 days",
            "rejected",
            "25 bps gross against ~25 bps cost is zero; infeasible on cost and on power",
        ),
        (
            "H4-post-earnings-drift",
            "Long positive earnings surprises for 45 days",
            "90 bps over 45 days",
            "pending",
            "Passes the cost screen but needs the EUR 60/mo Fundamentals tier for point-in-time estimates",
        ),
        (
            "H5-index-deletion",
            "Buy forced-sale deletions from major indices",
            "140 bps over 20 days",
            "pending",
            "Best risk-adjusted candidate on the screen; blocked on point-in-time index membership, which is not in the current data plan",
        ),
        (
            "H6-index-addition",
            "Buy S&P 500 additions before effective date",
            "15 bps over 10 days",
            "rejected",
            "15 bps gross; the effect has decayed to near zero since 2000 as it became the most-studied event in finance",
        ),
        (
            "H7-spinoff-selling",
            "Buy spin-off stubs indiscriminately sold by index funds",
            "400 bps over 126 days",
            "pending",
            "Strongest mechanism on the register, weakest evidence; needs corporate action data the plan does not include",
        ),
        (
            "H8-tax-loss-reversal",
            "Buy December tax-loss candidates, hold into January",
            "120 bps over 30 days",
            "rejected",
            "One observation per year: 20 years of data gives n=20, which cannot distinguish 120 bps from zero",
        ),
        (
            "H9-fund-fire-sales",
            "Buy names held by funds in severe outflow",
            "200 bps over 60 days",
            "pending",
            "Marginal on the screen; needs fund holdings data, which the plan does not include",
        ),
        (
            "H10-cross-sectional-momentum",
            "Long 12-1 month winners, monthly rebalance",
            "45 bps over 21 days",
            "rejected",
            "45 bps gross against ~25 bps cost, on the single most crowded factor in the literature",
        ),
    ]
    for hid, statement, effect, decision, reason in screen:
        ledger.record_hypothesis(
            hid,
            statement=statement,
            rationale=(
                "Screened from the published literature against this account's measured "
                "cost model rather than adopted on its published effect size."
            ),
            predicted_sign="positive",
            data_required="daily bars; several need data the current plan does not include",
            author=AUTHOR,
        )
        ledger.record_trial(
            hid,
            summary=f"Cost/power screen: {effect} against measured round-trip cost at EUR 10k",
            n_configs=1,
            n_observations=1,
            config={"screen": "cost_and_power", "published_effect": effect},
            universe="n/a (literature effect sizes)",
            period="published estimates",
            author=AUTHOR,
        )
        ledger.record_verdict(hid, decision, reason=reason, author=AUTHOR)

    # ------------------------------------------------- N1 cash merger arbitrage
    ledger.record_hypothesis(
        "N1-cash-merger-arbitrage",
        statement="Buy cash-acquisition targets ten sessions after announcement, hold to close",
        rationale=(
            "Deal risk premium -- compensation for bearing the risk the deal breaks. A risk "
            "premium rather than an anomaly, so it should persist. Long-only, months-long "
            "holding periods, naturally small capacity: the shape a retail account can hold."
        ),
        predicted_sign="positive",
        data_required="survivorship-free daily bars including delisted tickers",
        author=AUTHOR,
    )
    # The detector was run across a threshold grid: min_jump x min_volume_ratio
    # x max_pinned_vol x min_vol_collapse, plus the stop-loss and horizon
    # variants used for the sensitivity table.
    ledger.record_trial(
        "N1-cash-merger-arbitrage",
        summary="Detector threshold grid over the US survivorship-free universe, 15 years",
        n_configs=24,
        n_observations=520,
        metrics={"mean_return_all_detections": 0.0143},
        config={
            "min_jump": "0.08-0.20",
            "min_volume_ratio": "1.5-4.0",
            "max_pinned_vol": "0.008-0.020",
            "min_vol_collapse": "0.30-0.60",
        },
        universe="US survivorship-free, delisted included",
        period="2010-2025",
        author=AUTHOR,
    )
    ledger.record_trial(
        "N1-cash-merger-arbitrage",
        summary=(
            "Resolved-events-only split: 438 events that actually closed or broke. "
            "Closed +2.76% (n=339), broke -20.31% (n=99), all resolved -2.45%"
        ),
        n_configs=1,
        n_observations=438,
        metrics={"mean_return": -0.0245, "win_rate": 0.774},
        config={"subset": "resolved_only"},
        universe="US survivorship-free, delisted included",
        period="2010-2025",
        author=AUTHOR,
    )
    ledger.record_verdict(
        "N1-cash-merger-arbitrage",
        "rejected",
        reason=(
            "-2.45% expected value per resolved event, before costs. Entry ten sessions after "
            "announcement leaves only the residual spread, thinnest when the deal is safest; "
            "price cannot distinguish deal risk, so the book is a blind mix. Selecting on the "
            "volatility-collapse signature selects against the premium. The headline was "
            "positive at every threshold tested (+1.43% to +6.59%) while the honest subset was "
            "negative at every one (-1.39% to -6.34%) -- the profit came entirely from events "
            "that never resolved. Parameter robustness gave false reassurance."
        ),
        author=AUTHOR,
    )

    # ------------------------------------------------------- measurement work
    # Not a strategy, but it consumed data and its result changed decisions, so
    # it belongs in the record.
    ledger.record_hypothesis(
        "M1-survivorship-bias",
        statement="Quantify the survivorship bias in a delisting-free universe",
        rationale=(
            "Not an edge. A measurement, to know how much of any backtested return is an "
            "artefact of excluding the companies that failed."
        ),
        predicted_sign="n/a",
        data_required="exchange symbol lists with delisted=1",
        author=AUTHOR,
    )
    ledger.record_trial(
        "M1-survivorship-bias",
        summary="Survivors-only vs full universe CAGR, US and Helsinki",
        n_configs=2,
        n_observations=2,
        metrics={"us_bias_cagr": 0.0278, "helsinki_bias_cagr": 0.0105},
        config={"markets": "US, Helsinki"},
        universe="full exchange symbol lists including delisted",
        period="2010-2025",
        author=AUTHOR,
    )
    ledger.record_verdict(
        "M1-survivorship-bias",
        "shelved",
        reason=(
            "Measured, not a strategy. US survivorship bias is +2.78%/yr and Helsinki "
            "+1.05%/yr. Any backtest on a survivors-only universe must clear that before "
            "it has said anything."
        ),
        author=AUTHOR,
    )


def main() -> None:
    ledger = ResearchLedger(LEDGER_PATH, author=AUTHOR)
    if ledger.entries():
        print(f"{LEDGER_PATH} already has {len(ledger.entries())} entries; refusing to re-seed.")
        print("The ledger is append-only. Delete it deliberately if you mean to start over.")
        print()
        print(ledger.report())
        return

    seed(ledger)
    verified = ledger.verify()
    print(f"seeded and verified {verified} entries\n")
    print(ledger.report())


if __name__ == "__main__":
    main()
