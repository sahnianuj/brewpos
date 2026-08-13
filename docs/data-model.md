# Data Model

Bucket: **`starbucks`** — 4 scopes, 8 collections. Scopes group documents
by lifecycle/ownership (who writes them, how often, who needs to sync
them), which is also how the App Services channel/role design in
`sync-gateway/` is organized.

```
starbucks
├── catalog                    (managed by store managers, low write volume)
│   ├── stores
│   ├── menu_items
│   └── modifiers
├── people                     (staff + loyalty profiles)
│   ├── employees
│   └── customers
├── operations                 (the hot path - every order touches this)
│   ├── orders
│   └── order_status_events
└── inventory                  (per-store stock, decremented on order)
    └── stock_levels
```

## Entity-relationship overview

```
   store (1) ────────< orders (N)                store (1) ──< employees (N)
     │                    │  │                     │
     │                    │  └───< order_status_events (N)
     │                    │
     │                    └──── customer (N:1, optional - guest orders have none)
     │
     └──────────────────────────────────────< stock_levels (N)

   menu_item (1) ───────< order.items[] (N)      menu_item.recipe.ingredients[]
                                                        │  (ingredientId)
   modifier (1) ────< order.items[].customizations[]   │
        (matched by type+value,                        ▼
         extraCharge snapshotted onto the line)   stock_levels (matched by ingredientId, storeId)
```

Two relationship styles are deliberately mixed, which is worth calling out
in a demo:

- **Reference + snapshot** (`orders.items[].itemId` → `menu_items`,
  `orders.items[].customizations[].value` → `modifiers`): the order keeps
  the foreign key *and* copies the name/price at order time, so a later
  menu price change never rewrites history.
- **Live lookup** (`stock_levels` decremented from `menu_items.recipe`
  at order time): inventory is mutated, not snapshotted, because it's
  meant to reflect current reality.

## Collections

### `catalog.stores` — key `store::{storeId}`
```json
{
  "type": "store",
  "storeId": "10492",
  "name": "Market St & 5th Ave",
  "address": { "line1": "845 Market St", "city": "San Francisco", "state": "CA", "postalCode": "94103", "country": "US" },
  "timeZone": "America/Los_Angeles",
  "isActive": true,
  "stations": ["ESPRESSO_BAR", "WARMING_STATION"],
  "channels": ["store-10492"]
}
```

### `catalog.menu_items` — key `menu_item::{itemId}`
```json
{
  "type": "menu_item",
  "itemId": "DRINK_001",
  "name": "Caffe Latte",
  "category": "BEVERAGE",
  "station": "ESPRESSO_BAR",
  "availableSizes": ["TALL", "GRANDE", "VENTI"],
  "sizePricing": { "TALL": 4.45, "GRANDE": 4.95, "VENTI": 5.45 },
  "recipe": {
    "baseEspressoShots": { "TALL": 1, "GRANDE": 2, "VENTI": 2 },
    "ingredients": [
      { "ingredientId": "ESPRESSO_BEANS", "unit": "SHOT", "qtyBySize": { "TALL": 1, "GRANDE": 2, "VENTI": 2 } },
      { "ingredientId": "WHOLE_MILK", "unit": "OZ", "qtyBySize": { "TALL": 6, "GRANDE": 8, "VENTI": 10 } }
    ]
  },
  "allowedCustomizations": ["MILK_TYPE", "SYRUP", "ESPRESSO_SHOTS", "TOPPING"],
  "imageUrl": "https://placehold.co/640x640/00704A/FFFFFF?text=Caffe+Latte&font=roboto",
  "imageSource": "placeholder",
  "isActive": true
}
```
`recipe.ingredients[].ingredientId` is the link into `inventory.stock_levels`.
`imageSource` is currently always `placeholder` (a generated,
brand-neutral card) — see README for why. The field also accepts
`wikimedia_commons` if you swap in verified real photos later.

