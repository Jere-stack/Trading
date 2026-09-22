"""The research ledger: an append-only record of every trial ever run.

The Deflated Sharpe Ratio needs one input that nothing in a backtest can
supply: **how many configurations were actually evaluated before this one was
reported as the winner.** Get that number wrong and the whole apparatus of
validation is theatre. Expected maximum *annualised* Sharpe of worthless
strategies over three years of daily data (756 observations, Sharpe variance
under the null):

    10 trials   ->  0.91        200 trials  ->  1.60
    50 trials   ->  1.32      1,000 trials  ->  1.88

So a reported Sharpe of 1.3 is respectable after a single pre-registered test
and is exactly what fifty worthless strategies produce by chance. The
difference is entirely in a number held nowhere except the researcher's memory
-- which, across many sessions, several models and months of elapsed time, is
not a place at all.

This module is that place.

## Why append-only, and why a hash chain

A trial count that can be revised downward is not a trial count. The failure
mode is not fraud; it is the entirely reasonable-sounding thought *"that run
had a bug, so it shouldn't count."* Sometimes true. But applied freely it
silently deletes exactly the failed trials whose presence makes the count
honest, and it always deletes them in the direction that flatters the result.

So: entries are appended, never edited. Each carries the hash of the one
before, so altering an old entry breaks the chain from that point on and
`verify()` says where. Superseding a trial is allowed and requires a stated
reason -- but the superseded trial **still counts by default**, because the
work was still done and the decision could still have been influenced by it.

## Why JSONL rather than SQLite

The opposite choice from `portfolio/state.py`, deliberately. The paper state is
operational data, written constantly, read by machines. The ledger is the
scientific record: written rarely, read by humans, and its value depends on
being auditable. A text file diffs in review; a binary database hides an edit
inside an opaque blob. The file is small -- a few hundred lines over years.

## What counts as a trial

The rule that decides honesty: **any evaluation of a configuration against
data, where the result could have changed a decision.** That includes every
point of a parameter grid, not just the point reported; every re-run on a
different period or universe; and every variant tried and abandoned.

The one exclusion is a run that never produced an interpretable result at all
-- a crash, an empty sample. Those are recorded with `n_observations=0` and
excluded automatically, so the exclusion is visible rather than assumed.

Grid searches record as a single entry carrying `n_configs`, which
`trial_count` sums. Recording 200 rows for a 200-point grid is correct and
nobody does it; this makes the honest thing the easy thing.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

EntryKind = Literal["hypothesis", "trial", "verdict", "note"]
Verdict = Literal["rejected", "shelved", "paper", "live", "pending"]

GENESIS_HASH = "0" * 16
"""`prev_hash` of the first entry. A fixed, recognisable value rather than
null, so a chain missing its head is distinguishable from a chain starting
correctly."""


class LedgerIntegrityError(Exception):
    """The hash chain does not verify: an entry was edited or removed."""


def _canonical(payload: dict[str, Any]) -> str:
    """Stable serialisation, so the same content always hashes the same."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class LedgerEntry:
    """One immutable record. `entry_hash` covers every other field."""

    entry_id: int
    recorded_at: str
    kind: EntryKind
    hypothesis_id: str
    author: str
    """Who or what ran it -- a model identifier, or a person. The project
    expects to re-test old hypotheses with newer models; without this, results
    from different capabilities are indistinguishable in the record."""
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    n_configs: int = 1
    """Configurations evaluated by this entry. A grid search is one entry with
    many configs."""
    n_observations: int = 0
    """Sample size. Zero means the run produced no interpretable result and is
    excluded from the trial count."""
    metrics: dict[str, float] = field(default_factory=dict)
    supersedes: int | None = None
    prev_hash: str = GENESIS_HASH
    entry_hash: str = ""

    def body(self) -> dict[str, Any]:
        """Everything the hash covers, i.e. all of it except the hash."""
        return {
            "entry_id": self.entry_id,
            "recorded_at": self.recorded_at,
            "kind": self.kind,
            "hypothesis_id": self.hypothesis_id,
            "author": self.author,
            "summary": self.summary,
            "detail": self.detail,
            "n_configs": self.n_configs,
            "n_observations": self.n_observations,
            "metrics": self.metrics,
            "supersedes": self.supersedes,
            "prev_hash": self.prev_hash,
        }

    def to_json(self) -> str:
        return _canonical({**self.body(), "entry_hash": self.entry_hash})

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LedgerEntry:
        return cls(
            entry_id=int(raw["entry_id"]),
            recorded_at=str(raw["recorded_at"]),
            kind=raw["kind"],
            hypothesis_id=str(raw["hypothesis_id"]),
            author=str(raw["author"]),
            summary=str(raw["summary"]),
            detail=dict(raw.get("detail", {})),
            n_configs=int(raw.get("n_configs", 1)),
            n_observations=int(raw.get("n_observations", 0)),
            metrics={k: float(v) for k, v in raw.get("metrics", {}).items()},
            supersedes=raw.get("supersedes"),
            prev_hash=str(raw.get("prev_hash", GENESIS_HASH)),
            entry_hash=str(raw.get("entry_hash", "")),
        )


