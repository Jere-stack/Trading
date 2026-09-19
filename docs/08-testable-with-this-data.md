# What Is Actually Testable With This Data

Written after acquiring the EODHD subscription, to answer the only question
that matters now: **given what we have, which hypotheses can be tested, and
which cannot?**

---

## 1. The data inventory, verified

| Data | Status | Notes |
|---|---|---|
| Daily OHLCV, adjusted + unadjusted | **Yes** | 16,737 US symbols, 25.6M rows, 2011–2026 |
| Survivorship-free universe | **Yes** | 73.3% of the US universe is delisted |
| Delisting dates | **Yes** | Implicit: the last bar |
| **Dividend declarations** | **Yes** | Includes `declarationDate` — a genuine announcement date |
| Splits | **Partial** | Execution date only, no announcement date |
| Earnings dates | **No** | HTTP 403 — needs the €59.99 Fundamentals plan |
| Index membership history | **No** | Not sold at any EODHD tier |
| Fund holdings | **No** | Not sold at any EODHD tier |
| Short interest | **No** | HTTP 401 |
| Fundamentals | **No** | HTTP 403 |

---

## 2. What this rules out, definitively

Four of the register's surviving candidates are **not testable** and no amount
of cleverness changes that:

| ID | Hypothesis | Blocked by |
|---|---|---|
| H4 | Post-earnings announcement drift | No earnings dates |
| H5 | Index deletion forced selling | No index membership history |
| H7 | Spin-off indiscriminate selling | No corporate-action classification |
| H9 | Mutual fund fire sales | No fund holdings |

H4 is the cheapest to unblock: €59.99/month for one month of the Fundamentals
tier. H5, H7 and H9 need vendors EODHD does not compete with.

**This is worth sitting with.** The register's four survivors all depended on
event data, and the subscription bought price data. The universe is genuinely
valuable — it makes any test honest — but it does not by itself make those four
testable.

---

## 3. What the data *does* make testable

Only two categories remain, and they are the ones inferable from price, volume
and dividends.

### Inferable events

A cash acquisition leaves a signature nothing else produces: a jump on heavy
volume, then a **collapse in realised volatility** as the price pins to the
agreed cash price, then delisting. Measured on the US universe, 17% of delisted
names carry it. Implemented in `tradelab.research.events`.

### Dividend events

`declarationDate` gives real announcement dates. Measured on a 250-symbol
sample: **52% of the liquid universe pays dividends**, and cuts of >25% occur
at roughly **48/year**, i.e. ~715 events over 15 years — enough statistical
power to detect an effect of ~93 bps.

---

## 4. Worked example: cash merger arbitrage — **REJECTED**

The most promising candidate derivable from this data, tested properly and
rejected. The way it fails is more useful than the verdict.

**Why it looked good.** Deal risk premium is a real, economically grounded
compensation, not an anomaly. Long-only for cash deals. Holding periods of
months, so cost-feasible. Capacity-constrained, so genuinely retail-friendly.
And uniquely testable here, because broken deals only exist in a
survivorship-free universe.

**The result, 520 candidates over 15 years (~35/yr):**

| Bucket | n | Mean return |
|---|---|---|
| Deal closed (delisted) | 339 | **+2.76%** |
| Deal broke (stopped out) | 99 | **−20.31%** |
| **All resolved events** | **438** | **−2.45%** |
| Never resolved (not deals) | 82 | +29%… |

**23% of deals broke, at −20.31% each. That overwhelms the +2.76% earned on
the 77% that closed.** Expected value per event: **−2.45% gross, before costs.**

### The trap this exposes

The headline number is positive at *every* threshold combination tested, while
the honest number is negative at every one:

| Min jump | Max pinned vol | n | Headline mean | Resolved-only |
|---|---|---|---|---|
| 8% | 1.2% | 773 | **+4.61%** | **−3.70%** |
| 8% | 2.0% | 1,051 | **+6.59%** | **−6.34%** |
| 12% | 1.2% | 520 | **+3.55%** | **−2.45%** |
| 12% | 2.0% | 674 | **+4.95%** | **−4.68%** |
| 20% | 1.2% | 323 | **+1.43%** | **−1.39%** |

The entire apparent profit comes from events that never resolved — and those
are not deals at all. The top "winners" were Palantir (+372%), Celldex
(+245%), Bath & Body Works (+217%): **earnings jumps followed by a quiet
period**, which the detector cannot distinguish from a deal pin.

A researcher who stopped at the headline would have deployed capital into a
strategy with negative expected value, and the backtest would have looked
consistent across parameters — the usual reassurance that robustness checks are
supposed to provide.

### Why it fails, mechanically

Real merger-arb funds do make money. Three reasons this version cannot:

1. **Detection lag eats the spread.** Entry is 10 sessions after the
   announcement, by which point the median candidate has already moved +21%.
   What remains is the residual spread, which is thin precisely when the deal
   is safe.
2. **Price cannot price deal risk.** Arbitrageurs assess financing, regulatory
   exposure, and whether the buyer is strategic or financial. Price alone
   cannot, so you hold a blind mix.
3. **Adverse selection.** The cleanest-looking pins are the deals the market is
   most confident about — hence the thinnest spreads. The wide spreads, which
   pay, are wide *because* break risk is high. Selecting on the signature
   selects against the premium.

Point 3 is the general lesson: **when the signal you can observe is the
market's confidence, you systematically buy the low-paying half of the
distribution.**

---

## 5. What remains worth testing

In priority order.

### N2 — Dividend cut / omission drift *(best remaining candidate)*

Companies cutting a dividend by >25%. ~48 events/year, ~715 over 15 years,
with real announcement dates.

- **Mechanism.** Dividend cuts are strongly avoided by management, so a cut
  signals private information about deterioration. Dividend-mandated holders
  (income funds, retirees) are forced sellers regardless of valuation.
- **Statistical power:** adequate — the only remaining candidate of which this
  is true.
- **Problem: it is a short signal.** Long-only, you can only avoid. Testing
  whether the drift exists is still worth doing, because a confirmed
  persistent negative drift is directly usable as an *exclusion filter* on any
  other strategy.

### N3 — Dividend initiation drift

Companies starting a dividend. Positive signal, long-only tradable. Needs an
event-count check first; initiations are rarer than cuts.

### N5 — Drawdown reversal, measured honestly

"Buy the dip" is the most common retail rule and almost every published test of
it is survivorship-biased: the stocks that never recovered are missing. We can
run it properly. **Expect rejection** — and the rejection is worth having,
because it is the rule most likely to be tried on instinct.

### Rejected outright, no test needed

H1, H2, H3, H6, H10 (from the original screen) plus **N1 merger arbitrage**
above.

---

## 6. The honest position

After spending €20:

- **What was bought:** a genuinely survivorship-free universe, measured
  survivorship bias (US +2.78%/yr, Helsinki +1.05%/yr), a calibrated cost
  model, and 3,119 tradable US names against Helsinki's 19.
- **What was not bought:** the ability to test the four best hypotheses.
- **What was learned:** merger arbitrage, the most promising candidate
  derivable from price data, has **negative expected value** at −2.45% per
  resolved event — and would have looked profitable to anyone who did not
  separate resolved from unresolved events.

The infrastructure is now doing exactly what it was built for: rejecting
strategies cheaply, on real data, before capital is committed.
