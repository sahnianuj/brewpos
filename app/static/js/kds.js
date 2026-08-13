// Kitchen Display Screen (shared by /kds/espresso and /kds/warming, see
// window.STATION). This is the clearest demo of channel-based sync: this
// page's Replicator pulls `operations.orders` / `operations.order_status_events`
// filtered to ONE channel (store-{storeId}-espresso or -warming), so it only
// ever receives documents meant for this station - never the whole store's
// order stream, and never the other station's tickets.
import { COLLECTIONS, DocID, openDatabase } from "./cbl/client.js";
import { startSync, stopAll } from "./cbl/replication.js";
import { liveQuery, runQuery } from "./cbl/query.js";
import { advanceLineItem } from "./cbl/orderLogic.js";
import { saveDoc, saveExisting } from "./cbl/writes.js";

const STATION_CHANNEL = window.STATION === "ESPRESSO_BAR" ? "espresso" : "warming";

let db;
let replicators = [];
let queueToken = null;
let currentStoreId = window.DEFAULT_STORE_ID;

// Display label for each line-item status - only IN_PREPARATION reads
// differently than its raw enum value ("PREPARING" instead of
// "IN PREPARATION"); everything else just swaps underscores for spaces.
const STATUS_LABELS = { IN_PREPARATION: "PREPARING" };
function statusLabel(status) {
  return STATUS_LABELS[status] || status.replace("_", " ");
}

async function init() {
  db = await openDatabase();
  await setStore(currentStoreId);

  const stores = await runQuery(db, `SELECT s.* FROM \`${COLLECTIONS.stores}\` AS s ORDER BY s.name`);
  const storeSelect = document.getElementById("storeSelect");
  storeSelect.innerHTML = stores
    .map((s) => `<option value="${s.storeId}">${s.storeId} - ${s.name}</option>`)
    .join("");
  storeSelect.value = currentStoreId;
  storeSelect.addEventListener("change", () => setStore(storeSelect.value));
}

async function setStore(storeId) {
  currentStoreId = storeId;
  const channel = `store-${storeId}-${STATION_CHANNEL}`;

  stopAll(replicators);
  if (queueToken) queueToken.remove();

  const started = await startSync(`${window.STATION} KDS`, {
    [COLLECTIONS.stores]: { pull: true },
    [COLLECTIONS.employees]: { pull: true },
    [COLLECTIONS.orders]: { push: true, pull: true, channels: [channel] },
    [COLLECTIONS.orderEvents]: { push: true, pull: true, channels: [channel] },
  });
  replicators = started.replicators;

  const employees = await runQuery(
    db,
    `SELECT e.* FROM \`${COLLECTIONS.employees}\` AS e WHERE e.storeId = $storeId`,
    { storeId }
  );
  const relevant = employees.filter((e) => e.assignedStation === window.STATION || e.role === "MANAGER");
  const actorSelect = document.getElementById("actorSelect");
  actorSelect.innerHTML = relevant.map((e) => `<option value="${e.employeeId}">${e.displayName}</option>`).join("");

  // No UNNEST here on purpose: CBL-JS's query engine parses a `$station`
  // bind parameter referenced through an UNNEST alias (`i.station`) fine,
  // but then rejects it at bind time with "not a parameter of this query"
  // (field-tested, not a guess - see git history/chat log for the actual
  // stack trace) - a real gap in that alias's parameter tracking, not
  // something fixable by rewording the SQL. So we query orders at the
  // top level (same shape every other page already uses successfully)
  // and do the per-line-item station/status filtering in plain JS below -
  // still fully live, since the underlying query still re-fires on any
  // matching order's mutation.
  const sql = `
    SELECT META(o).id AS orderKey, o.orderId, o.orderName, o.storeId, o.customerId, o.createdAt, o.items
    FROM \`${COLLECTIONS.orders}\` AS o
    WHERE o.storeId = $storeId
      AND o.orderStatus IN ["PAID", "IN_PREPARATION"]
    ORDER BY o.createdAt
  `;
  const { token } = await liveQuery(db, sql, { storeId }, (orders) => {
    const queue = [];
    for (const o of orders) {
      for (const item of o.items) {
        if (item.station === window.STATION && (item.status === "QUEUED" || item.status === "IN_PREPARATION")) {
          queue.push({
            orderKey: o.orderKey,
            orderId: o.orderId,
            orderName: o.orderName,
            storeId: o.storeId,
            customerId: o.customerId,
            createdAt: o.createdAt,
            ...item,
          });
        }
      }
    }
    render(queue);
  });
  queueToken = token;
}

function render(queue) {
  const el = document.getElementById("queue");
  if (queue.length === 0) {
    el.innerHTML = '<p class="empty-state">No pending items for this station. 🎉</p>';
    return;
  }

  el.innerHTML = queue
    .map((item) => {
      const ageMinutes = (Date.now() - new Date(item.createdAt)) / 60000;
      const custText = (item.customizations || []).map((c) => c.label).join(", ");
      const nextStatus = item.status === "QUEUED" ? "IN_PREPARATION" : "READY";
      const actionLabel = item.status === "QUEUED" ? "Start" : "Mark Ready";
      return `
      <div class="ticket ${ageMinutes > 4 ? "aging" : ""}">
        <div class="ticket-head">
          <span class="order-id">${item.orderName || item.orderId}<i class="info-icon" title="${item.orderId}">i</i></span>
          <span class="elapsed">${timeAgo(item.createdAt)}</span>
        </div>
        <div><strong>${item.quantity}x ${item.name}</strong> - ${item.size}</div>
        ${custText ? `<div class="customizations">${custText}</div>` : ""}
        <div style="margin-top:0.6rem; display:flex; align-items:center; gap:0.6rem">
          <span class="badge ${item.status}">${statusLabel(item.status)}</span>
          <button data-order-key="${item.orderKey}" data-line="${item.lineItemId}" data-status="${nextStatus}">
            ${actionLabel}
          </button>
        </div>
      </div>`;
    })
    .join("");

  el.querySelectorAll("button[data-order-key]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await advanceTicket(btn.dataset.orderKey, btn.dataset.line, btn.dataset.status);
        // No manual refresh() call needed - the live query above re-fires
        // automatically once this save lands locally.
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
      }
    })
  );
}

async function advanceTicket(orderKey, lineItemId, newStatus) {
  const ordersColl = db.collections[COLLECTIONS.orders];
  const eventsColl = db.collections[COLLECTIONS.orderEvents];

  const order = await ordersColl.getDocument(DocID(orderKey));
  if (!order) throw new Error("Order not found locally (still syncing?)");

  const actorId = document.getElementById("actorSelect").value || null;
  const actorName =
    actorId && document.getElementById("actorSelect").selectedOptions[0]
      ? document.getElementById("actorSelect").selectedOptions[0].textContent
      : null;

  const { events } = advanceLineItem(order, lineItemId, newStatus, { id: actorId, name: actorName });
  await saveExisting(ordersColl, order);
  for (const { key, event } of events) {
    await saveDoc(eventsColl, key, event);
  }
}

init();
