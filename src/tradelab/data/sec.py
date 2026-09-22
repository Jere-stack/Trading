"""Free company fundamentals from the SEC, and the ticker-to-CIK mapping they need.

WHY THIS EXISTS
---------------
The project deferred fundamentals on cost -- a commercial feed at ~EUR 60/month
was 7.2% of a EUR 10k account. The SEC publishes every XBRL financial statement
filed since 2009 at data.sec.gov, free, with no key. Every fact carries the
date it was FILED, which is the property a backtest needs: a number can be used
only from the moment the market could have read it.

ONLY data.sec.gov IS USED
-------------------------
www.sec.gov rejects automated requests that do not declare a contact email in
the User-Agent. Rather than put the account holder's address in a header sent
to a third party, this module uses only data.sec.gov, which accepts a neutral
identifier. The one thing www.sec.gov offers that data.sec.gov lacks -- a
ticker-to-CIK file -- is replaced by the `frames` endpoint, which lists every
company reporting a concept in a period, INCLUDING companies later delisted.
That last property matters more than convenience: the current ticker file lists
survivors only, and building a universe from it is survivorship bias with extra
steps.

Set SEC_USER_AGENT to override the identifier. The SEC asks for a contact
address in it; supplying one is the operator's choice, not this code's.

Rate limit: the SEC's published ceiling is 10 requests/second. This client
stays under 8 and backs off on 403/429. Every response is cached to disk, so a
re-run makes no requests.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

__all__ = [
    "SecClient",
    "name_similarity",
    "normalise_name",
]

DEFAULT_USER_AGENT = "tradelab-research/1.0 (academic backtest; non-commercial)"
BASE = "https://data.sec.gov"

# Corporate-form and filler tokens that differ between vendors' spellings of
# the same company. "Apple Inc." (EODHD) and "APPLE INC" (SEC) must normalise
# identically; "Class A" and "/DE/" (state of incorporation) carry no identity.
_DROP_TOKENS = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ltd", "limited", "plc", "llc", "lp", "l", "p", "holdings", "holding",
    "group", "the", "class", "a", "b", "c", "com", "new", "de", "sa", "nv",
    "ag", "se", "adr", "ads", "trust", "and", "cl", "ord", "shs", "common",
    "stock", "international", "intl", "technologies", "technology",
    "ordinary", "shares",
}


def normalise_name(name: str | None) -> str:
    """A spelling of a company name that two vendors can agree on.

    Strips accents, the state-of-incorporation suffix the SEC appends
    ("/DE/"), punctuation, and corporate-form words. Deliberately does NOT
    stem or abbreviate beyond that: over-normalising is how "American
    Airlines" and "American Express" collide.
    """
    if not name:
        return ""
    # Apostrophes are DELETED rather than spaced: "O'Reilly" and its curly
    # form (U+2019) must both become "oreilly". Found by the gate-0 audit, where a curly
    # apostrophe left O'Reilly Automotive unmatched.
    text = re.sub(r"['\u2018\u2019\u02bc`]", "", str(name))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[/\\][a-z]{2}[/\\]", " ", text)  # /DE/, \NY\
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t not in _DROP_TOKENS]
    return " ".join(tokens)


def name_similarity(a: str, b: str) -> float:
    """Similarity of two normalised names in [0, 1], robust to word order and spacing.

    The best of three readings: character sequence, the same with spaces
    removed ("o reilly" / "oreilly"), and sorted tokens -- the SEC files some
    names surname-first, so "SCHWAB CHARLES CORP" must match "Charles Schwab".
    """
    if not a or not b:
        return 0.0
    plain = SequenceMatcher(None, a, b).ratio()
    squashed = SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio()
    tokens = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    return max(plain, squashed, tokens)


@dataclass
class SecClient:
    """Rate-limited, disk-cached client for data.sec.gov."""

    cache_dir: Path = Path("data/sec/cache")
    user_agent: str = field(
        default_factory=lambda: os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT)
    )
    min_interval: float = 0.13
    """Seconds between requests: ~7.7/s, under the SEC's 10/s ceiling."""
    max_retries: int = 5
    _last: float = field(default=0.0, repr=False)
    requests_made: int = field(default=0, repr=False)

    def _cache_path(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
        return self.cache_dir / f"{safe}.json.gz"

    def get_json(self, path: str) -> dict[str, Any] | None:
        """GET `path` from data.sec.gov. Returns None for a 404 (no such filer or
        no XBRL for it), which is an answer, not an error."""
        cached = self._cache_path(path)
        if cached.exists():
            with gzip.open(cached, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
            return payload if payload != {"__missing__": True} else None

        url = f"{BASE}{path}"
        for attempt in range(self.max_retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            request = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = response.read()
                    if response.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                self.requests_made += 1
                payload = json.loads(raw.decode("utf-8"))
                self._write(cached, payload)
                return payload
            except urllib.error.HTTPError as exc:
                self.requests_made += 1
                if exc.code == 404:
                    self._write(cached, {"__missing__": True})
                    return None
                if exc.code in (403, 429, 500, 502, 503):
                    # Back off hard: repeated 403s from the SEC escalate to a
                    # temporary block, and the block costs far more time than
                    # the wait does.
                    time.sleep(2.0 * (2**attempt))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                time.sleep(2.0 * (2**attempt))
        raise RuntimeError(f"SEC request failed after {self.max_retries} attempts: {url}")

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh)
        tmp.replace(path)

    # --------------------------------------------------------------- endpoints
    def frame(self, concept: str, period: str, unit: str = "USD") -> list[dict[str, Any]]:
        """Every company's value for `concept` in calendar `period` (e.g. CY2015Q4I).

        Used here for its side effect: the list of (cik, entityName) pairs that
        reported in that period, including companies that no longer exist.
        """
        payload = self.get_json(f"/api/xbrl/frames/us-gaap/{concept}/{unit}/{period}.json")
        return payload.get("data", []) if payload else []

    def companyfacts(self, cik: int) -> dict[str, Any] | None:
        return self.get_json(f"/api/xbrl/companyfacts/CIK{int(cik):010d}.json")

    def submissions(self, cik: int) -> dict[str, Any] | None:
        return self.get_json(f"/submissions/CIK{int(cik):010d}.json")
