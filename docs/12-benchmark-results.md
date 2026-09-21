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

---

# Round 2: the published strategies, tested

Sourced from the replication literature rather than from a backtest list,
because the base rate for "strategies from papers" is measurable and dismal:

- **Hou, Xue & Zhang (2020)**: 65% of 452 published anomalies fail a t>1.96
  hurdle; 82% fail at t>2.78.
- **McLean & Pontiff (2016)**: post-publication returns run ~26% below
  in-sample.
- **paperswithbacktest**, across 4,843 backtested papers: **median annualised
  return 1.5%**.

Two candidates were killed on published evidence without spending a test:
**volatility-managed portfolios** (Cederburg et al. show out-of-sample failure;
Barroso & Detzel show costs kill it) and the **low-risk family's
absolute-return claim** (lower returns than the market in most decades — higher
Sharpe only).

## Results — 165 monthly rebalances, 2012-10 to 2026-09

Point-in-time liquid universe, survivorship-free, prices screened, 45bps on the
traded fraction, 20 names, long-only.

| | CAGR | Vol | maxDD | Turnover | Cost | vs SPY | vs QQQ |
|---|---|---|---|---|---|---|---|
| N5 low idiosyncratic vol | 10.63% | 16.1% | −31.6% | 33% | 1.96% | **−4.24%** | −9.33% |
| N6 low total vol | 3.30% | 11.6% | −29.3% | 36% | 2.03% | **−11.57%** | −16.66% |
| N7 momentum 12-1 | 13.78% | 42.1% | −52.0% | 55% | 3.16% | **−1.09%** | −6.18% |
| N8 52-week high | 8.70% | 18.9% | −39.7% | 96% | 5.65% | **−6.17%** | −11.27% |
| *(control)* high vol | −5.18% | 44.6% | −85.9% | 36% | 1.86% | −20.05% | −25.14% |
| *(control)* momentum losers | −13.61% | 59.2% | −92.8% | 53% | 2.39% | −28.48% | −33.57% |
| **BUY & HOLD SPY** | **14.87%** | 18.6% | −35.2% | — | — | — | |
| **BUY & HOLD QQQ** | **19.96%** | | | — | — | | — |

**None beat the index.** But read the control rows — they are the informative
ones.

## The signals are real. The alpha is on the wrong side.

Momentum winners return 13.78%; momentum losers return −13.61%. **A 27-point
spread.** Low-vol returns 10.63% against high-vol's −5.18%, a 16-point spread.

The rankings work. The effects exist. **But the long leg alone does not beat
the index, and the short leg — where most of the spread lives — is closed to a
long-only cash-equity mandate.**

That is the structural finding of this whole exercise, and it generalises:
every price-only effect that survives replication is either a *risk reducer*
(better Sharpe, worse absolute return) or a *long-short spread* (needs
shorting). Neither is accessible to a long-only, unlevered account.

## N7b: can cost mitigation flip momentum?

Momentum was the one candidate where **cost, not signal, was binding**: gross
16.94% against SPY's 14.87%. So the full Novy-Marx & Velikov **buy/hold
spread** was implemented — their taxonomy names it the single most effective
simple cost mitigation.

A 4×4 grid of hold size × band, with the decision rule fixed *before* running:
**accept only if the same configuration beats SPY in both sample halves.**

The mitigation worked exactly as documented — turnover 51% → 37%, cost drag
2.99% → 2.12%/yr.

**Zero of 16 cells beat SPY in both halves.** Every single one loses in the
first half, by 2.84 to 17.0 points.

The best cell shows **+1.10%/yr over SPY** — and is precisely what the rule
exists to catch: best-of-16 selection on a result that is −2.84% in the first
half and +5.15% in the second. A regime bet wearing a backtest.

Risk makes it worse than the return gap suggests:

| | CAGR | Vol | maxDD | On €10,000 |
|---|---|---|---|---|
| SPY | 14.87% | 18.6% | −35.2% | −€3,520 at the trough |
| Momentum | 13.78% | **42.1%** | **−52.0%** | **−€5,200** |

2.3× the volatility, a deeper hole, and 20 names instead of 500 — to *match*
the index.

## A third data corruption, found by a nonsense result

The first low-volatility run returned **−2.03%/yr with a −48.7% drawdown**. A
low-volatility strategy cannot do that, so I looked at what it held: stocks
priced at exactly **$1,000,000.00 with 0.0% volatility.**

Sentinel values from overflowed split adjustments. **291 symbols** carry them;
**991** have 60+ consecutive identical closes. A constant series has zero
volatility, so a low-vol sort ranks it *first* — and it sits exactly at its
52-week high, so it poisons that signal too. The earlier >500%-move screen let
them through untouched, because a constant series has no moves at all.

After screening, low-vol returns 3.30% — a **5.3-point swing**.

These biases all run one way: zero volatility, infinite return, permanent
highs. **The direction that makes a strategy look fundable.**

## What it would cost to test the anomalies that *do* replicate

The survivors of Hou/Xue/Zhang are **value, profitability, investment** — all
fundamentals-based. Testing them needs EODHD's Fundamentals tier at €60/month.

| Account | Data cost as % of capital | Net edge needed just to break even |
|---|---|---|
| **€10,000** | **7.20%** | **7.20%/yr** |
| €25,000 | 2.88% | 2.88%/yr |
| €50,000 | 1.44% | 1.44%/yr |
| €100,000 | 0.72% | 0.72%/yr |

At a literature-plausible 3% net edge, **break-even is €24,000.**

Full stack at €10k — EOD €199 + fundamentals €720 + hosting €90 — is
**€1,009/yr, 10.1% of capital**, consuming **68% of a benchmark-matching
return before any alpha at all.**

**At €10,000, the data required to find an edge costs more than the edge is
worth.** That is not a research problem. It is an arithmetic one, and no amount
of model quality changes it.

## Standing at 134 configurations

A *worthless* strategy is now expected to show an annualised Sharpe of **1.52**
over three years of daily data, purely from selection across that many trials.
Every future result is measured against that bar, and it rises with each test.
