#!/usr/bin/env python3
"""
Definitively answers "is my custom sync function actually active on this
collection?" for all 8 collections - no UI, no guessing, no stale
snapshots.

Why this exists: the Admin REST API's `_config` endpoint (which would
normally answer this) returns 403 on Capella's managed App Services - it
doesn't expose the full self-hosted Sync Gateway Admin API surface.
Inspecting an OLD document's `_sync` xattr is also unreliable, since
import only reprocesses a document at its next mutation - a doc imported
before you last edited the sync function still shows the old result.

So this writes one brand-new throwaway document directly into each
collection (same path `seed_data.py` uses - the Server SDK, bypassing App
Services entirely), waits for Capella's import process to pick it up, then
reads back its `_sync` xattr and reports the channel(s) it actually got
assigned. Compares that against the channel your custom sync function
*should* produce:
  - Matches  -> the sync function is genuinely active. Good.
  - Channel equals the collection's own name (e.g. `menu_items`) -> Sync
    Gateway's zero-config default is still active - the custom function
    was never saved for this collection.
  - No `_sync` xattr at all -> Import Filter isn't enabled/active for this
    collection (or still on Capella's default `doc.type == "mobile"`
    filter, which the throwaway doc doesn't match).

The throwaway documents are deleted again at the end either way.

Usage:
    python scripts/verify_sync_function.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from couchbase.exceptions import DocumentNotFoundException, PathNotFoundException
from couchbase.subdocument import get as sd_get

from app import db
from app.config import get_settings

MARKER = "__synccheck__"

# (scope, collection, doc body satisfying that collection's sync function's
# required-field checks, expected primary channel)
CHECKS = [
    ("catalog", "stores", {"storeId": MARKER, "name": "Sync Check Store"}, f"store-{MARKER}"),
    ("catalog", "menu_items", {"itemId": MARKER, "name": "Sync Check Item", "station": "ESPRESSO_BAR"}, "public-menu"),
    ("catalog", "modifiers", {"modifierId": MARKER, "modifierType": "SIZE"}, "public-menu"),
    ("people", "employees", {"employeeId": MARKER, "storeId": MARKER, "role": "BARISTA"}, f"store-{MARKER}-staff"),
    ("people", "customers", {"customerId": MARKER}, f"user-{MARKER}"),
    (
        "operations",
        "orders",
        {"storeId": MARKER, "orderStatus": "PAID", "items": [{"station": "ESPRESSO_BAR"}]},
        f"store-{MARKER}",
    ),
    (
        "operations",
        "order_status_events",
        {"orderId": MARKER, "storeId": MARKER, "toStatus": "PAID"},
        f"store-{MARKER}",
    ),
    ("inventory", "stock_levels", {"storeId": MARKER, "ingredientId": MARKER}, f"store-{MARKER}"),
]

IMPORT_WAIT_SECONDS = 5
IMPORT_WAIT_RETRIES = 12


def check_one(scope: str, coll_name: str, body: dict, expected_channel: str) -> None:
    key = f"{MARKER}-{coll_name}"
    coll = db.collection(scope, coll_name)

    result = coll.upsert(key, body)
    readback = coll.get(key)
    print(f"{scope}.{coll_name}: wrote cas={result.cas}, read back body={readback.content_as[dict]}")

    channels: list[str] | None = None
    for attempt in range(IMPORT_WAIT_RETRIES):
        time.sleep(IMPORT_WAIT_SECONDS)
        try:
            result = coll.lookup_in(key, [sd_get("_sync", xattr=True)])
            sync_meta = result.content_as[dict](0)
            channel_set = sync_meta.get("channel_set") or sync_meta.get("channels") or []
            if isinstance(channel_set, dict):
                channels = sorted(channel_set.keys())
            else:
                channels = sorted(c["name"] if isinstance(c, dict) else c for c in channel_set)
            break
        except PathNotFoundException:
            continue  # not imported yet, or Import Filter is rejecting/not enabled - keep waiting
    else:
        channels = None

    print(f"{scope}.{coll_name}:")
    if channels is None:
        print("    ✗ no `_sync` xattr appeared after "
              f"{IMPORT_WAIT_SECONDS * IMPORT_WAIT_RETRIES}s - Import Filter is probably not "
              "enabled/active for this collection (see app-services-setup.md §2 step 8).")
    elif expected_channel in channels:
        print(f"    ✓ channels={channels} - custom sync function is active.")
    elif coll_name in channels:
        print(f"    ✗ channels={channels} - this is Sync Gateway's ZERO-CONFIG DEFAULT "
              f"(channel == collection name). The custom sync function for {coll_name} was "
              "never actually saved - go re-paste + Save it (see app-services-setup.md §2 step 7).")
    else:
        print(f"    ? channels={channels} - active, but doesn't match the expected "
              f"'{expected_channel}' - check the sync function's logic for this collection.")

    try:
        coll.remove(key)
    except DocumentNotFoundException:
        pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only", default=None,
        help="only test this one collection name (e.g. menu_items), for fast iteration",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.app_services_base_url:
        print("Note: APP_SERVICES_BASE_URL isn't set in .env - this script only needs CB_* "
              "(Server SDK) settings though, so it'll still run.")

    checks = [c for c in CHECKS if args.only is None or c[1] == args.only]
    if not checks:
        raise SystemExit(f"No collection named {args.only!r} - options are: {[c[1] for c in CHECKS]}")

    db.connect()
    print(
        f"\nWriting {len(checks)} throwaway test document(s) directly via the Server SDK, "
        f"then polling (up to {IMPORT_WAIT_SECONDS * IMPORT_WAIT_RETRIES}s each) for App "
        "Services to import them...\n"
    )
    for scope, coll_name, body, expected_channel in checks:
        check_one(scope, coll_name, body, expected_channel)
    print("\nDone. Throwaway documents were cleaned up (removed) either way.")


if __name__ == "__main__":
    main()