### `catalog.modifiers` — key `modifier::{modifierId}`
```json
{
  "type": "modifier",
  "modifierId": "MILK_OAT",
  "modifierType": "MILK_TYPE",
  "label": "Oat Milk",
  "value": "OAT_MILK",
  "extraCharge": 0.8,
  "applicableCategories": ["BEVERAGE"]
}
```

### `people.employees` — key `employee::{employeeId}`
```json
{
  "type": "employee",
  "employeeId": "barista_442",
  "displayName": "Casey Kim",
  "storeId": "10492",
  "role": "BARISTA",
  "assignedStation": "ESPRESSO_BAR",
  "isActive": true,
  "channels": ["store-10492-espresso", "store-10492-staff"]
}
```

### `people.customers` — key `customer::{customerId}`
```json
{
  "type": "customer",
  "customerId": "cust_99214",
  "displayName": "Jordan Lee",
  "homeStoreId": "10492",
  "loyalty": { "tier": "GOLD", "starsBalance": 312, "starsToNextReward": 13 },
  "paymentMethods": [{ "type": "STARBUCKS_CARD", "cardId": "SC-4471-8823", "balance": 42.1, "isDefault": true }],
  "channels": ["user-cust_99214"]
}
```

### `operations.orders` — key `order::{storeId}::{yyyyMMdd}::{seq}`
```json
{
  "type": "order",
  "orderId": "ORD-10492-8801",
  "storeId": "10492",
  "customerId": "cust_99214",
  "employeeId": "cashier_128",
  "channelOrigin": "POS_REGISTER_1",
  "orderStatus": "PAID",
  "payment": { "status": "COMPLETED", "method": "STARBUCKS_CARD", "amountPaid": 10.97, "transactionId": "TXN-9981241" },
  "items": [
    {
      "lineItemId": "line_1",
      "itemId": "DRINK_001",
      "name": "Caffe Latte",
      "size": "GRANDE",
      "station": "ESPRESSO_BAR",
      "status": "QUEUED",
      "quantity": 1,
      "customizations": [{ "type": "MILK_TYPE", "value": "OAT_MILK", "label": "Oat Milk", "extraCharge": 0.8 }],
      "unitPrice": 6.65,
      "lineTotal": 6.65
    }
  ],
  "subtotal": 10.1, "tax": 0.87, "total": 10.97,
  "createdAt": "2026-08-12T08:35:00Z", "readyAt": null, "completedAt": null,
  "channels": ["store-10492", "store-10492-espresso", "store-10492-warming", "user-cust_99214"]
}
```
`orderStatus`: `PAID → IN_PREPARATION → READY → COMPLETED` (or
`CANCELLED` at any point before `COMPLETED`). Each `items[].status`:
`QUEUED → IN_PREPARATION → READY` independently per station - the order
only advances once every line item has.

### `operations.order_status_events` — key `event::{orderId}::{seq}`
```json
{
  "type": "order_status_event",
  "eventId": "evt_88010001",
  "orderId": "ORD-10492-8801",
  "storeId": "10492",
  "scope": "ORDER",
  "lineItemId": null,
  "fromStatus": "NEW", "toStatus": "PAID",
  "station": null,
  "actorId": "cashier_128", "actorRole": "CASHIER",
  "timestamp": "2026-08-12T08:35:00Z"
}
```
Append-only audit trail (`scope` is `ORDER` or `LINE_ITEM`). This is the
collection a KDS ticket's history or a "where's my order" screen would
tail.

### `inventory.stock_levels` — key `stock::{storeId}::{ingredientId}`
```json
{
  "type": "stock_level",
  "storeId": "10492",
  "ingredientId": "ESPRESSO_BEANS",
  "name": "Espresso Roast Beans",
  "unit": "SHOT",
  "quantityOnHand": 842,
  "reorderThreshold": 150,
  "lastRestockedAt": "2026-08-11T05:00:00Z"
}
```

## Indexes

`scripts/setup_capella.py` creates a primary index on every collection
(fine at demo scale, called out there as not a production pattern) plus
these targeted secondary indexes:

