#!/usr/bin/env python3
"""
Creates the scopes, collections, and indexes this demo needs inside an
already-existing Capella bucket.

The bucket itself has to be created first, via the Capella UI or API -
Capella ties bucket creation to cluster storage/pricing settings, so it's
not something the Couchbase SDK can do for you. See README.md > Setup.

Safe to re-run: scope/collection creation tolerates "already exists", and
indexes are dropped and recreated each run so their definitions never drift
from what's listed below (and any primary index left over from an older
version of this script gets dropped too).

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

# Targeted secondary indexes that back every query pattern the app actually
# runs (KDS queues, pickup board, order lookup by orderId, customer order
# history, and the unfiltered "list everything" reads the setup/catalog
# pages do). No primary indexes: each collection's queries are covered by
# a purpose-built GSI instead, which is how you'd index this outside a demo
# too.
SECONDARY_INDEXES = [
    # catalog.stores - list_stores() filters type = "store" (this excludes
    # App Services' own sync-metadata docs living in the same collection,
    # e.g. `_sync:local:checkpoint/...`) and orders by name.
    ("idx_stores_type_name", "catalog", "stores", "type, name"),
    # catalog.menu_items - list_menu_items() filters type + isActive (+
    # optional category) and orders by category, name.
    (
        "idx_menu_items_active",
        "catalog",
        "menu_items",
        "type, isActive, category, name",
    ),
    # catalog.modifiers - list_modifiers() filters type = "modifier" and
    # orders by modifierType, label.
    (
        "idx_modifiers_type_label",
        "catalog",
        "modifiers",
        "type, modifierType, label",
    ),
    # people.employees - list_employees() filters type + storeId.
    ("idx_employees_store", "people", "employees", "type, storeId"),
    # people.customers - list_customers() filters type = "customer" and
    # orders by displayName.
    ("idx_customers_name", "people", "customers", "type, displayName"),
    (
        "idx_orders_store_status",
        "operations",
        "orders",
        "storeId, orderStatus, createdAt",
    ),
    ("idx_orders_orderId", "operations", "orders", "orderId"),
    ("idx_orders_customer", "operations", "orders", "customerId"),
    ("idx_events_orderId", "operations", "order_status_events", "orderId, timestamp"),
    # inventory.stock_levels - reads/writes here are all KV by key (see
    # inventory_repo.py); this index exists for ad hoc/ops querying only.
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


def drop_primary_indexes(bucket_name: str) -> None:
    """Drop any primary index on our collections, so a rerun stays GSI-only
    even on a cluster that was originally set up before this script stopped
    creating primary indexes."""
    seen = {(scope_name, collection_name) for _, scope_name, collection_name, _ in SECONDARY_INDEXES}
    for scope_name, collection_name in sorted(seen):
        statement = (
            f"DROP PRIMARY INDEX IF EXISTS ON "
            f"`{bucket_name}`.`{scope_name}`.`{collection_name}`"
        )
        db.cluster().query(statement).execute()
    print(f"  primary indexes dropped (if any existed) on {len(seen)} collections")


def ensure_indexes(bucket_name: str) -> None:
    drop_primary_indexes(bucket_name)

    for index_name, scope_name, collection_name, keys in SECONDARY_INDEXES:
        # DROP + CREATE rather than CREATE ... IF NOT EXISTS: an existing
        # index with this name but stale keys (e.g. after this list changes)
        # would otherwise silently keep serving the old definition forever.
        drop_statement = (
            f"DROP INDEX `{index_name}` IF EXISTS ON "
            f"`{bucket_name}`.`{scope_name}`.`{collection_name}`"
        )
        db.cluster().query(drop_statement).execute()

        statement = (
            f"CREATE INDEX `{index_name}` ON "
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
