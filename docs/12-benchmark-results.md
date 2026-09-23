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

---

# Round 3: the one that got through, and what killed it

Cleaning the universe changed every number. Five data defects had to be found
first — and each was caught only because a result looked wrong, never by
inspection:

| Defect | Scale | Caught by |
|---|---|---|
| Sentinel prices ($1,000,000.00) | 291 symbols | low-vol returning −2.03% |
| Stale series (60+ identical closes) | 991 symbols | same |
| Impossible moves (>500%/session) | 183 symbols | placebo mean +562% |
| **Not common stock** (preferreds, funds, foreign listings) | **54% of the list** | TMB Bank of Thailand outranking Microsoft |
| **Dollar volume overstated by the reverse-split factor** | **up to 1,250×** | random large-caps returning −1.51% |

After fixing all five, a cost-mitigation grid on momentum produced **two cells
that beat SPY in both sample halves** — the first result in this project to
clear a pre-registered rule.

## It was not evidence, and the arithmetic says so

2 of 16 is roughly what chance produces:

| Per-cell pass probability | P(≥2 pass by luck) |
|---|---|
| 5% | 18.9% |
| 15% | 71.6% |
| 25% | **93.7%** |

Momentum's overall excess in this universe is positive, so each half is better
than a coin flip and p is well above 25%.

## Four attacks, pre-specified

**1. Every split point, not the convenient one.**

| | Split dates where both halves win |
|---|---|
| h30/b40 | **51%** |
| h50/b10 | 71% |

**h30/b40 is a coin flip.** It cleared the rule because of where the median
date happened to fall — the exact fragility the rule was meant to catch and
didn't.

**2. Year by year.** A halves test has two observations; this has fourteen.

| | Positive years | Median year | Worst year |
|---|---|---|---|
| h30/b40 | 8 / 14 | +4.49% | **−41.40%** |
| h50/b10 | 7 / 14 | **+0.12%** | **−41.43%** |

Annual excess over SPY runs +50.0% (2020), −41.4% (2021), +43.0% (2022),
−23.4% (2024). Four consecutive years swinging ninety points. h50/b10's median
year is **zero**.

**3. Cost.** The edge survives to 150bps — so cost is *not* what kills it.
That matters: this rejection is about the signal's reliability, not account
size, and a larger account would not rescue it.

**4. Risk.**

| | CAGR | Vol | maxDD |
|---|---|---|---|
| h30/b40 | 19.82% | **36.6%** | **−37.2%** |
| SPY | 14.87% | 14.4% | −23.9% |

Two and a half times the volatility for a median year that beats the index by
4.5 points at best and 0.1 at worst. On €10,000, 2021's relative loss is
**€4,140**.

**Rejected.**

## What is actually true after all this

- **The universe handicap is real: −8.66%/yr**, and it is equal-weight versus
  cap-weight, not size. A random 20 *within large caps* still loses 8.68%/yr to
  SPY. In 2012–2026 cap-weighted concentration was the dominant factor and any
  equal-weighted book fought it.
- **Momentum generates genuine alpha over its own opportunity set: +8.58%/yr**
  (14.80% against a 6.21% random-20 baseline). That is not nothing.
- **It is not reliable enough to trade.** 8 of 14 positive years and a 51%
  split-point pass rate describe a signal with a real mean and enormous
  variance — which at €10,000, with no shorting and no leverage, is
  indistinguishable from gambling.
- **Low volatility does not beat high volatility on absolute return** here:
  11.06% against 14.85%. It wins on Sharpe (0.70 vs 0.33) and drawdown
  (−28.5% vs −55.9%). That is what the literature claims, and it is not the
  bar that was set.

## The deflation bar settles it

181 configurations have now been evaluated. Over fourteen years of daily data,
a **worthless** strategy is expected to show an annualised Sharpe of **0.73**
purely from selection across that many trials.

**h30/b40's realised Sharpe is 0.54.**

The best thing found in the entire search scores *below* what pure selection
noise produces at this trial count. There is no reading of that number which
supports trading it.

## Where this leaves the project

Twelve hypotheses tested against real data. Zero survivors. That is the system
working, not failing — every one of them was killed by a test fixed in advance,
and four were killed by data defects that would have produced false discoveries
had the tests been weaker.

The bar was never the problem. The problem is that **at €10,000, long-only and
unlevered, with price data alone, over a period when cap-weighted mega-caps
returned 14.87%/yr, there is nothing in the price-only anomaly space that
clears it reliably.**

The two remaining honest moves are unchanged and now better evidenced:

1. **Grow the account to ~€25,000 before buying fundamentals data.** The
   anomalies that actually replicate — value, profitability, investment — need
   it, and at €10k it costs 7.20%/yr, more than the edge is worth.
2. **Hold the index and keep the search running at zero incremental cost.**
   The infrastructure is built; each future test costs nothing but a rising
   deflation bar.

---

# Round 4: an original signal, and how fast it died

**N10 — Board Hesitation Breadth.** The fraction of US dividend payers
currently overdue against *their own* historical declaration cadence, as a
market-timing signal.

