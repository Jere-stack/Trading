# EODHD Setup — Your Steps

Everything on this page is implemented and tested. The provider works against
the live API (verified with their public demo token), so once you have a key
this is a single command.

---

## Do you need a computer, or is an iPad enough?

**It depends which phase, and the honest answer splits in two.**

| Phase | iPad enough? | Why |
|---|---|---|
| 1. Sign up, get API key | **Yes** | Just a web browser |
| 2. Download data, run research | **Yes** | The work runs in this cloud session, not on your device — with one caveat below |
| 3. Paper trading (1–2 months) | **No** | IB Gateway is a Java desktop app; there is no iOS version |
| 4. Live trading | **No** | Same |

### Why research works on an iPad

You talk to me through `claude.ai/code` in Safari. The download, storage,
backtesting and validation all execute in a Linux container in the cloud — none
of it runs on your device. Your iPad is a terminal, not the computer.

**The one caveat: this container is ephemeral.** It's wiped when the session
ends. So data downloaded here doesn't persist by itself.

Two things make that a non-issue:

1. **EODHD paid plans allow 100,000 API calls/day**, and a symbol's full
   history costs 1 call. Re-downloading a 500-symbol universe costs 500 calls —
   0.5% of a single day's budget. Refetching at the start of a session is
   cheap and takes about a minute.
2. **I can send you the stored Parquet files** and you keep them in Files /
   iCloud, then upload them back next session. A 500-symbol, 10-year dataset
   compresses to roughly 20–30 MB.

So for the research phase — which is where you are now, and which is the part
that decides whether any of this is worth doing — **an iPad is genuinely
sufficient.**

### Why paper trading is not

IB Gateway is a desktop Java application. There's no iOS build, and it must run
continuously through market hours with a daily re-authentication. This session
can't host it either: it's ephemeral and isn't always running.

When you reach that phase you'll need one of:

| Option | Cost | Notes |
|---|---|---|
| **Home computer left on** | €0 | Fine for European hours; US session is 16:30–23:00 Finnish time, so an evening-on machine covers it |
| **Hetzner VPS (CX22)** | ~€4/mo | EU-based (Germany), close to European venues, always on. What I'd pick |
| **Raspberry Pi 5** | ~€80 one-off | Runs IB Gateway; silent, low power, no recurring cost |

At €4/month a VPS is 0.5%/yr of a €10k account — meaningful but not decisive.
**Don't buy anything yet.** The research phase may well conclude that no
strategy survives, in which case you never need it.

---

## Step 1 — Sign up (5 minutes, iPad fine)

1. Go to **[eodhd.com/pricing](https://eodhd.com/pricing)**
2. Choose **"EOD Historical Data — All World"** — €19.99/month.
   - *Not* All-In-One (€99.99) — that adds fundamentals and intraday you don't
     need. The cost structure of a €10k account rules out intraday anyway.
   - **Pick monthly, not annual.** You need the history, not a live feed.
3. Create the account and copy your API token from the dashboard.

**Why monthly matters:** download everything in one session, then cancel. The
data is yours to keep and analyse locally. That turns €199/yr into a **€20
one-off** — 0.2% of your account. Re-subscribe for a month when you want fresher
data.

---

## Step 2 — Give me the token

Paste it in chat and I'll set it as an environment variable for the session.

**Two things I will not do, deliberately:**

- **Never commit it.** It's a credential. `.gitignore` blocks `.eodhd_token`
  and `*.token`, but the real protection is that it only ever lives in an
  environment variable.
- **Never put it on a command line.** It would land in shell history. The
  provider reads `EODHD_API_TOKEN` from the environment for exactly this reason.

Rotate the token in their dashboard once you've cancelled, if you like.

---

## Step 3 — Download (one command, ~1 minute)

```bash
export EODHD_API_TOKEN=your_token_here

# A survivorship-free US universe: active AND delisted tickers
tradelab data eodhd --dataset us-research --exchange US --years 10 --max-symbols 500

# Or Nasdaq Helsinki, in your base currency — no FX cost
tradelab data eodhd --dataset helsinki --exchange HE --years 10 --currency EUR
```

With `--symbols` omitted it builds the universe from the exchange's **active and
delisted** tickers, which is the entire reason to pay for this. It then fetches,
stores with provenance metadata, measures adjustment distortion, and runs the
quality audit — failing loudly on any CRITICAL issue.

Budget: 500 symbols ≈ 502 API calls out of 100,000/day. You could download
Helsinki, Stockholm, XETRA and the US several times over in one day.

---

## Step 4 — Verify and calibrate

```bash
tradelab data audit --dataset us-research
tradelab data calibrate --dataset us-research --notional 1000
```

`audit` checks the five ways market data lies. `calibrate` measures ADV,
volatility and spread per instrument, then screens on affordability — telling
you which names your account can actually trade. Expect it to reject a large
fraction; that's the point.

**This is the step that removes the standing caveat that every cost figure in
this system is a modelled default.**

---

## Step 5 — Research

Now the protocol in `docs/05-research-protocol.md` can run for real, starting
with H4 (PEAD), which has the statistical power to give a clear answer either
way, then H5 (index deletions), the best risk-adjusted candidate.

**Expect rejections.** The protocol is built to reject, and the most likely
outcome is that nothing survives. Paying €20 to establish that is good value.

---

## Step 6 — Cancel

Once downloaded, cancel the subscription. The data stays valid for research
indefinitely; only its freshness decays. Re-subscribe for a month when you want
to extend the history or add exchanges.

**Before you cancel, make sure the data is somewhere you control.** Ask me to
send you the Parquet files — I can push them to you directly, and you keep them
in iCloud or wherever suits.

---

## Two things worth knowing

### Licensed data must not be committed

EODHD licenses data for *your* use, not redistribution — and pushing it to a
GitHub repository is redistribution, even a private one. The whole `data/` tree
is in `.gitignore` for this reason, along with `*.parquet` and `*.csv`.

### Adjusted prices are correct for signals and slightly wrong for costs

The API returns both `close` (what actually traded) and `adjusted_close` (back-
adjusted for splits and dividends). They answer different questions:

- **Adjusted** is correct for returns and therefore signals. An unadjusted 4:1
  split reads as a −75% one-day return, which any reversal strategy will
  enthusiastically buy.
- **Unadjusted** is correct for per-share commission, tick size and minimum-
  price filters, because those depend on real price levels.

The provider defaults to adjusted — a wrong return corrupts the signal itself,
while a wrong commission is a bounded error — and carries both, so the error is
measurable rather than assumed.

On real McDonald's data over 2020–2024 the oldest adjusted prices are **85.4% of
what actually traded, a 17.1% per-share commission error.** Over ten years with
a stock split it is much larger. `tradelab data eodhd` reports this per symbol,
so you know when to distrust the cost model for older periods.

---

## What this costs in total

| Item | Cost | As % of €10,000 |
|---|---|---|
| EODHD, one month | €19.99 | 0.2% |
| IBKR market data (paper phase) | ~€5–15/mo | 0.6–1.8%/yr |
| VPS, *if* you reach live trading | ~€4/mo | 0.5%/yr |
| **Research phase total** | **~€20** | **0.2%** |

The research phase — the part that decides whether any of this is worth
pursuing — costs about €20 and needs nothing but your iPad.
