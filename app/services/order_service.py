"""Order lifecycle: creation, station hand-off, pickup - and the audit
trail / inventory side-effects that go with each transition.

This is the one place that touches multiple collections per call, which
is deliberate: it's the clearest spot to see how `orders`,
`order_status_events`, and `stock_levels` relate to each other and to
`catalog.menu_items` / `catalog.modifiers`.
"""
from __future__ import annotations

import itertools
import random
from datetime import datetime, timezone
from typing import Optional

from app.models import OrderCreateRequest
from app.repositories import catalog_repo, customer_repo, inventory_repo, order_repo

# Real coffee shop chains don't all charge the same sales tax; this is enough
# variation to make the point without modeling full tax jurisdictions.
STORE_TAX_RATES = {"10492": 0.0863, "20873": 0.1025}
DEFAULT_TAX_RATE = 0.0875

# Fallback "name on the cup" for guest orders that don't specify one (e.g.
# scripts/generate_orders.py, which doesn't hand-pick a name per order) -
# just enough variety that a screen full of demo orders doesn't look
# obviously synthetic.
GUEST_NAMES = [
    "Alex", "Sam", "Jordan", "Taylor", "Casey", "Morgan", "Riley", "Jamie",
    "Drew", "Quinn", "Avery", "Reese",
]

LINE_ITEM_TRANSITIONS = {
    "QUEUED": {"IN_PREPARATION"},
    "IN_PREPARATION": {"READY"},
}

_event_seq = itertools.count(1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _modifier_lookup() -> dict[tuple[str, str], dict]:
    return {(m["modifierType"], m["value"]): m for m in catalog_repo.list_modifiers()}


def _next_event_id(order_id: str, existing_count: int) -> tuple[str, str]:
    seq = existing_count + 1
    suffix = str(seq).zfill(3)
    return f"event::{order_id}::{suffix}", f"evt_{order_id.replace('ORD-', '').replace('-', '')}{suffix}"


def _employee_name(employee_id: Optional[str]) -> Optional[str]:
    """Snapshot an employee's display name wherever an employeeId is
    recorded - orders and events store both the id (for lookups/joins)
    and the name (so a register receipt or an event log reads without
    needing a second query)."""
    if not employee_id:
        return None
    employee = customer_repo.get_employee(employee_id)
    return employee["displayName"] if employee else None


def _order_name(customer_id: Optional[str], explicit_name: Optional[str]) -> Optional[str]:
    """The name a barista would call out / write on the cup - a loyalty
    customer's own name takes priority (real coffee shop chains always use the
    account name for a signed-in order), then whatever name was given
    explicitly (e.g. initials typed at Register), then a random guest name
    as a last resort so scripted/demo orders don't fall back to showing
    the raw orderId anywhere in the UI."""
    if customer_id:
        customer = customer_repo.get_customer(customer_id)
        if customer:
            return customer["displayName"]
    return explicit_name or random.choice(GUEST_NAMES)


def _record_event(
    order_id: str,
    store_id: str,
    scope: str,
    from_status: str,
    to_status: str,
    *,
    line_item_id: Optional[str] = None,
    station: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: str = "SYSTEM",
    note: Optional[str] = None,
) -> None:
    existing = order_repo.list_events_for_order(order_id)
    doc_id, event_id = _next_event_id(order_id, len(existing))
    event = {
        "type": "order_status_event",
        "eventId": event_id,
        "orderId": order_id,
        "storeId": store_id,
        "scope": scope,
        "lineItemId": line_item_id,
        "fromStatus": from_status,
        "toStatus": to_status,
        "station": station,
        "actorId": actor_id,
        "actorName": _employee_name(actor_id),
        "actorRole": actor_role,
        "timestamp": _iso(_now()),
    }
    if note:
        event["note"] = note
    order_repo.insert_event(doc_id, event)


def create_order(req: OrderCreateRequest) -> dict:
    store = catalog_repo.get_store(req.storeId)
    if not store:
        raise ValueError(f"Unknown store {req.storeId}")

    modifiers = _modifier_lookup()
    line_items: list[dict] = []
    stations: set[str] = set()
    subtotal = 0.0

    for idx, item_in in enumerate(req.items, start=1):
        menu_item = catalog_repo.get_menu_item(item_in.itemId)
        if not menu_item:
            raise ValueError(f"Unknown menu item {item_in.itemId}")
        if item_in.size not in menu_item["availableSizes"]:
            raise ValueError(f"{menu_item['name']} is not available in size {item_in.size}")

        unit_price = menu_item["sizePricing"][item_in.size]
        customizations = []
        for c in item_in.customizations:
            mod = modifiers.get((c.type, c.value))
            if not mod:
                raise ValueError(f"Unknown customization {c.type}={c.value}")
            unit_price += mod["extraCharge"]
            customizations.append(
                {
                    "type": c.type,
                    "value": c.value,
                    "label": mod["label"],
                    "extraCharge": mod["extraCharge"],
                }
            )

        unit_price = round(unit_price, 2)
        line_total = round(unit_price * item_in.quantity, 2)
        subtotal += line_total
        stations.add(menu_item["station"])

        line_items.append(
            {
                "lineItemId": f"line_{idx}",
                "itemId": menu_item["itemId"],
                "name": menu_item["name"],
                "size": item_in.size,
                "category": menu_item["category"],
                "station": menu_item["station"],
                "status": "QUEUED",
                "quantity": item_in.quantity,
                "customizations": customizations,
                "unitPrice": unit_price,
                "lineTotal": line_total,
            }
        )

        # Deplete matching ingredient stock (best-effort - unmapped
        # ingredients for the store are silently skipped).
        for ingredient in menu_item.get("recipe", {}).get("ingredients", []):
            qty_by_size = ingredient.get("qtyBySize", {})
            qty = qty_by_size.get(item_in.size)
            if qty:
                inventory_repo.decrement_stock(
                    req.storeId, ingredient["ingredientId"], qty * item_in.quantity
                )

    subtotal = round(subtotal, 2)
    tax_rate = STORE_TAX_RATES.get(req.storeId, DEFAULT_TAX_RATE)
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)

    now = _now()
    date_str = now.strftime("%Y%m%d")
    seq = order_repo.next_sequence(req.storeId, date_str)
    order_id = f"ORD-{req.storeId}-{seq}"
    key = order_repo.order_key(req.storeId, date_str, seq)

    channels = [f"store-{req.storeId}"]
    if "ESPRESSO_BAR" in stations:
        channels.append(f"store-{req.storeId}-espresso")
    if "WARMING_STATION" in stations:
        channels.append(f"store-{req.storeId}-warming")
    if req.customerId:
        channels.append(f"user-{req.customerId}")

    order_doc = {
        "type": "order",
        "orderId": order_id,
        "orderName": _order_name(req.customerId, req.orderName),
        "storeId": req.storeId,
        "customerId": req.customerId,
        "employeeId": req.employeeId,
        "employeeName": _employee_name(req.employeeId),
        "channelOrigin": req.channelOrigin,
        "orderStatus": "PAID",
        "payment": {
            "status": "COMPLETED",
            "method": req.paymentMethod,
            "amountPaid": total,
            "transactionId": f"TXN-{order_id.replace('ORD-', '')}",
        },
        "items": line_items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "createdAt": _iso(now),
        "updatedAt": _iso(now),
        "readyAt": None,
        "completedAt": None,
        "cancelReason": None,
        "channels": channels,
    }

    order_repo.create_order(key, order_doc)
    _record_event(
        order_id,
        req.storeId,
        "ORDER",
        "NEW",
        "PAID",
        actor_id=req.employeeId,
        actor_role="CASHIER" if req.employeeId else "CUSTOMER_APP",
    )
    order_doc["_key"] = key
    return order_doc


