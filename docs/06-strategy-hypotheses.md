# Strategy Hypothesis Register

**Status: no validated strategy. Zero strategies are approved for capital.**

This is the correct state of the project. The infrastructure is built and
tested; the strategy research has completed Stage 0–1 (pre-registration and
feasibility screening) and cannot proceed further without market data that has
not yet been acquired.

This document records hypotheses with priors stated **in advance**, so that
later results can be judged honestly. A prior written after seeing results is
not a prior.

Reproduce every figure here with `.venv/bin/python scripts/hypothesis_screen.py`.

---

## 1. What a €10,000 account actually has

Before listing candidates, it is worth being precise about the edge sources
available at this size, because most "retail advantage" claims do not survive
the cost model.

### Genuine advantages

1. **Capacity indifference.** An effect with €50m of capacity is worthless to a
   $10bn fund — 0.5% of AUM cannot move the needle and is not worth the
   operational risk — but entirely sufficient for €10k. **This is the only
   structural retail advantage that is both real and large.**

2. **No benchmark or career risk.** Institutions cannot hold 80% cash for
   months, cannot accept large tracking error, and cannot survive a 20%
   drawdown without redemptions. You can. This permits genuinely patient
   strategies that wait for rare setups — a real and underused freedom.

3. **No mandate constraints.** Minimum market cap, sector exclusions,
   must-be-fully-invested rules, index membership requirements. Constrained
   institutions are sometimes *forced* to trade at bad prices; an unconstrained
   buyer can supply that liquidity.

### Claimed advantages that fail the cost model

1. **"Trade the small caps institutions ignore."** The most common advice, and
   wrong. A Helsinki micro-cap costs **~322 bps round trip** (112 bps spread +
   28 bps impact + 21 bps commission, one way). A ~3% gross edge per trade would
   be needed to break even. Institutions are absent because the spread makes it
   uneconomic **for everyone** — not because retail size confers an advantage.
   Cost is proportional, so being small does not help.

2. **"Trade faster than institutions."** Our round-trip cost is 30 bps; a
   market maker's marginal cost is near zero. We are 100× more expensive per
   trade. Any strategy competing on speed or frequency is competing where we are
   structurally worst.

3. **"Use leverage to amplify a small edge."** Outside the mandate, and
   amplifying a 30 bps edge against a 30 bps cost amplifies the cost identically.

**The conclusion that shapes everything: the only viable regime is low-turnover,
liquid-universe, patient strategies exploiting capacity-constrained or
flow-driven effects.**

---

## 2. Screening results

All ten candidates were screened on cost feasibility and statistical power
before any data was acquired. Priors are sourced from published literature and
discounted for decay.

| ID | Hypothesis | Hold | Prior edge | Verdict |
|---|---|---|---|---|
| H1 | Overnight risk premium (buy close, sell open) | 1d | 3 bps | ❌ Infeasible (both) |
| H2 | Short-term reversal, 1 week | 5d | 35 bps | ❌ Cost infeasible |
| H3 | Turn-of-month flow | 4d | 25 bps | ❌ Infeasible (both) |
| H4 | Post-earnings announcement drift | 45d | 90 bps | ⚠️ Passes screen |
| H5 | Index deletion forced selling | 20d | 140 bps | ⚠️ Passes screen |
| H6 | Index addition premium, S&P 500 | 10d | 15 bps | ❌ Infeasible (both) |
| H7 | Spin-off indiscriminate selling | 126d | 400 bps | ⚠️ Marginal |
| H8 | December tax-loss selling reversal | 30d | 120 bps | ❌ Power infeasible |
| H9 | Mutual fund fire-sale pressure | 60d | 200 bps | ⚠️ Marginal |
| H10 | Cross-sectional momentum, 12-1 | 21d | 45 bps | ❌ Cost infeasible |

**4 of 10 survive. Surviving is not evidence of an edge** — it means only that
the hypothesis is not arithmetically impossible, and therefore that spending
data-engineering effort on it is defensible.

