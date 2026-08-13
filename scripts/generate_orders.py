#!/usr/bin/env python3
"""
Generates additional, realistic-looking orders directly in Capella by
calling the same order_service.create_order() the running app uses -
so pricing, station routing, inventory decrement, and the
order_status_event audit trail all come out exactly as they would from
a real register.

Requires the catalog (stores/menu_items/modifiers) to already be seeded
- run scripts/seed_data.py first.

Each generated order is also randomly walked forward through its
lifecycle (some left PAID, some IN_PREPARATION, some READY, some
COMPLETED, a few CANCELLED) so the KDS screens and pickup board have a
realistic mix to show right away.

Usage:
    python scripts/generate_orders.py --count 20
    python scripts/generate_orders.py --count 50 --store 10492 --seed 7
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.models import CustomizationIn, OrderCreateRequest, OrderLineItemIn
from app.repositories import catalog_repo, customer_repo
from app.services import order_service

LIFECYCLE_WEIGHTS = [
    ("PAID", 0.30),
    ("IN_PREPARATION", 0.20),
    ("READY", 0.20),
    ("COMPLETED", 0.25),
    ("CANCELLED", 0.05),
]


def pick_lifecycle() -> str:
    r = random.random()
    upto = 0.0
    for status, weight in LIFECYCLE_WEIGHTS:
        upto += weight
        if r <= upto:
            return status
    return "PAID"


def random_items(menu_items: list[dict], modifiers_by_type: dict) -> list[OrderLineItemIn]:
    beverages = [m for m in menu_items if m["category"] == "BEVERAGE"]
    foods = [m for m in menu_items if m["category"] == "FOOD"]

    picks = [random.choice(beverages)]
    if random.random() < 0.6:
        picks.append(random.choice(foods))
    if random.random() < 0.15:
        picks.append(random.choice(beverages))

    items = []
    for menu_item in picks:
        size = random.choice(menu_item["availableSizes"])
        customizations = []
        for c_type in menu_item.get("allowedCustomizations", []):
            if random.random() < 0.4:
                options = modifiers_by_type.get(c_type, [])
                if options:
                    mod = random.choice(options)
                    customizations.append(CustomizationIn(type=c_type, value=mod["value"]))
        items.append(
            OrderLineItemIn(
                itemId=menu_item["itemId"],
                size=size,
                quantity=1,
                customizations=customizations,
            )
        )
    return items


def walk_lifecycle(order: dict, target: str) -> None:
    order_id = order["orderId"]
    espresso_actor = "barista_442" if order["storeId"] == "10492" else "barista_884"
    warming_actor = "warm_339" if order["storeId"] == "10492" else "warm_910"

    if target == "PAID":
        return

    if target == "CANCELLED":
        order_service.cancel_order(order_id, "Auto-generated demo cancellation", "mgr_2201")
        return

    for item in order["items"]:
        actor = espresso_actor if item["station"] == "ESPRESSO_BAR" else warming_actor
        order_service.advance_line_item(order_id, item["lineItemId"], "IN_PREPARATION", actor)
    if target == "IN_PREPARATION":
        return

    for item in order["items"]:
        actor = espresso_actor if item["station"] == "ESPRESSO_BAR" else warming_actor
        order_service.advance_line_item(order_id, item["lineItemId"], "READY", actor)
    if target == "READY":
        return

    order_service.complete_order(order_id, "cashier_128")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--store", type=str, default=None, help="Restrict to one storeId")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    db.connect()

    stores = catalog_repo.list_stores()
    if args.store:
        stores = [s for s in stores if s["storeId"] == args.store]
    if not stores:
        raise SystemExit("No matching stores found - run scripts/seed_data.py first.")

    menu_items = catalog_repo.list_menu_items()
    modifiers = catalog_repo.list_modifiers()
    modifiers_by_type: dict[str, list[dict]] = {}
    for m in modifiers:
        modifiers_by_type.setdefault(m["modifierType"], []).append(m)

    customers = customer_repo.list_customers()
    employees = customer_repo.list_employees(stores[0]["storeId"])
    cashiers_by_store = {
        s["storeId"]: [e for e in customer_repo.list_employees(s["storeId"]) if e["role"] == "CASHIER"]
        for s in stores
    }

    created = []
    for _ in range(args.count):
        store = random.choice(stores)
        customer = random.choice(customers) if random.random() < 0.5 else None
        cashiers = cashiers_by_store.get(store["storeId"], [])
        employee = random.choice(cashiers) if cashiers and random.random() < 0.7 else None

        req = OrderCreateRequest(
            storeId=store["storeId"],
            customerId=customer["customerId"] if customer else None,
            employeeId=employee["employeeId"] if employee else None,
            channelOrigin="POS_REGISTER_1" if employee else "MOBILE_APP",
            paymentMethod=random.choice(["STARBUCKS_CARD", "CREDIT_CARD", "APPLE_PAY", "CASH"]),
            items=random_items(menu_items, modifiers_by_type),
        )
        order = order_service.create_order(req)
        target = pick_lifecycle()
        try:
            walk_lifecycle(order, target)
        except ValueError as e:
            print(f"  (skipped lifecycle walk for {order['orderId']}: {e})")
        created.append((order["orderId"], target))

    print(f"Created {len(created)} orders:")
    for order_id, status in created:
        print(f"  {order_id:<20} -> {status}")


if __name__ == "__main__":
    main()
