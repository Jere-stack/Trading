"""Statistical validation: the machinery for discarding strategies.

The premise of this module is that a good backtest is weak evidence. If you
test 200 variants and report the best, its Sharpe is mostly selection bias --
the expected maximum Sharpe of 200 *worthless* strategies over 3 years of daily
data is around 0.9. Reporting that number as an edge is the single most common
error in retail quant research, and it is the reason the default posture here is
rejection.

The tools, and what each one defends against:

* `deflated_sharpe_ratio` -- selection bias from multiple testing. Discounts an
  observed Sharpe by the expected maximum under the null given how many trials
  were actually run. Requires honesty about the trial count; understating it
  defeats the purpose.
* `probability_of_backtest_overfitting` -- in-sample selection that does not
  generalise. Via CSCV: if the configuration that wins in-sample lands in the
  bottom half out-of-sample more often than not, the selection process itself is
  overfitting regardless of any individual result.
* `minimum_track_record_length` -- answers "how long must I paper trade before
  this Sharpe is distinguishable from zero?" Usually a sobering number, and
  directly relevant to the 1-2 month paper trading plan.
* `purged_walk_forward_splits` -- lookahead leaking through overlapping labels.
  Purging plus an embargo removes observations adjacent to the test set.
* `parameter_stability` -- a fragile optimum. An edge that only exists at one
  parameter value is a curve fit; a real one degrades smoothly.
* `block_bootstrap_sharpe` / `permutation_test` -- distributional confidence
  without assuming normal iid returns, which financial returns are not.

References: Bailey & Lopez de Prado (2014) on the Deflated and Probabilistic
Sharpe Ratio; Bailey, Borwein, Lopez de Prado & Zhu (2016) on backtest
overfitting and CSCV; Lopez de Prado (2018) on purged cross-validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy import stats

EULER_MASCHERONI = 0.5772156649015329


# --------------------------------------------------------------- deflated SR


@dataclass(frozen=True)
class DeflatedSharpeResult:
    observed_sharpe: float
    expected_max_sharpe: float
    deflated_sharpe: float
    n_trials: int
    n_observations: int
    skew: float
    kurtosis: float

    @property
    def is_significant(self) -> bool:
        """Conventional 95% threshold. A gate, not a conclusion."""
        return self.deflated_sharpe > 0.95

    def verdict(self) -> str:
        if self.deflated_sharpe > 0.95:
            return "survives deflation at 95% -- worth paper trading"
        if self.deflated_sharpe > 0.80:
            return "marginal -- indistinguishable from selection bias; do not risk capital"
        return "fails deflation -- consistent with luck across the trials run"


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Expected maximum Sharpe across `n_trials` independent worthless strategies.

    This is the benchmark a real edge must beat. It grows with the number of
    trials, which is why "I tested 500 combinations and this was best" is
    evidence *against* a strategy unless it clears this bar.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if n_trials == 1:
        return 0.0
    sd = math.sqrt(max(sharpe_variance, 0.0))
    if sd == 0:
        return 0.0
    g = EULER_MASCHERONI
    a = stats.norm.ppf(1.0 - 1.0 / n_trials)
    b = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(sd * ((1.0 - g) * a + g * b))


def deflated_sharpe_ratio(
    returns: np.ndarray,
    n_trials: int,
    sharpe_variance: float | None = None,
    trial_sharpes: np.ndarray | None = None,
) -> DeflatedSharpeResult:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado).

    `returns` must be **per-period, not annualised** -- the deflation operates
    on the raw per-observation Sharpe.

    `n_trials` is the number of configurations actually evaluated, including the
    ones discarded. Pass the honest count; this is the input people fudge.

    `sharpe_variance` is the variance of Sharpe across trials. If `trial_sharpes`
    is supplied it is measured from them, which is far better than assuming.
    """
    returns = np.asarray(returns, dtype=float)
    n = returns.size
    if n < 3:
        raise ValueError(f"need at least 3 observations to deflate, got {n}")

    sd = returns.std(ddof=1)
    if sd == 0:
        return DeflatedSharpeResult(0.0, 0.0, 0.0, n_trials, n, 0.0, 3.0)
    sr = float(returns.mean() / sd)

    centred = returns - returns.mean()
    skew = float((centred**3).mean() / sd**3)
    kurt = float((centred**4).mean() / sd**4)

    if trial_sharpes is not None:
        trials = np.asarray(trial_sharpes, dtype=float)
        variance = float(trials.var(ddof=1)) if trials.size > 1 else 0.0
    elif sharpe_variance is not None:
        variance = float(sharpe_variance)
    else:
        # Fallback: variance of the Sharpe estimator itself under the null.
        variance = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2) / (n - 1)

    sr0 = expected_max_sharpe(n_trials, variance)

    denominator = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if denominator <= 0:
        # Non-normality severe enough to break the adjustment; refuse to report
        # a number rather than emit a misleading one.
        return DeflatedSharpeResult(sr, sr0, 0.0, n_trials, n, skew, kurt)

    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(denominator)
    return DeflatedSharpeResult(
        observed_sharpe=sr,
        expected_max_sharpe=sr0,
        deflated_sharpe=float(stats.norm.cdf(z)),
        n_trials=n_trials,
        n_observations=n,
        skew=skew,
        kurtosis=kurt,
    )


