"""Identifier generation.

Client order ids are generated deterministically from a monotonic counter plus a
run id rather than from `uuid4`, so that a backtest is byte-for-byte
reproducible and so that a replayed live session produces traceable, sortable
ids that can be matched against broker records.
"""

from __future__ import annotations

import itertools
import os
import re
from datetime import datetime

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class IdGenerator:
    """Monotonic, prefixed id generator.

    Not thread-safe by design: the engine is single-threaded per run, and making
    this thread-safe would paper over an architectural violation if it were ever
    called concurrently.
    """

    def __init__(self, run_id: str, start: int = 1) -> None:
        self.run_id = sanitize(run_id)
        self._counter = itertools.count(start)

    def next(self, prefix: str = "ord") -> str:
        return f"{sanitize(prefix)}-{self.run_id}-{next(self._counter):08d}"


def sanitize(text: str) -> str:
    """Strip characters that brokers reject in client-side identifiers."""
    return _SAFE.sub("_", text)


def new_run_id(mode: str, moment: datetime, suffix: str | None = None) -> str:
    """Build a human-sortable run id, e.g. `PAPER-20260917T184600Z-ab12cd`."""
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    tail = sanitize(suffix) if suffix else os.urandom(3).hex()
    return f"{sanitize(mode)}-{stamp}-{tail}"
