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


data_app = typer.Typer(help="Market data: fetch, audit and calibrate.", no_args_is_help=True)
app.add_typer(data_app, name="data")


@data_app.command("fetch")
def data_fetch(
    dataset: str = typer.Option(..., help="Name to store the dataset under"),
    symbols: str = typer.Option(..., help="Comma-separated symbols"),
    years: int = typer.Option(10, help="Years of history to request"),
    currency: str = typer.Option("USD"),
    exchange: str = typer.Option("SMART", help="SMART, HEX, NASDAQ, ..."),
    root: Path = typer.Option(Path("data")),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(7497, help="7497 paper, 7496 live"),
    client_id: int = typer.Option(11, help="Use a different id than the trading session"),
    overwrite: bool = typer.Option(False, help="Replace an existing dataset"),
) -> None:
    """Fetch historical bars from IBKR and store them with provenance.

    Requires IB Gateway running and `uv pip install -e '.[ibkr]'`.

    The fetch is deliberately slow: IBKR throttles historical requests and
    exceeding the limit drops the connection rather than returning an error.
    Expect roughly 11 seconds per request chunk.
    """
    from datetime import UTC, datetime, timedelta

    from tradelab.core.enums import Venue
    from tradelab.data.providers.base import ProviderError
    from tradelab.data.providers.ibkr import IbkrBarProvider
    from tradelab.data.quality import audit
    from tradelab.data.schema import BarSetMetadata
    from tradelab.data.store import BarStore

    try:
        import ib_async
    except ImportError as exc:
        typer.secho(
            "ib_async is not installed. Install with: uv pip install -e '.[ibkr]'",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not tickers:
        typer.secho("no symbols given", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    ib = ib_async.IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=30, readonly=True)
    except Exception as exc:
        typer.secho(
            f"could not connect to IB Gateway at {host}:{port}: {exc}\n"
            "Check it is running, the API is enabled, and today's "
            "re-authentication is done.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    try:
        provider = IbkrBarProvider(ib, currency=currency, venue=Venue(exchange.upper()))
        end = datetime.now(UTC)
        start = end - timedelta(days=365 * years)
        typer.echo(f"fetching {len(tickers)} symbol(s) from {provider.describe()}...")
        try:
            frame = provider.fetch(tickers, start, end, "1 day")
        except ProviderError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        metadata = BarSetMetadata(
            provider=provider.name,
            adjustment=provider.adjustment,
            bar_size="1 day",
            currency=currency.upper(),
            fetched_at=datetime.now(UTC),
            symbols=tuple(sorted(frame["symbol"].unique())),
            start=frame["timestamp"].min().to_pydatetime(),
            end=frame["timestamp"].max().to_pydatetime(),
            includes_delisted=provider.includes_delisted,
            notes=f"fetched via CLI, exchange={exchange}",
        )
        path = BarStore(root).write(dataset, frame, metadata, overwrite=overwrite)
        typer.secho(
            f"stored {len(frame):,} bars for {len(metadata.symbols)} symbol(s) at {path}",
            fg=typer.colors.GREEN,
        )

        report = audit(frame, adjustment=provider.adjustment)
        typer.echo("\n" + report.summary())
        if report.critical:
            typer.secho(
                "\nThis dataset has CRITICAL issues and is not safe to draw "
                "conclusions from. IBKR cannot serve delisted contracts, so an "
                "IBKR-built universe is survivorship-biased -- see docs/04-data.md.",
                fg=typer.colors.RED,
            )
    finally:
        ib.disconnect()


@data_app.command("audit")
def data_audit(
    dataset: str = typer.Option(..., help="Dataset name under <root>/bars/"),
    root: Path = typer.Option(Path("data"), help="Data root directory"),
    strict: bool = typer.Option(True, help="Exit non-zero on any CRITICAL issue"),
) -> None:
    """Audit a stored dataset for the five ways market data lies.

    Run this before any research. Data problems do not crash -- they change the
    answer, almost always in the flattering direction.
    """
    from tradelab.data.quality import audit
    from tradelab.data.store import BarStore

    store = BarStore(root)
    try:
        frame = store.read(dataset)
        metadata = store.metadata(dataset)
    except (FileNotFoundError, OSError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"dataset '{dataset}': {metadata.provider}, "
        f"adjustment={metadata.adjustment.value}, "
        f"includes_delisted={metadata.includes_delisted}"
    )
    report = audit(frame, adjustment=metadata.adjustment)
    typer.echo(report.summary())

    if report.critical:
        typer.secho(
            f"\n{len(report.critical)} CRITICAL issue(s): this data is not safe to "
            "draw conclusions from.",
            fg=typer.colors.RED,
        )
        if strict:
            raise typer.Exit(code=1)
    elif report.warnings:
        typer.secho(
            f"\n{len(report.warnings)} warning(s) -- read them before proceeding.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho("\nno issues detected", fg=typer.colors.GREEN)


@data_app.command("calibrate")
def data_calibrate(
    dataset: str = typer.Option(..., help="Dataset name under <root>/bars/"),
    root: Path = typer.Option(Path("data"), help="Data root directory"),
    notional: float = typer.Option(1000.0, help="Position size to screen against"),
    max_cost_bps: float = typer.Option(35.0, help="One-way cost budget in bps"),
    lookback: int = typer.Option(252, help="Bars used for the estimate"),
) -> None:
    """Measure ADV, volatility and spread, then screen on affordability.

    This is what removes the standing caveat that every cost figure is a
    modelled default.
    """
    from tradelab.data.calibration import calibrate, screen_universe
    from tradelab.data.store import BarStore

    store = BarStore(root)
    try:
        frame = store.read(dataset)
    except (FileNotFoundError, OSError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    stats = calibrate(frame, lookback=lookback)
    if not stats:
        typer.secho("no symbol had enough history to calibrate", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo(f"calibrated {len(stats)} instrument(s) over {lookback} bars:\n")
    for s in sorted(stats.values(), key=lambda x: -x.adv_currency):
        typer.echo(f"  {s.summary()}")

    screen = screen_universe(stats, position_notional=notional, max_cost_bps=max_cost_bps)
    typer.echo(f"\n{screen.summary()}")
    if screen.tradable:
        names = ", ".join(s.symbol for s in screen.tradable)
        typer.secho(f"\ntradable: {names}", fg=typer.colors.GREEN)
    else:
        typer.secho(
            "\nnothing in this universe is affordable at that position size",
            fg=typer.colors.RED,
        )


@data_app.command("fx")
def data_fx(
    base: str = typer.Option("EUR"),
    quote: str = typer.Option("USD"),
    years: int = typer.Option(3, help="Years of history to summarise"),
) -> None:
    """Fetch ECB reference rates and report the FX risk they imply."""
    import numpy as np

    from tradelab.data.providers.base import ProviderError
    from tradelab.data.providers.ecb_fx import EcbFxProvider

    provider = EcbFxProvider()
    try:
        history = provider.load()
        end = history.index.max().to_pydatetime()
        start = end.replace(year=end.year - years)
        series = provider.rates(base, quote, start, end)
    except ProviderError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    returns = np.diff(np.log(series.to_numpy()))
    vol = float(returns.std() * np.sqrt(252))
    typer.echo(
        f"{base}/{quote}: {len(series)} observations "
        f"{series.index.min():%Y-%m-%d} -> {series.index.max():%Y-%m-%d}"
    )
    typer.echo(f"  last {series.iloc[-1]:.4f}  min {series.min():.4f}  max {series.max():.4f}")
    typer.echo(f"  annualised volatility {vol:.2%}")
    typer.echo(f"  peak-to-trough {(series.max() / series.min() - 1):.1%}")
    typer.secho(
        f"\n  Unhedged {quote} exposure carries {vol:.1%} annual volatility, "
        "uncompensated.\n  A strategy earning 30 bps over 12 round trips a year "
        "makes ~3.6% gross.",
        fg=typer.colors.YELLOW,
    )


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
