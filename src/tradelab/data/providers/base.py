"""Market data provider protocol.

Providers differ in column names, timezone handling, adjustment policy and
missing-data conventions. They all return a canonical frame, and the conversion
happens inside the provider so nothing downstream has to know the source.

Every provider must declare its `adjustment` policy and whether its universe
`includes_delisted`. These are not metadata niceties -- they are the two facts
that determine whether a backtest built on the data means anything, and a
provider that cannot state them honestly should report `UNKNOWN` so the quality
auditor escalates rather than letting it pass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from tradelab.data.schema import Adjustment


class ProviderError(RuntimeError):
    """A provider could not supply the requested data."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class BarProvider(ABC):
    """Fetches historical bars and returns them in the canonical schema."""

    name: str = "unnamed"
    adjustment: Adjustment = Adjustment.UNKNOWN
    includes_delisted: bool = False

    @abstractmethod
    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        bar_size: str = "1 day",
    ) -> pd.DataFrame:
        """Return canonical bars for `symbols` over [start, end]."""

    def describe(self) -> str:
        return (
            f"{self.name} (adjustment={self.adjustment.value}, "
            f"includes_delisted={self.includes_delisted})"
        )


class FxProvider(ABC):
    """Supplies historical exchange rates.

    Separate from `BarProvider` because FX rates are needed even when no equity
    data is available -- a EUR-based portfolio holding USD stocks cannot compute
    equity, and therefore cannot evaluate a single risk limit, without them.
    """

    name: str = "unnamed"

    @abstractmethod
    def rates(self, base: str, quote: str, start: datetime, end: datetime) -> pd.Series:
        """Return a date-indexed series of `quote` units per one `base` unit."""
