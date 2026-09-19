# Strategy & Roadmap

Written after six sessions of building, one data purchase, and six rejected
hypotheses. A step back to ask what this project is actually for.

---

## Where we are

**Built and working:** production-grade infrastructure, 256 tests, a
survivorship-free US universe (16,737 symbols, 25.6M rows), a calibrated cost
model, a research protocol that has already rejected seven hypotheses, and an
append-only research ledger holding all 37 configurations ever evaluated.

**Validated strategies: zero.**

**Simulated trading: done.** 501 sessions through the full live path, zero risk
rejections — after fixing the four multi-currency defects it exposed.

**Never done: sent an order to a broker.** The IBKR integration has never
touched a real gateway, and that cannot be tested from this container.

That last line is the most important sentence in this document.

---

## Three hard truths that shape everything

### 1. €10,000 is the binding constraint — not skill, not tooling

Even a *working* edge nets €131–356/year at this size after data costs. The
best candidate in the register, if it were real, would earn about what a
weekend of contracting pays.

**You are not going to get rich on €10k.** Any plan that pretends otherwise is
lying.

### 2. We are fishing in the most-fished pond

Daily-bar US equities is the most analysed dataset in the history of finance.
Thousands of well-funded professionals with better data, lower costs and faster
execution have been over it for decades.

The odds that a price-only signal survives *there* that we can find with daily
bars are low. Our merger-arb test is the evidence: a genuinely good idea,
properly built, **negative expected value**.

### 3. AI does not give you private information

The honest version of "use AI to beat the market":

- ❌ **Won't work:** asking a model for trading ideas. Every published anomaly
  is in its training data, which means it is crowded by definition.
- ✅ **Does work:** using AI as a *research throughput multiplier*. What took a
  solo researcher a week now takes an hour, with the discipline maintained
  consistently rather than abandoned when it gets tedious.

The edge is not the ideas. **The edge is the rejection rate.**

---

## So what IS our edge?

Not data. Not speed. Not cost. All three are worse than a professional's.

**What we actually have:**

1. **Capacity indifference.** An effect with €50m of capacity is worthless to a
   fund and perfectly sufficient for us.
2. **No career risk.** We can hold cash for a year, accept high tracking error,
   and reject 99% of ideas without anyone demanding we deploy.
3. **Industrial-scale rejection.** With AI, we can test more hypotheses, more
   rigorously, than a solo human ever could — and never get bored enough to
   skip the walk-forward.

**Our edge is a disciplined search process, not a strategy.** That reframes the
goal completely.

---

## The end goal, stated plainly

> **Arrive at the point where capital — not capability — is the constraint.**

Concretely: by the time the account reaches €50–100k (through savings, mostly,
not returns), have a system that has been running for years, whose costs are
verified against real statements, and whose edges have survived honest
out-of-sample testing.

**At €10k you are not trying to make money. You are earning the right to deploy
serious capital later.** The account is a testbed. The system is the asset.

This also resolves the economics: spending time now on a system that returns
€200/year is absurd *if* €10k is the endpoint. It is entirely rational if the
system is still running when the account is 10× larger.

---

## The steps, in order

### Step 1 — Trade something. Anything. *(2–4 weeks)* — **half done**

**Done: the simulated path.** `scripts/paper_session.py` replays two years of
real daily US bars through signal → FX funding → risk gate → order → fill →
ledger → persisted state. It found four defects, every one of them invisible to
a single-currency backtest:

1. The FX rate was inverted (overvalues USD holdings by 32%)
2. FX funding ran *after* the risk gate, so nothing ever converted or traded
3. The currency budget double-counted a same-currency purchase, stalling gross
   exposure at 0.51x
4. The strategy divided EUR equity by a USD price — a currency error that looks
   like the risk gate refusing to fill the book

It now runs 501 sessions with **zero risk rejections**, and established a
constraint that shapes strategy design: whole-share rounding across ten US
mega-caps caps achievable gross exposure at **0.806x** on a €10,000 account. A
signal needing 20 concurrent positions is not implementable at this size
without fractional shares. See [10 Paper trading](10-paper-trading.md).

**Remaining: the broker path.** Connect IB Gateway, run the same runner against
the IBKR paper account, compare modelled costs against real fills.

This needs a machine that stays on, because the US close is 23:00 Helsinki and
a laptop that has to be awake for it eventually will not be. A €4/month virtual
server solves it; the full runbook, security posture and the read-only smoke
test are in [deploy/README.md](../deploy/README.md). The server is the easy
part — IBKR's 2FA under an unattended login is the thing to establish first,
and it costs nothing to establish on the paper account.