@dataclass(frozen=True)
class HypothesisSummary:
    """Rollup for one hypothesis, which is what the DSR call needs."""

    hypothesis_id: str
    statement: str
    trials: int
    """Configurations evaluated. The `n_trials` argument to the DSR."""
    entries: int
    first_tested: str | None
    last_tested: str | None
    verdict: Verdict
    verdict_reason: str
    authors: tuple[str, ...]
    sharpes: tuple[float, ...]

    @property
    def is_open(self) -> bool:
        return self.verdict in ("pending", "paper", "live")


class ResearchLedger:
    """Append-only trial record backed by a JSONL file.

    Typical use, in the order the protocol expects:

        ledger = ResearchLedger("research/ledger.jsonl")
        ledger.record_hypothesis("N2-dividend-cut-drift", statement=..., ...)
        ledger.record_trial("N2-dividend-cut-drift", summary=..., n_configs=36, ...)
        ledger.record_verdict("N2-dividend-cut-drift", "rejected", reason=...)

    and then, at the point of evaluating a result:

        n = ledger.trial_count("N2-dividend-cut-drift")
        deflated_sharpe_ratio(returns, n_trials=n)
    """

    def __init__(self, path: str | Path, author: str = "unknown") -> None:
        self.path = Path(path)
        self.default_author = author
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ read

    def entries(self) -> list[LedgerEntry]:
        """Every entry, oldest first. Returns empty for a ledger not yet started."""
        if not self.path.exists():
            return []
        out: list[LedgerEntry] = []
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(LedgerEntry.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise LedgerIntegrityError(
                    f"{self.path}:{lineno} is not a readable ledger entry: {exc}"
                ) from exc
        return out

    def verify(self) -> int:
        """Check the chain end to end. Returns the number of entries verified.

        Raises `LedgerIntegrityError` naming the first entry that fails, which
        is the earliest point the file was altered.
        """
        previous = GENESIS_HASH
        expected_id = 1
        entries = self.entries()
        for entry in entries:
            if entry.entry_id != expected_id:
                raise LedgerIntegrityError(
                    f"entry ids jump from {expected_id - 1} to {entry.entry_id}; "
                    "an entry was removed or reordered"
                )
            if entry.prev_hash != previous:
                raise LedgerIntegrityError(
                    f"entry {entry.entry_id} chains to {entry.prev_hash} but the "
                    f"previous entry hashes to {previous}; entry "
                    f"{entry.entry_id - 1} was edited"
                )
            recomputed = _hash(entry.body())
            if recomputed != entry.entry_hash:
                raise LedgerIntegrityError(
                    f"entry {entry.entry_id} hashes to {recomputed}, not the "
                    f"recorded {entry.entry_hash}; its own content was edited"
                )
            previous = entry.entry_hash
            expected_id += 1
        return len(entries)

    # ----------------------------------------------------------------- write

    def append(
        self,
        kind: EntryKind,
        hypothesis_id: str,
        summary: str,
        *,
        author: str | None = None,
        detail: dict[str, Any] | None = None,
        n_configs: int = 1,
        n_observations: int = 0,
        metrics: dict[str, float] | None = None,
        supersedes: int | None = None,
    ) -> LedgerEntry:
        """Append one entry, chained to the current tail.

        Verifies the existing chain first. Appending to a ledger that has been
        tampered with would launder the tampering by burying it under valid
        entries, so it is refused.
        """
        if not hypothesis_id.strip():
            raise ValueError("hypothesis_id is required: an unattributed trial is uncountable")
        if not summary.strip():
            raise ValueError("summary is required")
        if n_configs < 1:
            raise ValueError("n_configs must be >= 1")

        existing = self.entries()
        self.verify()
        if supersedes is not None:
            if not any(e.entry_id == supersedes for e in existing):
                raise ValueError(f"cannot supersede entry {supersedes}: no such entry")
            if not (detail or {}).get("supersede_reason"):
                raise ValueError(
                    "superseding a trial requires detail['supersede_reason']. "
                    "An unexplained supersede is how a trial count gets quietly "
                    "revised downward"
                )

        previous = existing[-1].entry_hash if existing else GENESIS_HASH
        entry = LedgerEntry(
            entry_id=len(existing) + 1,
            recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
            kind=kind,
            hypothesis_id=hypothesis_id.strip(),
            author=(author or self.default_author),
            summary=summary.strip(),
            detail=detail or {},
            n_configs=n_configs,
            n_observations=n_observations,
            # Coerced on WRITE because `from_dict` coerces on READ. Without
            # this, a caller passing an int metric -- `positive_years=8` -- is
            # hashed as `8` and read back as `8.0`, so the entry fails its own
            # verification and the chain is bricked from that point on. The
            # write and read paths have to agree on the type or the hash is
            # not a hash of the content, it is a hash of the caller's literal.
            metrics={k: float(v) for k, v in (metrics or {}).items()},
            supersedes=supersedes,
            prev_hash=previous,
        )
        entry = LedgerEntry(**{**entry.body(), "entry_hash": _hash(entry.body())})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.to_json() + "\n")
        return entry

    def record_hypothesis(
        self,
        hypothesis_id: str,
        *,
        statement: str,
        rationale: str,
        predicted_sign: str = "positive",
        data_required: str = "",
        author: str | None = None,
    ) -> LedgerEntry:
        """Register a hypothesis before testing it.

        `rationale` must state the economic or behavioural mechanism -- who is
        on the other side, and why they trade against their own interest.
        A hypothesis without one is a pattern, and patterns are free.
        """
        if not rationale.strip():
            raise ValueError(
                "rationale is required: a hypothesis with no stated mechanism "
                "is data mining with extra steps"
            )
        return self.append(
            "hypothesis",
            hypothesis_id,
            statement,
            author=author,
            detail={
                "rationale": rationale,
                "predicted_sign": predicted_sign,
                "data_required": data_required,
            },
        )

    def record_trial(
        self,
        hypothesis_id: str,
        *,
        summary: str,
        n_configs: int = 1,
        n_observations: int = 0,
        metrics: dict[str, float] | None = None,
        config: dict[str, Any] | None = None,
        universe: str = "",
        period: str = "",
        author: str | None = None,
        supersedes: int | None = None,
        supersede_reason: str = "",
    ) -> LedgerEntry:
        """Record an evaluation against data, whatever the outcome.

        Record failures with the same care as successes. A ledger holding only
        the trials that worked reports a trial count of one and deflates
        nothing.
        """
        detail: dict[str, Any] = {"config": config or {}, "universe": universe, "period": period}
        if supersede_reason:
            detail["supersede_reason"] = supersede_reason
        return self.append(
            "trial",
            hypothesis_id,
            summary,
            author=author,
            detail=detail,
            n_configs=n_configs,
            n_observations=n_observations,
            metrics=metrics or {},
            supersedes=supersedes,
        )

    def record_verdict(
        self,
        hypothesis_id: str,
        decision: Verdict,
        *,
        reason: str,
        author: str | None = None,
    ) -> LedgerEntry:
        """Close a hypothesis, or move it to paper or live.

        A verdict does not end the record. Re-testing a rejected hypothesis
        later -- with better data, or a stronger model -- appends further
        trials, and those still count toward the deflation.
        """
        if not reason.strip():
            raise ValueError("a verdict requires a reason")
        return self.append(
            "verdict",
            hypothesis_id,
            f"{decision}: {reason}",
            author=author,
            detail={"decision": decision, "reason": reason},
        )

    # --------------------------------------------------------------- queries

    def trial_count(
        self,
        hypothesis_id: str | None = None,
        *,
        count_superseded: bool = True,
    ) -> int:
        """Configurations evaluated -- the honest `n_trials` for the DSR.

        `hypothesis_id=None` counts the whole project. Which to use depends on
        the claim: deflating one hypothesis tested in isolation uses its own
        count; claiming "this is the best of everything we tried" uses the
        project total, because that is the selection that was actually made.

        `count_superseded=False` exists for the case where an earlier run was
        genuinely invalid, and it is off by default for a reason. Whenever it
        is used, the number being quoted is smaller than the number of times
        data was consulted.
        """
        total = 0
        superseded = {e.supersedes for e in self.entries() if e.supersedes is not None}
        for entry in self.entries():
            if entry.kind != "trial":
                continue
            if hypothesis_id is not None and entry.hypothesis_id != hypothesis_id:
                continue
            if entry.n_observations <= 0:
                continue  # produced no interpretable result
            if not count_superseded and entry.entry_id in superseded:
                continue
            total += entry.n_configs
        return total

    def trial_sharpes(self, hypothesis_id: str | None = None) -> list[float]:
        """Sharpes recorded across trials, for the DSR's variance estimate.

        Measuring the variance from the trials actually run is far better than
        assuming it, and is the whole reason trials are recorded with metrics
        rather than just counted.
        """
        out: list[float] = []
        for entry in self.entries():
            if entry.kind != "trial":
                continue
            if hypothesis_id is not None and entry.hypothesis_id != hypothesis_id:
                continue
            if "sharpe" in entry.metrics:
                out.append(entry.metrics["sharpe"])
        return out

    def hypotheses(self) -> list[HypothesisSummary]:
        """One rollup per hypothesis, in the order they were first recorded."""
        statements: dict[str, str] = {}
        trials: dict[str, int] = defaultdict(int)
        entry_counts: dict[str, int] = defaultdict(int)
        first: dict[str, str] = {}
        last: dict[str, str] = {}
        verdicts: dict[str, tuple[Verdict, str]] = {}
        authors: dict[str, list[str]] = defaultdict(list)
        sharpes: dict[str, list[float]] = defaultdict(list)
        order: list[str] = []

        for entry in self.entries():
            hid = entry.hypothesis_id
            if hid not in order:
                order.append(hid)
            entry_counts[hid] += 1
            if entry.author not in authors[hid]:
                authors[hid].append(entry.author)
            if entry.kind == "hypothesis":
                statements.setdefault(hid, entry.summary)
            elif entry.kind == "trial":
                if entry.n_observations > 0:
                    trials[hid] += entry.n_configs
                first.setdefault(hid, entry.recorded_at)
                last[hid] = entry.recorded_at
                if "sharpe" in entry.metrics:
                    sharpes[hid].append(entry.metrics["sharpe"])
            elif entry.kind == "verdict":
                verdicts[hid] = (
                    entry.detail.get("decision", "pending"),
                    entry.detail.get("reason", ""),
                )

        return [
            HypothesisSummary(
                hypothesis_id=hid,
                statement=statements.get(hid, ""),
                trials=trials[hid],
                entries=entry_counts[hid],
                first_tested=first.get(hid),
                last_tested=last.get(hid),
                verdict=verdicts.get(hid, ("pending", ""))[0],
                verdict_reason=verdicts.get(hid, ("pending", ""))[1],
                authors=tuple(authors[hid]),
                sharpes=tuple(sharpes[hid]),
            )
            for hid in order
        ]

    def report(self) -> str:
        """Human-readable summary, for the terminal and for a commit message."""
        rows = self.hypotheses()
        total = self.trial_count()
        lines = [
            f"Research ledger: {self.path}",
            f"  {len(rows)} hypotheses, {len(self.entries())} entries, "
            f"{total} configurations evaluated",
            "",
            f"  {'hypothesis':<28}{'trials':>8}  {'verdict':<10} tested",
        ]
        for row in rows:
            window = ""
            if row.first_tested:
                window = row.first_tested[:10]
                if row.last_tested and row.last_tested[:10] != window:
                    window += f" -> {row.last_tested[:10]}"
            lines.append(
                f"  {row.hypothesis_id[:27]:<28}{row.trials:>8}  {row.verdict:<10} {window}"
            )
        lines += [
            "",
            f"  Expected max Sharpe of {total} worthless strategies is the bar any",
            "  result must clear. Pass this count to deflated_sharpe_ratio.",
        ]
        return "\n".join(lines)


def deflate_from_ledger(
    returns: Any,
    ledger: ResearchLedger,
    hypothesis_id: str | None = None,
    *,
    project_wide: bool = False,
) -> Any:
    """Deflated Sharpe with the trial count read from the ledger, not typed in.

    The point of routing the call through here is that `n_trials` stops being
    an argument someone chooses. It is whatever the record says, and the record
    is append-only.

    `project_wide=True` deflates against every configuration ever evaluated,
    which is the right bar for "this is the best thing we found". Deflating a
    single pre-registered hypothesis against its own trials only is right when
    it was genuinely tested in isolation -- and by the time several candidates
    have been run, it rarely is.
    """
    from tradelab.research.validation import deflated_sharpe_ratio

    scope = None if project_wide else hypothesis_id
    n_trials = max(1, ledger.trial_count(scope))
    sharpes = ledger.trial_sharpes(scope)
    return deflated_sharpe_ratio(
        returns,
        n_trials=n_trials,
        trial_sharpes=None if len(sharpes) < 2 else sharpes,
    )
