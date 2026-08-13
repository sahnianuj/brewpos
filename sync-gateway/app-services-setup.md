# Wiring up Capella App Services (Sync Gateway)

The live app talks **only** to Capella App Services now - no Couchbase Server
SDK, no `/api/*` backend. Every page (`/pos`, `/kds/espresso`, `/kds/warming`,
`/pickup`, `/manager`) runs its own local **Couchbase Lite for JavaScript**
database in the browser (`app/static/js/cbl/`) and keeps it live with one or
more `Replicator`s pointed at App Services, scoped per page to specific
channels.

The Couchbase Server SDK is still used, but only by the one-time admin
scripts (`scripts/setup_capella.py`, `seed_data.py`, `generate_orders.py`,
`simulate_traffic.py`) to provision the bucket and load reference data - App
Services needs something to sync *from*. Run those first if you haven't -
see the main README's Setup section.

## The one thing that shapes everything below: 4 App Endpoints, not 1

**An App Endpoint is tied to exactly one scope.** This is confirmed
directly in Capella's own docs: creating an endpoint means picking "a
bucket and a scope" (singular), and "App Endpoints can share scopes but
cannot link to the same collections." There's no way to sync multiple
scopes through one endpoint/connection URL.

This demo's data model has **4 scopes** (`catalog`, `people`, `operations`,
`inventory`), so it needs **4 App Endpoints** - each with its own connection
URL, its own linked collections, its own sync functions attached, its own
CORS setting, and (see below) its own copy of the demo credential. They all
share the same host/port; only the path differs:

```
wss://<app-id>.apps.cloud.couchbase.com:4984/catalog
wss://<app-id>.apps.cloud.couchbase.com:4984/people
wss://<app-id>.apps.cloud.couchbase.com:4984/operations
wss://<app-id>.apps.cloud.couchbase.com:4984/inventory
```

`app/config.py` builds these 4 URLs itself from one `APP_SERVICES_BASE_URL`
- **name your 4 endpoints exactly `catalog`, `people`, `operations`,
`inventory`** (matching the scope names) so that works without extra config.

## 1. Prerequisites (skip if already done)

- Capella cluster with the `brew` bucket created, provisioned, and
  seeded (`scripts/setup_capella.py` + `seed_data.py`) - App Endpoints are
  created against existing scopes/collections, they don't create them.
- An App Services deployment attached to that cluster (**your cluster >
  App Services > Create App Services**, if you haven't already - sounds
  like you have this part done already).
