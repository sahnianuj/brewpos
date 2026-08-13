// Register screen: browse menu -> customize -> cart -> submit order.
//
// All data comes from the local Couchbase Lite database, kept live by a
// Replicator syncing with Capella App Services (see cbl/replication.js).
// Catalog collections (stores/menu/modifiers/employees/customers) pull
// unfiltered - they're public reference data. Orders/events/stock pull+push
// scoped to the selected store's channel, and are rescoped if the cashier
// switches stores (see setStore()).
import { COLLECTIONS, openDatabase } from "./cbl/client.js";
import { startSync, stopAll } from "./cbl/replication.js";
import { liveQuery, runQuery } from "./cbl/query.js";
import { saveDoc, decrementStock } from "./cbl/writes.js";
import { buildModifierIndex, createOrder } from "./cbl/orderLogic.js";

let db;
let replicators = [];
let menuItems = [];
let modifiersByType = {};
let modifierIndex = new Map();
let employeesById = new Map();
let customersById = new Map();
let cart = [];
let activeCategory = "BEVERAGE";
let currentStoreId = window.DEFAULT_STORE_ID;

// Menu cards show the item name as bold text on a colored swatch instead
// of a product photo - no images to source/license, and every item renders
// identically regardless of category. Same green-on-card look the original
// placeholder images used: beverages get the lighter green, food the
// darker green.
const SWATCH_COLORS = {
  BEVERAGE: "#00704A",
  FOOD: "#1E3932",
};

function swatchColor(item) {
  return SWATCH_COLORS[item.category] || SWATCH_COLORS.BEVERAGE;
}

function swatchHtml(item, sizeClass) {
  return `<div class="swatch ${sizeClass}" style="background:${swatchColor(item)}">${item.name}</div>`;
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

  const customers = await runQuery(db, `SELECT c.* FROM \`${COLLECTIONS.customers}\` AS c ORDER BY c.displayName`);
  customersById = new Map(customers.map((c) => [c.customerId, c]));
  const customerSelect = document.getElementById("customerSelect");
  customerSelect.innerHTML += customers
    .map((c) => `<option value="${c.customerId}">${c.displayName} (${c.loyalty.tier})</option>`)
    .join("");

  // A loyalty customer's own name is used automatically once picked - the
  // initials box is only for walk-up guests, same as a barista asking
  // "name for the order?" and writing it on the cup themselves. Guests must
  // give a name - we don't want orders going out anonymously as "Guest".
  const guestNameInput = document.getElementById("guestNameInput");
  const guestNameRequiredMark = document.getElementById("guestNameRequiredMark");
  function updateGuestNameRequirement() {
    const isGuest = !customerSelect.value;
    guestNameInput.disabled = !isGuest;
    guestNameInput.placeholder = isGuest ? "e.g. Sam" : "(using loyalty name)";
    guestNameRequiredMark.hidden = !isGuest;
    if (!isGuest) guestNameInput.value = "";
  }
  customerSelect.addEventListener("change", updateGuestNameRequirement);
  updateGuestNameRequirement();

  await liveQuery(
    db,
    `SELECT mo.* FROM \`${COLLECTIONS.modifiers}\` AS mo ORDER BY mo.modifierType, mo.label`,
    null,
    (rows) => {
      modifiersByType = {};
      for (const m of rows) (modifiersByType[m.modifierType] ||= []).push(m);
      modifierIndex = buildModifierIndex(rows);
    }
  );

  await liveQuery(
    db,
    `SELECT m.* FROM \`${COLLECTIONS.menuItems}\` AS m WHERE m.isActive = true ORDER BY m.category, m.name`,
    null,
    (rows) => {
      menuItems = rows;
      renderCategoryTabs();
      renderMenu();
    }
  );

  document.getElementById("submitOrderBtn").addEventListener("click", submitOrder);
}

/** Points the register at a store: reloads its staff list, and restarts
 *  the operations/inventory replication scoped to that store's channel -
 *  a live demo of a replicator being re-scoped, not just started once. */
async function setStore(storeId) {
  currentStoreId = storeId;

  stopAll(replicators);
  const started = await startSync("Register", {
    [COLLECTIONS.stores]: { pull: true },
    [COLLECTIONS.menuItems]: { pull: true },
    [COLLECTIONS.modifiers]: { pull: true },
    [COLLECTIONS.employees]: { pull: true },
    [COLLECTIONS.customers]: { pull: true },
    [COLLECTIONS.orders]: { push: true, pull: true, channels: [`store-${storeId}`] },
    [COLLECTIONS.orderEvents]: { push: true, pull: true, channels: [`store-${storeId}`] },
    [COLLECTIONS.stockLevels]: { push: true, pull: true, channels: [`store-${storeId}`] },
  });
  replicators = started.replicators;

  const employees = await runQuery(
    db,
    `SELECT e.* FROM \`${COLLECTIONS.employees}\` AS e WHERE e.storeId = $storeId`,
    { storeId }
  );
  employeesById = new Map(employees.map((e) => [e.employeeId, e]));
  const cashiers = employees.filter((e) => e.role === "CASHIER" || e.role === "MANAGER");
  const employeeSelect = document.getElementById("employeeSelect");
  employeeSelect.innerHTML = ['<option value="">(Mobile / self-checkout)</option>']
    .concat(cashiers.map((e) => `<option value="${e.employeeId}">${e.displayName} (${e.role})</option>`))
    .join("");
}

