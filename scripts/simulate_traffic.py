#!/usr/bin/env python3
"""
Feeds a steady stream of new orders straight into Capella Server, for a
live demo where you want the KDS screens / pickup board / manager
dashboard (all synced live via Capella App Services now - see
app/static/js/cbl/) to keep getting new activity without you manually
placing every order in the Register UI.

Writes via the same order_service.create_order() the app used to expose
over HTTP - that API is gone now that the live app talks to App Services
directly (see app/static/js/cbl/), so this script writes straight to
Capella Server via the Couchbase Python SDK instead, exactly like
generate_orders.py. Capella then syncs each write down to App Services,
and from there to every open browser tab, live.

Requires the catalog to already be seeded - run scripts/seed_data.py first.

Usage:
    python scripts/simulate_traffic.py
    python scripts/simulate_traffic.py --store 10492 --interval 4
    python scripts/simulate_traffic.py --auto-advance   # also plays barista/pickup for you
    python scripts/simulate_traffic.py --auto-advance --start-delay 2 --prep-delay 5 --pickup-delay 8
"""
from __future__ import annotations

import argparse
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.models import CustomizationIn, OrderCreateRequest, OrderLineItemIn
from app.repositories import catalog_repo, customer_repo
from app.services import order_service


def random_items(menu_items: list[dict], modifiers_by_type: dict) -> list[OrderLineItemIn]:
    beverages = [m for m in menu_items if m["category"] == "BEVERAGE"]
    foods = [m for m in menu_items if m["category"] == "FOOD"]

    picks = [random.choice(beverages)]
    if random.random() < 0.55:
        picks.append(random.choice(foods))

    items = []
    for menu_item in picks:
        size = random.choice(menu_item["availableSizes"])
        customizations = []
        for c_type in menu_item.get("allowedCustomizations", []):
            if random.random() < 0.35:
                options = modifiers_by_type.get(c_type, [])
                if options:
                    mod = random.choice(options)
                    customizations.append(CustomizationIn(type=c_type, value=mod["value"]))
        items.append(OrderLineItemIn(itemId=menu_item["itemId"], size=size, quantity=1, customizations=customizations))
    return items


def _jitter(delay: float) -> float:
    """+/-40% around `delay` so concurrently-cooking orders don't all tick
    over in lockstep - looks more like a real store on the KDS/pickup
    screens."""
    return random.uniform(delay * 0.6, delay * 1.4)


def run_item_lifecycle(order_id: str, item: dict, args: argparse.Namespace) -> None:
    """One line item's trip across its KDS station: QUEUED -> IN_PREPARATION
    ("Start") -> READY ("Ready"), each transition after a jittered delay -
    guaranteed to happen (not a coin flip), so the KDS screen always shows
    the ticket actually move through both states instead of sometimes
    freezing in QUEUED or jumping straight to done."""
    actor = "barista_442" if item["station"] == "ESPRESSO_BAR" else "warm_339"

    time.sleep(_jitter(args.start_delay))
    try:
        order_service.advance_line_item(order_id, item["lineItemId"], "IN_PREPARATION", actor)
    except ValueError:
        pass  # order already moved on (e.g. cancelled) - nothing to advance

    time.sleep(_jitter(args.prep_delay))
    try:
        order_service.advance_line_item(order_id, item["lineItemId"], "READY", actor)
    except ValueError:
        pass


def run_order_lifecycle(order: dict, args: argparse.Namespace) -> None:
    """Plays out one order's whole life in the background, independent of
    the main loop's order-placing cadence: every line item's station works
    in parallel (mirrors the espresso bar and warming station being
    independent in real life), and once every item is READY - which bumps
    the order itself to READY and puts it on the pickup board, see
    order_service.advance_line_item() - the order sits there for
    `--pickup-delay` seconds before being marked COMPLETED, the same way a
    customer eventually walks up and grabs their drink."""
    order_id = order["orderId"]
    station_threads = [
        threading.Thread(target=run_item_lifecycle, args=(order_id, item, args), daemon=True)
        for item in order["items"]
    ]
    for t in station_threads:
        t.start()
    for t in station_threads:
        t.join()

    time.sleep(_jitter(args.pickup_delay))
    try:
        # No employee actor - this mirrors a customer collecting their own
        # order off the pickup board, not a staff action.
        order_service.complete_order(order_id, None)
    except ValueError:
        pass  # e.g. already cancelled out of band


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", default="10492")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between orders")
    parser.add_argument(
        "--auto-advance",
        action="store_true",
        help="also play out each order's KDS (Start -> Ready) and pickup-board lifecycle in the background",
    )
    parser.add_argument(
        "--start-delay", type=float, default=3.0,
        help="avg seconds from QUEUED to IN_PREPARATION ('Start') on the KDS (--auto-advance only)",
    )
    parser.add_argument(
        "--prep-delay", type=float, default=7.0,
        help="avg seconds from IN_PREPARATION to READY on the KDS (--auto-advance only)",
    )
    parser.add_argument(
        "--pickup-delay", type=float, default=12.0,
        help="avg seconds an order sits READY on the pickup board before being marked picked up (--auto-advance only)",
    )
    args = parser.parse_args()

    db.connect()

    store = catalog_repo.get_store(args.store)
    if not store:
        raise SystemExit(f"Store {args.store} not found - run scripts/seed_data.py first.")

    menu_items = catalog_repo.list_menu_items()
    modifiers = catalog_repo.list_modifiers()
    modifiers_by_type: dict[str, list[dict]] = {}
    for m in modifiers:
        modifiers_by_type.setdefault(m["modifierType"], []).append(m)

    customers = customer_repo.list_customers()
    cashiers = [e for e in customer_repo.list_employees(args.store) if e["role"] == "CASHIER"]

    print(f"Simulating traffic for store {args.store} (Ctrl+C to stop)\n")
    try:
        while True:
            customer = random.choice(customers) if customers and random.random() < 0.4 else None
            employee = random.choice(cashiers) if cashiers and random.random() < 0.6 else None

            req = OrderCreateRequest(
                storeId=args.store,
                customerId=customer["customerId"] if customer else None,
                employeeId=employee["employeeId"] if employee else None,
                channelOrigin="POS_REGISTER_1" if employee else "MOBILE_APP",
                paymentMethod=random.choice(["BREW_CARD", "CREDIT_CARD", "APPLE_PAY", "CASH"]),
                items=random_items(menu_items, modifiers_by_type),
            )
            order = order_service.create_order(req)
            print(f"  placed {order['orderId']}  total={order['total']}")
            if args.auto_advance:
                threading.Thread(target=run_order_lifecycle, args=(order, args), daemon=True).start()

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