- Browsers: Safari 17+, Chrome 142+, Firefox 144+ (Couchbase Lite for
  JavaScript's minimums). Avoid private/incognito windows for the demo -
  they can restrict IndexedDB and make sync unreliable.

## 2. Create the 4 App Endpoints

Repeat this whole procedure **4 times**, once per row of this table:

| Endpoint name | Scope | Collections to link |
|---|---|---|
| `catalog` | `catalog` | `stores`, `menu_items`, `modifiers` |
| `people` | `people` | `employees`, `customers` |
| `operations` | `operations` | `orders`, `order_status_events` |
| `inventory` | `inventory` | `stock_levels` |

**For each row:**

1. **App Services > your App Services deployment > App Endpoints > Create
   App Endpoint.**
2. **App Endpoint Name**: type the exact name from the table (`catalog`,
   `people`, `operations`, or `inventory`).
3. **Linked Bucket**: select `brew`.
4. **Scope**: select the matching scope from the table.
5. **Linked Collections**: in the table shown, link every collection listed
   for that row (all of them - don't leave any unlinked, or that
   collection just won't sync).
6. Click **Create App Endpoint**.
7. **Attach the sync functions** for the collections you just linked: still
   inside this App Endpoint, open **(the collection) > Access Control and
   Data Validation** for each collection you linked in step 5, paste in the
   matching function from `sync-functions.js` (it has 8 functions, one per
   collection across all 4 scopes - use the one whose comment header names
   this collection), and **click Save/confirm explicitly** - don't assume
   navigating to the next collection saves it for you.
   - **How to know it actually saved** (field-tested the hard way): if a
     document ends up in a channel with the *exact same name as its
     collection* (e.g. a `menu_items` document lands in a channel literally
     called `menu_items`, instead of `public-menu` per our sync function),
     that's Sync Gateway's zero-config default behavior for a collection
     with **no** custom sync function attached - meaning your paste didn't
     save. Go back and redo it. (This is diagnosable by inspecting a
     document's `_sync` extended attribute via the Server SDK - see
     Troubleshooting in the main README.)
8. **Enable the Import Filter** for each of those same collections - a
   *separate* setting from the sync function above, and easy to miss
   entirely since nothing breaks loudly without it:
   - Still inside this App Endpoint: **Settings > Import Filter > select
     the collection** (repeat per collection, same as step 7).
   - Check **Enable Import Filter**.
   - **⚠️ This box is NOT the same as step 7's Access Control function, and
     the two must never contain each other's content** (field-tested the
     hard way - pasting the wrong one into the wrong box breaks import
     completely, silently, with zero error shown anywhere):
     | | Access Control (step 7) | Import Filter (this step) |
     |---|---|---|
     | Signature | `function (doc, oldDoc) { ... }` | `function (doc) { ... }` - **no `oldDoc`** |
     | Must return | nothing (calls `channel()`/`throw()` as side effects) | `true` or `false` |
     | Valid calls | `channel()`, `requireRole()`, `requireAccess()`, `throw()` | **none of those exist here** - it's plain JS on `doc` only |
     | If you paste the wrong one in | you'd see an obvious error/rejected save | **fails silently** - Sync Gateway treats a filter that calls an undefined function (e.g. `requireRole`) as erroring, and its fail-safe response is to just not import the document. No banner, no console error, nothing - the document simply never gets a `_sync` xattr, forever, until you fix it. |
   - **Replace whatever default function is pre-filled** - Capella
     pre-populates a generic starter template that only imports documents
     where `doc.type == "mobile"`, which silently excludes every document
     this app actually uses (`"menu_item"`, `"order"`, `"store"`, etc. are
     never `"mobile"`). Clear it and use:
     ```js
     function (doc) {
       return true;
     }
     ```
     We don't need import-time filtering for this demo - channel-based
     access (via the sync functions in step 7) already controls who sees
     what.
   - Click **Save** before moving to the next collection - same
     lose-your-changes-if-you-don't warning as step 7.
   - **Why this matters**: import only applies to *future* mutations from
     the moment it's enabled - documents already written by `seed_data.py`
     before you enable this won't retroactively appear. After enabling
     Import Filter (with the corrected function) on all 8 collections,
     re-run `python scripts/seed_data.py` once more so those documents get
     re-written and picked up under the now-correct filter.
9. **Enable CORS** on this endpoint: still inside it, open **App Endpoint
   Configuration > Advanced > CORS**, check the enable box, and set - field
   tested against a real Capella deployment, not just the doc:
   - **Origin**: the exact origin you'll serve this app from, e.g.
     `http://localhost:8000` (no trailing slash, no wildcard - browsers
     require an exact match here for authenticated/synced requests).
   - **Login Origin**: **required**, not optional, even though it's easy to
     skip past - the Save button stays disabled until it's filled in. Set
     it to the *same* value as Origin (`http://localhost:8000`). This
     specifically governs the `POST .../_session` call the replicator
     makes to authenticate, so leaving it blank breaks sync even if Origin
     alone looks correctly configured.
   - **Custom HTTP Headers** (this is the literal field label in the UI -
     the doc calls it "Allowed Headers"/`Access-Control-Allow-Headers`,
     same thing): a single comma-separated text value, typed as one
     string - it's **not** a tag/chip field where you press Enter after
     each header (that's how the App User "Assign Channels" field works,
     but not this one). Type exactly:
     ```
     Authorization, Content-Type
     ```
     The field's own placeholder example shows `ContentType` with no
     hyphen - that's a typo in the placeholder, not the real header name;
     use `Content-Type` (with the hyphen), which is what browsers
     actually send and what the official doc's own example uses.
   - **Max Age**: `3600` is a reasonable default (how long the browser
     caches the CORS preflight); the field default of `5` also works fine.
   - Click Save. If Save stays disabled after entering everything above,
     it's almost always the Login Origin field being empty.
10. Click through to **deploy/activate** this endpoint if Capella shows it
    as pending/draft after creation - a newly-created endpoint doesn't
    start serving traffic until it's deployed.

Do this for all 4 rows before moving on - a missing endpoint means every
page that needs that scope will show a red sync-error banner.

## 3. Create the demo Database Access credential (App User) - on each endpoint

App Users are created **per App Endpoint**, not once for the whole App
Services deployment - so this is a 4-times repeat, once inside each
endpoint you just created, using the **same username and password every
time** (so the app can use one set of credentials in `.env`).

### Scripted (recommended)

```bash
# in .env: fill in APP_SERVICES_BASE_URL / APP_SERVICES_USERNAME / APP_SERVICES_PASSWORD first
# (APP_SERVICES_ADMIN_USERNAME/PASSWORD are optional - they default to the above)
python scripts/assign_app_service_channels.py --dry-run   # preview the 4 requests, no network calls
python scripts/assign_app_service_channels.py             # actually create/update the App User on all 4 endpoints
```

This does exactly what the manual walkthrough below describes - it just
computes the channel lists straight from `data/samples/*.json` (so it's
never out of sync with your actual seeded stores/customers/employees) and
sends the 4 `PUT .../_user/{name}` requests itself via the Admin REST API.
It authenticates using `APP_SERVICES_ADMIN_USERNAME`/`APP_SERVICES_ADMIN_PASSWORD`
from `.env` - a separate variable from the browser's sync credential (even
though, confirmed against a real Capella deployment, the Admin API accepts
the same App User either way). Leave the `_ADMIN_` variables blank and it
falls back to `APP_SERVICES_USERNAME`/`PASSWORD` automatically; only set
them if you create a distinct admin credential. The one extra requirement:
**your current IP must already be in this App Service's Settings > Allowed
IP list** - a separate allowlist from the Data API/replication one, and
from the Capella cluster's own. Without it the script fails with a clear
connection error telling you that's the likely cause.