const CATEGORY_ORDER = ["BEVERAGE", "FOOD"];

function renderCategoryTabs() {
  const categories = [...new Set(menuItems.map((m) => m.category))].sort(
    (a, b) => CATEGORY_ORDER.indexOf(a) - CATEGORY_ORDER.indexOf(b)
  );
  const tabs = document.getElementById("categoryTabs");
  tabs.innerHTML = categories
    .map(
      (c) =>
        `<button class="${c === activeCategory ? "tab-active" : "secondary"}" data-cat="${c}" style="margin-right:0.5rem">${c === "BEVERAGE" ? "☕ Beverages" : "🥐 Food"}</button>`
    )
    .join("");
  tabs.querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      activeCategory = btn.dataset.cat;
      renderCategoryTabs();
      renderMenu();
    })
  );
}

function renderMenu() {
  const grid = document.getElementById("menuGrid");
  const items = menuItems.filter((m) => m.category === activeCategory);
  grid.innerHTML = items
    .map((m) => {
      const startPrice = Math.min(...Object.values(m.sizePricing));
      return `
      <div class="menu-item" data-item-id="${m.itemId}">
        ${swatchHtml(m, "grid")}
        <div class="body">
          <div class="name">${m.name}</div>
          <div class="price">From ${money(startPrice)}</div>
        </div>
      </div>`;
    })
    .join("");
  grid.querySelectorAll(".menu-item").forEach((el) =>
    el.addEventListener("click", () => openCustomizeModal(el.dataset.itemId))
  );
}