---

## 3. Rejected before testing, and why

These rejections are the cost model doing useful work. Each saved weeks of data
engineering.

### H1 — Overnight risk premium ❌

Requires buying every close and selling every open: **2,520 round trips/year,
75.6% annual cost drag** against a prior of ~3 bps per day. Dead by a factor of
~10 on cost alone.

Worth noting separately: even if costs were zero, this is probably a **risk
premium, not an inefficiency** — you are compensated for bearing overnight gap
risk. A premium earned for bearing risk is not an edge.

### H2 — Short-term reversal ❌

15.1% annual cost drag against a 35 bps prior (ratio 1.17, need ≥2.0). This
effect is real, but it has become a **market-making activity** conducted at
near-zero marginal cost. We would be entering a competition where our cost base
is 100× the incumbent's.

### H3 — Turn-of-month flow ❌

Fails both constraints: 18.9% cost drag, and with only 12 observations per year
it would take **74 years** to reach 80% power on a 25 bps effect. The flow
mechanism (regular pension contributions) is genuine, which makes this a clean
illustration that a real mechanism is not sufficient.

### H6 — S&P 500 index addition ❌

Prior set near zero deliberately: the index effect has been documented as
disappearing for large, well-telegraphed indices as front-runners crowded it.
Combined with a 10-day hold, it fails both constraints decisively (**247 years**
to power). Included to show that a famous anomaly can be fully arbitraged.

### H8 — December tax-loss selling ❌ (power only)

Passes cost comfortably (2.5% drag, ratio 4.0) but needs **290 events = 14.5
years**, giving only 66% power on 10 years of data. The mechanism is attractive
— a calendar deadline limits how far arbitrageurs will front-run it — but it
**cannot be validated to this protocol's standard.**

This is the honest failure mode worth dwelling on: *not* "this doesn't work"
but "this cannot be shown to work with the data available." Trading it anyway
would be trading on a story.

### H10 — Cross-sectional momentum ❌

3.6% cost drag against a 45 bps prior (ratio 1.50, need ≥2.0). Included
explicitly because momentum is the default retail recipe. Beyond cost: it is the
most crowded factor in existence, with severe crash risk (2009, 2016, 2020).
**No case can be made that it retains an edge after costs at this size**, which
is the standard the brief requires.

---

## 4. Candidates that survive screening

**None of these is validated. All require Stages 2–10.** Listed in order of my
assessed probability of surviving.

### H5 — Index deletion forced selling ⚠️ *Best risk-adjusted candidate*

**Mechanism.** When a stock is deleted from an index, index funds **must** sell
by the effective date regardless of price. This is genuinely price-inelastic
supply. A buyer with no deadline can supply liquidity and be compensated.

**Why not arbitraged away.** Three asymmetries favour the deletion side:
1. Arbitraging a deletion requires *buying* a stock that is usually distressed —
   uncomfortable, and unattractive to mandates with quality screens.
2. The addition side is far more attractive to arbitrageurs, so effort
   concentrates there (which is consistent with the addition effect decaying
   first — see H6).
3. Smaller indices (OMXH25, MSCI mid-cap, FTSE 250) are less monitored than the
   S&P 500.

**Screening.** 20-day hold, 3.8% cost drag, ratio 4.67. 30 events/year → 142
events needed = **4.7 years**, 98% power on 10 years. Passes both.

**Principal risk.** Deleted stocks are deleted for a reason — often
deteriorating fundamentals. The effect must be separable from simply buying
falling stocks. **Required control: compare against a matched sample of
similarly-declining stocks that were *not* deleted.** Without that control, this
hypothesis is untestable, and that control is the main piece of work.

**Data needed.** Historical index membership changes with announcement and
effective dates. The main obstacle: not freely available at quality for most
indices.

### H9 — Mutual fund fire-sale pressure ⚠️

