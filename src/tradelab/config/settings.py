"""Configuration loading.

Config is validated at load time, loudly. The alternative -- discovering that
`max_position_weight: 50` meant 50% but parsed as 5000% -- happens at 03:00 in a
live session, and by then it has already traded.

Layering: `base.yaml` holds everything mode-independent, then `paper.yaml` or
`live.yaml` overlays mode-specific values. Secrets never appear in either; they
come from the environment. A repository that can hold a live account number is
one commit away from holding it publicly.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tradelab.core.enums import RunMode
from tradelab.risk.limits import RiskLimits


class BrokerSettings(BaseModel):
    """Broker connection. Account ids come from the environment, not from YAML."""

    model_config = {"extra": "forbid"}

    name: str = "ibkr"
    host: str = "127.0.0.1"
    port: int = Field(default=7497, gt=0, lt=65536)
    client_id: int = Field(default=1, ge=0)
    account_id: str | None = None
    connect_timeout_seconds: int = Field(default=30, gt=0)
    readonly: bool = Field(
        default=False, description="Connect without order permissions. Useful for inspection."
    )
    max_reconnect_attempts: int = Field(default=5, ge=0)
    reconnect_backoff_seconds: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def _warn_on_live_port(self) -> BrokerSettings:
        # 7496 is IBKR's live port. Guard against it appearing in a paper config.
        if self.port == 7496 and self.name == "ibkr":
            object.__setattr__(self, "_is_live_port", True)
        return self


class CostSettings(BaseModel):
    """Transaction cost assumptions.

    These are the numbers that decide whether a strategy is viable, so they are
    explicit config rather than buried defaults. Recalibrate from real broker
    statements after the first month of paper trading.
    """

    model_config = {"extra": "forbid"}

    commission_schedule: str = Field(
        default="ibkr_tiered",
        description="'ibkr_tiered' | 'ibkr_fixed' | 'zero'. Zero is research-only.",
    )
    default_spread_bps: Decimal = Field(default=Decimal("5"), ge=0)
    spread_multiplier: Decimal = Field(
        default=Decimal("1.25"),
        ge=1,
        description=(
            "Inflates modelled spread because signals cluster in wide-spread "
            "conditions. Must be >= 1: a value below 1 assumes you trade in "
            "better-than-average conditions, which is backwards."
        ),
    )
    impact_coefficient: Decimal = Field(default=Decimal("0.75"), ge=0)
    limit_adverse_selection_bps: Decimal = Field(default=Decimal("2"), ge=0)
    require_calibrated_spread: bool = Field(
        default=False,
        description="Hard-error rather than guess a spread. Enable for final validation runs.",
    )
    fx_rate_bps: Decimal = Field(default=Decimal("0.20"), ge=0)
    fx_minimum: Decimal = Field(default=Decimal("2.00"), ge=0)

    @field_validator("commission_schedule")
    @classmethod
    def _known_schedule(cls, v: str) -> str:
        allowed = {"ibkr_tiered", "ibkr_fixed", "zero"}
        if v not in allowed:
            raise ValueError(f"commission_schedule must be one of {sorted(allowed)}, got {v!r}")
        return v


class SimulationSettings(BaseModel):
    """Fill-realism knobs. Defaults are conservative on purpose."""

    model_config = {"extra": "forbid"}

    max_volume_participation: Decimal = Field(default=Decimal("0.05"), gt=0, le=1)
    require_limit_penetration: bool = True
    fill_on_next_bar: bool = Field(
        default=True,
        description=(
            "Market orders fill on the bar after the signal. Setting this False "
            "introduces lookahead bias and exists only for diagnostic comparison."
        ),
    )
    allow_partial_fills: bool = True


class DataSettings(BaseModel):
    model_config = {"extra": "forbid"}

    root: Path = Path("data")
    bar_size: str = "1 day"
    calendar: str = "XNYS"
    universe_file: Path | None = None


class LoggingSettings(BaseModel):
    model_config = {"extra": "forbid"}

    level: str = "INFO"
    json_output: bool = True
    directory: Path = Path("logs")

    @field_validator("level")
    @classmethod
    def _valid_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log level must be one of {sorted(allowed)}")
        return upper


class Settings(BaseSettings):
    """Root configuration."""

    model_config = SettingsConfigDict(
        env_prefix="TRADELAB_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    mode: RunMode = RunMode.BACKTEST
    base_currency: str = "EUR"
    initial_cash: Decimal = Field(default=Decimal("10000"), gt=0)
    strategies: list[str] = Field(default_factory=list)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    costs: CostSettings = Field(default_factory=CostSettings)
    simulation: SimulationSettings = Field(default_factory=SimulationSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    state_path: Path = Path("state/tradelab.sqlite")

    @field_validator("base_currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        if len(v) != 3:
            raise ValueError(f"base_currency must be a 3-letter ISO code, got {v!r}")
        return v.upper()

    @model_validator(mode="after")
    def _guard_live_mode(self) -> Settings:
        """Refuse configurations that are dangerous specifically in LIVE mode.

        These checks exist because the cost of the mistake is asymmetric. A
        zero-commission live run is not a harmless misconfiguration -- it means
        every risk limit computed from modelled cost is wrong while real orders
        are being sent.
        """
        if self.mode is not RunMode.LIVE:
            return self

        problems: list[str] = []
        if self.costs.commission_schedule == "zero":
            problems.append(
                "commission_schedule='zero' in LIVE mode: cost-based risk limits "
                "would be computed from a cost model known to be false"
            )
        if not self.simulation.fill_on_next_bar:
            problems.append(
                "fill_on_next_bar=False in LIVE mode: this flag exists only to "
                "measure lookahead bias and has no meaning against a real broker"
            )
        if self.broker.port == 7497:
            problems.append(
                "broker.port=7497 is IBKR's PAPER port but mode is LIVE; "
                "one of the two is wrong"
            )
        if self.risk.portfolio.max_gross_exposure > Decimal("1"):
            problems.append(
                f"max_gross_exposure={self.risk.portfolio.max_gross_exposure} implies "
                "leverage, which is outside the stated cash-equity mandate"
            )
        if problems:
            raise ValueError(
                "unsafe LIVE configuration:\n  - " + "\n  - ".join(problems)
            )
        return self


def load_settings(
    config_dir: Path | str = "config",
    mode: RunMode | str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """Load layered YAML config, then env vars, then explicit overrides.

    Precedence (lowest to highest): base.yaml, <mode>.yaml, environment,
    `overrides`. Environment beats YAML so that secrets and per-host values
    never need to be committed.
    """
    directory = Path(config_dir)
    merged: dict[str, Any] = {}

    base_file = directory / "base.yaml"
    if base_file.exists():
        merged = _deep_merge(merged, _read_yaml(base_file))

    resolved_mode = mode or os.environ.get("TRADELAB_MODE") or merged.get("mode")
    if resolved_mode is not None:
        mode_value = resolved_mode.value if isinstance(resolved_mode, RunMode) else str(resolved_mode)
        mode_file = directory / f"{mode_value.lower()}.yaml"
        if mode_file.exists():
            merged = _deep_merge(merged, _read_yaml(mode_file))
        merged["mode"] = mode_value.upper()

    if overrides:
        merged = _deep_merge(merged, overrides)

    return Settings(**merged)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `overlay` into `base`, returning a new dict."""
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
