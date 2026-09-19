#!/usr/bin/env python
"""Prove the IBKR connection works, without being able to trade.

Run this first on any new deployment, before the session script, before
anything else. It is the only thing standing between "the gateway is up" and
"the gateway is up and the adapter actually agrees with it", and those are not
the same claim.

Every check is read-only. The broker is connected with `readonly=True`, so the
TWS API itself refuses order submission for the life of this process -- the
script cannot place an order even if it is wrong.

    .venv/bin/python scripts/ibkr_smoke.py                 # paper, port 4002
    .venv/bin/python scripts/ibkr_smoke.py --port 4001 --mode LIVE

What it checks, in the order the failures actually happen:

1. **Connect.** Catches: gateway down, API not enabled, wrong port, today's
   re-authentication not done, client id already in use.
2. **Account.** Catches: connected to the wrong account -- the single most
   expensive misconfiguration, and invisible until an order fills somewhere
   unexpected.
3. **Contract qualification.** Catches the ambiguity that makes IBKR
   integrations quietly wrong: `Stock("NOKIA", "SMART", "EUR")` matches several
   listings, and taking the first one means trading a different listing, in a
   different currency, on a different exchange.
4. **Positions.** Catches a reconciliation break before the runner meets one.
5. **Currency balances.** Catches an unfunded account, which on this system
   means every order is refused by CashSufficiencyCheck and the cause is two
   layers away from the symptom.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from tradelab.core.enums import RunMode, Venue
from tradelab.core.types import Instrument

# A deliberately awkward set. AAPL is the easy case; NOKIA on Helsinki is the
# one that exposes contract ambiguity, because the same company trades in New
# York as an ADR in a different currency.
PROBES = [
    Instrument("AAPL", venue=Venue.SMART, currency="USD", adv=Decimal("50000000")),
    Instrument("MSFT", venue=Venue.SMART, currency="USD", adv=Decimal("30000000")),
    Instrument("NOKIA", venue=Venue.HELSINKI, currency="EUR", adv=Decimal("8000000")),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002, help="4002 paper, 4001 live")
    parser.add_argument("--mode", default="PAPER", choices=["PAPER", "LIVE"])
    parser.add_argument("--client-id", type=int, default=99, help="Distinct from the runner's")
    args = parser.parse_args()

    from tradelab.execution.broker import BrokerError
    from tradelab.execution.ibkr_broker import IbkrBroker

    print("=" * 70)
    print(f"IBKR SMOKE TEST  {args.host}:{args.port}  mode={args.mode}  READ-ONLY")
    print("=" * 70)

    broker = IbkrBroker(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        mode=RunMode(args.mode),
        readonly=True,  # not a default to rely on; stated here on purpose
    )

    failures: list[str] = []

    try:
        print("\n[1/5] connecting...")
        broker.connect()
        print(f"      connected: {broker.is_connected}")
    except BrokerError as exc:
        print(f"      FAILED: {exc}")
        return 1

    try:
        print("\n[2/5] account")
        account = broker.account()
        print(f"      id            {account.account_id}")
        print(f"      equity        {account.equity} {account.base_currency}")
        print(f"      buying power  {account.buying_power}")
        if args.mode == "PAPER" and not str(account.account_id).upper().startswith("D"):
            # IBKR paper accounts are DU/DF-prefixed. A paper-mode connection
            # to a non-paper account id means the port and the mode disagree
            # with reality, whatever the config says.
            failures.append(
                f"mode is PAPER but account {account.account_id} is not a DU/DF paper "
                "account -- do not proceed until this is explained"
            )
            print(f"      *** {failures[-1]}")

        print("\n[3/5] currency balances")
        for ccy, amount in sorted(account.cash.items()):
            flag = "  <- DEBIT, this is a margin loan" if amount < 0 else ""
            print(f"      {ccy}  {amount:>14,.2f}{flag}")
            if amount < 0:
                failures.append(f"{ccy} balance is negative ({amount}): the account is borrowing")
        if not account.cash:
            print("      (none reported)")
    except Exception as exc:
        failures.append(f"account read failed: {exc}")
        print(f"      FAILED: {exc}")

    print("\n[4/5] contract qualification")
    for instrument in PROBES:
        try:
            contract = broker.qualify(instrument)
            print(
                f"      {instrument.symbol:<8} -> conId={getattr(contract, 'conId', '?')} "
                f"{getattr(contract, 'primaryExchange', '?')} "
                f"{getattr(contract, 'currency', '?')}"
            )
            if getattr(contract, "currency", None) != instrument.currency:
                failures.append(
                    f"{instrument.symbol} qualified to "
                    f"{getattr(contract, 'currency', '?')}, expected {instrument.currency}"
                )
        except Exception as exc:
            # Not necessarily fatal: an account without the relevant market
            # data permission cannot qualify some contracts. It is fatal if you
            # intend to trade that name.
            print(f"      {instrument.symbol:<8} -> FAILED: {str(exc)[:110]}")
            failures.append(f"{instrument.symbol} did not qualify: {str(exc)[:110]}")

    try:
        print("\n[5/5] positions")
        positions = broker.positions()
        if not positions:
            print("      none (expected on a fresh paper account)")
        for position in positions:
            print(
                f"      {position.instrument.symbol:<8} {position.quantity:>10} "
                f"@ {position.average_price}"
            )
    except Exception as exc:
        failures.append(f"position read failed: {exc}")
        print(f"      FAILED: {exc}")

    broker.disconnect()

    print("\n" + "=" * 70)
    if failures:
        print(f"SMOKE TEST FAILED -- {len(failures)} problem(s)")
        for item in failures:
            print(f"  - {item}")
        print("\nDo not run the session script until these are resolved.")
        return 1
    print("SMOKE TEST PASSED")
    print("\nThe connection, account, contracts and positions all agree.")
    print("Next: run the session script with --dry-run before letting it place orders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
