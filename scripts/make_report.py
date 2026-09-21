#!/usr/bin/env python
"""Generate the HTML performance report from the persisted track record.

    .venv/bin/python scripts/make_report.py

Writes `state/paper/report.html`: one self-contained file, no network, no build
step. Open it directly, or serve the directory over Tailscale and bookmark it
on a phone.

Run it after each session. `deploy/systemd/tradelab-session.service` does that
automatically, so the report on the server is never older than the last run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tradelab.reporting.html import write_report

NOTE = (
    "The strategy running here is a monthly equal-weight basket of ten US large "
    "caps. It has no edge and is not supposed to: it exists to exercise the live "
    "path end to end, and its return is the market's, not a signal's. Read this "
    "page as a test of the plumbing, not of an investment idea."
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("state/paper/paper.sqlite"))
    parser.add_argument("--out", type=Path, default=Path("state/paper/report.html"))
    parser.add_argument("--base-currency", default="EUR")
    parser.add_argument("--mode", default="PAPER", choices=["PAPER", "LIVE"])
    parser.add_argument("--note", default=NOTE, help="Shown in the header banner")
    args = parser.parse_args()

    if not args.state.exists():
        raise SystemExit(f"no state at {args.state}; run a session first")

    path = write_report(
        args.state,
        args.out,
        base_currency=args.base_currency,
        strategy_note=args.note,
        mode=args.mode,
    )
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    print("\nTo read it from a phone, serve the directory over Tailscale:")
    print(f"  python -m http.server 8080 --bind 127.0.0.1 --directory {path.parent}")
    print("  then open http://<tailscale-name>:8080/report.html")


if __name__ == "__main__":
    main()