function openCustomizeModal(itemId) {
  const item = menuItems.find((m) => m.itemId === itemId);
  const root = document.getElementById("customizeModalRoot");

  const sizeOptions = item.availableSizes
    .map(
      (s, i) =>
        `<label class="opt-choice"><input type="radio" name="size" value="${s}" ${i === 0 ? "checked" : ""}> ${s} - ${money(item.sizePricing[s])}</label>`
    )
    .join("");

  const customizationGroups = (item.allowedCustomizations || [])
    .map((type) => {
      const options = modifiersByType[type] || [];
      const allowNone = type !== "MILK_TYPE";
      const choices = [
        allowNone
          ? `<label class="opt-choice"><input type="radio" name="mod_${type}" value="" checked> Standard (no charge)</label>`
          : "",
        ...options.map(
          (o, i) =>
            `<label class="opt-choice"><input type="radio" name="mod_${type}" value="${o.value}" ${!allowNone && i === 0 ? "checked" : ""}> ${o.label}${o.extraCharge ? " (+" + money(o.extraCharge) + ")" : ""}</label>`
        ),
      ].join("");
      return `<div class="opt-group"><div class="opt-label">${type.replace("_", " ")}</div>${choices}</div>`;
    })
    .join("");

  root.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal">
        <h3>${item.name}</h3>
        ${swatchHtml(item, "grid")}
        <div class="opt-group">
          <div class="opt-label">Size</div>
          ${sizeOptions}
        </div>
        ${customizationGroups}
        <div class="opt-group">
          <div class="opt-label">Quantity</div>
          <input type="number" id="qtyInput" value="1" min="1" max="10" style="width:4rem">
        </div>
        <div style="display:flex; gap:0.5rem; justify-content:flex-end; margin-top:1rem">
          <button class="secondary" id="modalCancel">Cancel</button>
          <button id="modalAdd">Add to Order</button>
        </div>
      </div>
    </div>`;

  document.getElementById("modalCancel").addEventListener("click", () => (root.innerHTML = ""));
  document.getElementById("modalAdd").addEventListener("click", () => {
    addToCart(item, root);
    root.innerHTML = "";
  });
}

function addToCart(item, root) {
  const size = root.querySelector('input[name="size"]:checked').value;
  const qty = parseInt(document.getElementById("qtyInput").value, 10) || 1;
  const customizations = [];
  let unitPrice = item.sizePricing[size];

  for (const type of item.allowedCustomizations || []) {
    const picked = root.querySelector(`input[name="mod_${type}"]:checked`);
    if (picked && picked.value) {
      const mod = (modifiersByType[type] || []).find((m) => m.value === picked.value);
      if (mod) {
        customizations.push({ type, value: mod.value, label: mod.label, extraCharge: mod.extraCharge });
        unitPrice += mod.extraCharge;
      }
    }
  }
  unitPrice = Math.round(unitPrice * 100) / 100;

  cart.push({
    itemId: item.itemId,
    name: item.name,
    size,
    quantity: qty,
    customizations,
    unitPrice,
    lineTotal: Math.round(unitPrice * qty * 100) / 100,
  });
  renderCart();
}

function renderCart() {
  const linesEl = document.getElementById("cartLines");
  const totalsEl = document.getElementById("cartTotals");
  const submitBtn = document.getElementById("submitOrderBtn");

  if (cart.length === 0) {
    linesEl.innerHTML = '<p class="muted">Cart is empty.</p>';
    totalsEl.innerHTML = "";
    submitBtn.disabled = true;
    return;
  }

  linesEl.innerHTML = cart
    .map((line, idx) => {
      const custText = line.customizations.map((c) => c.label).join(", ");
      return `
      <div class="cart-line">
        <div>
          <div>${line.quantity}x ${line.name} (${line.size})</div>
          ${custText ? `<div class="meta">${custText}</div>` : ""}
        </div>
        <div style="text-align:right">
          <div>${money(line.lineTotal)}</div>
          <button class="secondary" data-idx="${idx}" style="margin-top:0.25rem; font-size:0.75rem; padding:0.2rem 0.5rem">Remove</button>
        </div>
      </div>`;
    })
    .join("");

  linesEl.querySelectorAll("button[data-idx]").forEach((btn) =>
    btn.addEventListener("click", () => {
      cart.splice(parseInt(btn.dataset.idx, 10), 1);
      renderCart();
    })
  );

  const subtotal = cart.reduce((sum, l) => sum + l.lineTotal, 0);
  totalsEl.innerHTML = `
    <div class="cart-total-row grand"><span>Subtotal</span><span>${money(subtotal)}</span></div>
    <div class="muted" style="font-size:0.8rem">Tax + final total calculated at submit.</div>`;
  submitBtn.disabled = false;
}

async function submitOrder() {
  const storeId = document.getElementById("storeSelect").value;
  const customerId = document.getElementById("customerSelect").value || null;
  const employeeId = document.getElementById("employeeSelect").value || null;
  const employeeName = employeeId ? employeesById.get(employeeId)?.displayName ?? null : null;
  const guestNameInput = document.getElementById("guestNameInput");
  const resultEl = document.getElementById("orderResult");

  // Loyalty customer -> their own name, automatically. Walk-up guest -> a
  // name is required at Register - we don't want orders going out with
  // "Guest" as the name, never the raw orderId, which is an internal key
  // only.
  if (!customerId && !guestNameInput.value.trim()) {
    resultEl.innerHTML = `<div class="card" style="background:#fbdcd9">Enter a name for the order before submitting.</div>`;
    guestNameInput.focus();
    return;
  }
  const orderName = customerId
    ? customersById.get(customerId)?.displayName ?? "Loyalty Customer"
    : guestNameInput.value.trim();

  try {
    const menuItemsById = new Map(menuItems.map((m) => [m.itemId, m]));
    const { key, order, events, stockDecrements } = createOrder({
      storeId,
      customerId,
      orderName,
      employeeId,
      employeeName,
      channelOrigin: employeeId ? "POS_REGISTER_1" : "MOBILE_APP",
      paymentMethod: "STARBUCKS_CARD",
      items: cart.map((l) => ({
        itemId: l.itemId,
        size: l.size,
        quantity: l.quantity,
        customizations: l.customizations.map((c) => ({ type: c.type, value: c.value })),
      })),
      menuItemsById,
      modifierIndex,
    });

    const ordersColl = db.collections[COLLECTIONS.orders];
    const eventsColl = db.collections[COLLECTIONS.orderEvents];
    const stockColl = db.collections[COLLECTIONS.stockLevels];

    await saveDoc(ordersColl, key, order);
    for (const { key: eventKey, event } of events) {
      await saveDoc(eventsColl, eventKey, event);
    }
    for (const d of stockDecrements) {
      await decrementStock(stockColl, d.storeId, d.ingredientId, d.amount);
    }

    resultEl.innerHTML = `<div class="card" style="background:#d9f2e3">
      Order for <strong>${order.orderName}</strong> submitted - total ${money(order.total)}.
      <span class="muted" style="font-size:0.8rem">(${order.orderId})</span><br>
      <span class="muted">Watch the KDS screens and pickup board - they'll update live, no refresh needed.</span>
    </div>`;
    cart = [];
    renderCart();
    guestNameInput.value = "";
  } catch (e) {
    resultEl.innerHTML = `<div class="card" style="background:#fbdcd9">${e.message}</div>`;
    console.error("[pos] order submission failed", e);
  }
}

init();
