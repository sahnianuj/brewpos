// Order lifecycle logic - the JS equivalent of app/services/order_service.py.
//
// Deliberately pure (no CBL/Collection calls in here): every function takes
// plain data in and returns plain data out (documents + keys to save,
// stock deltas to apply). This makes it independently testable with plain
// Node (see docs/data-model.md), and keeps the actual collection.save()
// calls - the only part that differs page to page - in pos.js/kds.js.
import { newEventIds, newOrderIds } from "./ids.js";

// Real coffee shop chains don't all charge the same sales tax; this is enough
// variation to make the point without modeling full tax jurisdictions.
export const STORE_TAX_RATES = { "10492": 0.0863, "20873": 0.1025 };
export const DEFAULT_TAX_RATE = 0.0875;

const LINE_ITEM_TRANSITIONS = {
  QUEUED: new Set(["IN_PREPARATION"]),
  IN_PREPARATION: new Set(["READY"]),
};

function nowIso() {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

function modifierKey(type, value) {
  return `${type}::${value}`;
}

/** Builds a lookup map from the modifiers collection's documents, keyed the
 *  way createOrder expects it: `${modifierType}::${value}`. */
export function buildModifierIndex(modifierDocs) {
  const index = new Map();
  for (const m of modifierDocs) index.set(modifierKey(m.modifierType, m.value), m);
  return index;
}

/** Builds an order_status_event document + its storage key.
 *  Returns { key, event } - `key` is NOT part of the document body, same
 *  convention as order documents (see createOrder). */
export function buildEvent(orderId, storeId, scope, fromStatus, toStatus, opts = {}) {
  const { key, eventId } = newEventIds(orderId);
  const event = {
    type: "order_status_event",
    eventId,
    orderId,
    storeId,
    scope,
    lineItemId: opts.lineItemId ?? null,
    fromStatus,
    toStatus,
    station: opts.station ?? null,
    actorId: opts.actorId ?? null,
    actorName: opts.actorName ?? null,
    actorRole: opts.actorRole ?? "SYSTEM",
    timestamp: nowIso(),
  };
  if (opts.note) event.note = opts.note;
  return { key, event };
}

/**
 * Pure order-creation logic - equivalent of order_service.py::create_order.
 * Given the request and the catalog already loaded from local collections,
 * returns everything the caller needs to persist: the order document + its
 * key, the initial PAID event (+ its key), and which ingredient quantities
 * to decrement from stock_levels.
 *
 * @throws Error with a user-facing message on any validation failure.
 */
export function createOrder({
  storeId,
  customerId,
  orderName,
  employeeId,
  employeeName,
  channelOrigin,
  paymentMethod,
  items,
  menuItemsById,
  modifierIndex,
}) {
  const lineItems = [];
  const stations = new Set();
  const stockDecrements = [];
  let subtotal = 0;

  items.forEach((itemIn, idx) => {
    const menuItem = menuItemsById.get(itemIn.itemId);
    if (!menuItem) throw new Error(`Unknown menu item ${itemIn.itemId}`);
    if (!menuItem.availableSizes.includes(itemIn.size)) {
      throw new Error(`${menuItem.name} is not available in size ${itemIn.size}`);
    }

    let unitPrice = menuItem.sizePricing[itemIn.size];
    const customizations = [];
    for (const c of itemIn.customizations || []) {
      const mod = modifierIndex.get(modifierKey(c.type, c.value));
      if (!mod) throw new Error(`Unknown customization ${c.type}=${c.value}`);
      unitPrice += mod.extraCharge;
      customizations.push({ type: c.type, value: c.value, label: mod.label, extraCharge: mod.extraCharge });
    }
    unitPrice = round2(unitPrice);
    const quantity = itemIn.quantity || 1;
    const lineTotal = round2(unitPrice * quantity);
    subtotal += lineTotal;
    stations.add(menuItem.station);

    lineItems.push({
      lineItemId: `line_${idx + 1}`,
      itemId: menuItem.itemId,
      name: menuItem.name,
      size: itemIn.size,
      category: menuItem.category,
      station: menuItem.station,
      status: "QUEUED",
      quantity,
      customizations,
      unitPrice,
      lineTotal,
    });

    for (const ingredient of menuItem.recipe?.ingredients || []) {
      const qty = ingredient.qtyBySize?.[itemIn.size];
      if (qty) {
        stockDecrements.push({ storeId, ingredientId: ingredient.ingredientId, amount: qty * quantity });
      }
    }
  });

  subtotal = round2(subtotal);
  const taxRate = STORE_TAX_RATES[storeId] ?? DEFAULT_TAX_RATE;
  const tax = round2(subtotal * taxRate);
  const total = round2(subtotal + tax);

  const { key, orderId } = newOrderIds(storeId);
  const timestamp = nowIso();

  const channels = [`store-${storeId}`];
  if (stations.has("ESPRESSO_BAR")) channels.push(`store-${storeId}-espresso`);
  if (stations.has("WARMING_STATION")) channels.push(`store-${storeId}-warming`);
  if (customerId) channels.push(`user-${customerId}`);

  const order = {
    type: "order",
    orderId,
    // The name a barista would actually call out / write on a cup - a
    // loyalty customer's display name, or the initials a guest gave at
    // Register. `orderId` stays around as the internal collision-safe key
    // (see ids.js), but it's never meant to be shown to a human - this is.
    orderName: orderName || null,
    storeId,
    customerId: customerId || null,
    employeeId: employeeId || null,
    employeeName: employeeName || null,
    channelOrigin,
    orderStatus: "PAID",
    payment: {
      status: "COMPLETED",
      method: paymentMethod,
      amountPaid: total,
      transactionId: `TXN-${orderId.replace("ORD-", "")}`,
    },
    items: lineItems,
    subtotal,
    tax,
    total,
    createdAt: timestamp,
    updatedAt: timestamp,
    readyAt: null,
    completedAt: null,
    cancelReason: null,
    channels,
  };

  const paidEvent = buildEvent(orderId, storeId, "ORDER", "NEW", "PAID", {
    actorId: employeeId,
    actorName: employeeName,
    actorRole: employeeId ? "CASHIER" : "CUSTOMER_APP",
  });

  return { key, order, events: [paidEvent], stockDecrements };
}

/** Advances one line item's status, cascading the order-level status per
 *  the same rules as order_service.py::advance_line_item. Mutates and
 *  returns `order` in place; returns the events (+keys) to save alongside
 *  it. `actor` is `{id, name}` of the barista/warming partner, if known. */
export function advanceLineItem(order, lineItemId, newStatus, actor = {}) {
  if (order.orderStatus === "COMPLETED" || order.orderStatus === "CANCELLED") {
    throw new Error(`Order ${order.orderId} is already ${order.orderStatus}`);
  }
  const item = order.items.find((i) => i.lineItemId === lineItemId);
  if (!item) throw new Error(`Unknown line item ${lineItemId} on order ${order.orderId}`);

  const allowed = LINE_ITEM_TRANSITIONS[item.status];
  if (!allowed || !allowed.has(newStatus)) {
    throw new Error(`Cannot move line item from ${item.status} to ${newStatus}`);
  }

  const events = [];
  const fromStatus = item.status;
  item.status = newStatus;
  order.updatedAt = nowIso();

  const actorRole = item.station === "ESPRESSO_BAR" ? "BARISTA" : "WARMING_PARTNER";
  events.push(
    buildEvent(order.orderId, order.storeId, "LINE_ITEM", fromStatus, newStatus, {
      lineItemId,
      station: item.station,
      actorId: actor.id,
      actorName: actor.name,
      actorRole,
    })
  );

  // First item starting prep bumps the whole order to IN_PREPARATION.
  if (order.orderStatus === "PAID" && newStatus === "IN_PREPARATION") {
    order.orderStatus = "IN_PREPARATION";
    events.push(
      buildEvent(order.orderId, order.storeId, "ORDER", "PAID", "IN_PREPARATION", {
        actorId: actor.id,
        actorName: actor.name,
      })
    );
  }

  // Last item reaching READY bumps the whole order to READY.
  if (order.items.every((i) => i.status === "READY") && order.orderStatus !== "READY") {
    order.orderStatus = "READY";
    order.readyAt = nowIso();
    events.push(
      buildEvent(order.orderId, order.storeId, "ORDER", "IN_PREPARATION", "READY", {
        actorId: actor.id,
        actorName: actor.name,
      })
    );
  }

  return { order, events };
}

/** Marks a READY order COMPLETED (picked up). */
export function completeOrder(order, actor = {}) {
  if (order.orderStatus !== "READY") {
    throw new Error(`Order ${order.orderId} is not READY for pickup (status=${order.orderStatus})`);
  }
  const timestamp = nowIso();
  const fromStatus = order.orderStatus;
  order.orderStatus = "COMPLETED";
  order.completedAt = timestamp;
  order.updatedAt = timestamp;

  const event = buildEvent(order.orderId, order.storeId, "ORDER", fromStatus, "COMPLETED", {
    actorId: actor.id,
    actorName: actor.name,
  });
  return { order, events: [event] };
}

/** Cancels an order that hasn't been completed yet. */
export function cancelOrder(order, reason, actor = {}) {
  if (order.orderStatus === "COMPLETED" || order.orderStatus === "CANCELLED") {
    throw new Error(`Order ${order.orderId} is already ${order.orderStatus}`);
  }
  const fromStatus = order.orderStatus;
  const cancelReason = reason || "Cancelled at register";
  order.orderStatus = "CANCELLED";
  order.cancelReason = cancelReason;
  order.updatedAt = nowIso();
  order.payment.status = "REFUNDED";

  const event = buildEvent(order.orderId, order.storeId, "ORDER", fromStatus, "CANCELLED", {
    actorId: actor.id,
    actorName: actor.name,
    actorRole: "MANAGER",
    note: cancelReason,
  });
  return { order, events: [event] };
}
