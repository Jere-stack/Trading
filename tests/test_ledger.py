"""Research ledger tests.

The ledger's only job is to make a trial count that cannot be quietly revised
downward. Every test here names the revision it blocks.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from tradelab.research.ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    ResearchLedger,
    deflate_from_ledger,
)


@pytest.fixture
def ledger(tmp_path) -> ResearchLedger:
    return ResearchLedger(tmp_path / "ledger.jsonl", author="test")


def a_hypothesis(ledger, hid="H1"):
    return ledger.record_hypothesis(
        hid, statement="buy the dip", rationale="someone is forced to sell"
    )


class TestAppendOnly:
    def test_entries_chain_to_their_predecessor(self, ledger):
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="run", n_observations=100)
        first, second = ledger.entries()
        assert first.prev_hash == GENESIS_HASH
        assert second.prev_hash == first.entry_hash
        assert ledger.verify() == 2

    def test_editing_an_old_entry_is_detected(self, ledger):
        """Prevents: a failed trial being rewritten as a successful one."""
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="bad result", n_observations=100)
        ledger.record_trial("H1", summary="another", n_observations=100)

        lines = ledger.path.read_text().splitlines()
        tampered = json.loads(lines[1])
        tampered["summary"] = "good result"
        lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        ledger.path.write_text("\n".join(lines) + "\n")

        with pytest.raises(LedgerIntegrityError, match="entry 2 hashes to"):
            ledger.verify()

    def test_deleting_an_entry_is_detected(self, ledger):
        """Prevents: the failed trials disappearing and the count dropping."""
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="one", n_observations=100)
        ledger.record_trial("H1", summary="two", n_observations=100)

        lines = ledger.path.read_text().splitlines()
        del lines[1]
        ledger.path.write_text("\n".join(lines) + "\n")

        with pytest.raises(LedgerIntegrityError, match="removed or reordered"):
            ledger.verify()

    def test_appending_to_a_tampered_ledger_is_refused(self, ledger):
        """Prevents: laundering an edit by burying it under valid entries."""
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="one", n_observations=100)

        lines = ledger.path.read_text().splitlines()
        tampered = json.loads(lines[0])
        tampered["summary"] = "rewritten"
        lines[0] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        ledger.path.write_text("\n".join(lines) + "\n")

        with pytest.raises(LedgerIntegrityError):
            ledger.record_trial("H1", summary="two", n_observations=100)

    def test_a_fresh_ledger_verifies_as_empty(self, ledger):
        assert ledger.verify() == 0
        assert ledger.trial_count() == 0


class TestTrialCounting:
    def test_a_grid_counts_every_configuration(self, ledger):
        """Prevents: reporting the best of 200 combinations as n_trials=1."""
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="grid", n_configs=200, n_observations=500)
        assert ledger.trial_count("H1") == 200

    def test_counts_accumulate_across_entries_and_sessions(self, ledger):
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="a", n_configs=36, n_observations=500)
        # A second session, a different model, months later.
        later = ResearchLedger(ledger.path, author="a-newer-model")
        later.record_trial("H1", summary="b", n_configs=12, n_observations=500)
        assert later.trial_count("H1") == 48

    def test_project_total_spans_hypotheses(self, ledger):
        """The right bar for 'this is the best thing we found'."""
        a_hypothesis(ledger, "H1")
        a_hypothesis(ledger, "H2")
        ledger.record_trial("H1", summary="a", n_configs=10, n_observations=500)
        ledger.record_trial("H2", summary="b", n_configs=5, n_observations=500)
        assert ledger.trial_count("H1") == 10
        assert ledger.trial_count() == 15

    def test_a_run_with_no_sample_does_not_count(self, ledger):
        """A crash is not a trial; the zero sample makes the exclusion visible."""
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="crashed before any result", n_observations=0)
        assert ledger.trial_count("H1") == 0

    def test_superseded_trials_still_count_by_default(self, ledger):
        """Prevents: 'that run had a bug' quietly deleting the failed trials."""
        a_hypothesis(ledger)
        original = ledger.record_trial("H1", summary="buggy", n_configs=10, n_observations=500)
        ledger.record_trial(
            "H1",
            summary="rerun after fixing the off-by-one",
            n_configs=10,
            n_observations=500,
            supersedes=original.entry_id,
            supersede_reason="volume index was off by one; detections went 54 -> 520",
        )
        assert ledger.trial_count("H1") == 20
        assert ledger.trial_count("H1", count_superseded=False) == 10

    def test_superseding_without_a_reason_is_refused(self, ledger):
        a_hypothesis(ledger)
        original = ledger.record_trial("H1", summary="one", n_observations=100)
        with pytest.raises(ValueError, match="supersede_reason"):
            ledger.record_trial(
                "H1", summary="two", n_observations=100, supersedes=original.entry_id
            )

    def test_superseding_a_nonexistent_entry_is_refused(self, ledger):
        a_hypothesis(ledger)
        with pytest.raises(ValueError, match="no such entry"):
            ledger.record_trial(
                "H1",
                summary="x",
                n_observations=100,
                supersedes=99,
                supersede_reason="because",
            )


class TestRequiredDiscipline:
    def test_a_hypothesis_needs_a_stated_mechanism(self, ledger):
        """Prevents: data mining entering the register as a hypothesis."""
        with pytest.raises(ValueError, match="no stated mechanism"):
            ledger.record_hypothesis("H1", statement="this pattern works", rationale="")

    def test_a_verdict_needs_a_reason(self, ledger):
        a_hypothesis(ledger)
        with pytest.raises(ValueError, match="requires a reason"):
            ledger.record_verdict("H1", "rejected", reason="  ")

    def test_an_unattributed_trial_is_refused(self, ledger):
        with pytest.raises(ValueError, match="uncountable"):
            ledger.record_trial("   ", summary="x", n_observations=10)


class TestReporting:
    def test_rollup_carries_verdict_and_authors(self, ledger):
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="a", n_configs=4, n_observations=500)
        ledger.record_verdict("H1", "rejected", reason="negative after costs")
        (row,) = ledger.hypotheses()
        assert row.trials == 4
        assert row.verdict == "rejected"
        assert row.verdict_reason == "negative after costs"
        assert row.authors == ("test",)
        assert not row.is_open

    def test_a_reopened_hypothesis_keeps_accumulating(self, ledger):
        """A verdict closes a question; it does not reset the trial count."""
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="a", n_configs=10, n_observations=500)
        ledger.record_verdict("H1", "rejected", reason="no edge")
        ledger.record_trial(
            "H1", summary="retested with better data", n_configs=6, n_observations=900
        )
        assert ledger.trial_count("H1") == 16


class TestDeflation:
    def test_trial_count_flows_into_the_deflated_sharpe(self, ledger):
        """The point of the whole module: n_trials is not a typed-in argument."""
        a_hypothesis(ledger)
        ledger.record_trial("H1", summary="a", n_configs=4, n_observations=500)
        returns = np.random.default_rng(0).normal(0.0008, 0.01, 750)

        few = deflate_from_ledger(returns, ledger, "H1")
        assert few.n_trials == 4

        ledger.record_trial("H1", summary="b", n_configs=496, n_observations=500)
        many = deflate_from_ledger(returns, ledger, "H1")
        assert many.n_trials == 500
        # Same returns, more trials tested: a strictly higher bar and a weaker
        # result. This is the entire mechanism the ledger exists to feed.
        assert many.expected_max_sharpe > few.expected_max_sharpe
        assert many.deflated_sharpe < few.deflated_sharpe

    def test_project_wide_deflation_uses_every_hypothesis(self, ledger):
        a_hypothesis(ledger, "H1")
        a_hypothesis(ledger, "H2")
        ledger.record_trial("H1", summary="a", n_configs=10, n_observations=500)
        ledger.record_trial("H2", summary="b", n_configs=90, n_observations=500)
        returns = np.random.default_rng(1).normal(0.0008, 0.01, 750)
        assert deflate_from_ledger(returns, ledger, "H1").n_trials == 10
        assert deflate_from_ledger(returns, ledger, "H1", project_wide=True).n_trials == 100


class TestSeededLedger:
    """The committed ledger is the project's actual record; guard it."""

    def test_the_committed_ledger_verifies(self):
        from pathlib import Path

        path = Path("research/ledger.jsonl")
        if not path.exists():
            pytest.skip("ledger not yet seeded")
        committed = ResearchLedger(path)
        assert committed.verify() > 0
        assert committed.trial_count() > 0
        # Merger arbitrage was tested and rejected; that must survive in the
        # record, because its trials still deflate everything tested after it.
        rows = {r.hypothesis_id: r for r in committed.hypotheses()}
        assert rows["N1-cash-merger-arbitrage"].verdict == "rejected"
        assert rows["N1-cash-merger-arbitrage"].trials >= 25
