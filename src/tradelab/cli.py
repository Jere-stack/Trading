"""Command-line interface.

Kept deliberately small. The CLI is a thin entry point; anything worth testing
lives in a module, not in a command handler.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from tradelab.core.enums import RunMode

app = typer.Typer(
    add_completion=False,
    help="Stocks-only automated trading system with cost-realistic backtesting.",
    no_args_is_help=True,
)


@app.command()
def config(
    mode: str = typer.Option("BACKTEST", help="BACKTEST | PAPER | LIVE"),
    config_dir: Path = typer.Option(Path("config"), help="Directory holding the YAML config"),
) -> None:
    """Load and validate configuration, printing the resolved settings.

    Run this before every live session. It is the cheapest way to catch a
    misconfigured limit, and in LIVE mode it applies additional safety checks
    that will refuse dangerous combinations outright.
    """
    from tradelab.config.settings import load_settings

    try:
        settings = load_settings(config_dir, RunMode(mode.upper()))
    except Exception as exc:
        typer.secho(f"configuration rejected:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho(f"configuration valid for {settings.mode.value}", fg=typer.colors.GREEN)
    typer.echo(
        json.dumps(
            {
                "mode": settings.mode.value,
                "base_currency": settings.base_currency,
                "initial_cash": str(settings.initial_cash),
                "broker": f"{settings.broker.name} {settings.broker.host}:{settings.broker.port}",
                "commission_schedule": settings.costs.commission_schedule,
                "max_position_weight": str(settings.risk.position.max_position_weight),
                "min_order_notional": str(settings.risk.position.min_order_notional),
                "max_cost_bps": str(settings.risk.position.max_cost_bps_of_notional),
                "max_open_positions": settings.risk.portfolio.max_open_positions,
                "max_daily_loss_pct": str(settings.risk.loss.max_daily_loss_pct),
                "max_drawdown_pct": str(settings.risk.loss.max_drawdown_pct),
                "strategy_budgets": {k: str(v) for k, v in settings.risk.strategy_budgets.items()},
            },
            indent=2,
        )
    )


@app.command()
def costs() -> None:
    """Print the transaction cost report (regenerates the broker-doc figures)."""
    from scripts.cost_report import (
        break_even_summary,
        fx_policy,
        microcap_trap,
        round_trip_commission_table,
        tiered_vs_fixed,
    )

    round_trip_commission_table()
    tiered_vs_fixed()
    fx_policy()
    microcap_trap()
    break_even_summary()


@app.command()
def screen() -> None:
    """Screen strategy hypotheses on cost and statistical power, before any data."""
    from scripts.hypothesis_screen import main as run_screen

    run_screen()


@app.command("check-broker")
def check_broker(
    mode: str = typer.Option("PAPER", help="PAPER | LIVE"),
    config_dir: Path = typer.Option(Path("config")),
) -> None:
    """Connect to IB Gateway, reconcile, and report account state.

    Run this before every session. It verifies the connection, confirms the
    port matches the intended mode, and prints broker-reported positions so
    that a reconciliation break is visible before any order is sent.
    """
    from tradelab.config.settings import load_settings
    from tradelab.execution.broker import BrokerError
    from tradelab.execution.ibkr_broker import IbkrBroker

    settings = load_settings(config_dir, RunMode(mode.upper()))
    broker = IbkrBroker(
        host=settings.broker.host,
        port=settings.broker.port,
        client_id=settings.broker.client_id,
        account_id=settings.broker.account_id,
        mode=settings.mode,
        readonly=True,  # Inspection only: never submit from this command.
    )
    try:
        broker.connect()
    except BrokerError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    try:
        account = broker.account()
        typer.secho(
            f"connected: account {account.account_id}, equity "
            f"{account.equity} {account.base_currency}",
            fg=typer.colors.GREEN,
        )
        for ccy, amount in sorted(account.cash.items()):
            typer.echo(f"  cash {ccy}: {amount}")
        positions = broker.positions()
        typer.echo(f"  broker reports {len(positions)} open position(s):")
        for position in positions:
            typer.echo(
                f"    {position.instrument.key}: {position.quantity} @ {position.average_price}"
            )
        if broker.errors:
            typer.secho(
                f"  {len(broker.errors)} API error(s) during session:", fg=typer.colors.YELLOW
            )
            for code, message in broker.errors[:10]:
                typer.echo(f"    [{code}] {message}")
    finally:
        broker.disconnect()


if __name__ == "__main__":
    app()
