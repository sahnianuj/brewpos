"""
Couchbase connection layer.

Holds a single Cluster connection for the process (created on FastAPI
startup) and exposes small helpers so repositories don't need to know
about connection setup - just `collection("catalog", "menu_items")` or
`query(...)`.

Bucket / scope / collection layout (see docs/data-model.md for details):

    starbucks
    ├── catalog      stores, menu_items, modifiers
    ├── people       employees, customers
    ├── operations   orders, order_status_events
    └── inventory    stock_levels
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any, Iterable

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.exceptions import CouchbaseException
from couchbase.options import ClusterOptions, QueryOptions

from app.config import get_settings

logger = logging.getLogger("starbucks.db")

# scope -> collection names, the single source of truth for the data model
SCOPES: dict[str, list[str]] = {
    "catalog": ["stores", "menu_items", "modifiers"],
    "people": ["employees", "customers"],
    "operations": ["orders", "order_status_events"],
    "inventory": ["stock_levels"],
}

_cluster: Cluster | None = None


def connect() -> Cluster:
    """Create (once) and return the process-wide Cluster connection.

    Prints progress with `print()` rather than only `logger.info(...)`,
    since scripts (setup_capella.py, seed_data.py, ...) run standalone and
    never call logging.basicConfig() - a plain logger.info() call in that
    context is silently dropped, which is exactly what made a slow/failed
    connection look like a silent hang.
    """
    global _cluster
    if _cluster is not None:
        return _cluster

    settings = get_settings()
    timeout = settings.cb_connect_timeout_seconds
    auth = PasswordAuthenticator(settings.cb_username, settings.cb_password)
    options = ClusterOptions(auth)
    # wan_development gives generous timeouts for the post-connect
    # operations (query/management/etc: 120s) - appropriate for Capella's
    # latency. But it ALSO sets bootstrap_timeout to 120s, which is what
    # actually governs a bad/unreachable connection string: a typo'd host
    # or wrong region doesn't fail fast, it silently hangs for up to two
    # minutes before raising - easy to mistake for the app being frozen.
    # wait_until_ready()'s own timeout below does NOT bound this phase, so
    # we override it explicitly to the same configurable timeout.
    options.apply_profile("wan_development")
    timeout_delta = timedelta(seconds=timeout)
    options["bootstrap_timeout"] = timeout_delta
    options["resolve_timeout"] = timeout_delta
    options["dns_srv_timeout"] = timeout_delta
    options["connect_timeout"] = timeout_delta

    print(
        f"[db] Connecting to {settings.cb_conn_string} as '{settings.cb_username}' "
        f"(bucket='{settings.cb_bucket}', timeout={timeout}s) ..."
    )
    started = time.monotonic()
    try:
        cluster = Cluster(settings.cb_conn_string, options)
        cluster.wait_until_ready(timedelta(seconds=timeout))
    except CouchbaseException as e:
        elapsed = time.monotonic() - started
        print(f"[db] ✗ Connection FAILED after {elapsed:.1f}s: {type(e).__name__}: {e}")
        print(
            "[db]   Common causes: wrong CB_CONN_STRING (must be couchbases://...), "
            "wrong CB_USERNAME/CB_PASSWORD, your IP not in Capella's Allowed IP "
            "Addresses list, or the `starbucks` bucket doesn't exist yet."
        )
        raise
    except Exception as e:
        elapsed = time.monotonic() - started
        print(f"[db] ✗ Connection FAILED after {elapsed:.1f}s: {type(e).__name__}: {e}")
        raise

    elapsed = time.monotonic() - started
    print(f"[db] ✓ Connected in {elapsed:.1f}s.")
    logger.info("Connected to Capella cluster at %s", settings.cb_conn_string)

    _cluster = cluster
    return _cluster


def disconnect() -> None:
    global _cluster
    if _cluster is not None:
        _cluster.close()
        _cluster = None


def cluster() -> Cluster:
    if _cluster is None:
        return connect()
    return _cluster


def bucket():
    settings = get_settings()
    return cluster().bucket(settings.cb_bucket)


def collection(scope_name: str, collection_name: str):
    """Return a Collection handle, e.g. collection('operations', 'orders')."""
    return bucket().scope(scope_name).collection(collection_name)


def scope(scope_name: str):
    return bucket().scope(scope_name)


def query(statement: str, *args: Any, **kwargs: Any) -> Iterable[dict]:
    """Run a N1QL query scoped to the `starbucks` bucket and return rows.

    Positional/keyword args are passed straight to QueryOptions, e.g.:
        query("SELECT ... WHERE storeId = $storeId", storeId="10492")
    """
    result = cluster().query(statement, QueryOptions(*args, **kwargs))
    return list(result.rows())