The construction was genuinely unpublished as far as two targeted searches
could establish — market-breadth literature is entirely advance/decline
(price), aggregate-dividend literature is entirely dividend-price ratios
(valuation), and neither counts boards deviating from their own rhythm. The
*firm-level* link is published ([Economics Letters
2016](https://www.sciencedirect.com/science/article/abs/pii/S016517651630266X):
longer intervals between announcements predict cuts) and was never claimed.

The series looked right. Mean 6.30%, and the two highest readings in twenty
years were **2009 (7.74%)** and **2020 (12.37%)** — boards visibly hesitating
in both crises.

It was dead in three checks, with no backtest run.

## 1. The lead-lag gate — the premise is backwards

| Lag (months) | Correlation with SPY |
|---|---|
| **−6** | **−0.099** ← strongest |
| −1 | +0.033 |
| 0 | +0.016 |
| +7 | +0.093 |

**The signal follows the market by six months.** A board in mid-2009 delays
because it has just watched its order book collapse — information already in
the price. The best positive-lag reading (+7 months) has the **wrong sign**:
high hesitation associated with *high* forward returns.

The lesson generalises beyond this idea: **aggregate private information at
board level does not lead the market at monthly frequency. It trails it.**

## 2. The placebo beats the real signal

| | Peak abs. correlation |
|---|---|
| Real signal | 0.099 |
| **Shuffled declaration dates** | **0.132** |

Permuting declaration dates *within each firm* — preserving every firm's
declaration count and the overall date distribution, destroying only the
ordering — produces a *stronger* relationship. The construct carries no
information whatsoever; the weak correlation is calendar and universe
composition.

## 3. Coverage is severely survivorship-biased

| Declaration-date coverage | Firms | Later stopped paying |
|---|---|---|
| < 50% | 1,574 | **92.5%** |
| ≥ 50% | 4,241 | 46.4% |

**A 46-point gap.** EODHD holds declaration dates precisely for the firms that
survived. The eligible universe therefore systematically excludes the firms
most likely to hesitate — the measure is computed on a survivor-tilted subset
and would have been biased *even if the mechanism had been real*.

## What this round cost, and what it bought

Three cheap checks. No backtest. That is what pre-registering the **test
order** buys: the gate most likely to kill the idea runs first, so a dead
hypothesis costs an afternoon instead of a week — and the trial count rises by
3 instead of 30.

**Thirteen hypotheses tested against real data. Zero survivors.**

---

# Round 5: a pre-registered scan, and a lesson about out-of-sample testing

Round 4 killed one signal on a lead-lag gate. That raised a sharper question
than the signal itself: **is there *any* cheaply-computable market-state
measure that leads the index?** Round 5 answers it properly instead of
guessing — a scan with the candidate list, the controls, the split, and the
kill criteria all fixed before a single correlation was computed.

## The design

**Twelve candidates**, declared up front, computed from OHLCV alone — nothing
licensed, nothing that costs €60/month:

| Novel constructions | Published controls |
|---|---|
| `clv_breadth` — where the close sits in the day's range, averaged | `new_low_breadth` — fraction near a 52-week low |
| `return_dispersion` — cross-sectional spread of daily returns | `avg_correlation` — index vol ÷ mean single-stock vol |
| `return_skew` — cross-sectional third moment | `illiquidity` — aggregate Amihud |
| `range_expansion` — today's range vs its trailing norm | |
| `gap_breadth` — fraction gapping outside the prior range | |
| `volume_concentration` — Herfindahl of dollar volume | |
| `listing_rate` / `delisting_rate` — universe entries and exits | |
| `reversal_breadth` — fraction closing against the open | |

The three controls are the point of the design. A scan that finds nothing
might mean *nothing leads the market*, or it might mean *the pipeline is
broken*. Controls separate those two. A scan that finds something might mean
a discovery — or that the pipeline manufactures correlations. Controls
separate those too.

**Discovery on 2011–2018. Confirmation on 2019–2026.** Disjoint. The lag was
chosen in the first half and tested, unchanged, in the second.

## Discovery found five. Out of sample killed three.

| Candidate | Lag | In-sample | Out-of-sample | Holds? |
|---|---|---|---|---|
| `clv_breadth` | +8 | **+0.302** | −0.012 | no |
| `return_skew` | +12 | **−0.299** | −0.038 | no |
| `delisting_rate` | +11 | **−0.215** | +0.099 | no — sign flip |
| `listing_rate` | +11 | −0.240 | −0.205 | yes |
| `avg_correlation` *[control]* | +5 | −0.382 | −0.390 | yes |

Seven of twelve never passed discovery. Of the five that did, **three
collapsed or reversed out of sample** — and all three were novel
constructions. `clv_breadth` at +0.302 over eight years looked like a real
finding. It was worth −0.012 on data it had not seen.

The two survivors then went to stress testing.

## `listing_rate` — dead on arithmetic, not on data

The series is a 90-day rolling count. Rolling counts are overlapping, so
consecutive monthly readings are nearly the same number: **lag-1
autocorrelation 0.973.**

    n_eff = n(1−r)/(1+r) = 177 × 0.027 / 1.973 = 2.4

**177 monthly observations carry 2.4 independent ones.** The standard error
on a correlation at that sample size is ±1.000 — the entire admissible range.
The claimed −0.177 sits 0.2 SE from zero. It replicated out of sample because
a near-constant series replicates itself, not because anything is there.

## `avg_correlation` — survived three attacks, died on the fourth

This one was interesting enough to be worth attacking properly, and the first
three attacks all failed to kill it:

| Attack | Result | Verdict |
|---|---|---|
| **Persistence** — is the "lead" just the signal tracking the market? | lag 0 only −0.107 against −0.361 at +5 | survives |
| **Mirror lag** — is it symmetric, i.e. no direction? | −5 reads **+0.120**, asymmetry 0.241 | survives |
| **Effective sample** — enough independent observations? | n_eff 62.1, the reading sits 2.8 SE out | survives |
| **Estimation window** — does it exist at only one parameter? | see below | **dead** |

`average_correlation` is computed over a rolling window. The window was set to
60 days at the start and never varied. Varying it:

| Window | Corr at +5 | Peak lag | Peak corr |
|---|---|---|---|
| 20 | −0.03 | +8 | −0.120 |
| 40 | −0.02 | +6 | −0.164 |
| **60** | **−0.36** | **+5** | **−0.361** |
| 90 | −0.08 | +8 | **+0.257** |
| 120 | +0.10 | +9 | **+0.255** |

The −0.36 exists at one window and nowhere else. Two windows away in either
direction it is worth −0.02, and at the long end the peak **flips sign**. The
peak lag wanders +8, +6, +5, +8, +9 with no stable structure. There is no
economic story for an effect specific to five months of lag measured over
exactly sixty days of correlation — and a real effect does not vanish because
you measured its input over a slightly different window.

This trips a kill criterion written down in round 3, before this hypothesis
existed: **the effect exists only at one parameter value.**

## The lesson, which is the actual output of this round

> **Out-of-sample replication does not protect against a parameter artefact
> when the same parameter is used in both halves.**

The OOS test validated the **lag**, which had been chosen in-sample. It could
not validate the **window**, because the window was fixed at 60 days in both
halves. Both halves faithfully replicated the same artefact — and the
replication read as confirmation.

What eventually prompted the check was not a statistic. It was the *shape* of
the lead-lag profile: a narrow spike at +5 rather than a hump spanning
adjacent lags. Real economic effects are smooth in their parameters. Spikes
are fits.

## The multiple-testing arithmetic, which says the same thing

Discovery examined **12 candidates × 25 lags = 300 correlations.** A 2.8-SE
reading has p ≈ 0.005 two-tailed. Chance alone therefore delivers

    300 × 0.005 = 1.5 findings

The scan found exactly **one**. That is not a near-miss discovery; it is the
expected yield of a lottery with 300 tickets, and it would have been suspect
even if the window test had come back clean.

## What round 5 cost and what it bought

Seventeen configurations. No backtest, no strategy, no capital. It bought
three things:

1. A **direct answer** to "does any free market-state measure lead the S&P?"
   Across twelve candidates and twenty-five lags: **no.**
2. Confirmation the pipeline is sound — the controls behaved exactly as
   published work says they should, so the null results are real nulls.
3. A methodological hole found and closed: **every parameter that is not
   varied across the split is untested by the split.** Future hypotheses vary
   estimation windows before, not after, out-of-sample confirmation.

**Fourteen hypotheses tested against real data. Zero survivors. 201
configurations.**

---

# Round 6: €25,000, and the finding that changes the bar

Two questions, asked together: does raising the account to €25,000 make any of
the fourteen rejections tradable, and if not, what should be done instead?

## What €25,000 actually fixes

Real improvements, all of them measured rather than assumed:

| | €10,000 | €25,000 |
|---|---|---|
| Round-trip cost (20 names) | 40.2 bps | **33.1 bps** |
| Cash stranded by whole-share rounding | 4.05% | **2.15%** |
| Positions too small to buy one share | 3.3% | **1.4%** |
| Research stack as share of capital | 8.70% | **3.48%** |

The commission floor is the mechanism: IBKR Tiered charges
`max(shares × $0.0035, $0.35)`, and at a €540 position that $0.35 floor binds
hard. At €1,350 it mostly stops binding.

## What it does not fix

Round 3 rejected N7 momentum and recorded a prediction about exactly this
question:

> The edge survives to 150bps — so cost is **not** what kills it. That matters:
> this rejection is about the signal's reliability, not account size, and a
> larger account would not rescue it.

**The prediction held.** At €25k costs, momentum's headline excess over SPY
rises from +2.43% to +2.81% — and every number that matters stays where it was:

| | Momentum | SPY |
|---|---|---|
| CAGR | 17.68% | 14.87% |
| Volatility | **39.9%** | 14.4% |
| Max drawdown | **−44.3%** | −23.9% |
| Sharpe | **0.44** | 1.03 |
| Deflation bar at 250 trials | **0.76** | — |

Realised Sharpe **0.44 against a bar of 0.76**: the strategy scores below what
pure selection noise produces at this trial count. Eight positive years in
fourteen, median +2.47%, worst year **−41.91%** — which on €25,000 is
**−€10,478** relative to simply holding the index.

And after the €870/yr research stack, the excess is **−0.67%**. At €25k the
best of fourteen hypotheses, in its most flattering configuration, loses to
buying an ETF.

One nuance recorded against the rejection: the parameter surface is **not** a
fragile optimum — 17 of 25 cells beat SPY. The signal's mean is genuinely
positive. It is the variance that makes it untradable, which is a more
permanent objection than overfitting.

## M3 — the universe handicap is concentration, not weighting

> **⚠ Numbers withdrawn in round 9.** The top-20 and top-30 books below held ~4.6
> unidentifiable securities a month (local-currency foreign listings, codes carrying
> defunct firms' names). The figures in this section must not be relied on; the
> qualitative claim is unverified, not refuted. See *Round 9 → M5*.

Round 3 measured the handicap (−8.66%/yr for a random equal-weighted 20) but
never separated its two possible causes. SPY is not merely a basket of large
caps; it is a **cap-weighted** basket, and 2012–2026 was an era of extreme
mega-cap concentration. So: is the penalty for equal-weighting, or for drawing
from a wide pool?

**It is not weighting.** Four schemes, 200 random draws each:

| Weighting | vs SPY |
|---|---|
| Equal | −7.19% |
| √(dollar volume) | −7.46% |
| DV capped at 25% | −7.65% |
| Dollar volume | **−8.12%** ← worse |

Size-weighting made it *worse*. It did raise the share of draws beating SPY
(2% → 9%) while lowering the mean — more lottery tickets, not more edge.

**It is concentration.** Narrowing the draw pool collapses the deficit,
monotonically:

| Draw pool | vs SPY |
|---|---|
| Top 500 by DV | −6.87% |
| Top 100 | −5.78% |
| Top 50 | −4.74% |
| Top 30 | −1.26% |
| **Top 20** | **−0.39%** |

The index's return lived in a handful of names, and a 20-name draw from a wide
pool almost never held them.

### What this changes

**The alpha bar is a property of the universe.** A signal selecting from 5,000
names must overcome ~7 points before it is level with the index. The same
signal selecting within the top 30–50 starts 1–5 points behind. Every one of
the fourteen rejections was scored against the punishing version.

This does not resurrect them — N7 fails on variance, not on the baseline — but
it says where the next one should look.

### What it is not

Holding the top 20 by dollar volume returned **18.62%** against SPY's 14.87%,
at only 16% monthly turnover. That is **not** a validated strategy and is not
treated as one:

- Sharpe **0.85 against SPY's 1.05** — worse risk-adjusted
- Volatility 21.9% vs 14.2%, drawdown −40.8% vs −23.9%
- Halves are **+1.20% then +6.40%** — a concentrated bet on a mega-cap regime
  that fourteen years cannot distinguish from a permanent effect

And a caveat against the conclusion: dollar volume is a poor proxy for market
cap, since it also ranks on turnover. A true cap-weighted test would be cleaner
and is not possible with the current data.

## A bug in the integrity mechanism, found by using it

Recording `positive_years=8` bricked the ledger. `from_dict` coerces metrics to
`float` on read while `append` stored the caller's literal, so an int hashed as
`8` and read back as `8.0` — the entry failed its own verification, and because
appending to a broken chain is refused, the ledger became permanently
unwritable.

Fixed by coercing on write, with four regression tests. The malformed entry was
discarded via `git checkout` rather than by truncating the file, so the
append-only file was never edited in place.

The mechanism worked exactly as designed: it refused to bury a bad entry under
valid ones. It was simply wrong about the cause.

## N12 — pre-registered, not yet tested

**The institutional accumulation footprint.** An institution taking a position
large relative to a stock's daily volume cannot do it in one trade; impact
forces the order to be split across sessions, and participation algorithms do
this explicitly at 5–20% of each day's volume. Kyle (1985) derives the same
behaviour from theory.

The prediction is about the **shape** of volume, not its level:

- A news pop is **one** enormous session, then baseline. Attention is spent.
- An accumulation is volume elevated 20–50% for **ten to twenty consecutive**
  sessions, with no single dramatic day — because the algorithm is avoiding one.

Both produce the same elevated monthly *average*, which is why turnover, Amihud
and the volume ratio cannot separate them: averaging destroys the distinction.
The published volume-return work — Gervais/Kaniel/Mingelgrin's high-volume
premium, Lee/Swaminathan's turnover-conditioned momentum — is built on the
level, and the one-day spike this signal discards is much of what drives it.

    footprint = (elevated sessions / window) × (mean close-location on those sessions)

A one-day 8× spike closing on its high scores 1/21 × 1.0 = 0.05. Fifteen
sessions at 1.3× closing two-thirds up their range score 15/21 × 0.33 = 0.24 —
five times larger, from a pattern with a far smaller peak. There is a unit test
asserting exactly that ordering; if it ever fails the hypothesis is void.

**Eight kill criteria are fixed in the ledger before any return was computed**,
including two placebos (shuffle volume; shuffle direction), a full parameter
grid, momentum orthogonality, earnings exclusion, and an effective-sample test.
Per M3 it will be tested **within the top 50–100 by dollar volume**, where the
baseline is 1–5 points from SPY rather than 7.

**Prior: low.** Fourteen hypotheses, zero survivors. The base rate says this
dies too, and the value is in killing it cheaply.

**Fourteen hypotheses tested. Zero survivors. 250 configurations.**

---

# Round 7: N12 run as a research team would run it

The hypothesis and its eight kill criteria were fixed in the ledger before any
return existed. This round executed them in order and stopped at the first
kill — then performed an autopsy, which is a different activity and is labelled
as one.

## Dead at gate B, the first gate that could kill it

No backtest was run. No portfolio was constructed. The information coefficient
— does the signal rank next month's returns at all — is far cheaper than a
backtest and much harder to fool, so it goes first.

| | |
|---|---|
| Information coefficient | **+0.0029** |
| t-statistic on effective sample | **+0.23** |
| Effective observations | 118.3 of 167 |
| Hit rate | **49%** |

A coin flip. The pre-registered bar was \|t\| ≥ 2.

## The autopsy, with the null written down first

One parameterisation is one observation, so criterion 3 varies all four
parameters — 48 cells. **The expected result under the null was recorded
before the run:** ~2.2 cells above \|t\| = 2 if independent, expected maximum
\|t\| around 2.0–2.3, and reviving the hypothesis would need a *coherent region*
at \|t\| ≥ 3.5.

| | |
|---|---|
| Cells evaluated | 48 |
| Positive IC | 26 (54%) |
| Cells above \|t\| = 2 | **2** |
| Strongest cell | **\|t\| = 2.08** |

Exactly the null. And both "significant" cells carry the **wrong sign** at a
10-day window — that is short-term reversal, a known effect, not accumulation.

## The decisive result: worse than random

Fifty volume shuffles, building an empirical null rather than trusting a
standard error:

| | |
|---|---|
| Real IC | +0.0029 |
| **Null mean** | **+0.0217** |
| Real, in null standard deviations | **−2.82** |
| Empirical p | **1.000** |

**A randomly shuffled volume series produces a better signal than the real
one.** The mechanism is clear in hindsight: shuffling makes the elevated-day
set effectively random, so the placebo degenerates into a noisy mean
close-location — and mean close-location carries *more* information than
close-location conditioned on high volume.

Conditioning on elevated volume does not merely fail to add. **It destroys
information that was already there.** The hypothesis is refuted in its own
terms, not merely unsupported.

The component decomposition agrees: persistence alone — the novel part — scores
**t +0.27**. Elevated-day volume share scores **t −0.21**.

## A by-product, and why it is not a finding

Mean close-location value over 21 sessions scores IC **+0.0309**, sitting 3.63
standard deviations above a cross-sectional permutation null (empirical
p = 0.020, the floor 50 draws can resolve).

It is recorded as an observation and **not** as a result:

1. **Post-hoc** — it emerged from the autopsy of a failed hypothesis, after 72
   configurations this round and 322 in the project.
2. **Not novel** — this is the Accumulation/Distribution and Chaikin Money Flow
   family, 1980s technical analysis.
3. **Weak** — an IC of 0.03 is marginal once costs and breadth are accounted.
4. **Untested** — it has faced none of the gates. N7 momentum also had a
   genuinely positive mean and died on variance.

## A defect found in my own test design

This generalises beyond N12 and is the most reusable thing the round produced.

**A within-stock time shuffle preserves each stock's long-run mean.** A name
that habitually settles near its highs still does after shuffling. For any
signal that is a *trailing average*, that placebo destroys only the timing and
leaves the level — so it tests far less than it appears to, and it will flatter
the signal.

The evidence was unmissable once looked at: two of three such shuffles
reproduced the close-location result at **t +2.01 and +2.61**, against a real
t of +2.74. A statistic whose null produces 2.61 cannot support a claim at 2.74.

The correct null for a cross-sectional characteristic is a **cross-sectional
permutation** — keep every series intact, permute which stock's signal maps to
which stock's forward return. Both nulls are now implemented in
`tradelab.research.ic`, and every future hypothesis is tested against the
appropriate one.

## What the round cost

No backtest. No portfolio. One session. That is what pre-registering the test
*order* buys: the gate most likely to kill runs first, and the cheapest gate is
the one that can.

**Fifteen hypotheses tested. Zero survivors. 322 configurations.**

---

# Round 8: searching outside the box

Fifteen hypotheses, all built from prices and volume, all dead. This round
changed what was searched rather than searching harder: new data sources, new
strategy families, the most popular GitHub projects, and — most useful — a free
academic dataset that answers "what still works after publication" directly.

## What the search eliminated, cheaply

| Candidate | Verdict | Why |
|---|---|---|
| **LLM trading agents** — [TradingAgents](https://github.com/TauricResearch/TradingAgents) (~108k ★), [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) (~59k ★) | ✗ | Stars are not returns. [FINSABER](https://arxiv.org/abs/2505.07078) (KDD 2026): LLM advantages "deteriorate significantly" over 20 years and 100+ symbols — too conservative in bull markets, too aggressive in bear. And any LLM backtest inside the model's training window is contaminated: GPT-4o recalls S&P 500 closes within 1%. |
| **Congress-trading ETFs** (NANC, KRUZ) | ✗ | NANC +7 pts vs S&P 500 since 2023, KRUZ −8. Explained by sector tilt (tech vs energy); neither beats the market risk-adjusted. |
| **Lazy Prices** (10-K text changes) | ✗ | A [replication on the S&P 100](https://github.com/iqueipopg/lazy-prices), 2009–2026: alpha −0.93%, t −0.49, **deflated Sharpe 0.13** across 54 variants. |
| **Insider purchases** (Form 4) | ? | Original: +82 bps/month for opportunistic buys. Recent studies are mixed; one finds no timing premium surviving a placebo. Free data; not tested. |
| **Dividend month premium** | ? | JFE 2013, replicated in Germany, **not** in Japan; a Nordic thesis finds foreign ownership weakens it. Data on disk; not tested. |

## M4 — which published factors survive publication?

[Jensen, Kelly & Pedersen](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249)
(*Journal of Finance*, 2023) catalogue 153 US anomalies in 13 themes and
publish the returns [free](https://jkpfactors.com/data), through December 2025.
**Returns earned after a factor's publication year are returns its authors
could not have fitted** — an out-of-sample test run by the passage of time.

**Decay.** Post-publication premium averages +1.54%/yr against +4.05%
in-sample: **38% survives**, close to McLean–Pontiff's ~42%. 79% of factors stay
positive. Shrunk to a third, not dead.

**Individual factors in the last decade are noise.** 2015–2025: 5 factors at
t ≥ 2 long-short, 4 in the bias-free long leg — against ~3.5 by chance.

### A construction bias I nearly reported as signal

The first pass showed 126 of 153 long legs beating the market, 16 at t ≥ 2.
That is **not** evidence. The check that caught it: do the *short* legs also
beat the market? They don't (−0.16%) — but in 2015–2025 the *average* of each
factor's three terciles beat the market by **+0.42%/yr**, and even the
**middle** tercile beat it 76% of the time. JKP's tercile portfolios carry an
offset against their own market series.

The corrected measure differences each long tercile against its own factor's
tercile average, which cancels the offset exactly. **The count falls from 16 to
4.**

### At the theme level, three survive every lens

`long*` is the bias-free long leg — the part a long-only book can capture.

| Theme | ls post-pub | ls 2015–25 | long* 2015–25 | factors positive | mega-cap 2015–25 |
|---|---|---|---|---|---|
| **Momentum** | +2.85% | +3.55% | **+1.53%** | 88% | +2.42% |
| **Quality** | +2.47% | +2.51% | **+1.22%** | 82% | +2.41% |
| **Profitability** | +2.35% | +2.44% | **+1.08%** | 91% | +1.14% |
| Value | +2.74% | +1.36% | +0.78% | 78% | **−0.85%** |
| Low Risk | +0.42% | +0.45% | **−0.03%** | 56% | +0.76% |
| Seasonality | −0.09% | −0.26% | **−0.12%** | 33% | +0.04% |
| Size | +0.07% | −1.43% | **−0.77%** | 20% | +1.69% |

### The most reassuring number in the round

The themes this project tested and rejected **on its own** are exactly the
ones JKP's data shows dead or weak for a long-only book: Low Risk (N5, N6) at
−0.03%, Seasonality −0.12%, Size −0.77%, Short-Term Reversal +0.25%. And price
momentum — rejected here on variance with 17 of 25 cells positive — sits in the
theme JKP finds strongest.

**Two independent pipelines on different data agree.** The infrastructure is
not why fifteen hypotheses died.

### Realism

+1.1% to +1.5%/yr is the **gross**, theme-level premium for portfolios of
hundreds of stocks. A 20–30 stock book captures it with far more noise. This is
a modest edge, and anything built on it should be expected to struggle against
the deflation bar on a 14-year sample.

## A correction to the standing recommendation

Quality and Profitability need company fundamentals, which this project had
deferred on cost grounds — EODHD fundamentals at ~€60/month, 7.2% of a €10k
account. **That objection was wrong.** The SEC serves XBRL fundamentals free
at `data.sec.gov`: verified reachable, 503 us-gaap tags for Apple back to 2007,
every row carrying its **filing date** so point-in-time reconstruction is
possible. The real cost is engineering — tag names change (Apple's revenue tag
switched in 2018 when the ASC 606 revenue standard took effect) and delisted
tickers need mapping to SEC CIK numbers — not money.

## A Finnish structural point

An **osakesäästötili** (equity savings account, €100k deposit limit since 2024)
defers all tax on trades and dividends until withdrawal — and **cannot hold
ETFs, only individual listed stocks, including foreign ones**. So for a Finnish
investor, an active stock strategy belongs *inside* one: every rebalance in an
ordinary taxable account crystallises capital gains tax. For passive exposure
an accumulating UCITS ETF already defers tax, so the account matters most for
exactly the kind of strategy M4 points to. Two costs: foreign dividend
withholding can't be credited inside the account (small for low-yield
mega-caps), and Finnish providers offer no public trading API, so rebalancing
would be manual — ~10–20 orders a quarter at the turnover these strategies run.

## N13 — pre-registered, not built

**Quality + Profitability + Momentum, long-only, top 100 by dollar volume,
from free SEC fundamentals.** Components fixed at 1/3 each and not tuned.
Nine kill criteria are locked in the ledger before a single fundamental is
downloaded, including:

- a **data audit** — delisted-firm CIK coverage within 6 points of survivors,
  or the test does not run;
- the fundamentals must add value **after neutralising momentum** — otherwise
  it is N7 again;
- it must beat a **size-matched naive book** (the 20 largest names), not merely
  SPY — M3 showed concentration alone beats SPY.

**Prior: higher than any of the fifteen before it, and still low in absolute
terms.**

**Fifteen hypotheses tested. Zero survivors. 475 configurations.**

---

# Round 9: N13 stopped at the data audit, and what the audit found

N13 — quality + profitability + momentum from free SEC fundamentals — was built
as far as its first pre-registered gate. It stopped there. **No return was ever
computed.** What gate 0 exposed matters more than N13 would have.

## The build

- **`tradelab.data.sec`** — a rate-limited, disk-cached client for
  `data.sec.gov`. It deliberately avoids `www.sec.gov`, which rejects
  automated requests unless the User-Agent carries a contact email; rather
  than send the account holder's address to a third party, the client uses a
  neutral identifier (overridable via `SEC_USER_AGENT`).
- **Ticker → CIK mapping** built from the SEC `frames` endpoint: 15,767
  filers, 23,880 historical name spellings, **including companies since
  delisted** — the current ticker file lists survivors only.
- **Matching rules fixed in advance.** A ticker match is accepted for a
  delisted symbol only if the names also agree, because reissued tickers
  would otherwise graft one firm's accounts onto another's prices.

## Gate 0: two passes, both failed

| | Survivors mapped | Delisted mapped | Gap | Threshold |
|---|---|---|---|---|
| First pass | 92.9% | 63.9% | **+29.0** | 6 |
| Final pass | 96.8% | 71.2% | **+25.6** | 6 |

After the first failure, fixes were **decided before re-running** and
recorded: apostrophe and word-order handling, all historical SEC names,
broader candidate search with *unchanged* acceptance thresholds, and a narrow
fixed rule removing vendor mislabels — applied only to symbols still unmatched,
so a genuine US company the matcher missed keeps counting against the gate.
**One re-run was allowed.** It failed. N13 is shelved, not rejected: it was
never tested.

### Why it failed is the finding

Of 32 delisted symbols still unmatched, only **~7** are genuine US firms lost
to renames (DowDuPont, Michael Kors → Capri, Overstock → Beyond…). Matching all
seven would still leave a ~19-point gap. The rest cannot be identified as US
companies:

- **~13 foreign listings and IFRS filers** — Novatek, Polyus, NLMK, BDO
  Unibank, UniCredit, JDE Peet's, Boozt…
- **5 codes with no name at all** — GC, SK, SQ, AIP_OLD, ASPI_OLD
- **Codes carrying names of firms that ceased to exist before this panel
  begins** — CompuCom (private 2004), Vintage Petroleum (acquired 2006), Longs
  Drug Stores (acquired 2008) — yet present in the **2012–2026** top 100 by
  dollar volume. Stale names on some other security's prices.

A survivorship-clean test must know what the delisted securities *are*. Here a
quarter of them cannot be identified, so no matcher could pass this gate. The
stop is correct on the merits, not just on procedure.

## M5 — the contamination reached earlier results

| | Unidentifiable securities |
|---|---|
| Top 100 by dollar volume | **11.8** per month (max 23) |
| Top 20 by dollar volume | **4.62** per month, in **168 of 169** months |

Sberbank — a Moscow listing priced in roubles — sat in the "US top 20" for
**103 months**. "Hampton Industries" 72, "Longs Drug Stores" 67, "Vintage
Petroleum" 44. (Some unmatched names — Alibaba, TSMC, ASML — are genuine
US-listed foreign issuers and do belong.) Local-currency prices inflate dollar
volume, so these securities concentrate exactly at the top of a dollar-volume
ranking.

**Consequences:**

- **M3's numbers are withdrawn.** About a quarter of its top-20 book was
  unidentifiable, and the sign of the effect cannot be determined. The
  qualitative claim is unverified, not refuted.
- **N12's verdict stands.** Its universe carried ~12% noise, but the rejection
  rests on the real signal scoring 2.82 sd *below* its own shuffled null, which
  12% noise cannot explain.
- **M4 is unaffected** — it uses JKP's CRSP/Compustat-based data, not this
  universe.

### The general lesson

Round 2 fixed a version of this (TMB Bank at $24,100 outranking Microsoft)
with a metadata filter. The filter trusted the same vendor labels that are
wrong — EODHD marks Kuwaiti and Russian securities "NYSE / USD / Common
Stock" — so the defect survived. **A filter built on a vendor's own metadata
cannot catch that vendor's metadata errors.** An external registry, the SEC,
is what exposed it.

## What would unblock N13

A survivorship-free security master with historical index membership. The
standard retail option,
[Norgate Data](https://norgatedata.com/stockmarketpackages.php), includes
delisted US stocks and historical S&P 500 constituents at **US$787.50/year**
(US$433 for six months). As a *recurring* cost that is ~2.9% of €25,000 — larger
than the edge it would test. As a *one-off* six-month research subscription it
would answer whether N13 survives, after which it could be cancelled.

**Fifteen hypotheses tested, zero survivors; N13 shelved at the data audit.
476 configurations.**

---

# Round 10: N13 tested on SEC-cleaned data, and rejected

At the account holder's request N13 was run despite gate 0, on a universe
rebuilt against an external registry, with a full comparison against simply
holding the index. Every rule was recorded in the ledger before any result
(entries 103–105); a defective first run is reported beside the verdict run
(entries 106–109 record both runs, the defect and the verdict).

## The clean universe

- **Security master:** all 16,559 companies that filed an XBRL balance sheet
  with the SEC 2009–2025, US GAAP or IFRS, 19 currencies, all four quarter-ends
  (so March and September year-end foreign filers are included) — with current
  tickers, former names, industry code and domicile.
- **Identification of the 787 candidate symbols:** 500 by current ticker, 238
  by name or former name, **21 removed as contamination** (foreign listings
  labelled US, codes with no company behind them), 28 unresolved and excluded.
- **Ties.** 31 name matches tied between several filers — normalising away
  "Group" and "plc" is what lets vendor spellings agree, so Kraft Foods Group
  ties with Mondelez (formerly "Kraft Foods Inc"). Lowest-CIK-first picked
  wrongly in about ten. Ties now go to the closest full legal name, then to
  the filer whose filing dates best overlap the symbol's trading dates; all 31
  decisions were printed and checked by eye.
- **Reorganisations** (Disney 2019, Cigna 2018, BlackRock 2024, Medtronic
  2015…) issue a new SEC identity to a stock that never stopped trading. 18
  such companies are linked to their predecessor so their financial history
  continues.
- **Prices outside a company's SEC filing life are blanked** for
  name-identified codes (19,666 points), and only real US sessions are kept.

**Gate 0 still fails:** 99.6% of survivors identified vs 90.6% of delisted
securities, a **9.0-point gap** against a limit of 6 (round 9: 25.6). Of the
26 unresolved delisted codes, about 11 are themselves contamination (Polyus,
BDO Unibank, Boozt, and stale names such as Longs Drug Stores); about 15 are
genuine companies missed, mostly foreign issuers that filed no usable XBRL in
the years they traded (Potash, Silver Wheaton, Nexen, Yandex), plus First
Republic, which filed with the FDIC rather than the SEC. That split is
post-hoc and does not change the gate; it says the remaining tilt is modest
and mostly foreign.

## Run 1 — seen, and defective

Run 1 stopped at gate 1 (IC +0.039, t +1.47). Its diagnostics then showed
seven broken month-ends: removing the foreign listings had left **105 rows
that are not US sessions** (102 US holidays with no price, three with one
stray price). A 12-1 momentum lookback landing on one lost every stock's
momentum; Memorial Day 2021 as a month-end emptied the universe. Every ranked
book sat in cash for those months. The fix has no free parameter — keep the
dates SPY traded — and `run_n13` now refuses to run if a month cannot fill the
book. Run 1 was recorded as a trial, with the defect, before run 2 existed.

## Run 2 — the verdict run

| Gate | Test | Result |
|---|---|---|
| 1 | Composite ranks next-quarter returns, t ≥ 2 | **IC +0.036, t +1.38 — FAIL** |
| 2–4 | Null, components, value beyond momentum | not run |
| 5 | Beats the 20 largest eligible stocks, both halves | 16.3% vs 17.6%; 16.9% vs 27.3% — fail |
| 6 | Sharpe ≥ S&P 500 | 0.83 vs 0.90 — fail |
| 7–8 | Parameter grid holds, both halves | 0 of 12 cells beat the baseline, in any window — fail |
| 9 | Deflation bar, 566 trials | 0.829 vs 0.828 — passes by 0.001; excess-return reading +0.27 fails |

**N13 is rejected.** With 0 of 12 variants beating a naive book of the 20
largest eligible stocks, no plausible correction of the 9-point audit gap
reverses it. Run 1 had given the same verdict.

## Against simply holding the index, 2012-09 to 2026-09

After 0.33% round-trip costs, US dollars, dividends reinvested:

| Book | Per year | Volatility | Sharpe | Worst fall | Years ahead of SPY |
|---|---|---|---|---|---|
| **N13 strategy (20 stocks)** | **16.7%** | 21% | 0.83 | −38% | 9/15 |
| S&P 500 (SPY) | 14.6% | 17% | 0.90 | −34% | — |
| Nasdaq-100 (QQQ) | 19.4% | 21% | 0.96 | −35% | 11/15 |
| 20 largest eligible stocks | 22.6% | 25% | 0.95 | −51% | 11/15 |
| 20 largest, any sector | 22.1% | 24% | 0.94 | −46% | 12/15 |
| Equal-weight top 100 | 15.3% | 20% | 0.82 | −39% | 8/15 |

N13 made about 2 points a year more than the S&P 500 but with more risk: a
lower Sharpe ratio and a deeper worst fall. In euros: 17.7% against 15.6%.
Its worst year relative to the index was 2022, −13.4 points — about **−€3,350
on €25,000**.

### The "20 largest" rows, and what they are not

M3's withdrawn claim — concentration in the largest names beats the index —
**reappears on clean data**: +7.5 points a year over SPY, 12 of 15 years. It is
still not evidence. These books were yardsticks in a test of something else,
not a pre-registered hypothesis; 2012–2026 is the era in which a handful of
mega-caps carried the market; the residual survivorship tilt flatters them;
and their worst fall was −46% to −51%. The Nasdaq-100 fund took much of the
same move with a −35% worst fall, no trading and no stock selection. Testing
concentration properly would need a period it has not already been seen to
win in.

## What round 10 established

- **A reusable clean-universe pipeline** from free SEC data: identification,
  contamination removal, reorganisation links, point-in-time fundamentals.
  Survivorship is not solved (9.0 points) but is measured.
- **Quality + profitability + momentum does not survive** on the large-cap
  universe a €25,000 account can trade, before or after the data defect.
- **Neither index was beaten on a risk-adjusted basis** by anything this
  project has tested. For a Finnish investor the plain options remain the
  honest benchmark: a Nasdaq-100 or S&P 500 fund, noting that an
  osakesäästötili cannot hold ETFs.

**Sixteen hypotheses tested, zero survivors. 566 configurations.**
