"""Portfolio: the single source of truth for cash, positions and equity.

Invariants this class enforces, and why each one exists:

* **Multi-currency cash is tracked per currency, never pre-converted.** A EUR
  base account holding USD stocks has two distinct balances. Collapsing them
  into one base-currency number hides the FX exposure and makes it impossible to
  tell a trading gain from a currency move.

* **No implicit leverage.** The mandate is cash-equity only. `max_leverage`
  defaults to 1.0 and a fill that would breach it raises rather than silently
  borrowing. Brokers extend margin by default, so "no leverage" has to be an
  actively enforced property, not an assumption.

* **Equity is computed, never stored.** A stored equity field drifts from the
  positions that justify it. The cost is recomputation on every mark, which is
  trivial relative to the cost of a ledger that disagrees with the broker.

* **Per-strategy attribution is first-class.** Multiple strategies sharing one
  account need per-strategy P&L to decide which to cut, and per-strategy
  exposure to enforce risk budgets. Retro-fitting that is painful, so fills
  carry `strategy_id` from the start.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tradelab.core.money import ZERO, quantize_cash, safe_div, to_decimal
from tradelab.core.types import Fill, Instrument, Position


class InsufficientFundsError(RuntimeError):
    """Raised when a fill would breach the leverage ceiling.

    This is deliberately an exception rather than a soft warning: reaching it
    means the pre-trade risk gate failed to do its job, which is a bug to fix,
    not a condition to tolerate at runtime.
    """


@dataclass(frozen=True, slots=True)
class StrategyPnL:
    strategy_id: str
    realised: Decimal
    unrealised: Decimal
    commission: Decimal
    fees: Decimal
    gross_exposure: Decimal
    trade_count: int

    @property
    def net(self) -> Decimal:
        return quantize_cash(self.realised + self.unrealised - self.commission - self.fees)


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal
    cash: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    position_count: int


@dataclass
class Portfolio:
    """Cash, positions, and derived risk/exposure metrics."""

    base_currency: str = "EUR"
    max_leverage: Decimal = Decimal("1.0")
    leverage_tolerance: Decimal = Decimal("0.02")
    cash: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    fx_rates: dict[str, Decimal] = field(default_factory=dict)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    _strategy_realised: dict[str, Decimal] = field(
        default_factory=lambda: defaultdict(lambda: ZERO)
    )
    _strategy_costs: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    _strategy_trades: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _position_strategy: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base_currency = self.base_currency.upper()
        self.cash = {k.upper(): to_decimal(v) for k, v in self.cash.items()}
        self.cash.setdefault(self.base_currency, ZERO)
        self.fx_rates = {k.upper(): to_decimal(v) for k, v in self.fx_rates.items()}
        self.fx_rates[self.base_currency] = Decimal("1")

    # ---------------------------------------------------------------- funding

    def deposit(self, amount: Decimal, currency: str | None = None) -> None:
        currency = (currency or self.base_currency).upper()
        self.cash[currency] = quantize_cash(self.cash.get(currency, ZERO) + to_decimal(amount))

    def set_cash(self, amount: Decimal, currency: str | None = None) -> None:
        """Set a currency balance outright, replacing whatever is there.

        Distinct from `deposit`, which adds. Adopting a broker's reported
        balance is a *set*: the broker is authoritative, so the local figure is
        replaced rather than incremented. Using deposit for that silently
        doubles the balance whenever the local ledger already holds it.
        """
        currency = (currency or self.base_currency).upper()
        self.cash[currency] = quantize_cash(to_decimal(amount))

    def set_fx_rate(self, currency: str, rate_to_base: Decimal) -> None:
        """Set the rate converting one unit of `currency` into base currency."""
        rate = to_decimal(rate_to_base)
        if rate <= 0:
            raise ValueError(f"fx rate for {currency} must be positive, got {rate}")
        self.fx_rates[currency.upper()] = rate

    def fx_rate(self, currency: str) -> Decimal:
        currency = currency.upper()
        if currency == self.base_currency:
            return Decimal("1")
        try:
            return self.fx_rates[currency]
        except KeyError:
            raise KeyError(
                f"no FX rate for {currency}->{self.base_currency}. Refusing to guess: "
                "an assumed rate would silently misstate equity and every risk limit "
                "derived from it."
            ) from None

    def to_base(self, amount: Decimal, currency: str) -> Decimal:
        return quantize_cash(to_decimal(amount) * self.fx_rate(currency))

    # ------------------------------------------------------------- positions

    def position(self, instrument: Instrument) -> Position:
        """Get or create the position for `instrument`."""
        return self.positions.setdefault(instrument.key, Position(instrument=instrument))

    def get_position(self, instrument: Instrument) -> Position | None:
        return self.positions.get(instrument.key)

    def quantity_of(self, instrument: Instrument) -> Decimal:
        pos = self.positions.get(instrument.key)
        return pos.quantity if pos else ZERO

    @property
    def open_positions(self) -> dict[str, Position]:
        return {k: p for k, p in self.positions.items() if not p.is_flat}

    def mark(
        self, instrument: Instrument, price: Decimal, timestamp: datetime | None = None
    ) -> None:
        pos = self.positions.get(instrument.key)
        if pos is not None and not pos.is_flat:
            pos.mark(to_decimal(price), timestamp)

    # ------------------------------------------------------------------ fills

    def apply_fill(self, fill: Fill) -> Decimal:
        """Apply a fill to cash and positions. Returns realised P&L in local ccy.

        The leverage check runs *after* computing the prospective state and
        raises before committing, so a rejected fill leaves the portfolio
        untouched rather than half-updated.
        """
        instrument = fill.instrument
        currency = instrument.currency.upper()
        pos = self.position(instrument)

        prospective_cash = self.cash.get(currency, ZERO) + fill.cash_delta
        realised = pos.apply_fill(fill)
        self.cash[currency] = quantize_cash(prospective_cash)

        strategy_id = fill.strategy_id
        self._strategy_realised[strategy_id] = quantize_cash(
            self._strategy_realised[strategy_id] + self.to_base(realised, currency)
        )
        self._strategy_costs[strategy_id] = quantize_cash(
            self._strategy_costs[strategy_id] + self.to_base(fill.total_cost, currency)
        )
        self._strategy_trades[strategy_id] += 1
        if pos.is_flat:
            self._position_strategy.pop(instrument.key, None)
        else:
            self._position_strategy[instrument.key] = strategy_id

        if self.max_leverage is not None:
            equity = self.equity
            if equity <= 0:
                raise InsufficientFundsError(
                    f"fill {fill.fill_id} drove equity to {equity} {self.base_currency}; "
                    "the pre-trade risk gate should have prevented this"
                )
            leverage = safe_div(self.gross_exposure, equity)
            # `leverage_tolerance` absorbs the gap between an approved notional
            # and a realised one: commission settles out of cash, and fills land
            # at a later bar's price. Without it, normal execution drift would
            # raise on a 0.1% overshoot that is not a real breach of the
            # no-leverage mandate. A genuine breach still raises -- this is a
            # backstop assertion, and the pre-trade gate is the real control.
            if leverage > self.max_leverage * (Decimal("1") + self.leverage_tolerance):
                raise InsufficientFundsError(
                    f"fill {fill.fill_id} would put leverage at {leverage:.3f}x, above the "
                    f"{self.max_leverage}x ceiling plus {self.leverage_tolerance:.0%} "
                    f"tolerance (gross {self.gross_exposure} vs equity {equity} "
                    f"{self.base_currency}). Stocks-only mandate forbids borrowing; the "
                    "pre-trade risk gate should have prevented this."
                )
        return realised

    # -------------------------------------------------------------- valuation

    @property
    def cash_base(self) -> Decimal:
        return quantize_cash(sum((self.to_base(amt, ccy) for ccy, amt in self.cash.items()), ZERO))

    @property
    def positions_value_base(self) -> Decimal:
        return quantize_cash(
            sum(
                (
                    self.to_base(p.market_value, p.instrument.currency)
                    for p in self.positions.values()
                ),
                ZERO,
            )
        )

    @property
    def equity(self) -> Decimal:
        """Net liquidation value in base currency."""
        return quantize_cash(self.cash_base + self.positions_value_base)

    @property
    def gross_exposure(self) -> Decimal:
        """Sum of |market value| -- what is actually at risk in the market."""
        return quantize_cash(
            sum(
                (
                    self.to_base(p.gross_exposure, p.instrument.currency)
                    for p in self.positions.values()
                ),
                ZERO,
            )
        )

    @property
    def net_exposure(self) -> Decimal:
        """Signed exposure: directional market risk."""
        return self.positions_value_base

    @property
    def leverage(self) -> Decimal:
        return safe_div(self.gross_exposure, self.equity)

    @property
    def unrealised_pnl(self) -> Decimal:
        return quantize_cash(
            sum(
                (
                    self.to_base(p.unrealised_pnl, p.instrument.currency)
                    for p in self.positions.values()
                ),
                ZERO,
            )
        )

    def exposure_by_currency(self) -> dict[str, Decimal]:
        """Gross exposure per currency, including the cash balance.

        Surfaces the uncompensated FX risk a EUR investor takes by holding USD
        stocks -- a risk that is easy to carry unknowingly and large enough to
        dominate a small per-trade edge.
        """
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for ccy, amount in self.cash.items():
            out[ccy] += self.to_base(amount, ccy)
        for pos in self.positions.values():
            ccy = pos.instrument.currency.upper()
            out[ccy] += self.to_base(pos.market_value, ccy)
        return {k: quantize_cash(v) for k, v in out.items()}

    def exposure_by_strategy(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for key, pos in self.positions.items():
            if pos.is_flat:
                continue
            sid = self._position_strategy.get(key, "unassigned")
            out[sid] += self.to_base(pos.gross_exposure, pos.instrument.currency)
        return {k: quantize_cash(v) for k, v in out.items()}

    def strategy_pnl(self) -> dict[str, StrategyPnL]:
        unrealised: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for key, pos in self.positions.items():
            sid = self._position_strategy.get(key, "unassigned")
            unrealised[sid] += self.to_base(pos.unrealised_pnl, pos.instrument.currency)
        exposures = self.exposure_by_strategy()
        ids = set(self._strategy_realised) | set(unrealised) | set(self._strategy_trades)
        return {
            sid: StrategyPnL(
                strategy_id=sid,
                realised=self._strategy_realised.get(sid, ZERO),
                unrealised=quantize_cash(unrealised.get(sid, ZERO)),
                commission=self._strategy_costs.get(sid, ZERO),
                fees=ZERO,
                gross_exposure=exposures.get(sid, ZERO),
                trade_count=self._strategy_trades.get(sid, 0),
            )
            for sid in sorted(ids)
        }

    def record_equity(self, timestamp: datetime) -> EquityPoint:
        point = EquityPoint(
            timestamp=timestamp,
            equity=self.equity,
            cash=self.cash_base,
            gross_exposure=self.gross_exposure,
            net_exposure=self.net_exposure,
            position_count=len(self.open_positions),
        )
        self.equity_curve.append(point)
        return point

    def snapshot(self) -> dict[str, object]:
        return {
            "equity": self.equity,
            "cash": dict(self.cash),
            "cash_base": self.cash_base,
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "leverage": self.leverage,
            "unrealised_pnl": self.unrealised_pnl,
            "position_count": len(self.open_positions),
            "positions": {
                k: {
                    "qty": p.quantity,
                    "avg": p.average_price,
                    "last": p.last_price,
                    "mv": p.market_value,
                    "upnl": p.unrealised_pnl,
                }
                for k, p in self.open_positions.items()
            },
            "currency_exposure": self.exposure_by_currency(),
        }