If you'd rather do it by hand (or the script hits something unexpected),
the exact same channel assignments, with the reasoning per collection,
are below.

### Manual (or to understand what the script is doing)

**The channels aren't one-per-endpoint - they're one-per-collection**, because
each collection's sync function (`sync-functions.js`) calls `channel(...)`
on its own, independently of the others sharing that endpoint. A channel
you grant on the App User is what makes documents *from a specific
collection's channel assignment* visible - so the tables below show it at
the collection level first, with the channel(s) that collection's function
actually assigns, and then the flat list to type into the UI is just the
union of that endpoint's rows. If you ever add a 9th collection or change
a sync function's `channel(...)` calls, this is the table to update.

Values below assume the seeded sample data as-is (2 stores, 3 customers) -
see the note after each table if you've changed it.

#### Endpoint `catalog`

| Collection | Sync function assigns... | Because |
|---|---|---|
| `stores` | `store-{storeId}` | one channel per store document |
| `menu_items` | `public-menu` | same channel for every item - not store-specific |
| `modifiers` | `public-menu` | same channel for every modifier - not store-specific |

→ **Assign Channels** on this endpoint's App User: `public-menu`,
`store-10492`, `store-20873` (one `store-{id}` per seeded store, from
`stores.json`).

#### Endpoint `people`

| Collection | Sync function assigns... | Because |
|---|---|---|
| `employees` | `store-{storeId}-staff`, plus `store-{storeId}-espresso` or `store-{storeId}-warming` if that employee is assigned to a station | staff roster is per-store; station-assigned staff also land in their station's channel |
| `customers` | `user-{customerId}` | each customer's profile is its own private channel |

→ **Assign Channels**: `store-10492-staff`, `store-20873-staff`, plus
`store-10492-espresso`, `store-10492-warming`, `store-20873-espresso`,
`store-20873-warming` (covers every seeded employee's station channel -
see `employees.json`), plus one `user-{customerId}` per seeded customer:
`user-cust_99214`, `user-cust_55821`, `user-cust_10045` (from
`customers.json`).

#### Endpoint `operations`

| Collection | Sync function assigns... | Because |
|---|---|---|
| `orders` | `store-{storeId}` always; `store-{storeId}-espresso` if any line item is an espresso-bar item; `store-{storeId}-warming` if any line item is a warming-station item; `user-{customerId}` if the order has a customer | one order document fans out to every screen that needs to see it |
| `order_status_events` | same pattern as `orders`, but keyed off the event's own `station` field instead of scanning line items | mirrors the order it belongs to, so a KDS ticket's history syncs alongside the ticket |

