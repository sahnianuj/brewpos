"""Reads/writes against the `operations` scope (orders, order_status_events).

This is where the interesting N1QL lives - the espresso/warming station
queues are a straight UNNEST over `orders.items`, which is the same shape
Sync Gateway would replicate to a real KDS device via the
`store-{storeId}-espresso` / `store-{storeId}-warming` channels.
"""
from __future__ import annotations

from couchbase.exceptions import DocumentNotFoundException
from couchbase.options import DeltaValue, IncrementOptions, SignedInt64

from app import db
from app.config import get_settings

BUCKET = get_settings().cb_bucket
ORDERS = ("operations", "orders")
EVENTS = ("operations", "order_status_events")


def order_key(store_id: str, date_str: str, seq: int) -> str:
    return f"order::{store_id}::{date_str}::{seq}"


def next_sequence(store_id: str, date_str: str) -> int:
    """Atomic per-store-per-day order counter, e.g. counter::10492::20260812.

    delta/initial must be the SDK's ConstrainedInt wrapper types (DeltaValue /
    SignedInt64), not plain ints - the SDK validates this client-side before
    any network call, so passing plain ints fails every single increment.
    """
    counter_key = f"counter::{store_id}::{date_str}"
    result = db.collection(*ORDERS).binary().increment(
        counter_key, IncrementOptions(delta=DeltaValue(1), initial=SignedInt64(1))
    )
    return result.content


def create_order(key: str, order_doc: dict) -> None:
    db.collection(*ORDERS).upsert(key, order_doc)


def save_order(key: str, order_doc: dict) -> None:
    db.collection(*ORDERS).upsert(key, order_doc)


def get_order_by_key(key: str) -> dict | None:
    try:
        result = db.collection(*ORDERS).get(key)
        doc = result.content_as[dict]
        doc["_key"] = key
        return doc
    except DocumentNotFoundException:
        return None


def get_order_by_order_id(order_id: str) -> dict | None:
    statement = (
        f"SELECT META(o).id AS _key, o.* "
        f"FROM `{BUCKET}`.`operations`.`orders` AS o "
        "WHERE o.orderId = $orderId LIMIT 1"
    )
    rows = list(db.query(statement, orderId=order_id))
    return rows[0] if rows else None


def list_station_queue(store_id: str, station: str) -> list[dict]:
    """One row per line item that's not yet READY, oldest order first."""
    statement = f"""
        SELECT o.orderId, o.storeId, o.customerId, o.createdAt, i.*
        FROM `{BUCKET}`.`operations`.`orders` AS o
        UNNEST o.items AS i
        WHERE o.storeId = $storeId
          AND i.station = $station
          AND i.status IN ["QUEUED", "IN_PREPARATION"]
          AND o.orderStatus IN ["PAID", "IN_PREPARATION"]
        ORDER BY o.createdAt
    """
    return list(db.query(statement, storeId=store_id, station=station))


def list_ready_for_pickup(store_id: str) -> list[dict]:
    statement = f"""
        SELECT o.* FROM `{BUCKET}`.`operations`.`orders` AS o
        WHERE o.storeId = $storeId AND o.orderStatus = "READY"
        ORDER BY o.readyAt
    """
    return list(db.query(statement, storeId=store_id))


def list_orders_for_store(store_id: str, limit: int = 100) -> list[dict]:
    statement = f"""
        SELECT o.* FROM `{BUCKET}`.`operations`.`orders` AS o
        WHERE o.storeId = $storeId
        ORDER BY o.createdAt DESC
        LIMIT $limit
    """
    return list(db.query(statement, storeId=store_id, limit=limit))


def list_orders_for_customer(customer_id: str, limit: int = 25) -> list[dict]:
    statement = f"""
        SELECT o.* FROM `{BUCKET}`.`operations`.`orders` AS o
        WHERE o.customerId = $customerId
        ORDER BY o.createdAt DESC
        LIMIT $limit
    """
    return list(db.query(statement, customerId=customer_id, limit=limit))


def insert_event(key: str, event_doc: dict) -> None:
    db.collection(*EVENTS).upsert(key, event_doc)


def list_events_for_order(order_id: str) -> list[dict]:
    statement = f"""
        SELECT e.* FROM `{BUCKET}`.`operations`.`order_status_events` AS e
        WHERE e.orderId = $orderId
        ORDER BY e.timestamp
    """
    return list(db.query(statement, orderId=order_id))
