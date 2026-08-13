// Client-generated, collision-resistant document ids.
//
// The Python/admin path (app/services/order_service.py, used only by
// scripts/setup_capella.py & friends) generates order/event ids from a
// centralized atomic KV counter (binary().increment()) - that only works
// because there's exactly one writer, a seeding script.
//
// This browser app is the opposite: potentially many concurrent, even
// offline, writers (multiple registers, KDS stations). A centralized
// counter can't work there - two offline registers could hand out the same
// sequence number. So ids here are generated locally: a sortable timestamp
// plus a short random suffix for collision avoidance. This is the standard
// pattern for offline-first/edge systems, and deliberately different from
// the server-side admin path.

function randomSuffix(length = 4) {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  let out = "";
  for (const b of bytes) out += chars[b % chars.length];
  return out;
}

function compactTimestamp(date = new Date()) {
  // e.g. "20260812T193045123"
  return date.toISOString().replace(/[-:.]/g, "");
}

/** New order document key + human-facing orderId for a store.
 *  key:     order::10492::20260812T193045123::a1b2
 *  orderId: ORD-10492-20260812T193045123-a1b2 */
export function newOrderIds(storeId) {
  const ts = compactTimestamp();
  const suffix = randomSuffix();
  return {
    key: `order::${storeId}::${ts}::${suffix}`,
    orderId: `ORD-${storeId}-${ts}-${suffix}`,
  };
}

/** New order_status_event document key + eventId for a given order. */
export function newEventIds(orderId) {
  const ts = compactTimestamp();
  const suffix = randomSuffix();
  return {
    key: `event::${orderId}::${ts}${suffix}`,
    eventId: `evt_${ts}${suffix}`,
  };
}