**Success looks like:** zero unexplained reconciliation breaks over 20 sessions,
and a cost model within 20% of actual commissions.

*Why first: every hour of alpha research is wasted if the execution path is
broken. It was — in four separate places.*

---

### Step 2 — Build the research ledger *(~1 week)* — **done**

A permanent, machine-readable record of **every hypothesis ever tested**, at
`research/ledger.jsonl`. Append-only, SHA-256 chained, seeded with the **37
configurations** already evaluated.

This is not bookkeeping. Over three years of daily data the expected maximum
annualised Sharpe of *worthless* strategies is 0.91 at 10 trials, 1.32 at 50
and 1.60 at 200 — so a Sharpe of 1.3 is a respectable single-test result and is
precisely what fifty worthless strategies produce by chance. That count was
held nowhere.

Editing or removing an entry breaks the chain and the CLI names the first
failing entry; appending to a broken chain is refused. Superseding a trial
needs a written reason and the superseded trial still counts.
`deflate_from_ledger()` reads `n_trials` from the record so it stops being an
argument anyone chooses.

```bash
.venv/bin/python -m tradelab.cli ledger
```

See [11 Research ledger](11-research-ledger.md).

**Success looks like:** any future session, with any model, can answer "how many
hypotheses have we tested?" and get the true number. It can: 37, and rising.

---

### Step 3 — Industrial hypothesis testing *(ongoing)*

Now the AI multiplier applies. Target **20–50 properly tested hypotheses per
quarter**, versus the ~10 done so far in six sessions.

Queue, in priority order:

| | Hypothesis | Why |
|---|---|---|
| 1 | **Dividend cut drift** | Only remaining candidate with adequate statistical power (~715 events) |
| 2 | **Drawdown reversal, honestly measured** | Every published test is survivorship-biased; we can do it properly. Expect rejection |
| 3 | **PEAD** | Needs €60 for one month of Fundamentals data. Decide if worth it |
| 4 | Systematic generation | Let the model propose from structure, not from published lists |

**Expect ≥90% rejection.** That is the process working, not failing.

---

### Step 4 — Decision gate *(~6 months in)*

Honest review:

- **Something survived** → deploy at 25% size, continue paper on the rest
- **Nothing survived** → index the core capital, keep the research running at
  low intensity. This is a perfectly respectable outcome
- **The system is unreliable** → fix it, or stop

---

### Step 5 — The scale trigger

At **€50k+**, re-run the feasibility screen. Round-trip cost falls from ~30 bps
to ~5–10 bps, which resurrects several hypotheses rejected purely on cost
(short-term reversal, cross-sectional momentum, turn-of-month).

**Several rejections in the register are rejections of our account size, not of
the ideas.**

---

## Architecture for an evolving AI

So that a better model next year is immediately more useful:

**Separate the harness from the researcher.**

- **The harness** (data, cost model, risk engine, validation protocol) is
  stable, tested code. It does not change when the model changes.
- **The researcher** (hypothesis generation, feature design, interpretation) is
  the AI. A newer model plugs into the same harness and is simply better at it.

**Three rules that make this work:**

1. **Discipline lives in code, not in the model's intentions.** The protocol,
   the trial count, the pre-registration — all enforced by the harness. *A
   smarter model is also better at rationalising a result it likes.*
2. **State is machine-readable.** The hypothesis register and research ledger
   are data files, not prose, so any model can read the full history and
   continue without re-deriving it.
3. **Results are immutable.** Once a test is run, it is recorded — including
   the failures. This is what keeps the multiple-testing correction honest
   across years and across models.

---

## My honest assessment

**Probability that we find a real, tradable edge in 12 months: maybe 15–25%.**

That is not pessimism, it is the base rate. Most professional quant research
programmes, with better resources, also fail to find durable new edges.

**What I would actually do:**

1. Do Step 1 now. Not trading anything after six sessions of building is the
   real risk in this project.
2. Build the ledger. Without it, everything downstream is statistically
   unsound.
3. Keep the core capital in a low-cost index fund while researching. The
   opportunity cost of *not* being invested exceeds any expected research
   return at this size.
4. Treat the research as a multi-year capability build, funded by savings, not
   as a way to make money this year.

**The single best decision available is to stop building and start trading —
even badly, even trivially — so that the next six months of research is grounded
in what actually happens when orders hit a real broker.**