**Mechanism.** Funds facing large redemptions sell holdings proportionally,
without regard to price. Documented in Coval & Stafford (2007) — the strongest
academic support in the forced-flow family.

**Screening.** 60-day hold, 1.3% cost drag, ratio 6.67. 25 events/year → 209
events = **8.4 years**, 86% power. Marginal on power.

**Principal risk, and it may be fatal.** Fund holdings are disclosed quarterly
with a **45-day lag**. By the time the data is visible, the fire sale is largely
complete and the price pressure may have reversed. The original paper's edge may
be unavailable to anyone relying on public disclosure. **Test the lagged-data
version specifically** — testing with contemporaneous holdings would produce a
result that cannot be traded, which is a subtle but complete lookahead bias.

### H7 — Spin-off indiscriminate selling ⚠️ *Strongest mechanism, weakest evidence*

**Mechanism.** Parent-company shareholders receive shares in a business they did
not choose. Index funds must sell if the spinco is not in the index. There is
no analyst coverage yet, and the holder base is mismatched. Selling is
indiscriminate and price-insensitive.

**Screening.** 126-day hold, 0.6% cost drag, ratio 13.33 — the most comfortable
cost profile of any candidate. But 15 events/year → 110 events = **7.3 years**,
90% power. Marginal.

**Why it is nonetheless ranked third.** With ~15 usable events per year, a
10-year sample gives ~150 observations against per-event noise of ~1,684 bps.
Even a real 400 bps effect would be estimated with wide error bars, and **a
handful of outliers could dominate the result** — exactly the condition under
which a backtest is least trustworthy. Additionally, dedicated spin-off funds
and ETFs exist, so the effect has attracted capital.

**Data needed.** Clean historical spin-off dates and the resulting entity
mapping. Genuinely hard to obtain free and accurately; corporate action data is
where free datasets are weakest.

### H4 — Post-earnings announcement drift ⚠️ *Passes screen; I expect it to fail*

**Mechanism.** Investors underreact to earnings surprises; prices drift in the
surprise direction for weeks (Bernard & Thomas 1989).

**Screening.** The best numbers of any candidate: 45-day hold, 1.7% cost drag,
ratio 3.00, 200 events/year → 773 events = **3.9 years**, 99% power. It is the
only candidate with comfortable statistical power, because earnings are frequent.

**Why I expect it to fail anyway.** This is *the* textbook anomaly. It is in
every curriculum, every factor library, and every commercial signal vendor's
product. Crowding in liquid US large caps is close to certain, and the published
decay is consistent with that. The brief's standard — do not default to known
recipes "unless you can show a clear reason why they still have edge after
costs" — and I cannot show such a reason for liquid large caps.

**It is listed because it is cheap to test and has the statistical power to give
a clear answer**, unlike H5/H7/H9. A clean rejection of H4 is a genuinely useful
result, and its high event count makes it the best candidate for validating that
the research pipeline itself works correctly.

---

## 5. The central bind

The screening reveals a structural tension that is the honest headline of this
research:

> **The candidates with the strongest economic mechanism (H7 spin-offs, H9 fire
> sales) are the rarest, and therefore the hardest to validate. The candidate
> with ample statistical power (H4 PEAD) is the most crowded.**

This is not accidental. Effects remain uncrowded partly *because* they are rare —
rarity is what makes them unattractive to institutions and hard to build a
business on. But rarity is also exactly what makes them impossible to prove with
retail-accessible data.

**A strategy must be frequent enough to validate and rare enough to be
uncrowded. That intersection is narrow, and it may well be empty at this account
size.**

Anyone claiming a validated retail edge in liquid equities should be asked which
side of this bind they resolved, and how.

---

## 6. What a data subscription costs against these edges

Survivorship-bias-free history is not free. EODHD end-of-day all-world,
including delisted tickers and Nasdaq Helsinki, is €199/year — **1.99% of a
€10,000 account, paid whether or not the strategy trades.**