def advance_line_item(order_id: str, line_item_id: str, new_status: str, actor_id: str | None) -> dict:
    order = order_repo.get_order_by_order_id(order_id)
    if not order:
        raise ValueError(f"Unknown order {order_id}")
    if order["orderStatus"] in ("COMPLETED", "CANCELLED"):
        raise ValueError(f"Order {order_id} is already {order['orderStatus']}")

    item = next((i for i in order["items"] if i["lineItemId"] == line_item_id), None)
    if not item:
        raise ValueError(f"Unknown line item {line_item_id} on order {order_id}")

    current = item["status"]
    if new_status not in LINE_ITEM_TRANSITIONS.get(current, set()):
        raise ValueError(f"Cannot move line item from {current} to {new_status}")

    item["status"] = new_status
    now = _now()
    order["updatedAt"] = _iso(now)

    _record_event(
        order_id,
        order["storeId"],
        "LINE_ITEM",
        current,
        new_status,
        line_item_id=line_item_id,
        station=item["station"],
        actor_id=actor_id,
        actor_role="BARISTA" if item["station"] == "ESPRESSO_BAR" else "WARMING_PARTNER",
    )

    # First item starting prep bumps the whole order to IN_PREPARATION.
    if order["orderStatus"] == "PAID" and new_status == "IN_PREPARATION":
        order["orderStatus"] = "IN_PREPARATION"
        _record_event(
            order_id, order["storeId"], "ORDER", "PAID", "IN_PREPARATION", actor_id=actor_id
        )

    # Last item reaching READY bumps the whole order to READY.
    if all(i["status"] == "READY" for i in order["items"]) and order["orderStatus"] != "READY":
        order["orderStatus"] = "READY"
        order["readyAt"] = _iso(now)
        _record_event(
            order_id, order["storeId"], "ORDER", "IN_PREPARATION", "READY", actor_id=actor_id
        )

    order_repo.save_order(order["_key"], {k: v for k, v in order.items() if k != "_key"})
    return order


def complete_order(order_id: str, actor_id: str | None) -> dict:
    order = order_repo.get_order_by_order_id(order_id)
    if not order:
        raise ValueError(f"Unknown order {order_id}")
    if order["orderStatus"] != "READY":
        raise ValueError(f"Order {order_id} is not READY for pickup (status={order['orderStatus']})")

    now = _now()
    order["orderStatus"] = "COMPLETED"
    order["completedAt"] = _iso(now)
    order["updatedAt"] = _iso(now)
    _record_event(order_id, order["storeId"], "ORDER", "READY", "COMPLETED", actor_id=actor_id)
    order_repo.save_order(order["_key"], {k: v for k, v in order.items() if k != "_key"})
    return order


def cancel_order(order_id: str, reason: str | None, actor_id: str | None) -> dict:
    order = order_repo.get_order_by_order_id(order_id)
    if not order:
        raise ValueError(f"Unknown order {order_id}")
    if order["orderStatus"] in ("COMPLETED", "CANCELLED"):
        raise ValueError(f"Order {order_id} is already {order['orderStatus']}")

    now = _now()
    previous_status = order["orderStatus"]
    order["orderStatus"] = "CANCELLED"
    order["cancelReason"] = reason or "Cancelled at register"
    order["updatedAt"] = _iso(now)
    order["payment"]["status"] = "REFUNDED"
    _record_event(
        order_id,
        order["storeId"],
        "ORDER",
        previous_status,
        "CANCELLED",
        actor_id=actor_id,
        actor_role="MANAGER",
        note=order["cancelReason"],
    )
    order_repo.save_order(order["_key"], {k: v for k, v in order.items() if k != "_key"})
    return order
