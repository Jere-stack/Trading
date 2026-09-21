"""Read-model for the persisted track record.

Everything here reads the state database and computes; nothing writes to it.
That separation is deliberate -- a reporting layer that can mutate state is one
bad query away from corrupting the ledger it exists to display.
"""

from tradelab.reporting.metrics import (
    DrawdownPoint,
    EquityPoint,
    PerformanceSummary,
    StrategyResult,
    load_equity_curve,
    max_drawdown,
    strategy_results,
    summarise,
)

__all__ = [
    "DrawdownPoint",
    "EquityPoint",
    "PerformanceSummary",
    "StrategyResult",
    "load_equity_curve",
    "max_drawdown",
    "strategy_results",
    "summarise",
]
