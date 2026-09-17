"""Event-driven backtest engine.

Why a custom engine rather than an off-the-shelf one: the requirement is that
paper and live run *the same code*. That is only achievable if the engine's
strategy-facing contract is identical across modes, which means owning the
contract. Existing frameworks either couple the strategy API to their own
backtest internals, hide the fill model, or are unmaintained.

The per-timestamp sequence is fixed and its order is the whole anti-lookahead
argument:

    1. Advance the clock to the bar timestamp.
    2. Publish bars into strategy history and mark positions.
    3. Fill orders resting from *previous* timestamps against this bar.
    4. Call `strategy.on_bar` -- it now sees this bar as the latest closed bar.
    5. Pass emitted intents through the risk gate.
    6. Submit survivors, which become eligible to fill from the *next* bar.

Step 3 before step 4 is what makes "orders fill after the signal" structural.
Step 5 between 4 and 6 is what makes the risk gate unbypassable. A strategy
cannot reorder these because it does not drive the loop.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from tradelab.core.clock import SimulationClock
from tradelab.core.enums import RiskDecision, RunMode
from tradelab.core.ids import IdGenerator, new_run_id
from tradelab.core.money import ZERO
from tradelab.core.types import Bar, Fill, OrderRequest, Quote
from tradelab.costs.commission import CommissionModel, ibkr_default_router
from tradelab.costs.slippage import SlippageModel, SpreadImpactSlippage
from tradelab.execution.sim_broker import SimulatedBroker, SimulationConfig
from tradelab.portfolio.portfolio import EquityPoint, Portfolio
from tradelab.risk.engine import RiskContext, RiskEngine, dedupe_key
from tradelab.risk.killswitch import RiskState
from tradelab.risk.limits import RiskLimits
from tradelab.strategy.base import Strategy, StrategyContext


@dataclass
class RejectionRecord:
    timestamp: datetime
    strategy_id: str
    symbol: str
    quantity: Decimal
    reason: str


@dataclass
class BacktestResult:
    """Everything needed to judge a run, including why orders were blocked.

    `rejections` is not diagnostics -- it is a primary result. A strategy whose
    orders are 80% rejected on cost grounds is not a strategy with a small edge;
    it is a strategy that cannot be traded at this account size, and the equity
    curve alone will not tell you that.
    """

    run_id: str
    equity_curve: list[EquityPoint]
    fills: list[Fill]
    rejections: list[RejectionRecord]
    portfolio: Portfolio
    start: datetime | None
    end: datetime | None
    bars_processed: int
    orders_submitted: int
    orders_resized: int

    @property
    def final_equity(self) -> Decimal:
        return self.equity_curve[-1].equity if self.equity_curve else ZERO

    @property
    def initial_equity(self) -> Decimal:
        return self.equity_curve[0].equity if self.equity_curve else ZERO

    @property
    def total_commission(self) -> Decimal:
        return sum((f.commission + f.fees for f in self.fills), ZERO)

    @property
    def total_return(self) -> Decimal:
        if self.initial_equity == 0:
            return ZERO
        return (self.final_equity - self.initial_equity) / self.initial_equity

    def rejection_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self.rejections:
            key = rec.reason.split(":")[0].strip() or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def cost_drag_bps(self) -> Decimal:
        """Total explicit cost as bps of initial equity. The honesty metric."""
        if self.initial_equity == 0:
            return ZERO
        return (self.total_commission / self.initial_equity) * Decimal("10000")


@dataclass
class BacktestEngine:
    """Runs one strategy (or several) over a bar stream."""

    strategies: list[Strategy]
    initial_cash: Decimal = Decimal("10000")
    base_currency: str = "EUR"
    limits: RiskLimits = field(default_factory=RiskLimits)
    commission_model: CommissionModel = field(default_factory=ibkr_default_router)
    slippage_model: SlippageModel = field(default_factory=SpreadImpactSlippage)
    sim_config: SimulationConfig = field(default_factory=SimulationConfig)
    fx_rates: dict[str, Decimal] = field(default_factory=dict)
    history_size: int = 512
    record_equity_every_bar: bool = True

    def run(
        self,
        bars: list[Bar],
        quotes: list[Quote] | None = None,
    ) -> BacktestResult:
        if not bars:
            raise ValueError("no bars supplied; nothing to backtest")

        ordered = sorted(bars, key=lambda b: (b.timestamp, b.instrument.key))
        quote_index = self._index_quotes(quotes or [])

        run_id = new_run_id("BACKTEST", ordered[0].timestamp)
        clock = SimulationClock(ordered[0].timestamp)
        portfolio = Portfolio(
            base_currency=self.base_currency,
            max_leverage=self.limits.portfolio.max_gross_exposure,
            fx_rates=dict(self.fx_rates),
        )
        portfolio.deposit(self.initial_cash)
        broker = SimulatedBroker(
            clock=clock,
            portfolio=portfolio,
            mode=RunMode.BACKTEST,
            commission_model=self.commission_model,
            slippage_model=self.slippage_model,
            config=self.sim_config,
            account_id=run_id,
        )
        broker.connect()
        risk = RiskEngine(limits=self.limits)
        state = RiskState()
        ids = IdGenerator(run_id)

        histories: dict[str, deque[Bar]] = {}
        live_quotes: dict[str, Quote] = {}
        price_stamps: dict[str, datetime] = {}
        all_fills: list[Fill] = []
        rejections: list[RejectionRecord] = []
        submitted = 0
        resized = 0

        contexts = {
            s.strategy_id: StrategyContext(
                now=clock.now(),
                strategy_id=s.strategy_id,
                equity=portfolio.equity,
                _bars=histories,
                _quotes=live_quotes,
                _positions=portfolio.positions,
            )
            for s in self.strategies
        }

        def record_fill(fill: Fill) -> None:
            all_fills.append(fill)
            ctx = contexts.get(fill.strategy_id)
            strategy = next((s for s in self.strategies if s.strategy_id == fill.strategy_id), None)
            if ctx is not None and strategy is not None:
                strategy.on_fill(ctx, fill)

        broker.on_fill(record_fill)

        for strategy in self.strategies:
            strategy.on_start(contexts[strategy.strategy_id])

        session: date | None = None
        bars_processed = 0

        for timestamp, group in _group_by_timestamp(ordered):
            # ---- 1. advance the clock
            clock.set(timestamp)
            if session != timestamp.date():
                if session is not None:
                    state.end_session(portfolio.equity)
                    state.kill_switch.clear_soft(timestamp)
                session = timestamp.date()
                state.start_session(session, portfolio.equity)

            # ---- 2. publish bars and mark positions
            for bar in group:
                key = bar.instrument.key
                histories.setdefault(key, deque(maxlen=self.history_size)).append(bar)
                price_stamps[key] = timestamp
                portfolio.mark(bar.instrument, bar.close, timestamp)
                q = quote_index.get((key, timestamp))
                if q is not None:
                    live_quotes[key] = q
                    broker.update_quote(q)
                bars_processed += 1

            state.observe_equity(portfolio.equity)

            # ---- 3. fill orders resting from previous timestamps
            for bar in group:
                broker.process_bar(bar)

            # ---- 4. strategies see this bar as the latest closed bar
            bar_map = {b.instrument.key: b for b in group}
            open_keys = {o.instrument.key for o in broker.open_orders()}
            intents: list[OrderRequest] = []
            for strategy in self.strategies:
                ctx = contexts[strategy.strategy_id]
                ctx.now = timestamp
                ctx.equity = portfolio.equity
                ctx._open_order_keys = open_keys
                relevant = {
                    inst.key: bar_map[inst.key] for inst in strategy.universe if inst.key in bar_map
                }
                if not relevant or not strategy.is_warm(ctx):
                    ctx.drain()
                    continue
                strategy.on_bar(ctx, relevant)
                intents.extend(ctx.drain())

            if not intents:
                if self.record_equity_every_bar:
                    portfolio.record_equity(timestamp)
                continue

            # ---- 5. risk gate
            risk_ctx = RiskContext(
                portfolio=portfolio,
                state=state,
                limits=self.limits,
                now=timestamp,
                reference_prices={k: b.close for k, b in bar_map.items()},
                quotes=live_quotes,
                price_timestamps=price_stamps,
                commission_model=self.commission_model,
                slippage_model=self.slippage_model,
                market_open=True,
            )
            for intent in intents:
                result = risk.evaluate(intent, risk_ctx)
                if not result.is_approved:
                    rejections.append(
                        RejectionRecord(
                            timestamp=timestamp,
                            strategy_id=intent.strategy_id,
                            symbol=intent.instrument.symbol,
                            quantity=intent.quantity,
                            reason=result.reason,
                        )
                    )
                    continue
                approved = result.approved_request
                if result.decision is RiskDecision.RESIZE:
                    resized += 1
                # ---- 6. submit; eligible to fill from the next bar
                was_flat = portfolio.quantity_of(approved.instrument) == 0
                broker.submit(ids.next("ord"), approved)
                state.record_order(timestamp, dedupe_key(approved))
                if was_flat:
                    state.new_positions_today += 1
                submitted += 1

            if self.record_equity_every_bar:
                portfolio.record_equity(timestamp)

        if not self.record_equity_every_bar or not portfolio.equity_curve:
            portfolio.record_equity(ordered[-1].timestamp)

        for strategy in self.strategies:
            strategy.on_finish(contexts[strategy.strategy_id])

        return BacktestResult(
            run_id=run_id,
            equity_curve=portfolio.equity_curve,
            fills=all_fills,
            rejections=rejections,
            portfolio=portfolio,
            start=ordered[0].timestamp,
            end=ordered[-1].timestamp,
            bars_processed=bars_processed,
            orders_submitted=submitted,
            orders_resized=resized,
        )

    @staticmethod
    def _index_quotes(quotes: list[Quote]) -> dict[tuple[str, datetime], Quote]:
        return {(q.instrument.key, q.timestamp): q for q in quotes}


def _group_by_timestamp(bars: list[Bar]):
    """Yield (timestamp, bars) groups. Input must be sorted by timestamp."""
    if not bars:
        return
    current = bars[0].timestamp
    batch: list[Bar] = []
    for bar in bars:
        if bar.timestamp != current:
            yield current, batch
            current, batch = bar.timestamp, []
        batch.append(bar)
    if batch:
        yield current, batch
