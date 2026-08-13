/**
 * App Services / Sync Gateway sync functions for the `starbucks` bucket.
 *
 * Capella App Services assigns access control at the COLLECTION level (not
 * one global function branching on a `type` field, which was the old
 * single-default-collection pattern). Each block below is a standalone
 * function meant to be pasted into that collection's
 * "Access Control and Data Validation" function in the App Services UI:
 *
 *   Database > <your app> > <scope> > <collection> > Access Control
 *
 * All eight are included here as one file purely for convenience/review;
 * copy each `function(doc, oldDoc) { ... }` block into its matching
 * collection individually. See app-services-setup.md for the channel
 * design these rely on and how they map to the four scopes.
 */

/* ---------------------------------------------------------------------
 * catalog.stores
 * Store metadata - admin/manager-managed reference data.
 * ------------------------------------------------------------------- */
function (doc, oldDoc) {
  if (doc._deleted) {
    requireRole("admin");
    return;
  }
  requireRole(["admin", "manager"]);

  if (!doc.storeId) throw({ forbidden: "store document missing storeId" });
  if (!doc.name) throw({ forbidden: "store document missing name" });

  channel("store-" + doc.storeId);
}

/* ---------------------------------------------------------------------
 * catalog.menu_items
 * Menu/recipe catalog - manager-managed, readable storewide and by the
 * public menu displayed in the mobile app.
 * ------------------------------------------------------------------- */
function (doc, oldDoc) {
  if (doc._deleted) {
    requireRole("manager");
    return;
  }
  requireRole("manager");

  if (!doc.itemId) throw({ forbidden: "menu_item missing itemId" });
  if (!doc.name) throw({ forbidden: "menu_item missing name" });
  if (!doc.station) throw({ forbidden: "menu_item missing station" });

  channel("public-menu");
}

/* ---------------------------------------------------------------------
 * catalog.modifiers
 * Milk/syrup/topping/size pricing catalog - same access shape as
 * menu_items, kept as its own collection since it changes independently
 * (e.g. seasonal syrup pricing) from the menu itself.
 * ------------------------------------------------------------------- */
function (doc, oldDoc) {
  if (doc._deleted) {
    requireRole("manager");
    return;
  }
  requireRole("manager");

  if (!doc.modifierId) throw({ forbidden: "modifier missing modifierId" });
  if (!doc.modifierType) throw({ forbidden: "modifier missing modifierType" });

  channel("public-menu");
}

/* ---------------------------------------------------------------------
 * people.employees
 * Staff roster + station assignment. Only managers/admins write it;
 * each partner's own record (plus their store's roster) is readable by
 * store staff so a KDS login screen can show "who's signed in".
 * ------------------------------------------------------------------- */
function (doc, oldDoc) {
  if (doc._deleted) {
    requireRole("manager");
    return;
  }
  requireRole(["admin", "manager"]);

  if (!doc.employeeId) throw({ forbidden: "employee missing employeeId" });
  if (!doc.storeId) throw({ forbidden: "employee missing storeId" });
  if (!doc.role) throw({ forbidden: "employee missing role" });

  channel("store-" + doc.storeId + "-staff");
  if (doc.assignedStation === "ESPRESSO_BAR") {
    channel("store-" + doc.storeId + "-espresso");
  } else if (doc.assignedStation === "WARMING_STATION") {
    channel("store-" + doc.storeId + "-warming");
  }
}

/* ---------------------------------------------------------------------
 * people.customers
 * Loyalty profile - each customer can only sync their own document; the
 * app server (writing loyalty/points updates after an order) can write
 * any customer via the "app_server" role.
 * ------------------------------------------------------------------- */
function (doc, oldDoc) {
  if (doc._deleted) {
    requireRole(["admin", "app_server"]);
    return;
  }
  if (!doc.customerId) throw({ forbidden: "customer missing customerId" });

  requireAccess(["user-" + doc.customerId, "role:app_server"]);
  channel("user-" + doc.customerId);
}

/* ---------------------------------------------------------------------
 * operations.orders
 * The core, highest-write-volume collection. Routes each order into the
 * store-wide channel plus the specific station channel(s) its line
 * items touch, and into the placing customer's personal channel.
 * ------------------------------------------------------------------- */
function (doc, oldDoc) {
  if (doc._deleted) {
    requireRole("manager");
    return;
  }

  if (!doc.storeId) throw({ forbidden: "order missing storeId" });
  if (!doc.orderStatus) throw({ forbidden: "order missing orderStatus" });
  if (!doc.items || doc.items.length === 0) throw({ forbidden: "order missing items" });

  // POS registers, KDS stations, and the app server itself may all write
  // orders (register creates it, KDS stations flip line-item status).
  requireAccess(["store-" + doc.storeId, "role:app_server"]);

  channel("store-" + doc.storeId);

  var hasEspressoItems = false;
  var hasWarmingItems = false;
  for (var i = 0; i < doc.items.length; i++) {
    if (doc.items[i].station === "ESPRESSO_BAR") hasEspressoItems = true;
    if (doc.items[i].station === "WARMING_STATION") hasWarmingItems = true;
  }
  if (hasEspressoItems) channel("store-" + doc.storeId + "-espresso");
  if (hasWarmingItems) channel("store-" + doc.storeId + "-warming");

  if (doc.customerId) {
    channel("user-" + doc.customerId);
  }
}

/* ---------------------------------------------------------------------
 * operations.order_status_events
 * Append-only audit trail. Never updated after creation (enforced
 * below), routed to the same store/station channels as the order it
 * belongs to so a KDS screen's "ticket history" can subscribe to it too.
 * ------------------------------------------------------------------- */
function (doc, oldDoc) {
  if (doc._deleted) {
    requireRole("manager");
    return;
  }
  if (oldDoc) {
    throw({ forbidden: "order_status_event documents are append-only" });
  }

  if (!doc.orderId) throw({ forbidden: "event missing orderId" });
  if (!doc.storeId) throw({ forbidden: "event missing storeId" });
  if (!doc.toStatus) throw({ forbidden: "event missing toStatus" });

  requireAccess(["store-" + doc.storeId, "role:app_server"]);

  channel("store-" + doc.storeId);
  if (doc.station === "ESPRESSO_BAR") channel("store-" + doc.storeId + "-espresso");
  if (doc.station === "WARMING_STATION") channel("store-" + doc.storeId + "-warming");
}

/* ---------------------------------------------------------------------
 * inventory.stock_levels
 * Per-store ingredient stock. Written by the app server on order
 * creation (decrement) and by managers on restock; readable storewide
 * so a manager dashboard can show low-stock warnings.
 * ------------------------------------------------------------------- */
function (doc, oldDoc) {
  if (doc._deleted) {
    requireRole("manager");
    return;
  }
  if (!doc.storeId) throw({ forbidden: "stock_level missing storeId" });
  if (!doc.ingredientId) throw({ forbidden: "stock_level missing ingredientId" });

  requireAccess(["store-" + doc.storeId, "role:app_server"]);
  channel("store-" + doc.storeId);
}
