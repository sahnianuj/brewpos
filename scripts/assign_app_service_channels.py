#!/usr/bin/env python3
"""
Creates/updates the demo App User on all 4 App Endpoints (catalog, people,
operations, inventory) with exactly the channels each one needs - the
scripted equivalent of the manual Security > App Users walkthrough in
sync-gateway/app-services-setup.md §3, so you don't have to click through
4 forms and copy channel names by hand.

Channels are computed FROM the actual seeded data (data/samples/*.json),
not hardcoded - add a store or customer to the sample data and re-run this
and the grants stay correct.

--- What this calls ---

Uses Capella App Services' Admin REST API:
    PUT https://{host}:4985/{endpointName}/_user/{username}
documented at https://docs.couchbase.com/cloud/app-services/references/rest_api_admin.html
with a `collection_access` body that grants channels per collection - the
exact same shape as the per-collection tables in app-services-setup.md §3.

Two things about reaching that API on Capella specifically:
  1. Your current IP must be added to this App Service's
     Settings > Allowed IP list first - this script will fail to even
     connect otherwise (a clear connection error, not a hang).
  2. It authenticates with HTTP Basic Auth - confirmed against a real
     Capella deployment to be the *same* App User credential being
     granted channels (APP_SERVICES_USERNAME/PASSWORD from .env), not a
     separate admin-only credential. --admin-username/--admin-password
     exist only in case a future/different deployment needs something else.

Usage:
    python scripts/assign_app_service_channels.py                 # do it
    python scripts/assign_app_service_channels.py --dry-run        # preview only, no network calls
    python scripts/assign_app_service_channels.py --admin-username foo --admin-password bar  # override, if needed
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

# scope -> collections in it (must match app/static/js/cbl/client.js's
# COLLECTIONS and the App Endpoints created per sync-gateway/app-services-setup.md §2)
SCOPES = {
    "catalog": ["stores", "menu_items", "modifiers"],
    "people": ["employees", "customers"],
    "operations": ["orders", "order_status_events"],
    "inventory": ["stock_levels"],
}


def load(name: str) -> list[dict]:
    path = SAMPLES_DIR / f"{name}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def compute_channels() -> dict[str, dict[str, list[str]]]:
    """Returns {scope: {collection: [channels]}} computed from seeded data -
    the exact per-collection breakdown documented in app-services-setup.md §3,
    derived instead of hand-copied so it can't drift out of sync."""
    stores = load("stores")
    customers = load("customers")
    employees = load("employees")

    store_ids = [s["storeId"] for s in stores]
    customer_channels = sorted({f"user-{c['customerId']}" for c in customers})

    staff_channels = sorted({f"store-{sid}-staff" for sid in store_ids})
    station_channels: set[str] = set()
    for e in employees:
        station = e.get("assignedStation")
        if station == "ESPRESSO_BAR":
            station_channels.add(f"store-{e['storeId']}-espresso")
        elif station == "WARMING_STATION":
            station_channels.add(f"store-{e['storeId']}-warming")

    store_channels = sorted({f"store-{sid}" for sid in store_ids})
    espresso_channels = sorted({f"store-{sid}-espresso" for sid in store_ids})
    warming_channels = sorted({f"store-{sid}-warming" for sid in store_ids})

    return {
        "catalog": {
            "stores": store_channels,
            "menu_items": ["public-menu"],
            "modifiers": ["public-menu"],
        },
        "people": {
            "employees": sorted(set(staff_channels) | station_channels),
            "customers": customer_channels,
        },
        "operations": {
            "orders": sorted(set(store_channels) | set(espresso_channels) | set(warming_channels) | set(customer_channels)),
            "order_status_events": sorted(set(store_channels) | set(espresso_channels) | set(warming_channels) | set(customer_channels)),
        },
        "inventory": {
            "stock_levels": store_channels,
        },
    }