| ID | Gross/yr | Trading cost | Data cost | **Net/yr** | Net € | Break-even account |
|---|---|---|---|---|---|---|
| H4 | 5.04% | 1.68% | 1.99% | **1.37%** | €137 | €5,923 |
| H5 | 4.20% | 0.90% | 1.99% | **1.31%** | €131 | €6,030 |
| H7 | 6.00% | 0.45% | 1.99% | **3.56%** | €356 | €3,586 |
| H9 | 5.00% | 0.75% | 1.99% | **2.26%** | €226 | €4,682 |

Break-even account sizes sit below €10,000, so the subscription is justified —
**conditional on the edges being real, which none is.**

The net figures set expectations honestly: **one to three hundred euros a year
on €10,000, with data consuming a third to half of gross profit.** That is what
a €10k systematic equity account can expect even when a real edge is found. It
is not a reason to stop; it is a reason to be clear about why you are doing
this. The system scales, the returns compound, and the same edge at €50,000
pays a 0.40% data cost instead of 1.99%.

**Note that these use the register's own priors, which are deliberately
generous.** A realistic outcome after validation is lower, and the most likely
outcome remains zero surviving strategies.

---

## 7. What happens next

In order. Nothing skips ahead.

1. **Acquire data.** Daily bars with **delisted and acquired names included** —
   survivorship-biased universes overstate returns substantially. Corporate
   actions (splits, dividends) properly adjusted. The practical route is one
   month of EODHD (€19.99, no commitment): download the universe, store it
   locally with provenance, cancel, and run the protocol against the local copy.
   TradingView cannot supply this — see `docs/04-data.md`.
2. **Calibrate the cost model.** Measure actual median quoted spreads per
   instrument and populate `Instrument.spread_bps`. Until then every cost figure
   is a modelled default, and `require_calibrated_spread=True` should be enabled
   for final runs so a guessed spread cannot pass silently into a result.
3. **Validate the pipeline on H4**, the highest-power candidate. The first goal
   is to confirm that the research machinery behaves correctly, including that it
   can produce a clean rejection.
4. **Then H5**, the best risk-adjusted candidate, with the matched-sample
   control as the central piece of work.
5. **H9 and H7 only if data quality permits**, with results reported alongside
   their power limitations rather than as clean conclusions.

### The most likely outcome

**Zero validated strategies.** This is an acceptable and probably the correct
result at €10,000. It is strictly better than deploying an overfitted strategy:
rejection costs research time, deployment costs capital plus the months spent
discovering the failure.

If nothing survives, the rational alternatives are:

- **A low-cost index holding** for the core of the capital — which is the honest
  benchmark every candidate above must beat net of costs, and a bar none of them
  has yet cleared.
- **Waiting for scale.** At ~€100k, per-order minimums stop binding, round-trip
  cost falls to ~5–10 bps, and several rejected candidates (H2, H10, possibly
  H3) re-enter feasibility. The cost constraint, not the ideas, is what binds
  today.
- **Continuing to research** while the capital sits in the index, which costs
  nothing but time.

Deploying a strategy because effort was spent on it is the one option with
negative expected value.

---

## References

- Bernard, V. & Thomas, J. (1989). *Post-Earnings-Announcement Drift.*
- Coval, J. & Stafford, E. (2007). *Asset Fire Sales (and Purchases) in Equity Markets.*
- Shleifer, A. (1986). *Do Demand Curves for Stocks Slope Down?*
- Greenwood, R. & Sammon, M. *The Disappearing Index Effect.*
- Bailey, D. & López de Prado, M. (2014). *The Deflated Sharpe Ratio.*
- Bailey, D., Borwein, J., López de Prado, M. & Zhu, Q. (2016). *The Probability of Backtest Overfitting.*
- López de Prado, M. (2018). *Advances in Financial Machine Learning.*
- Frazzini, A. & Pedersen, L. (2014). *Betting Against Beta.*