| Index | Collection | Keys | Backs |
|---|---|---|---|
| `idx_orders_store_status` | `operations.orders` | `storeId, orderStatus, createdAt` | KDS queues, pickup board |
| `idx_orders_orderId` | `operations.orders` | `orderId` | Order lookup by orderId |
| `idx_orders_customer` | `operations.orders` | `customerId` | Customer order history |
| `idx_events_orderId` | `operations.order_status_events` | `orderId, timestamp` | Order audit trail |
| `idx_employees_store` | `people.employees` | `storeId` | Staff roster / KDS login |
| `idx_stock_store` | `inventory.stock_levels` | `storeId` | Manager stock view |

The KDS queue query is the one worth reading end to end
(`app/repositories/order_repo.py::list_station_queue`, admin-tooling only
now) - it's a straight `UNNEST` over `orders.items`:

```sql
SELECT o.orderId, o.storeId, o.customerId, o.createdAt, i.*
FROM `starbucks`.`operations`.`orders` AS o
UNNEST o.items AS i
WHERE o.storeId = $storeId
  AND i.station = $station
  AND i.status IN ["QUEUED", "IN_PREPARATION"]
  AND o.orderStatus IN ["PAID", "IN_PREPARATION"]
ORDER BY o.createdAt
```

The live app runs the local equivalent of this same query inside the
browser - see below.

## Local queries in the browser (Couchbase Lite for JavaScript)

The live app (`app/static/js/cbl/`) never runs the server-side query
above. Instead, each browser tab keeps its own local copy of whatever
collections/channels it's synced (via a `Replicator`), and queries that
local copy with the same N1QL/SQL++ dialect - just addressed differently,
since there's no bucket locally, only collections:

- No `` `starbucks`. `` bucket prefix.
- The scope and collection are combined into one backtick-quoted,
  dotted identifier: `` `operations.orders` `` instead of
  `` `starbucks`.`operations`.`orders` `` - see `COLLECTIONS` in
  `app/static/js/cbl/client.js` for the full list of 8.

The Espresso/Warming KDS screens' live query (`app/static/js/kds.js`) is
the direct local analog of the query above:

```sql
SELECT META(o).id AS orderKey, o.orderId, o.storeId, o.customerId, o.createdAt, i.*
FROM `operations.orders` AS o
UNNEST o.items AS i
WHERE o.storeId = $storeId
  AND i.station = $station
  AND i.status IN ["QUEUED", "IN_PREPARATION"]
  AND o.orderStatus IN ["PAID", "IN_PREPARATION"]
ORDER BY o.createdAt
```

Registered via `Query.addChangeListener(...)` (see
`app/static/js/cbl/query.js::liveQuery`) rather than `.execute()`, so the
callback re-fires automatically whenever synced data changes - this is
what replaced polling entirely.

**Note on `scope.collection` naming**: Couchbase Lite for JavaScript is a
very new SDK (GA'd Nov 2025); this dotted-name convention is confirmed
against its published `.d.ts` type declarations and official docs as of
writing, but hasn't been exercised against a live App Services endpoint in
this repo yet - see the README's Troubleshooting section.

## Client-generated document ids (browser writes only)

The Server SDK admin path (`order_repo.py::next_sequence`) generates
order/event ids from a centralized atomic KV counter
(`binary().increment()`) - fine for a single seeding script, but wrong for
an offline-first app with many potential concurrent writers (multiple
registers, KDS stations): two offline registers could hand out the same
sequence number.

`app/static/js/cbl/ids.js` uses a different, deliberately incompatible
scheme for anything created in the browser: a sortable timestamp plus a
short random suffix, e.g. `order::10492::20260812T193045123::a1b2` /
`ORD-10492-20260812T193045123-a1b2`. Both schemes coexist in the same
`operations.orders` collection without conflict - they're just two valid
ways to generate a unique key, chosen per write path based on its
concurrency model.