→ **Assign Channels**: `store-10492`, `store-10492-espresso`,
`store-10492-warming`, `store-20873`, `store-20873-espresso`,
`store-20873-warming`, `user-cust_99214`, `user-cust_55821`,
`user-cust_10045`.

#### Endpoint `inventory`

| Collection | Sync function assigns... | Because |
|---|---|---|
| `stock_levels` | `store-{storeId}` | ingredient stock is per-store, no per-station split |

→ **Assign Channels**: `store-10492`, `store-20873`.

**If you've changed the sample data**: add a `store-{id}[-espresso/-warming]`
entry per store you added (`stores.json`), and a `user-{customerId}` entry
per customer you added (`customers.json`) to whichever endpoint's list
above includes that pattern.

**For each of the 4 endpoints, using its list from above:**

1. Inside that App Endpoint: **Security > App Users > Create App User**.
2. **App User name**: `demo-web-app` (or whatever you'll put in
   `APP_SERVICES_USERNAME` - just use the same value on all 4).
3. **Password**: same password every time - this becomes
   `APP_SERVICES_PASSWORD`.
4. Leave **Assigned App Roles** empty - this demo doesn't use named App
   Roles at all, only channel grants. A handful of the sync functions in
   step 7 also call `requireRole("manager")` etc. on writes to `stores` /
   `menu_items` / `modifiers` / `employees` / `customers`, but that's fine
   to leave unsatisfied: the live app never writes to those collections,
   only reads them, and a channel grant alone is enough for reads.
5. **Assign Channels**: type every channel from that endpoint's list
   above, pressing Enter after each one to add it.
6. Click **Create App User**.

**Hardening later:** split this single shared credential into per-page
credentials (a `register` user, an `espresso-kds` user with access to only
`store-{id}` + `store-{id}-espresso` on the `operations` endpoint, etc.) -
the per-page channel filters in `app/static/js/cbl/*.js` already narrow
what each page *asks for*; per-credential channel grants on each endpoint
would enforce it server-side too, which is the right posture for anything
beyond a demo.

## 4. Configure `.env`

```bash
APP_SERVICES_BASE_URL=wss://<app-id>.apps.cloud.couchbase.com:4984
APP_SERVICES_USERNAME=demo-web-app
APP_SERVICES_PASSWORD=...   # whatever you set in §3

# Optional - only if you want the Admin API script (§3) using a different
# credential than the browser syncs with. Leave blank to reuse the above.
APP_SERVICES_ADMIN_USERNAME=
APP_SERVICES_ADMIN_PASSWORD=
```

Find `<app-id>` on the App Services deployment's overview page in Capella
(each endpoint's individual URL is also shown there if you want to
double-check the host/port match what you typed). No scope name goes on
the end here - the app appends `/catalog`, `/people`, etc. itself (see
`app_services_url_for_scope()` in `app/config.py`).

Restart `uvicorn app.main:app --reload` after editing `.env` - it's only
read at process startup.

## 5. Verifying it's working

**A green "Synced" banner does not by itself mean data is flowing** - it
only means the Replicator connected and has nothing left to do, which is
equally true if it correctly synced everything *or* if it's correctly
synced zero documents because a channel/import/sync-function step above
was missed. Check for actual data, not just banner color.

1. Open `/pos` - the amber "App Services isn't configured" banner (see
   `app/templates/base.html`) should be gone, replaced by a blue
   "Connecting..." then green "✓ Synced live... (4 endpoints)" banner
   (Register uses all 4 scopes). If it stays amber, `.env` wasn't picked
   up (restart needed); if it goes red, the error message in the banner
   (and the browser console) says why - most commonly CORS not enabled on
   one of the 4 endpoints (§2 step 9), a wrong endpoint name (must be
   `catalog`/`people`/`operations`/`inventory` exactly), or a missing App
   User on one of the 4 endpoints (§3).
   - **The specific console error `TypeError: Failed to fetch` / "Server
     connection failed... CORS settings" on a `POST .../_session` call**
     is CORS, not a real network problem (confirmed by directly checking
     the preflight response) - and the two most likely causes, in order,
     are: (a) **Login Origin** left blank on that endpoint (Save won't
     even enable without it, but it's easy to miss that requirement and
     save with just Origin filled in), or (b) **Custom HTTP Headers** not
     actually containing `Authorization, Content-Type` - see §2 step 9 for
     the exact field behavior, since it's easy to enter this wrong.
