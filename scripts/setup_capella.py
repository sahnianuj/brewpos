#!/usr/bin/env python3
"""
Creates the scopes, collections, and indexes this demo needs inside an
already-existing Capella bucket.

The bucket itself has to be created first, via the Capella UI or API -
Capella ties bucket creation to cluster storage/pricing settings, so it's
not something the Couchbase SDK can do for you. See README.md > Setup.

Safe to re-run: every create call tolerates "already exists".

Usage:
    python scripts/setup_capella.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from couchbase.exceptions import (
    CollectionAlreadyExistsException,
    ScopeAlreadyExistsException,
)

from app import db
from app.config import get_settings

PRIMARY_INDEX_COLLECTIONS = [
    ("catalog", "stores"),
    ("catalog", "menu_items"),
    ("catalog", "modifiers"),
    ("people", "employees"),
    ("people", "customers"),
    ("operations", "orders"),
    ("operations", "order_status_events"),
    ("inventory", "stock_levels"),
]

# A handful of targeted secondary indexes to back the query patterns the
# app actually runs (KDS queues, pickup board, order lookup by orderId,
# customer order history). Primary indexes above are fine for a demo but
# would not be how you'd index this in production.
SECONDARY_INDEXES = [
    (
        "idx_orders_store_status",
        "operations",
        "orders",
        "storeId, orderStatus, createdAt",
    ),
    ("idx_orders_orderId", "operations", "orders", "orderId"),
    ("idx_orders_customer", "operations", "orders", "customerId"),
    ("idx_events_orderId", "operations", "order_status_events", "orderId, timestamp"),
    ("idx_employees_store", "people", "employees", "storeId"),
    ("idx_stock_store", "inventory", "stock_levels", "storeId"),
]


def ensure_scopes_and_collections(bucket_name: str) -> None:
    bucket = db.cluster().bucket(bucket_name)
    coll_manager = bucket.collections()

    scopes = sorted(db.SCOPES.keys())
    for scope_name in scopes:
        try:
            coll_manager.create_scope(scope_name)
            print(f"  created scope `{scope_name}`")
        except ScopeAlreadyExistsException:
            print(f"  scope `{scope_name}` already exists")

        for collection_name in db.SCOPES[scope_name]:
            try:
                coll_manager.create_collection(scope_name, collection_name)
                print(f"    created collection `{scope_name}`.`{collection_name}`")
            except CollectionAlreadyExistsException:
                print(f"    collection `{scope_name}`.`{collection_name}` already exists")


def ensure_indexes(bucket_name: str) -> None:
    for scope_name, collection_name in PRIMARY_INDEX_COLLECTIONS:
        statement = (
            f"CREATE PRIMARY INDEX IF NOT EXISTS ON "
            f"`{bucket_name}`.`{scope_name}`.`{collection_name}`"
        )
        db.cluster().query(statement).execute()
        print(f"  primary index ready on `{scope_name}`.`{collection_name}`")

    for index_name, scope_name, collection_name, keys in SECONDARY_INDEXES:
        statement = (
            f"CREATE INDEX `{index_name}` IF NOT EXISTS ON "
            f"`{bucket_name}`.`{scope_name}`.`{collection_name}`({keys})"
        )
        db.cluster().query(statement).execute()
        print(f"  index `{index_name}` ready on `{scope_name}`.`{collection_name}`")


def main() -> None:
    settings = get_settings()
    db.connect()

    print(f"\nChecking bucket `{settings.cb_bucket}` exists ...")
    try:
        db.cluster().buckets().get_bucket(settings.cb_bucket)
        print(f"  found `{settings.cb_bucket}`.")
    except Exception as e:
        print(
            f"  ✗ Bucket `{settings.cb_bucket}` was not found or is not reachable "
            f"({type(e).__name__}: {e}).\n"
            "Create it first in the Capella UI (Databases > your cluster > Buckets > "
            "Create Bucket), then re-run this script.\n"
        )
        raise SystemExit(1)

    print("\nCreating scopes/collections...")
    ensure_scopes_and_collections(settings.cb_bucket)

    print("\nCreating indexes (this can take a few seconds per index)...")
    ensure_indexes(settings.cb_bucket)

    print("\nDone. Next: python scripts/seed_data.py")


if __name__ == "__main__":
    main()
