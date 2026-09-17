"""Tests for the statistical validation machinery.

These tests are load-bearing. The whole research protocol rests on the claim
that this code correctly rejects strategies that are only lucky, so the tests
construct known-worthless strategies and assert that they are rejected.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from tradelab.research.validation import (
    block_bootstrap_sharpe,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    minimum_track_record_length,
    parameter_stability,
    permutation_test,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    purged_walk_forward_splits,
)


class TestExpectedMaxSharpe:
    def test_grows_with_trial_count(self):
        """The selection-bias benchmark must rise as more variants are tried."""
        var = 1.0 / 755
        values = [expected_max_sharpe(n, var) for n in (2, 10, 100, 1000)]
        assert values == sorted(values)
        assert all(v > 0 for v in values)

    def test_single_trial_has_no_selection_bias(self):
        assert expected_max_sharpe(1, 1.0 / 755) == 0.0

    def test_zero_variance_gives_zero(self):
        assert expected_max_sharpe(100, 0.0) == 0.0

    def test_rejects_invalid_trial_count(self):
        with pytest.raises(ValueError):
            expected_max_sharpe(0, 0.1)


class TestDeflatedSharpe:
    def test_rejects_best_of_many_random_strategies(self):
        """The core claim: the winner of a large random search must not pass.

        This is the exact failure mode the whole module exists to prevent, so it
        is tested against a strategy known a priori to have zero edge.
        """
        rng = np.random.default_rng(11)
        best_returns, best_sharpe = None, -np.inf
        for _ in range(200):
            r = rng.normal(0, 0.01, 756)
            s = r.mean() / r.std(ddof=1)
            if s > best_sharpe:
                best_sharpe, best_returns = s, r

        annualised = best_sharpe * np.sqrt(252)
        assert annualised > 1.0, "fixture should produce a flattering Sharpe"

        result = deflated_sharpe_ratio(best_returns, n_trials=200)
        assert not result.is_significant
        assert result.expected_max_sharpe > 0

    def test_understating_trials_inflates_significance(self):
        """Pins why the trial count must be honest: it is the whole adjustment."""
        rng = np.random.default_rng(11)
        best_returns, best_sharpe = None, -np.inf
        for _ in range(200):
            r = rng.normal(0, 0.01, 756)
            s = r.mean() / r.std(ddof=1)
            if s > best_sharpe:
                best_sharpe, best_returns = s, r

        honest = deflated_sharpe_ratio(best_returns, n_trials=200).deflated_sharpe
        dishonest = deflated_sharpe_ratio(best_returns, n_trials=1).deflated_sharpe
        assert dishonest > honest
        assert dishonest > 0.9 and honest < 0.9

    def test_genuinely_strong_signal_survives(self):
        """Guards against the opposite error: rejecting everything regardless."""
        rng = np.random.default_rng(3)
        # A large, real edge: annualised Sharpe around 3.
        returns = rng.normal(0.003, 0.01, 1000)
        result = deflated_sharpe_ratio(returns, n_trials=50)
        assert result.is_significant, result.verdict()

    def test_uses_measured_trial_variance_when_supplied(self):
        rng = np.random.default_rng(5)
        returns = rng.normal(0.001, 0.01, 500)
        trials = rng.normal(0, 0.05, 100)
        measured = deflated_sharpe_ratio(returns, n_trials=100, trial_sharpes=trials)
        assumed = deflated_sharpe_ratio(returns, n_trials=100)
        assert measured.expected_max_sharpe != assumed.expected_max_sharpe

    def test_requires_enough_observations(self):
        with pytest.raises(ValueError, match="at least 3"):
            deflated_sharpe_ratio(np.array([0.01, 0.02]), n_trials=10)

    def test_zero_variance_is_not_infinite_sharpe(self):
        result = deflated_sharpe_ratio(np.zeros(100), n_trials=10)
        assert result.deflated_sharpe == 0.0


class TestProbabilisticSharpe:
    def test_strong_edge_beats_zero_benchmark(self):
        rng = np.random.default_rng(2)
        assert probabilistic_sharpe_ratio(rng.normal(0.002, 0.01, 1000)) > 0.95

    def test_noise_does_not_beat_zero_benchmark(self):
        rng = np.random.default_rng(2)
        assert probabilistic_sharpe_ratio(rng.normal(0.0, 0.01, 1000)) < 0.95

    def test_higher_benchmark_is_harder(self):
        rng = np.random.default_rng(4)
        r = rng.normal(0.001, 0.01, 1000)
        assert probabilistic_sharpe_ratio(r, 0.0) > probabilistic_sharpe_ratio(r, 0.1)


class TestMinimumTrackRecord:
    def test_weaker_edge_needs_longer_record(self):
        """Pins the fact that makes short paper trading non-evidential."""
        rng = np.random.default_rng(9)
        lengths = []
        for sharpe_ann in (0.5, 1.0, 2.0):
            base = rng.normal(0, 1.0, 5000)
            base = (base - base.mean()) / base.std(ddof=1)
            r = base * 0.01 + (sharpe_ann / np.sqrt(252)) * 0.01
            lengths.append(minimum_track_record_length(r))
        assert lengths == sorted(lengths, reverse=True)
        # A Sharpe-1.0 strategy must need well over a 2-month paper window.
        assert lengths[1] > 250

    def test_no_edge_needs_infinite_record(self):
        """A negligible edge must report as indeterminable, not as a huge number."""
        rng = np.random.default_rng(1)
        r = rng.normal(0, 0.01, 1000)
        r = r - r.mean()  # zero mean up to float precision
        assert minimum_track_record_length(r) == float("inf")

    def test_negative_edge_needs_infinite_record(self):
        rng = np.random.default_rng(1)
        assert minimum_track_record_length(rng.normal(-0.001, 0.01, 1000)) == float("inf")


class TestPBO:
    def test_noise_selection_is_uninformative(self):
        """Selecting on noise must show a high probability of overfitting."""
        rng = np.random.default_rng(13)
        matrix = rng.normal(0, 0.01, (504, 16))
        result = probability_of_backtest_overfitting(matrix, n_splits=8)
        assert result.pbo > 0.35, f"noise selection should look overfit, got {result.pbo}"

    def test_persistent_edge_has_low_pbo(self):
        """A config with a real, stable edge must be selected reliably."""
        rng = np.random.default_rng(17)
        matrix = rng.normal(0, 0.01, (504, 10))
        matrix[:, 3] += 0.004  # column 3 has a persistent real edge
        result = probability_of_backtest_overfitting(matrix, n_splits=8)
        assert result.pbo < 0.1, f"real edge should generalise, got {result.pbo}"
        assert not result.is_overfit

    def test_rejects_odd_splits(self):
        rng = np.random.default_rng(1)
        with pytest.raises(ValueError, match="even"):
            probability_of_backtest_overfitting(rng.normal(0, 1, (200, 4)), n_splits=7)

    def test_rejects_single_config(self):
        rng = np.random.default_rng(1)
        with pytest.raises(ValueError, match="at least 2 config"):
            probability_of_backtest_overfitting(rng.normal(0, 1, (200, 1)), n_splits=4)


class TestWalkForward:
    def test_embargo_creates_a_real_gap(self):
        """Pins the leak: adjacent observations share feature/label windows."""
        splits = purged_walk_forward_splits(1000, n_splits=5, embargo=20)
        assert splits
        for s in splits:
            assert s.test_start - s.train_end == 20
            assert s.train_end > s.train_start
            assert s.test_end > s.test_start

    def test_test_windows_are_disjoint_and_ordered(self):
        splits = purged_walk_forward_splits(1000, n_splits=5, embargo=10)
        for a, b in itertools.pairwise(splits):
            assert a.test_end <= b.test_start

    def test_anchored_training_grows_rolling_does_not(self):
        anchored = purged_walk_forward_splits(1000, n_splits=4, embargo=5, anchored=True)
        rolling = purged_walk_forward_splits(1000, n_splits=4, embargo=5, anchored=False)
        assert all(s.train_start == 0 for s in anchored)
        anchored_lengths = [s.train_end - s.train_start for s in anchored]
        assert anchored_lengths == sorted(anchored_lengths)
        assert any(s.train_start > 0 for s in rolling)

    def test_impossible_embargo_is_rejected(self):
        with pytest.raises(ValueError, match="embargo"):
            purged_walk_forward_splits(100, n_splits=5, test_size=15, embargo=50)

    def test_too_few_observations_rejected(self):
        with pytest.raises(ValueError, match="too few"):
            purged_walk_forward_splits(5, n_splits=5)


class TestParameterStability:
    def test_plateau_is_stable(self):
        scores = {(float(a), float(b)): 1.0 for a in range(5) for b in range(5)}
        scores[(2.0, 2.0)] = 1.05
        result = parameter_stability(scores, ["fast", "slow"])
        assert result.is_stable
        assert result.n_neighbours == 4

    def test_lone_spike_is_unstable(self):
        """Pins the curve-fit signature: a single good point amid bad ones."""
        scores = {(float(a), float(b)): 0.05 for a in range(5) for b in range(5)}
        scores[(2.0, 2.0)] = 2.5
        result = parameter_stability(scores, ["fast", "slow"])
        assert not result.is_stable
        assert result.degradation > 0.5

    def test_mismatched_names_rejected(self):
        with pytest.raises(ValueError, match="names"):
            parameter_stability({(1.0, 2.0): 0.5}, ["only_one"])


class TestBootstrapAndPermutation:
    def test_bootstrap_interval_brackets_observed(self):
        rng = np.random.default_rng(21)
        out = block_bootstrap_sharpe(rng.normal(0.001, 0.01, 756), n_resamples=400)
        assert out["ci_lower_5"] <= out["observed"] <= out["ci_upper_95"]

    def test_bootstrap_flags_unreliable_edge(self):
        """A weak edge must show a material probability of being negative."""
        rng = np.random.default_rng(23)
        out = block_bootstrap_sharpe(rng.normal(0.0002, 0.01, 504), n_resamples=400)
        assert out["p_negative"] > 0.1

    def test_permutation_detects_absent_timing_skill(self):
        """Pins the exposure-vs-timing distinction a Sharpe cannot make."""
        rng = np.random.default_rng(31)
        asset = rng.normal(0.0005, 0.01, 1000)
        positions = rng.choice([0.0, 1.0], size=1000)  # random timing
        strategy = positions * asset
        out = permutation_test(strategy, positions, asset, n_permutations=300)
        assert out["p_value"] > 0.05

    def test_permutation_detects_real_timing_skill(self):
        rng = np.random.default_rng(33)
        asset = rng.normal(0.0, 0.01, 1000)
        positions = (asset > 0).astype(float)  # perfect foresight
        strategy = positions * asset
        out = permutation_test(strategy, positions, asset, n_permutations=300)
        assert out["p_value"] < 0.01

    def test_permutation_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="same length"):
            permutation_test(np.zeros(10), np.zeros(10), np.zeros(9))
