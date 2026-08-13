// Opens the shared local Couchbase Lite database for this browser tab.
//
// Loaded with zero build tooling: @couchbase/lite-js ships a real ESM
// bundle, so it can be imported straight from a CDN like any other ES
// module - no npm install, no webpack/vite. Pin the version explicitly so
// a CDN cache/publish doesn't change behavior under us.
const CBL_VERSION = "1.0.1";
const CBL_CDN_URL = `https://cdn.jsdelivr.net/npm/@couchbase/lite-js@${CBL_VERSION}/+esm`;

// jsdelivr's `+esm` build of this package is a CJS-in-ESM wrapper: the
// module namespace has ONLY a `default` export (verified directly - `node
// --print "Object.keys(await import(url))"` returns just ["default"]),
// and the real classes (Database, Replicator, ...) are properties of that
// default export object, not top-level named exports.
const cbl = (await import(CBL_CDN_URL)).default;
export const { Database, Replicator, DocID, meta, Query, LastWriteWins } = cbl;

// Collection names are declared flat but use dotted "scope.collection"
// strings - that's how this SDK addresses App Services' real scopes
// (confirmed against the SDK's own "Manage Scopes and Collections" docs).
// This is the single source of truth other cbl/*.js modules import from.
export const COLLECTIONS = {
  stores: "catalog.stores",
  menuItems: "catalog.menu_items",
  modifiers: "catalog.modifiers",
  employees: "people.employees",
  customers: "people.customers",
  orders: "operations.orders",
  orderEvents: "operations.order_status_events",
  stockLevels: "inventory.stock_levels",
};

// Mirrors the N1QL secondary indexes created server-side by
// scripts/setup_capella.py, so the same query shapes stay fast locally.
const DB_CONFIG = {
  name: "starbucks-pos",
  version: 1,
  collections: {
    [COLLECTIONS.stores]: { indexes: ["storeId"] },
    [COLLECTIONS.menuItems]: { indexes: ["itemId", "category"] },
    [COLLECTIONS.modifiers]: { indexes: ["modifierType"] },
    [COLLECTIONS.employees]: { indexes: ["storeId", "employeeId"] },
    [COLLECTIONS.customers]: { indexes: ["customerId"] },
    [COLLECTIONS.orders]: {
      indexes: ["storeId", "orderStatus", "orderId", "customerId", "createdAt"],
    },
    [COLLECTIONS.orderEvents]: { indexes: ["orderId", "timestamp"] },
    [COLLECTIONS.stockLevels]: { indexes: ["storeId", "ingredientId"] },
  },
};

let dbPromise = null;

/** Opens (once per tab) the local IndexedDB-backed database with all 8
 *  collections declared. Safe to call from every page module - subsequent
 *  calls return the same cached promise. */
export function openDatabase() {
  if (!dbPromise) {
    dbPromise = Database.open(DB_CONFIG);
  }
  return dbPromise;
}
