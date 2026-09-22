#!/usr/bin/env python
"""Record, BEFORE any N13 result exists, why the test is being run and on what terms.

Gate 0 stopped N13 in round 9. The account holder then chose to proceed on data
cleaned against the SEC registry. That decision, and every rule fixed ahead of
the run, belongs in the ledger before the numbers do -- otherwise the record
cannot show which choices were made blind and which after seeing results.

Run:  .venv/bin/python -m scripts.record_round10_pre
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
        "Account holder elects to run N13 despite gate 0, on SEC-cleaned data; rules fixed before results",
        detail={
            "decision": (
                "Round 9 stopped N13 at gate 0 and recommended not testing on the vendor's "
                "universe. The account holder chose to proceed with contaminated data filtered "
                "out, and a comparison against simply holding the S&P 500. Results will carry the "
                "gate-0 caveat, with the residual audit gap re-measured and printed beside them."
            ),
            "cleaning_rules": (
                "(1) identify every symbol against an SEC security master built from all 16,297 XBRL "
                "filers 2009-2025 (US GAAP and IFRS, 19 currencies): listed symbols by current "
                "ticker, delisted by name or former name at >= 0.92, or ticker plus name >= 0.80; "
                "(2) never identify non-US legal forms, tickers over 5 characters, or codes with no "
                "real name; (3) blank prices more than 365 days before a company's first SEC filing "
                "or 400 days after its last; (4) one security per company per date, the more liquid."
            ),
            "universe": "top 100 by trailing 60-session median dollar volume among IDENTIFIED securities, monthly",
            "other_stocks": (
                "foreign companies listed in the US and filing 20-F with the SEC are included; their "
                "IFRS statements give currency-free ratios from a single filing"
            ),
            "composite": (
                "equal-weighted mean of percentile ranks of profitability (GP/assets), quality "
                "(operating income/equity) and 12-1 momentum, among top-100 names with all three; "
                "a name missing any component cannot be selected"
            ),
            "baseline": "20 largest by dollar volume from the SAME eligible set, same rebalance, band and costs",
            "verdict_rule": (
                "pre-registered gates in order; the verdict is the first failure. The comparison "
                "with SPY and QQQ is computed regardless, as requested, and is description after a kill"
            ),
            "gate_9_reading": (
                "applied as written (raw Sharpe vs deflation bar); the stricter reading, the Sharpe of "
                "returns in excess of SPY, reported beside it because a long-only book clears a raw "
                "bar on market beta alone"
            ),
            "no_results_seen": True,
        },
    )
    print(f"recorded; chain verified: {ledger.verify()} entries, trials {ledger.trial_count()}")


if __name__ == "__main__":
    main()
