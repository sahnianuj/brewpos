"""Reads/writes against the `inventory` scope (stock_levels).

Order creation decrements matching stock docs using a sub-document
counter mutation - an atomic `quantityOnHand -= n` that doesn't require
a read-modify-write round trip, which matters once multiple registers
are hitting the same ingredient concurrently.
"""
from __future__ import annotations

from couchbase.exceptions import DocumentNotFoundException, PathNotFoundException
from couchbase.subdocument import counter as sd_counter

from app import db

STOCK = ("inventory", "stock_levels")


def get_stock(store_id: str, ingredient_id: str) -> dict | None:
    try:
        result = db.collection(*STOCK).get(f"stock::{store_id}::{ingredient_id}")
        return result.content_as[dict]
    except DocumentNotFoundException:
        return None


def decrement_stock(store_id: str, ingredient_id: str, amount: int) -> None:
    """Best-effort decrement; silently no-ops if the ingredient isn't tracked
    for this store (keeps the demo catalog free to grow without every item
    needing a matching stock doc)."""
    if amount <= 0:
        return
    key = f"stock::{store_id}::{ingredient_id}"
    try:
        db.collection(*STOCK).mutate_in(key, [sd_counter("quantityOnHand", -amount)])
    except (DocumentNotFoundException, PathNotFoundException):
        return
