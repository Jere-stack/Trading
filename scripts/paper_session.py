#!/usr/bin/env python
"""Run one paper-trading session. Resumable, auditable, committed to git.

Run:  .venv/bin/python scripts/paper_session.py

**Purpose is plumbing, not profit.** This exercises the full live path --
signal, risk gate, order, fill, ledger, persisted state, session rollover --
against real daily prices, using the simulator's cost model in place of a
broker. What it validates and what it does not:

    validates    the runner, risk engine, cost model, state persistence,
                 session handling, and the strategy interface
    does NOT     IBKR API integration, real fill prices, real commissions

Those need IB Gateway on a real machine, and are the second half of Step 1.

**State lives in git**, because the container this runs in is wiped between
sessions. Committing `state/paper/` after each run makes the track record
durable, and makes it auditable: the equity curve, every order and every fill
are in version history, so a result cannot be quietly revised later. That
property is worth more here than the mild awkwardness of committing a binary.

The run is resumable. It replays only sessions newer than the last one
recorded, so running it twice in a day is a no-op rather than a double-trade.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from tradelab.core.clock import SimulationClock
from tradelab.core.enums import RunMode, Venue
from tradelab.core.types import Instrument
from tradelab.costs.commission import ibkr_default_router
from tradelab.costs.slippage import SpreadImpactSlippage
from tradelab.data.providers.eodhd import EodhdProvider
from tradelab.data.store import BarStore
from tradelab.engine.live import LiveRunner
from tradelab.execution.sim_broker import SimulatedBroker
from tradelab.portfolio.state import StateStore
from tradelab.risk.limits import (
    OperationalLimits,
    PortfolioLimits,
    PositionLimits,
    RiskLimits,
)
from tradelab.strategy.examples.monthly_equal_weight import MonthlyEqualWeight

# Ten large, liquid US names. Chosen for liquidity and sector spread, not for
# any expected return -- this basket is a test instrument.
UNIVERSE = ["AAPL", "MSFT", "JNJ", "PG", "XOM", "JPM", "UNH", "HD", "KO", "VZ"]

STATE_DIR = Path("state/paper")
CONFIG_PATH = STATE_DIR / "config.json"


def build_instruments(spreads: dict[str, float] | None = None) -> list[Instrument]:
    spreads = spreads or {}
    return [
        Instrument(
            symbol=symbol,
            venue=Venue.SMART,
            currency="USD",
            adv=Decimal("5000000"),
            sigma_daily=Decimal("0.018"),
            spread_bps=Decimal(str(spreads.get(symbol, 2.0))),
        )
        for symbol in UNIVERSE
    ]


def paper_limits() -> RiskLimits:
    """Production defaults, loosened only where a 10-name basket needs it."""
    return RiskLimits(
        position=PositionLimits(
            max_position_weight=Decimal("0.15"),
            min_order_notional=Decimal("400"),
            max_cost_bps_of_notional=Decimal("35"),
            max_participation_of_adv=Decimal("0.01"),
        ),
        operational=OperationalLimits(
            # Daily bars, so "stale" must tolerate weekends and holidays. The
            # 300s default is calibrated for an intraday feed and would reject
            # every Monday.
            max_stale_data_seconds=4 * 24 * 3600,
            max_orders_per_day=60,
        ),
        portfolio=PortfolioLimits(
            max_open_positions=12,
            max_new_positions_per_day=12,
            max_gross_exposure=Decimal("1.0"),
            max_currency_exposure=Decimal("1.0"),  # USD-only book, EUR base
        ),
    )


def fetch_bars(instruments: list[Instrument], years: int, token: str | None) -> pd.DataFrame:
    provider = EodhdProvider(api_token=token, exchange="US", max_workers=8)
    end = datetime.now(UTC)
    start = end - timedelta(days=365 * years)
    return provider.fetch([i.symbol for i in instruments], start, end, "1 day")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--years", type=int, default=2, help="History to seed from")
    parser.add_argument("--eur-usd", type=float, default=1.1481)
    parser.add_argument("--token", default=None, help="Defaults to EODHD_API_TOKEN")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATE_DIR / "paper.sqlite"
    first_run = not state_path.exists()

    print("=" * 74)
    print(
        f"PAPER SESSION  {datetime.now(UTC):%Y-%m-%d %H:%M UTC}"
        f"{'   (FIRST RUN)' if first_run else ''}"
    )
    print("=" * 74)

    instruments = build_instruments()
    print(f"fetching {len(instruments)} symbols, {args.years}y...")
    bars_frame = fetch_bars(instruments, args.years, args.token)
    print(
        f"  {len(bars_frame):,} bars, "
        f"{bars_frame['timestamp'].min():%Y-%m-%d} -> {bars_frame['timestamp'].max():%Y-%m-%d}"
    )

    store = StateStore(state_path)
    history = store.equity_history()
    last_processed = history[-1][0] if history else None
    if last_processed:
        print(
            f"  resuming after {last_processed:%Y-%m-%d} ({len(history)} sessions already recorded)"
        )
        bars_frame = bars_frame[bars_frame["timestamp"] > pd.Timestamp(last_processed)]
        if bars_frame.empty:
            print("\nno new sessions since the last run; nothing to do.")
            return

    by_key = {i.symbol: i for i in instruments}
    bar_store = BarStore("data")
    bars = bar_store.to_bars(bars_frame, {k: v for k, v in by_key.items()}, currency="USD")
    bars.sort(key=lambda b: (b.timestamp, b.instrument.symbol))
    print(f"  {len({b.timestamp for b in bars})} session(s) to process")

    clock = SimulationClock(bars[0].timestamp)
    runner = LiveRunner(
        strategies=[MonthlyEqualWeight(instruments)],
        broker=None,
        limits=paper_limits(),
        mode=RunMode.PAPER,
        base_currency="EUR",
        clock=clock,
        state_store=store,
        commission_model=ibkr_default_router(),
        slippage_model=SpreadImpactSlippage(),
        fx_rates={"USD": Decimal(str(args.eur_usd))},
    )
    runner.broker = SimulatedBroker(
        clock=clock,
        portfolio=runner.portfolio,
        mode=RunMode.PAPER,
        commission_model=ibkr_default_router(),
        slippage_model=SpreadImpactSlippage(),
        owns_ledger=False,  # the runner owns the ledger; see SimulatedBroker
        account_id="PAPER",
    )
    if first_run:
        runner.portfolio.deposit(Decimal(str(args.initial_cash)))

    if args.dry_run:
        print("\ndry run: stopping before any orders are placed.")
        return

    runner.start()

    # Mirror the backtest ordering exactly: fill resting orders against this
    # bar BEFORE the strategy sees it, so an order cannot fill on its own
    # signal bar.
    grouped: dict[datetime, list] = {}
    for bar in bars:
        grouped.setdefault(bar.timestamp, []).append(bar)

    for timestamp in sorted(grouped):
        clock.set(timestamp)
        for bar in grouped[timestamp]:
            runner.broker.process_bar(bar)
        # Publish the whole session at once. Dispatching per bar would run the
        # strategy while the rest of the universe still holds yesterday's
        # prices.
        runner.on_bars(grouped[timestamp])

    equity = runner.portfolio.equity
    print()
    print("=" * 74)
    print("RESULT")
    print("=" * 74)
    print(f"  equity           EUR {equity:>12,.2f}")
    print(f"  cash             {runner.portfolio.cash}")
    print(
        f"  gross exposure   EUR {runner.portfolio.gross_exposure:>12,.2f} "
        f"({runner.portfolio.leverage:.2f}x)"
    )
    print(f"  open positions   {len(runner.portfolio.open_positions)}")
    for key, position in sorted(runner.portfolio.open_positions.items()):
        print(
            f"    {key:<20} {position.quantity:>8} @ {position.average_price:>9.2f} "
            f"-> {position.last_price:>9.2f}  upnl {position.unrealised_pnl:>10,.2f}"
        )

    fills = store.recent_events(500)
    n_fills = sum(1 for e in fills if e["kind"] == "fill")
    n_rejects = sum(1 for e in fills if e["kind"] == "risk_reject")
    print(f"\n  fills this run   {n_fills}")
    print(f"  risk rejections  {n_rejects}")
    for event in [e for e in fills if e["kind"] == "risk_reject"][:5]:
        print(f"    {event['message'][:100]}")

    runner.stop(cancel_open_orders=True)

    CONFIG_PATH.write_text(
        json.dumps(
            {
                "universe": UNIVERSE,
                "strategy": "monthly_equal_weight",
                "initial_cash_eur": args.initial_cash,
                "base_currency": "EUR",
                "started": history[0][0].isoformat() if history else datetime.now(UTC).isoformat(),
                "last_session": max(grouped).isoformat(),
                "purpose": (
                    "Plumbing validation, not alpha. Simulated fills against real "
                    "daily prices. Does not validate IBKR API integration."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nstate persisted to {state_path} -- commit it to make the run durable.")


if __name__ == "__main__":
    main()