def admin_url_for_scope(base_url: str, scope: str) -> str:
    """wss://host:4984 -> https://host:4985/{scope} (see module docstring)."""
    parsed = urlparse(base_url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Could not parse hostname out of APP_SERVICES_BASE_URL={base_url!r}")
    return f"https://{hostname}:4985/{scope}"


def put_user(admin_url: str, username: str, password: str, channels_by_collection: dict[str, list[str]],
             admin_username: str | None, admin_password: str | None, dry_run: bool) -> None:
    url = f"{admin_url}/_user/{quote(username)}"
    body = {
        "name": username,
        "password": password,
        "admin_channels": [],
        "collection_access": {
            admin_url.rsplit("/", 1)[-1]: {  # scope name, from the tail of admin_url
                collection: {"admin_channels": channels} for collection, channels in channels_by_collection.items()
            }
        },
    }

    if dry_run:
        print(f"  [dry-run] PUT {url}")
        print(f"  [dry-run] body: {json.dumps(body, indent=2)}")
        return

    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PUT", headers={"Content-Type": "application/json"})
    if admin_username and admin_password:
        import base64

        token = base64.b64encode(f"{admin_username}:{admin_password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"  {resp.status} {url}")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        print(f"  ✗ {e.code} {url}\n    {body_text}")
        if e.code in (401, 403):
            print(
                "    -> looks like the Admin API needs a credential this script didn't send. "
                "Retry with --admin-username/--admin-password, or check Capella's docs/support "
                "for how your App Services deployment expects Admin API calls to authenticate."
            )
        raise SystemExit(1)
    except urllib.error.URLError as e:
        print(f"  ✗ Could not reach {url}: {e.reason}")
        print(
            "    -> most likely your current IP isn't in this App Service's "
            "Settings > Allowed IP list yet (see app-services-setup.md), or the hostname/port is wrong."
        )
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default=None, help="defaults to APP_SERVICES_USERNAME from .env")
    parser.add_argument("--password", default=None, help="defaults to APP_SERVICES_PASSWORD from .env")
    parser.add_argument("--admin-username", default=None, help="defaults to APP_SERVICES_ADMIN_USERNAME, then APP_SERVICES_USERNAME, from .env")
    parser.add_argument("--admin-password", default=None, help="defaults to APP_SERVICES_ADMIN_PASSWORD, then APP_SERVICES_PASSWORD, from .env")
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent, make no network calls")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.app_services_base_url:
        raise SystemExit("APP_SERVICES_BASE_URL isn't set in .env - see sync-gateway/app-services-setup.md §4.")

    username = args.username or settings.app_services_username
    password = args.password or settings.app_services_password
    if not username or not password:
        raise SystemExit("Need a username/password - set APP_SERVICES_USERNAME/APP_SERVICES_PASSWORD in .env or pass --username/--password.")

    # Admin API credential: --admin-username/password > APP_SERVICES_ADMIN_*
    # > the sync credential itself (confirmed against a real Capella
    # deployment to work, since today they're the same App User - see
    # effective_admin_username/password in app/config.py).
    admin_username = args.admin_username or settings.effective_admin_username
    admin_password = args.admin_password or settings.effective_admin_password

    channels = compute_channels()

    print(f"Assigning channels to App User '{username}' on 4 App Endpoints:\n")
    for scope, collections in SCOPES.items():
        channels_by_collection = channels[scope]
        admin_url = admin_url_for_scope(settings.app_services_base_url, scope)
        print(f"{scope}:")
        for collection in collections:
            print(f"  {collection}: {channels_by_collection[collection]}")
        put_user(admin_url, username, password, channels_by_collection, admin_username, admin_password, args.dry_run)
        print()

    if args.dry_run:
        print("Dry run only - nothing was sent. Re-run without --dry-run to apply.")
    else:
        print("Done. Restart the app and check the sync banners on each page.")


if __name__ == "__main__":
    main()
