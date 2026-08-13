// Pickup board: shows every order that's fully READY across both stations,
// lets a partner mark it handed to the customer. Pulls the broad
// `store-{storeId}` channel (not a per-station one) since a ready order may
// have come from either station.
import { COLLECTIONS, DocID, openDatabase } from "./cbl/client.js";
import { startSync, stopAll } from "./cbl/replication.js";
import { liveQuery, runQuery } from "./cbl/query.js";
import { completeOrder } from "./cbl/orderLogic.js";
import { saveDoc, saveExisting } from "./cbl/writes.js";

let db;
let replicators = [];
let boardToken = null;
let currentStoreId = window.DEFAULT_STORE_ID;

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

  stopAll(replicators);
  if (boardToken) boardToken.remove();

  const started = await startSync("Pickup Board", {
    [COLLECTIONS.stores]: { pull: true },
    [COLLECTIONS.orders]: { push: true, pull: true, channels: [`store-${storeId}`] },
    [COLLECTIONS.orderEvents]: { push: true, pull: true, channels: [`store-${storeId}`] },
  });
  replicators = started.replicators;

  const sql = `
    SELECT META(o).id AS orderKey, o.*
    FROM \`${COLLECTIONS.orders}\` AS o
    WHERE o.storeId = $storeId AND o.orderStatus = "READY"
    ORDER BY o.readyAt
  `;
  const { token } = await liveQuery(db, sql, { storeId }, render);
  boardToken = token;
}

function render(orders) {
  const el = document.getElementById("board");
  if (orders.length === 0) {
    el.innerHTML = '<p class="empty-state">Nothing waiting for pickup right now.</p>';
    return;
  }
  el.innerHTML = orders
    .map((o) => {
      const itemsText = o.items.map((i) => `${i.quantity}x ${i.name}`).join(", ");
      return `
      <div class="ticket">
        <div class="ticket-head">
          <span class="order-id">${o.orderName || o.orderId}<i class="info-icon" title="${o.orderId}">i</i></span>
          <span class="elapsed">Ready ${timeAgo(o.readyAt)}</span>
        </div>
        <div>${itemsText}</div>
        <div class="muted" style="font-size:0.8rem; margin-top:0.2rem">${o.customerId ? "Loyalty order" : "Guest order"}</div>
        <button data-order-key="${o.orderKey}" style="width:100%; margin-top:0.75rem">Mark Picked Up</button>
      </div>`;
    })
    .join("");

  el.querySelectorAll("button[data-order-key]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await markPickedUp(btn.dataset.orderKey);
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
      }
    })
  );
}

async function markPickedUp(orderKey) {
  const ordersColl = db.collections[COLLECTIONS.orders];
  const eventsColl = db.collections[COLLECTIONS.orderEvents];

  const order = await ordersColl.getDocument(DocID(orderKey));
  if (!order) throw new Error("Order not found locally (still syncing?)");

  const { events } = completeOrder(order, {});
  await saveExisting(ordersColl, order);
  for (const { key, event } of events) {
    await saveDoc(eventsColl, key, event);
  }
}

init();
