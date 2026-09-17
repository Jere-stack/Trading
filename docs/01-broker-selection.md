# Broker Selection for a Finnish Resident, Stocks Only, ~€10,000

**Recommendation: Interactive Brokers Ireland (IBIE).** There is no close second
for this specific combination of constraints.

All figures below are IBKR's published retail schedules as of **September 2026**
and must be re-verified before funding, then recalibrated against your own
statements (see [Calibration](#calibration-is-not-optional)).

---

## 1. The decision in one table

| Requirement | IBKR Ireland | Nordnet | Alpaca (US entity) | Saxo Bank | DEGIRO / T212 |
|---|---|---|---|---|---|
| EU-regulated entity for a Finn | Yes (CBI, Ireland) | Yes (Finland) | No (US, SEC/FINRA) | Yes (Denmark) | Yes |
| Production-grade trading API | **Yes** | Effectively no | **Yes (best-in-class)** | Yes (OpenAPI) | **No** |
| Broker-provided paper environment | **Yes**, live data | No | **Yes** | Yes (SIM) | No |
| Nasdaq Helsinki + US + EU venues | **Yes** | Nordics + some | US only | Yes | Yes |
| Cost at €10k order sizes | **Lowest** | High floors | $0 commission | Higher + custody fee | Low but no API |
| Finnish tax pre-reporting | No | **Yes** | No | No | No |
| Verdict | **Recommended** | Disqualified: no API | Viable US-only sleeve | Runner-up | Disqualified: no API |

**Nordnet, DEGIRO and Trading212 are disqualified on a single hard requirement:**
no supported programmatic order API. Nordnet's external API has never been a
supported retail algo-trading product and there is no paper environment. For a
fully automated system this is not a trade-off, it is an exclusion.

---

## 2. Why IBKR wins

**It is the only option that is simultaneously EU-regulated, API-first, and
gives access to both Helsinki and the US.** That last point is not a
nice-to-have — it is what makes the cost model workable, as Section 4 shows.

1. **Regulatory fit.** As a Finnish resident you contract with Interactive
   Brokers Ireland Ltd, regulated by the Central Bank of Ireland. Assets fall
   under the Irish Investor Compensation Scheme (up to €20,000, which fully
   covers a €10k account) plus IBKR's own asset segregation. IBKR Group is a
   listed company with a strong balance sheet — relevant because you are taking
   multi-year counterparty exposure.

2. **Paper-to-live is a port number.** The TWS API exposes a paper account on
   port 7497 and the live account on 7496, with an identical message protocol,
   identical order types, and *live market data in paper*. This is precisely the
   property required, and it is why the architecture can genuinely promise
   seamless switching rather than approximate it.

3. **Costs are the lowest available at this size** (Section 4).

4. **No inactivity or platform fees**, so a strategy that trades rarely is not
   penalised — which matters a great deal, because Section 4 concludes that
   trading rarely is the only affordable option.

5. **Mature Python path.** Use [`ib_async`](https://github.com/ib-api-reloaded/ib_async),
   the actively maintained successor to `ib_insync` (whose original author passed
   away; the library has been unmaintained since 2021 and mishandles modern
   asyncio event loops). Do not start a new project on `ib_insync`.

### The genuine costs of choosing IBKR

Stated plainly, because they are real:

- **No Finnish tax pre-reporting.** Nordnet reports to Verohallinto and
  pre-fills your return; IBKR does not. You must self-report every disposal
  with FIFO cost basis. Budget real effort here, and note that an automated
  strategy can generate hundreds of disposals a year. IBKR Flex Queries can
  export the data, but you own the reconciliation. This is the single strongest
  argument for Nordnet, and it loses only because Nordnet cannot be automated
  at all.
- **A steep learning curve.** TWS/IB Gateway must stay running and
  authenticated, sessions expire, and the API has genuine quirks (ambiguous
  contract resolution, pacing violations, silent rejections).
- **Market data is a paid subscription**, priced per exchange.

---

## 3. Alpaca: worth understanding, not the primary choice

Alpaca has the best API in the industry and free paper trading, so it deserves a
straight answer rather than dismissal.

**A 2026 development that looks decisive but is not.** Alpaca acquired
WealthKernel, created **Alpaca Europe** authorised by Spain's CNMV, passported
across 29 EEA countries including Finland, and launched European equities
starting with Xetra. That sounds like an EU-regulated Alpaca.

**It is not available to you.** Alpaca Europe is a **Broker API** product — B2B
infrastructure sold to fintechs building their own brokerage apps, with
onboarding and commercial minimums appropriate to institutions. A private
individual with €10,000 cannot practically use it. Verify this directly with
Alpaca before acting on it; if their retail self-directed offering ever launches
in Finland, this recommendation should be revisited, because the combination
would be genuinely compelling.

The route actually open to you is **Alpaca Securities LLC (US)** as a non-US
resident, which carries:

- **US regulation only.** SIPC coverage, no EU investor protection, no ICS.
- **The Pattern Day Trader rule.** A margin account under $25,000 is limited to
  three day trades per five business days. A cash account avoids PDT but ties up
  capital in T+1 settlement. Either way, a €10k account is materially
  constrained in intraday strategies.
- **US stocks only.** No Helsinki, no EU venues.
- **A naive paper fill model** — it does not model spread or impact
  realistically, so it validates integration but not economics.
- **W-8BEN, 15% treaty withholding on dividends, and self-reporting to
  Verohallinto** — the same tax burden as IBKR, with less mature reporting tools.

**Where Alpaca does win: commission-free US trading.** Section 4 shows
commission is the dominant cost at this account size, so zero commission is a
genuinely large advantage. The reason it still loses is that it removes the
EUR-denominated universe, which is what eliminates FX cost entirely — and it
brings PDT, weaker regulatory protection, and startup counterparty risk.

**A defensible hybrid**, once the IBKR path is working: run a US-only sleeve on
Alpaca to exploit zero commission, with IBKR as the primary venue. The
architecture supports this — it is one more `Broker` implementation. Do not do
this first; two broker integrations before one validated strategy is
over-engineering.

---

## 4. The cost analysis that drives every other decision

These are computed by this repository's cost models
(`src/tradelab/costs/`), not estimated. Reproduce them with
`scripts/cost_report.py`.

### Commission is brutal at small position sizes

Round-trip commission as basis points of notional, IBKR retail schedules:

| Position size | US stock (Tiered) | Helsinki stock (0.05%, min €1.25) |
|---|---|---|
| €250 | ~55 bps | ~100 bps |
| €500 | ~31 bps | ~50 bps |
| €1,000 | ~16 bps | ~25 bps |
| €2,500 | ~8 bps | ~10 bps |
| €5,000 | ~5 bps | ~10 bps |

**The binding constraint of this entire project:** at €10,000 with 8–12
positions, positions are €800–1,200 and round-trip commission is **20–30 bps**.
Add spread (2–10 bps for liquid names) and you need roughly **25–40 bps of gross
edge per round trip just to break even.**

Three consequences follow, and they are not negotiable:

1. **High-frequency and intraday strategies are dead on arrival.** A strategy
   trading daily needs ~250 × 30 bps = 75% gross annual return to break even.
2. **Wide diversification is unaffordable.** 30 positions of €330 each pay
   ~45 bps round trip. Concentration (8–12 names) is forced by economics, so
   risk control must come from stops and a portfolio kill switch rather than
   from diversification.
3. **Holding periods must be weeks to months**, giving perhaps 10–40 round trips
   per year in total.

### Choose IBKR Tiered, not Fixed — the opposite of common advice

| Order size | Tiered | Fixed | Cheaper |
|---|---|---|---|
| 10 shares | $0.38 | $1.00 | **Tiered** |
| 50 shares | $0.51 | $1.00 | **Tiered** |
| 100 shares | $0.67 | $1.00 | **Tiered** |
| 130 shares | $0.87 | $1.00 | **Tiered** |
| 150 shares | $1.00 | $1.00 | crossover |
| 500 shares | $2.50 | $2.50 | Fixed above ~140 |

The crossover is near **140 shares**. A €10k account taking €500–1,500 positions
holds well under 140 shares of any stock above ~$10, so **Tiered is materially
cheaper across this account's realistic order sizes.** Re-derive this from your
own realised order-size distribution with
`tradelab.costs.commission.cheaper_us_schedule` — it is an account-level setting
at the broker, so the decision should follow the distribution, not one order.

*Caveat from IBKR's own documentation:* API orders **directed** to a specific
venue cannot use Tiered pricing. Only SmartRouted API orders can. Route
SmartRouted unless you have a specific reason not to.

### FX conversion: the cost nobody models

IBKR charges 0.20 bp with a **USD 2.00 minimum** per conversion.

| Converted | Cost | As bps |
|---|---|---|
| €500 | $2.00 | **40 bps** |
| €1,000 | $2.00 | **20 bps** |
| €10,000 | $2.00 | 2 bps |
| €100,000 | $2.00 | 0.2 bps |

Over 100 round trips of €1,000 in US stocks:

- Converting per trade: **€400/year (40 bps)**
- Converting monthly in blocks: **€24/year (2.4 bps)**

**A €376/year difference — 3.8% of a €10,000 account, annually.** This produces
a hard operational rule: **hold a standing USD balance and convert in
infrequent, large blocks.** Never convert per trade. `tradelab.costs.fx`
quantifies both policies so the decision is visible rather than assumed.

Note the second-order cost: an unhedged USD balance is uncompensated EUR/USD
exposure for a EUR investor, and EUR/USD moves 6–10% a year — easily swamping a
30 bps/trade edge. The portfolio tracks currency exposure explicitly
(`max_currency_exposure`, default 70%) rather than pretending base-currency
returns equal local-currency returns.

### Illiquid small caps are not a frontier — they are a trap

The intuitive move at retail size is to hunt where institutions cannot go. The
cost model kills it. A Helsinki micro-cap with a 180 bps quoted spread and
€15k daily volume, buying €600:

```
spread (half, × 1.25 signal-conditional multiplier) ≈ 112 bps
market impact (square-root law)                     ≈  28 bps
commission                                          ≈  21 bps
                                                    ───────────
one way                                             ≈ 161 bps
round trip                                          ≈ 322 bps  (3.2%)
```

**A strategy would need a ~3% gross edge per trade to break even.** Nothing
robust delivers that. This is why the default risk limits reject orders whose
modelled cost exceeds 35 bps of notional, and why `min_price` defaults to €3.

**This finding matters more than it first appears:** it closes off the most
commonly recommended "retail advantage" (small caps institutions ignore). The
institutions are absent because the spread makes it uneconomic for *everyone*,
not because they cannot reach it. Retail size does not confer an advantage
where the cost is proportional.

---

## 5. Concrete setup

1. **Open** an IBKR Ireland individual cash account (**not margin** — a cash
   account structurally enforces the no-leverage mandate at the broker, which is
   stronger than enforcing it in code alone). Expect 1–3 days.
2. **Commissions:** select **Tiered**.
3. **Market data** (verify current prices; some bundles are waived or fee-waived
   above a monthly commission threshold):
   - Nasdaq Nordic Equity — for Helsinki.
   - *US Securities Snapshot and Futures Value Bundle* — cheap top-of-book;
     sufficient, since the strategies this cost structure permits are daily-bar
     strategies that do not need depth.
   - Skip depth-of-book and news. Budget roughly €5–15/month.
4. **Enable the API:** TWS or IB Gateway → Global Configuration → API →
   Settings → *Enable ActiveX and Socket Clients*, socket port 7497 (paper),
   trusted IP 127.0.0.1. Use **IB Gateway**, not full TWS, for unattended
   operation — it is lighter and more stable. Note it requires a daily
   re-authentication; plan for it (`docs/02-architecture.md`, Operations).
5. **Funding:** SEPA transfer in EUR. Convert to USD only in blocks, and only
   once a strategy actually needs USD.
6. **Tax:** set up a monthly IBKR Flex Query export from day one, including
   paper trading. Reconstructing FIFO cost basis a year later from statements is
   far harder than capturing it as you go.

### Calibration is not optional

Every cost figure here is a modelled default. After your first month of *paper*
trading, compare modelled cost against IBKR's reported commissions per fill. If
the model is optimistic, every backtest is overstated and every conclusion in
`docs/06-strategy-hypotheses.md` needs revisiting. A cost model that has never
been diffed against a real statement is a guess, however precise it looks.

---

## 6. What would change this recommendation

Intellectual honesty requires naming the conditions under which this is wrong:

- **Alpaca launches retail self-directed accounts in Finland under Alpaca
  Europe.** Zero commission plus EU regulation would be a serious challenge,
  since commission is the dominant cost here. Re-evaluate if it happens.
- **The account grows past ~€100k.** Per-order minimums stop binding,
  diversification becomes affordable, and the calculus in Section 4 relaxes
  substantially. More strategies become viable.
- **You decide the tax burden outweighs automation.** A legitimate conclusion. If
  self-reporting hundreds of disposals is unacceptable, Nordnet plus manual
  execution of a low-turnover strategy is a coherent alternative — but it is a
  different project from this one.

---

## Sources

- [Interactive Brokers Ireland — Stock commissions](https://www.interactivebrokers.ie/en/pricing/commissions-stocks.php)
- [Interactive Brokers Ireland — European stock commissions](https://www.interactivebrokers.ie/en/pricing/commissions-stocks-europe.php)
- [Interactive Brokers — Spot currency commissions](https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php)
- [Interactive Brokers Ireland — Market data pricing](https://www.interactivebrokers.ie/en/pricing/market-data-pricing.php)
- [ib_async — maintained IBKR Python API](https://github.com/ib-api-reloaded/ib_async)
- [Alpaca — EEA passporting to 29 countries](https://alpaca.markets/blog/alpaca-completes-eea-passporting-to-29-countries-expanding-access-to-regulated-investment-services-across-europe/)
- [Alpaca — European expansion via WealthKernel](https://www.businesswire.com/news/home/20260421441080/en/Alpaca-Expands-into-Europe-with-WealthKernel-Acquisition-and-Launch-of-European-Equities-Trading)
- [Alpaca Europe — Broker API documentation](https://docs.alpaca.markets/eu/docs/about-broker-api)
- [Alpaca — Non-US resident accounts](https://alpaca.markets/learn/live-trading-account-non-us)
