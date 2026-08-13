#!/usr/bin/env python3
"""
Loads every data/samples/*.json file into its matching
bucket.scope.collection in Capella.

Filename -> collection mapping is derived from app.db.SCOPES, so this
stays in sync with the data model automatically - see docs/data-model.md.

Usage:
    python scripts/seed_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.config import get_settings

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


def collection_lookup() -> dict[str, tuple[str, str]]:
    """collection_name -> (scope_name, collection_name)"""
    return {
        coll: (scope, coll) for scope, colls in db.SCOPES.items() for coll in colls
    }


def main() -> None:
    settings = get_settings()
    db.connect()
    lookup = collection_lookup()

    print(f"Seeding bucket `{settings.cb_bucket}` from {SAMPLES_DIR}\n")

    total = 0
    for path in sorted(SAMPLES_DIR.glob("*.json")):
        collection_name = path.stem
        if collection_name not in lookup:
            print(f"  skipping {path.name} (no matching collection `{collection_name}`)")
            continue

        scope_name, coll_name = lookup[collection_name]
        docs = json.loads(path.read_text())
        coll = db.collection(scope_name, coll_name)

        for doc in docs:
            key = doc["_id"]
            body = {k: v for k, v in doc.items() if k != "_id"}
            coll.upsert(key, body)

        print(f"  {scope_name}.{coll_name:<22} {len(docs):>3} docs  <- {path.name}")
        total += len(docs)

    print(f"\nDone. {total} documents upserted.")


if __name__ == "__main__":
    main()
