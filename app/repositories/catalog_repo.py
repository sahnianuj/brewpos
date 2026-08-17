"""Reads against the `catalog` scope (stores, menu_items, modifiers)."""
from __future__ import annotations

from couchbase.exceptions import DocumentNotFoundException

from app import db
from app.config import get_settings

BUCKET = get_settings().cb_bucket


def get_store(store_id: str) -> dict | None:
    try:
        result = db.collection("catalog", "stores").get(f"store::{store_id}")
        return result.content_as[dict]
    except DocumentNotFoundException:
        return None


def list_stores() -> list[dict]:
    # `type = "store"` excludes App Services' own sync-metadata docs
    # (`_sync:local:checkpoint/...`, `_sync:syncInfo`), which otherwise
    # come back from an unfiltered SELECT * over the collection.
    rows = db.query(
        f"SELECT s.* FROM `{BUCKET}`.`catalog`.`stores` AS s "
        'WHERE s.type = "store" ORDER BY s.name'
    )
    return list(rows)


def list_menu_items(category: str | None = None) -> list[dict]:
    statement = (
        f"SELECT m.* FROM `{BUCKET}`.`catalog`.`menu_items` AS m "
        'WHERE m.type = "menu_item" AND m.isActive = true'
    )
    params = {}
    if category:
        statement += " AND m.category = $category"
        params["category"] = category
    statement += " ORDER BY m.category, m.name"
    return list(db.query(statement, **params))


def get_menu_item(item_id: str) -> dict | None:
    try:
        result = db.collection("catalog", "menu_items").get(f"menu_item::{item_id}")
        return result.content_as[dict]
    except DocumentNotFoundException:
        return None


def list_modifiers() -> list[dict]:
    # `type = "modifier"` excludes App Services' own sync-metadata docs
    # (`_sync:local:checkpoint/...`, `_sync:syncInfo`), which otherwise
    # come back from an unfiltered SELECT * over the collection.
    rows = db.query(
        f"SELECT mo.* FROM `{BUCKET}`.`catalog`.`modifiers` AS mo "
        'WHERE mo.type = "modifier" ORDER BY mo.modifierType, mo.label'
    )
    return list(rows)
