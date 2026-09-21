# Can anything here beat the S&P 500?

The bar, stated as numbers rather than a slogan (2004–2026, total return):

| | CAGR | Vol | Sharpe | Max drawdown |
|---|---|---|---|---|
| **SPY** | **10.85%** | 18.6% | 0.58 | −55.2% |
| **QQQ** | **14.92%** | 21.4% | 0.70 | −53.4% |
| IWM | 8.83% | 23.7% | 0.37 | −59.0% |

Four hypotheses have now been tested against real data. **All four are
rejected.** 113 configurations have been evaluated in total.

---

## The measurement change that matters

A strategy returning 8% in a year the index returned 11% has lost. So every
figure below is an **abnormal return** — the stock minus the benchmark over the
identical window — and every one is read against a **placebo** of random dates
run through the same pipeline.

The placebo is not a formality. It is what killed N2.

---

## N4 — Index trend timing ❌

*Hold SPY/QQQ only while above their own N-day average.*

This hypothesis existed because **cost is what kills everything else.** A rule
trading one ETF two to four times a year pays ~10 bps/yr in total, against 45
bps **per trade** for stock-level strategies. An edge can be badly decayed and
still clear that bar.

| | Beats buy-and-hold on return | Median shortfall |
|---|---|---|
| SPY, 6 lookbacks | **0 / 6** | −3.80%/yr |
| QQQ, 6 lookbacks | **0 / 6** | −5.00%/yr |

Zero out of six, on both ETFs, in the full sample **and in both halves
separately**.

The risk-adjusted claim in the literature *does* hold — SPY Sharpe 0.59 → 0.72,
max drawdown −55% → −20%. Those papers are not wrong; they answer a different
question. Turning that Sharpe into a **return** advantage requires leverage:
levered to the index's own volatility it yields +1.27%/yr on SPY and +0.36%/yr
on QQQ — a coin-flip before borrowing costs, and leverage is forbidden by the
mandate.

**Cost was never the binding constraint here.** That makes this the cleanest
rejection in the register: it is about the signal, and a larger account would
not rescue it.

## N2 — Dividend cut drift ❌

*Stocks underperform after cutting their dividend ≥25%.*

1,710 declared cuts across 1,094 tradable symbols. The headline looked real:

> −1.12% median abnormal return at 63 days, clustered t = **−2.31**

A placebo of random dates in the same universe shows **−1.03%**. The excess is
**−0.09%**. At 126 and 252 days, cutters do *better* than the placebo (+0.60%,
+3.07%).

**The significance was measuring the universe's drift against SPY, not the
event.** Without the placebo this would have been written up as a genuine
effect with a significant t-statistic.

Two pre-registered kill criteria also fired:

- **Split-half sign flip** at 2 of 5 horizons (126d: +2.15% early, −2.82% late)
- **The cut-size gradient runs backwards, monotonically:**

| Cut size | 25–40% | 40–60% | 60–80% | 80–100% |
|---|---|---|---|---|
| Mean CAR, 63d | −3.24% | −0.90% | +0.15% | **+0.88%** |

The most severe cuts perform *best*. That is the mechanism's own prediction
inverted — stronger evidence against it than a null result would have been.

## N3 — Dividend initiation drift ❌

*Stocks outperform after initiating their first dividend* — the forced-**buyer**
mirror, and the one that actually fits a long-only mandate.

276 genuine initiations (2,056 of 2,332 first-observed dividends discarded
because the company may have been paying before its data begins).

| Horizon | Excess over placebo | Clustered t |
|---|---|---|
| 63d | −1.33% | −2.49 |
| 252d | −4.19% | −2.99 |

**The sign is backwards.** Initiators underperform, consistently, in both
sample halves.

There is a plausible story — initiating a dividend signals a company has run
out of things to invest in, a life-cycle maturity signal rather than a
confidence signal. **It is not claimed as an edge and must not be.** Flipping a
hypothesis's sign after seeing the data, in a 276-event sample, is exactly the
data mining this protocol exists to prevent. If it is worth anything, it is
worth pre-registering and testing on data this study never touched.

## N1 — Cash merger arbitrage ❌

Rejected earlier: −2.45% expected value per resolved event. Positive headline
at every threshold tested (+1.43% to +6.59%) while the honest resolved-events
subset was negative at every one.

---

## Two data findings, both now fixed in code

**SPY is the wrong benchmark for a survivorship-free universe.** A placebo
across all liquidity drifts −3.11% at 126 days *with no event at all*, because
the median small stock underperforms a cap-weighted index. Split by liquidity,
the same placebo is +0.95% at 63d in the top third and −3.84% in the middle.

The liquidity screen fixes the mismatch and matches reality: at €10k with a
45bps budget, the illiquid two thirds were never tradable. **A result measured
where you cannot trade is not a result.**

**183 liquid symbols carry impossible prices** from failed split adjustments —
POW_OLD "moves" from $0.005 to $15,600 in one session, 312 million percent. One
such series put **+562%** into a placebo mean whose median was −0.67%. Series
with a >500% single-session move are now rejected at load, and every result
reports a trimmed mean beside the raw one.

---

## What the arithmetic actually says

The register's best *screened* candidate is H7 spin-offs at **3.56%/yr net**.
Against QQQ's 14.92%, nothing we have is within a factor of four.

**That is not the right comparison, and it is worth being precise about why.**

You do not beat a 15%/yr benchmark by finding a 3% edge and abandoning the
benchmark. **Alpha adds to beta.** The question is not "does the strategy beat
QQQ" but "does holding QQQ *plus* the strategy beat holding QQQ alone":

| | 2% edge | 3% edge | 5% edge |
|---|---|---|---|
| on 30% of capital | +0.60%/yr | +0.90%/yr | +1.50%/yr |

A 3% edge deployed on 30% of capital beats the index by 0.9%/yr. Real, and it
compounds. It is also nothing like "beat the Nasdaq," and pretending otherwise
is how people end up with a concentrated portfolio and a worse outcome than an
ETF.

**We do not yet have the 3% edge.**

---

## The deflation bar, now

113 configurations evaluated. Over three years of daily data, a **worthless**
strategy is expected to show an annualised Sharpe of **1.49** purely from
selection across that many trials. Over twenty years, **0.58**.

Every future result is measured against that, and the bar rises with each test.
This is the cost of searching, and it is why the register discards rather than
accumulates.

```bash
.venv/bin/python -m tradelab.cli ledger
```