def probabilistic_sharpe_ratio(returns: np.ndarray, benchmark_sharpe: float = 0.0) -> float:
    """Probability that the true per-period Sharpe exceeds `benchmark_sharpe`."""
    returns = np.asarray(returns, dtype=float)
    n = returns.size
    if n < 3:
        raise ValueError("need at least 3 observations")
    sd = returns.std(ddof=1)
    if sd == 0:
        return 0.0
    sr = float(returns.mean() / sd)
    centred = returns - returns.mean()
    skew = float((centred**3).mean() / sd**3)
    kurt = float((centred**4).mean() / sd**4)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if denom <= 0:
        return 0.0
    z = (sr - benchmark_sharpe) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(stats.norm.cdf(z))


def minimum_track_record_length(
    returns: np.ndarray,
    benchmark_sharpe: float = 0.0,
    confidence: float = 0.95,
    tolerance: float = 1e-6,
) -> float:
    """Observations needed for the Sharpe to clear `benchmark_sharpe` at `confidence`.

    Directly answers how long to paper trade. The answer is frequently longer
    than the intended 1-2 months, which is itself the useful finding: a short
    paper period cannot validate a modest edge, it can only catch
    implementation bugs. Both are worth doing; only one is statistical evidence.

    Returns infinity when the observed edge is at or below the benchmark, or
    within `tolerance` of it, since no finite record would establish it.
    """
    returns = np.asarray(returns, dtype=float)
    if returns.size < 3:
        raise ValueError("need at least 3 observations")
    sd = returns.std(ddof=1)
    if sd == 0:
        return float("inf")
    sr = float(returns.mean() / sd)
    # Treat an edge within `tolerance` of the benchmark as indeterminable rather
    # than returning an astronomically large finite number. A per-period Sharpe
    # of 1e-6 is ~1.6e-5 annualised; whether float residue leaves it marginally
    # positive or negative is noise, and "2.1e34 days" is not a usable answer.
    if sr - benchmark_sharpe <= tolerance:
        return float("inf")
    centred = returns - returns.mean()
    skew = float((centred**3).mean() / sd**3)
    kurt = float((centred**4).mean() / sd**4)
    z = stats.norm.ppf(confidence)
    numerator = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    return float(1.0 + numerator * (z / (sr - benchmark_sharpe)) ** 2)


# ------------------------------------------------------------------- CSCV/PBO


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    n_splits: int
    n_configs: int
    in_sample_sharpes: list[float]
    out_of_sample_sharpes: list[float]
    logits: list[float]

    @property
    def is_overfit(self) -> bool:
        return self.pbo > 0.5

    def verdict(self) -> str:
        if self.pbo <= 0.10:
            return "selection generalises well"
        if self.pbo <= 0.30:
            return "some overfitting -- treat the chosen config as one of many"
        if self.pbo <= 0.50:
            return "substantial overfitting -- selection is barely informative"
        return "selection is overfitting; the in-sample winner underperforms out of sample"


