"""Money and quantity primitives.

Design rule: **all cash and position accounting uses `Decimal`, never `float`.**

Floats accumulate representation error under repeated addition. A ledger that is
the source of truth for real capital must reconcile exactly against broker
statements, and 0.01 discrepancies that compound over thousands of fills make
reconciliation impossible. Analytics and research code may use floats freely --
that is a lossy read-model, not the ledger.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Cash is tracked to 4dp internally (sub-cent) so that per-share commissions and
# FX conversions do not silently round to zero before aggregation.
CASH_QUANT = Decimal("0.0001")
# Prices to 6dp: covers sub-penny US quotes and EUR/SEK tick sizes.
PRICE_QUANT = Decimal("0.000001")
# Quantities to 8dp to permit fractional shares where a broker supports them.
QTY_QUANT = Decimal("0.00000001")

ZERO = Decimal("0")


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    """Coerce to Decimal without inheriting binary float error.

    Floats are routed through `repr` so that 0.1 becomes Decimal("0.1") rather
    than Decimal("0.1000000000000000055511151231257827021181583404541015625").
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    try:
        return Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"cannot interpret {value!r} as a decimal") from exc


def quantize_cash(value: Decimal | int | float | str) -> Decimal:
    return to_decimal(value).quantize(CASH_QUANT, rounding=ROUND_HALF_UP)


def quantize_price(value: Decimal | int | float | str) -> Decimal:
    return to_decimal(value).quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


def quantize_qty(value: Decimal | int | float | str) -> Decimal:
    return to_decimal(value).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def round_to_lot(qty: Decimal, lot_size: Decimal = Decimal("1")) -> Decimal:
    """Round *down* in magnitude to a whole multiple of `lot_size`.

    Rounding down is deliberate: rounding a size up can breach a risk limit that
    was checked against the pre-rounded value.
    """
    if lot_size <= 0:
        raise ValueError("lot_size must be positive")
    sign = Decimal(-1) if qty < 0 else Decimal(1)
    magnitude = abs(qty)
    lots = (magnitude / lot_size).to_integral_value(rounding="ROUND_FLOOR")
    return sign * lots * lot_size


def bps(value: Decimal | int | float) -> Decimal:
    """Convert basis points to a decimal fraction. 25 bps -> 0.0025."""
    return to_decimal(value) / Decimal("10000")


def to_bps(fraction: Decimal | int | float) -> Decimal:
    """Convert a decimal fraction to basis points. 0.0025 -> 25."""
    return to_decimal(fraction) * Decimal("10000")


def safe_div(numerator: Decimal, denominator: Decimal, default: Decimal = ZERO) -> Decimal:
    """Division that returns `default` instead of raising on a zero denominator.

    Used in exposure/utilisation ratios where a zero-equity account must not
    crash the risk engine -- it must reject.
    """
    if denominator == 0:
        return default
    return numerator / denominator
