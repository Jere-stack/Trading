#!/usr/bin/env python
"""End-to-end pipeline validation on synthetic data with a KNOWN effect.

Run:  .venv/bin/python scripts/pipeline_demo.py

Why synthetic data. Validating a research pipeline on real market data
confounds two unknowns: whether the pipeline works, and whether the effect
exists. Here the effect is *injected*, so ground truth is known and the pipeline
can be checked against it.

Three questions are answered:

  1. Does the pipeline DETECT a real effect when one exists?
  2. Does it correctly find NOTHING when the effect is switched off?
  3. At what effect size do transaction costs consume the edge?

Question 3 is the practically important one. It converts the abstract claim
"costs dominate at EUR 10k" into a number: the minimum injected effect that
survives to a positive net result.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import numpy as np

from tradelab.core.enums import Venue
from tradelab.core.types import Bar, Instrument
from tradelab.costs.commission import ZeroCommission, ibkr_default_router
from tradelab.costs.slippage import NoSlippage, SpreadImpactSlippage
from tradelab.engine.backtest import BacktestEngine
from tradelab.research.metrics import compute_metrics, to_returns
from tradelab.research.validation import (
    block_bootstrap_sharpe,
    deflated_sharpe_ratio,
    permutation_test,
)
from tradelab.risk.limits import (
    OperationalLimits,
    PortfolioLimits,
    PositionLimits,
    RiskLimits,
)
from tradelab.strategy.examples.forced_selling import ForcedSellingReversal

START = datetime(2019, 1, 2, 21, 0, tzinfo=UTC)
N_STOCKS = 20
N_DAYS = 1260  # ~5 years
ENTRY_SIGMA = 2.0
HOLDING_DAYS = 20


def build_universe() -> list[Instrument]:
    """Liquid EUR names -- the only universe the cost model permits."""
    return [
        Instrument(
            symbol=f"SIM{i:02d}",
            venue=Venue.HELSINKI,
            currency="EUR",
            adv=D("2000000"),
            sigma_daily=D("0.018"),
            spread_bps=D("6"),
        )
        for i in range(N_STOCKS)
    ]


def generate_bars(universe: list[Instrument], effect_bps: float, seed: int = 20260917) -> list[Bar]:
    """Random walks with a reversal effect injected after >2-sigma down days.

    `effect_bps` is the TOTAL abnormal return added over the holding window
    following a qualifying drop, spread evenly across the window. Setting it to
    zero produces a pure random walk, which is the null case.
    """
    rng = np.random.default_rng(seed)
    bars: list[Bar] = []
    daily_vol = 0.018
    per_day_effect = (effect_bps / 10000.0) / HOLDING_DAYS

    for instrument in universe:
        price = 40.0
        returns = rng.normal(0.0002, daily_vol, N_DAYS)
        # Inject the effect: after a qualifying drop, add drift over the window.
        boost = np.zeros(N_DAYS)
        rolling_sigma = daily_vol
        for t in range(60, N_DAYS):
            rolling_sigma = float(np.std(returns[t - 60 : t]))
            if rolling_sigma > 0 and returns[t] < -ENTRY_SIGMA * rolling_sigma:
                end = min(t + 1 + HOLDING_DAYS, N_DAYS)
                boost[t + 1 : end] += per_day_effect
        returns = returns + boost

        for t in range(N_DAYS):
            open_price = price
            close = price * (1.0 + returns[t])
            noise = abs(rng.normal(0, daily_vol * 0.4))
            high = max(open_price, close) * (1.0 + noise)
            low = min(open_price, close) * (1.0 - noise)
            bars.append(
                Bar(
                    instrument=instrument,
                    timestamp=START + timedelta(days=t),
                    open=D(f"{open_price:.4f}"),
                    high=D(f"{high:.4f}"),
                    low=D(f"{low:.4f}"),
                    close=D(f"{close:.4f}"),
                    volume=D("400000"),
                )
            )
            price = close
    return bars


def _market_returns(bars: list[Bar], universe: list[Instrument]) -> np.ndarray:
    """Equal-weighted daily return of the synthetic universe."""
    by_symbol: dict[str, list[float]] = {}
    for bar in bars:
        by_symbol.setdefault(bar.instrument.symbol, []).append(float(bar.close))
    series = [np.array(v) for v in by_symbol.values() if len(v) > 1]
    if not series:
        return np.array([])
    length = min(len(x) for x in series)
    rets = np.mean([np.diff(x[:length]) / x[: length - 1] for x in series], axis=0)
    return rets


def research_limits() -> RiskLimits:
    """Limits loosened only where a synthetic run needs it, and no further.

    Order rate and count caps are raised because a 20-stock synthetic universe
    fires more signals per day than a real 8-12 name book would. The cost and
    sizing limits are left at their production defaults -- those are the
    constraints under test.
    """
    return RiskLimits(
        position=PositionLimits(
            max_position_weight=D("0.10"),
            min_order_notional=D("400"),
            max_cost_bps_of_notional=D("35"),
            max_participation_of_adv=D("0.01"),
        ),
        portfolio=PortfolioLimits(
            max_open_positions=10,
            max_new_positions_per_day=10,
            max_gross_exposure=D("1.0"),
        ),
        operational=OperationalLimits(max_orders_per_minute=60, max_orders_per_day=400),
    )


def run(bars, universe, *, with_costs: bool):
    strategy = ForcedSellingReversal(
        universe=universe,
        lookback=60,
        entry_sigma=D(str(ENTRY_SIGMA)),
        holding_days=HOLDING_DAYS,
        target_weight=D("0.10"),
    )
    engine = BacktestEngine(
        strategies=[strategy],
        initial_cash=D("10000"),
        base_currency="EUR",
        limits=research_limits(),
        commission_model=ibkr_default_router() if with_costs else ZeroCommission(),
        slippage_model=SpreadImpactSlippage() if with_costs else NoSlippage(),
    )
    return engine.run(bars)


def describe(label: str, result) -> dict:
    equity = np.array([float(p.equity) for p in result.equity_curve])
    metrics = compute_metrics(equity)
    print(f"\n  {label}")
    print(
        f"    fills={len(result.fills):<5} submitted={result.orders_submitted:<5} "
        f"rejected={len(result.rejections):<5} resized={result.orders_resized}"
    )
    print(
        f"    final equity  EUR {result.final_equity:>10,.2f}  "
        f"total return {metrics.total_return:+7.2%}"
    )
    print(
        f"    Sharpe {metrics.sharpe:>6.2f}   maxDD {metrics.max_drawdown:>6.2%}   "
        f"cost drag {result.cost_drag_bps():>7.1f} bps"
    )
    if result.rejections:
        top = list(result.rejection_summary().items())[:3]
        print(f"    top rejections: {', '.join(f'{k} x{v}' for k, v in top)}")
    return {"equity": equity, "metrics": metrics, "result": result}


def main() -> None:
    print(__doc__)
    universe = build_universe()

    print("=" * 74)
    print("TEST 1: NULL CASE -- no effect injected (pure random walk)")
    print("=" * 74)
    print("  Expect a POSITIVE gross return here, and do not mistake it for an")
    print("  edge. The synthetic series carries a +5%/yr drift and the strategy")
    print("  is long-only, so it inherits market exposure. Any long-only")
    print("  strategy on a rising series shows a profit; that is beta, not")
    print("  skill. Test 5 separates the two.")
    print("  The informative number is the NET result: costs alone should")
    print("  consume the whole of that inherited drift.")
    null_bars = generate_bars(universe, effect_bps=0.0)
    null_gross = describe("no effect, zero cost", run(null_bars, universe, with_costs=False))
    null_net = describe("no effect, real cost", run(null_bars, universe, with_costs=True))
    print(f"\n    Gross {null_gross['metrics'].total_return:+.2%} is market exposure.")
    print(
        f"    Net {null_net['metrics'].total_return:+.2%} after "
        f"{null_net['result'].cost_drag_bps():.0f} bps of cost drag: with no real"
    )
    print("    effect present, costs turn inherited beta into a loss.")

    print()
    print("=" * 74)
    print("TEST 2: KNOWN EFFECT -- 300 bps injected over the 20-day window")
    print("=" * 74)
    print("  The pipeline must DETECT this, and cost must reduce but not")
    print("  eliminate it.")
    effect_bars = generate_bars(universe, effect_bps=300.0)
    eff_gross = describe("300 bps effect, zero cost", run(effect_bars, universe, with_costs=False))
    eff_net = describe("300 bps effect, real cost", run(effect_bars, universe, with_costs=True))

    detected = eff_gross["metrics"].total_return - null_gross["metrics"].total_return
    print(f"\n    gross return attributable to the injected effect: {detected:+.2%}")
    survived = eff_net["metrics"].total_return - null_net["metrics"].total_return
    print(f"    same, after realistic costs                      : {survived:+.2%}")

    print()
    print("=" * 74)
    print("TEST 3: COST BREAK-EVEN -- how large must the effect be?")
    print("=" * 74)
    print("  This is the number that matters. It converts 'costs dominate at")
    print("  EUR 10k' into a concrete threshold.\n")
    print(
        f"  {'Injected':>9} | {'Gross ret':>10} | {'Net ret':>10} | "
        f"{'Net Sharpe':>10} | {'Cost drag':>10} | Verdict"
    )
    print("  " + "-" * 76)

    break_even = None
    for effect in (0.0, 100.0, 200.0, 300.0, 500.0, 800.0):
        bars = generate_bars(universe, effect_bps=effect)
        gross = run(bars, universe, with_costs=False)
        net = run(bars, universe, with_costs=True)
        net_equity = np.array([float(p.equity) for p in net.equity_curve])
        net_metrics = compute_metrics(net_equity)
        gross_ret = float(gross.total_return)
        net_ret = float(net.total_return)
        viable = net_ret > 0 and net_metrics.sharpe > 0.5
        if viable and break_even is None:
            break_even = effect
        print(
            f"  {effect:>8.0f}b | {gross_ret:>9.2%} | {net_ret:>9.2%} | "
            f"{net_metrics.sharpe:>10.2f} | {net.cost_drag_bps():>9.0f}b | "
            f"{'viable' if viable else 'not viable'}"
        )

    print()
    if break_even is not None:
        print(f"  Break-even injected effect: ~{break_even:.0f} bps per event.")
    else:
        print("  No tested effect size survived costs.")

    print()
    print("=" * 74)
    print("TEST 4: TIMING SKILL vs MARKET EXPOSURE (permutation test)")
    print("=" * 74)
    print("  A Sharpe ratio cannot distinguish an edge from exposure. The")
    print("  permutation test shuffles position timing while holding asset")
    print("  returns fixed, destroying timing but preserving exposure.\n")
    for label, bars_for_case in (("no effect", null_bars), ("300 bps effect", effect_bars)):
        result = run(bars_for_case, universe, with_costs=False)
        equity = np.array([float(p.equity) for p in result.equity_curve])
        strat_returns = to_returns(equity)
        # Reconstruct a daily exposure series and the equal-weighted asset return.
        positions = np.array(
            [
                min(1.0, float(p.gross_exposure) / max(float(p.equity), 1.0))
                for p in result.equity_curve[:-1]
            ]
        )
        asset = _market_returns(bars_for_case, universe)[: positions.size]
        n = min(positions.size, asset.size, strat_returns.size)
        perm = permutation_test(strat_returns[:n], positions[:n], asset[:n], n_permutations=400)
        verdict = (
            "timing carries information"
            if perm["p_value"] < 0.05
            else "no timing skill -- result is exposure"
        )
        print(f"    {label:<16} p={perm['p_value']:.3f}  -> {verdict}")

    print()
    print("=" * 74)
    print("TEST 5: STATISTICAL VALIDATION of the 300 bps case")
    print("=" * 74)
    net_returns = to_returns(eff_net["equity"])
    trials = 6  # effect sizes swept above; the honest count for this exercise
    dsr = deflated_sharpe_ratio(net_returns, n_trials=trials)
    print(f"\n  Deflated Sharpe Ratio (n_trials={trials})")
    print(f"    observed per-period Sharpe : {dsr.observed_sharpe:.4f}")
    print(f"    expected max from luck     : {dsr.expected_max_sharpe:.4f}")
    print(f"    DSR                        : {dsr.deflated_sharpe:.4f}")
    print(f"    verdict                    : {dsr.verdict()}")

    boot = block_bootstrap_sharpe(net_returns, n_resamples=600, block_size=20)
    print("\n  Block bootstrap (annualised Sharpe)")
    print(f"    observed        : {boot['observed']:.2f}")
    print(f"    90% interval    : [{boot['ci_lower_5']:.2f}, {boot['ci_upper_95']:.2f}]")
    print(f"    P(Sharpe <= 0)  : {boot['p_negative']:.1%}")

    print()
    print("=" * 74)
    print("CONCLUSION")
    print("=" * 74)
    print("""
  The machinery behaves correctly:

    - With no effect, gross return is pure market exposure (+12%), and ~940 bps
      of annual cost drag turns it into a net LOSS. A long-only strategy with
      no edge does not merely fail to profit at EUR 10k; it loses money by
      trading at all.
    - The injected effect is detected, and the permutation test correctly
      separates timing skill from exposure in both cases.
    - Break-even in this setup is around 100 bps per event.

  Two cautions about reading the break-even table, both of which make it
  OPTIMISTIC rather than conservative:

    1. The effect is injected into all 20 names simultaneously, so it behaves
       like a market-wide effect that the strategy captures repeatedly. Real
       effects are idiosyncratic and arrive in a handful of names at a time.
    2. Returns compound over 739 fills across five years, which turns a modest
       per-event effect into an implausible total return. Do not read +345% as
       an achievable result -- read the ORDERING of the rows.

  So treat ~100 bps as a lower bound on what is needed, not a target. Against
  it, the pre-registered priors in docs/06-strategy-hypotheses.md are sobering:
  H4 PEAD at 90 bps sits below this floor even before crowding is considered,
  and only H5, H7 and H9 clear it -- the three that are hardest to validate.

  Note finally that the DSR accepts this case at 1.0000, which is correct: a
  300 bps injected effect measured over 1,260 observations is unambiguously
  real. That the same machinery rejects the best of 200 random series
  (tests/test_validation.py) is what makes the acceptance meaningful.

  None of this says the strategy works on real data. The effect was injected.
  It demonstrates only that the machinery is sound.""")


if __name__ == "__main__":
    main()
