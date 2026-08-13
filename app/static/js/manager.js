// Manager dashboard: today's orders + a few rollup stats for the store.
// Read-only (never writes), so it only pulls `store-{storeId}` - the broad
// store-wide channel every order/event/stock doc for that store lands in.
import { COLLECTIONS, openDatabase } from "./cbl/client.js";
import { startSync, stopAll } from "./cbl/replication.js";
import { liveQuery, runQuery } from "./cbl/query.js";

let db;
let replicators = [];
let ordersToken = null;
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
  if (ordersToken) ordersToken.remove();

  const started = await startSync("Manager Dashboard", {
    [COLLECTIONS.stores]: { pull: true },
    [COLLECTIONS.employees]: { pull: true },
    [COLLECTIONS.orders]: { pull: true, channels: [`store-${storeId}`] },
    [COLLECTIONS.orderEvents]: { pull: true, channels: [`store-${storeId}`] },
    [COLLECTIONS.stockLevels]: { pull: true, channels: [`store-${storeId}`] },
  });
  replicators = started.replicators;

  const sql = `
    SELECT o.* FROM \`${COLLECTIONS.orders}\` AS o
    WHERE o.storeId = $storeId
    ORDER BY o.createdAt DESC
    LIMIT 100
  `;
  const { token } = await liveQuery(db, sql, { storeId }, (orders) => {
    renderStats(orders);
    renderTable(orders);
  });
  ordersToken = token;
}

function renderStats(orders) {
  const active = orders.filter((o) => !["COMPLETED", "CANCELLED"].includes(o.orderStatus));
  const completed = orders.filter((o) => o.orderStatus === "COMPLETED");
  const revenue = completed.reduce((sum, o) => sum + o.total, 0);
  const avg = completed.length ? revenue / completed.length : 0;

  const tiles = [
    ["Orders (loaded)", orders.length],
    ["Active", active.length],
    ["Completed", completed.length],
    ["Revenue (completed)", money(revenue)],
    ["Avg Order Value", money(avg)],
  ];
  document.getElementById("stats").innerHTML = tiles
    .map(([label, value]) => `<div class="stat-tile"><div class="value">${value}</div><div class="label">${label}</div></div>`)
    .join("");
}

// Display label for each orderStatus - only IN_PREPARATION reads
// differently than its raw enum value ("PREPARING" instead of
// "IN PREPARATION"); everything else just swaps underscores for spaces.
const STATUS_LABELS = { IN_PREPARATION: "PREPARING" };
function statusLabel(status) {
  return STATUS_LABELS[status] || status.replace("_", " ");
}

function renderTable(orders) {
  document.getElementById("ordersBody").innerHTML = orders
    .map(
      (o) => `
      <tr>
        <td>${o.orderName || o.orderId}<i class="info-icon" title="${o.orderId}">i</i></td>
        <td><span class="badge ${o.orderStatus}">${statusLabel(o.orderStatus)}</span></td>
        <td>${o.items.map((i) => `${i.quantity}x ${i.name}`).join(", ")}</td>
        <td>${money(o.total)}</td>
        <td>${o.employeeName || "Mobile / Self"}</td>
        <td>${o.channelOrigin}</td>
        <td>${timeAgo(o.createdAt)}</td>
      </tr>`
    )
    .join("");
}

init();