def probability_of_backtest_overfitting(
    returns_matrix: np.ndarray, n_splits: int = 10
) -> PBOResult:
    """PBO via Combinatorially Symmetric Cross-Validation.

    `returns_matrix` is (T observations x N configurations). Every configuration
    tried must be included -- PBO measures whether the *selection procedure*
    generalises, so omitting the losers makes the result meaningless.

    Method: split the timeline into `n_splits` blocks, take every balanced
    combination as in-sample with its complement as out-of-sample, pick the
    best config in-sample, and record its relative rank out-of-sample. PBO is
    the frequency with which that rank falls below the median.
    """
    matrix = np.asarray(returns_matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError(f"expected a 2-D (T x N) matrix, got shape {matrix.shape}")
    t, n_configs = matrix.shape
    if n_configs < 2:
        raise ValueError("PBO needs at least 2 configurations to choose between")
    if n_splits % 2 != 0:
        raise ValueError("n_splits must be even so blocks split in half evenly")
    if t < n_splits * 2:
        raise ValueError(f"need at least {n_splits * 2} observations for {n_splits} splits")

    blocks = np.array_split(np.arange(t), n_splits)
    half = n_splits // 2
    is_sharpes: list[float] = []
    oos_sharpes: list[float] = []
    logits: list[float] = []

    for combo in combinations(range(n_splits), half):
        is_idx = np.concatenate([blocks[i] for i in combo])
        oos_idx = np.concatenate([blocks[i] for i in range(n_splits) if i not in combo])

        is_perf = _sharpe_columns(matrix[is_idx])
        oos_perf = _sharpe_columns(matrix[oos_idx])

        best = int(np.nanargmax(is_perf))
        is_sharpes.append(float(is_perf[best]))
        oos_sharpes.append(float(oos_perf[best]))

        # Relative rank of the in-sample winner within the OOS distribution.
        order = np.argsort(oos_perf)
        rank = int(np.where(order == best)[0][0]) + 1
        omega = rank / (n_configs + 1)
        omega = min(max(omega, 1e-9), 1 - 1e-9)
        logits.append(float(math.log(omega / (1 - omega))))

    pbo = float(np.mean([1.0 if lg <= 0 else 0.0 for lg in logits]))
    return PBOResult(
        pbo=pbo,
        n_splits=n_splits,
        n_configs=n_configs,
        in_sample_sharpes=is_sharpes,
        out_of_sample_sharpes=oos_sharpes,
        logits=logits,
    )


def _sharpe_columns(block: np.ndarray) -> np.ndarray:
    mean = block.mean(axis=0)
    sd = block.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(sd > 0, mean / sd, -np.inf)
    return np.nan_to_num(out, nan=-np.inf)


# ------------------------------------------------------------- walk-forward


@dataclass(frozen=True)
class Split:
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_slice(self) -> slice:
        return slice(self.train_start, self.train_end)

    @property
    def test_slice(self) -> slice:
        return slice(self.test_start, self.test_end)

    def __str__(self) -> str:
        return f"train[{self.train_start}:{self.train_end}] test[{self.test_start}:{self.test_end}]"


def purged_walk_forward_splits(
    n_observations: int,
    n_splits: int = 5,
    test_size: int | None = None,
    embargo: int = 0,
    anchored: bool = True,
) -> list[Split]:
    """Walk-forward splits with a purge gap and embargo between train and test.

    The embargo is not ceremony. Any feature computed over a lookback window, or
    any label realised over a holding period, makes observations adjacent to the
    split boundary contain information from both sides. Without a gap, a
    20-day-lookback feature leaks up to 20 days of test data into training.

    Set `embargo` to at least the longest of (feature lookback, holding period).

    `anchored=True` grows the training window from the start (more data, but
    older regimes retain weight). `anchored=False` uses a rolling window of
    fixed length (adapts to regime change, less data). Run both: a strategy that
    only works anchored is usually relying on a single historical episode.
    """
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")
    if n_observations < n_splits * 2:
        raise ValueError(f"{n_observations} observations is too few for {n_splits} splits")

    if test_size is None:
        test_size = n_observations // (n_splits + 1)
    if test_size < 1:
        raise ValueError("test_size must be >= 1")

    splits: list[Split] = []
    first_train = n_observations - n_splits * test_size
    if first_train - embargo < 1:
        raise ValueError(
            f"embargo {embargo} plus {n_splits} test windows of {test_size} leaves no "
            f"training data in {n_observations} observations"
        )

    for i in range(n_splits):
        test_start = first_train + i * test_size
        test_end = min(test_start + test_size, n_observations)
        train_end = test_start - embargo
        train_start = 0 if anchored else max(0, train_end - first_train)
        if train_end - train_start < 1 or test_end <= test_start:
            continue
        splits.append(Split(train_start, train_end, test_start, test_end))
    return splits


# ----------------------------------------------------- stability & bootstrap


@dataclass(frozen=True)
class StabilityResult:
    best_params: dict[str, float]
    best_score: float
    neighbour_mean: float
    neighbour_std: float
    degradation: float
    n_neighbours: int

    @property
    def is_stable(self) -> bool:
        """Stable when neighbours retain most of the optimum's performance.

        A sharp peak surrounded by poor results is a curve fit: the parameter
        was selected to match noise. A real effect has a plateau, because the
        underlying mechanism does not change discontinuously at an arbitrary
        threshold.
        """
        if self.best_score <= 0:
            return False
        return self.degradation < 0.5 and self.neighbour_mean > 0


def parameter_stability(
    scores: dict[tuple[float, ...], float], param_names: list[str]
) -> StabilityResult:
    """Assess whether the best parameter set sits on a plateau or a spike.

    `scores` maps a parameter tuple to its score (higher is better). Neighbours
    are the points adjacent along each axis in the sampled grid.
    """
    if not scores:
        raise ValueError("no scores supplied")
    keys = list(scores)
    arity = len(keys[0])
    if any(len(k) != arity for k in keys):
        raise ValueError("all parameter tuples must have the same length")
    if len(param_names) != arity:
        raise ValueError(f"{len(param_names)} names for {arity}-tuple parameters")

    axes = [sorted({k[i] for k in keys}) for i in range(arity)]
    best_key = max(scores, key=lambda k: scores[k])
    best_score = scores[best_key]

    neighbours: list[float] = []
    for axis, values in enumerate(axes):
        pos = values.index(best_key[axis])
        for offset in (-1, 1):
            j = pos + offset
            if 0 <= j < len(values):
                candidate = list(best_key)
                candidate[axis] = values[j]
                score = scores.get(tuple(candidate))
                if score is not None:
                    neighbours.append(score)

    if not neighbours:
        return StabilityResult(
            best_params=dict(zip(param_names, best_key, strict=True)),
            best_score=best_score,
            neighbour_mean=float("nan"),
            neighbour_std=float("nan"),
            degradation=float("nan"),
            n_neighbours=0,
        )

    arr = np.asarray(neighbours, dtype=float)
    mean = float(arr.mean())
    degradation = (best_score - mean) / abs(best_score) if best_score != 0 else float("inf")
    return StabilityResult(
        best_params=dict(zip(param_names, best_key, strict=True)),
        best_score=best_score,
        neighbour_mean=mean,
        neighbour_std=float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        degradation=float(degradation),
        n_neighbours=int(arr.size),
    )


def block_bootstrap_sharpe(
    returns: np.ndarray,
    n_resamples: int = 2000,
    block_size: int = 20,
    periods_per_year: int = 252,
    seed: int | None = 42,
) -> dict[str, float]:
    """Bootstrap Sharpe confidence intervals using overlapping blocks.

    Blocks rather than iid resampling because financial returns exhibit
    volatility clustering and autocorrelation; iid bootstrap understates the
    interval width, which flatters the strategy.
    """
    returns = np.asarray(returns, dtype=float)
    n = returns.size
    if n < block_size * 2:
        raise ValueError(f"need at least {block_size * 2} observations for block bootstrap")

    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block_size))
    samples = np.empty(n_resamples, dtype=float)
    max_start = n - block_size

    for i in range(n_resamples):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        drawn = np.concatenate([returns[s : s + block_size] for s in starts])[:n]
        sd = drawn.std(ddof=1)
        samples[i] = (drawn.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else 0.0

    observed_sd = returns.std(ddof=1)
    observed = (
        float(returns.mean() / observed_sd * np.sqrt(periods_per_year)) if observed_sd > 0 else 0.0
    )
    return {
        "observed": observed,
        "mean": float(samples.mean()),
        "std": float(samples.std(ddof=1)),
        "ci_lower_5": float(np.quantile(samples, 0.05)),
        "ci_upper_95": float(np.quantile(samples, 0.95)),
        "p_negative": float((samples <= 0).mean()),
    }


def permutation_test(
    strategy_returns: np.ndarray,
    positions: np.ndarray,
    asset_returns: np.ndarray,
    n_permutations: int = 1000,
    seed: int | None = 42,
) -> dict[str, float]:
    """Test whether signal *timing* carries information.

    Shuffles the position series while keeping asset returns fixed, which
    destroys timing but preserves both the return distribution and the strategy's
    exposure profile. If shuffled timing performs as well as the real thing, the
    result came from exposure, not from the signal -- a distinction a Sharpe
    ratio cannot make.
    """
    positions = np.asarray(positions, dtype=float)
    asset_returns = np.asarray(asset_returns, dtype=float)
    if positions.size != asset_returns.size:
        raise ValueError(
            f"positions ({positions.size}) and asset_returns ({asset_returns.size}) "
            "must be the same length"
        )
    observed_arr = np.asarray(strategy_returns, dtype=float)
    observed_sd = observed_arr.std(ddof=1)
    observed = float(observed_arr.mean() / observed_sd) if observed_sd > 0 else 0.0

    rng = np.random.default_rng(seed)
    null = np.empty(n_permutations, dtype=float)
    for i in range(n_permutations):
        shuffled = rng.permutation(positions)
        r = shuffled * asset_returns
        sd = r.std(ddof=1)
        null[i] = (r.mean() / sd) if sd > 0 else 0.0

    return {
        "observed_sharpe": observed,
        "null_mean": float(null.mean()),
        "null_std": float(null.std(ddof=1)),
        "p_value": float((null >= observed).mean()),
        "percentile": float((null < observed).mean()),
    }