2. **The menu grid on `/pos` should show items.** If the banner is green
   but the grid is empty, work through these in order (all field-tested,
   all silent failures - nothing errors, it just shows nothing):
   - **DevTools > Application > IndexedDB > `brew-pos` >
     `catalog.menu_items`** - does it actually contain documents? If
     empty, it's a server-side/import problem, not a rendering bug.
   - **Import Filter not enabled, or its default filter still active**
     (§2 step 8) - the single most likely cause. Capella's default import
     filter only imports `doc.type == "mobile"` documents, which silently
     excludes everything this app writes.
   - **The Access Control function pasted into the Import Filter box (or
     vice versa)** - these are two different fields (see the table in §2
     step 8) and mixing them up is easy since both live in
     `sync-functions.js`-shaped files. If Import Filter's box contains
     `requireRole()`/`channel()`/`throw()` calls, that's the Access Control
     function in the wrong place - it'll error on every document (calling
     an undefined function) and Sync Gateway's fail-safe is to just not
     import it, with **zero error shown anywhere** - no banner, no console
     message, the document just never gets a `_sync` xattr. Confirmed with
     `python scripts/verify_sync_function.py`, which writes a fresh
     throwaway document per collection and reports back exactly this.
   - **Sync function never actually saved** (§2 step 7) - if a document's
     channel ends up matching its collection name exactly instead of what
     the sync function specifies (e.g. `menu_items` instead of
     `public-menu`), the custom function didn't save.
   - **Import only applies to future mutations** - if you just fixed
     either of the above, you must re-run `python scripts/seed_data.py`
     afterward so the existing documents get re-written and picked up.
3. Open `/kds/espresso` and `/kds/warming` - each should go green too
   (2 endpoints each: `catalog` + `operations`).
4. Place an order on `/pos`, then check the KDS tabs - the matching line
   item should appear **without a page refresh**. If it doesn't, re-check
   that the `operations` endpoint's App User (§3) actually has the
   `store-{id}-espresso` / `store-{id}-warming` channels granted - a
   channel-grant gap on that one endpoint is the most likely first-contact
   issue.
5. If something's stuck, check the browser console (each Replicator logs
   its own errors there, tagged `[cbl] replicator error (scope=...)`) -
   that tells you exactly which of the 4 endpoints is the problem, rather
   than just "sync isn't working."

## Appendix: full channel reference

Channels are assigned per-collection by `sync-functions.js`, not per
endpoint - §2 step 7's per-collection tables are the source of truth for
setup. This is the same information laid out by channel instead, for
quick lookup:

| Channel | Carries | Endpoint it lives on | Subscribed by |
|---|---|---|---|
| `store-{storeId}` | Store doc, full order/event/stock stream for that store | `catalog`, `operations`, `inventory` | Pickup board, manager dashboard, register writes |
| `store-{storeId}-espresso` | Orders/events touching the espresso bar | `operations` | Espresso Bar KDS |
| `store-{storeId}-warming` | Orders/events touching the warming station | `operations` | Warming Station KDS |
| `store-{storeId}-staff` | Employee roster for that store | `people` | Store staff login screens |
| `public-menu` | menu_items, modifiers | `catalog` | Register (menu is not store-specific) |
| `user-{customerId}` | That customer's own profile + orders | `people`, `operations` | Loyalty/mobile lookups |

A single `order` document commonly lands in 3-4 channels at once (its
store, one or both station channels, and the customer's personal channel)
- that's intentional: one write fans out to every device that needs to see
it. It's also exactly why the Espresso KDS's `operations`-endpoint
Replicator (filtered to `store-{id}-espresso`) never receives a Warming
Station ticket even though both tickets came from the same order document.

This demo doesn't use named **App Roles** at all (every App User created
in §3 has an empty role list) - access is entirely channel-based. The
sync functions do still call `requireRole("manager")` on writes to
catalog/people collections, which is simply never satisfied since the
live app never writes to those collections - reads don't need it.
