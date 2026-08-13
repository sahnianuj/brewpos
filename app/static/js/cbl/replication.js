// Starts one Replicator per scope a page needs, against Capella App
// Services, and keeps the shared #syncBanner (see base.html) reflecting
// real connection state across all of them.
//
// Why "one per scope": Capella App Services ties each App Endpoint to
// exactly one scope (confirmed against Capella's own docs - an endpoint
// is created by picking a single bucket + scope, and "App Endpoints can
// share scopes but cannot link to the same collections"). Our data model
// has 4 scopes, so there are 4 endpoint URLs (see
// app_services_url_for_scope() in app/config.py and
// sync-gateway/app-services-setup.md) - a page that needs collections
// from more than one scope runs more than one Replicator, all against the
// same local Database, all started/stopped together.
//
// Deliberately never fails silently: an unconfigured or unreachable App
// Services shows a clear on-page message instead of the page just looking
// broken - the same lesson learned the hard way with the Python backend's
// Capella connection timeout (see app/db.py).
import { openDatabase, Replicator } from "./client.js";

function bannerEl() {
  return document.getElementById("syncBanner");
}

export function showBanner(kind, message) {
  const el = bannerEl();
  if (!el) return;
  el.hidden = false;
  el.className = `sync-banner ${kind}`;
  el.textContent = message;
}

export function hideBanner() {
  const el = bannerEl();
  if (el) el.hidden = true;
}

function scopeOf(dottedCollectionName) {
  return dottedCollectionName.split(".")[0];
}

/**
 * Opens the local database and, if App Services is configured, starts one
 * Replicator per scope referenced in `collectionConfig` - each with an
 * optional list of Sync Gateway channels to pull (server-side filtering:
 * this is what makes e.g. the Espresso KDS only ever sync espresso-station
 * documents).
 *
 * @param {string} pageLabel - shown in banner text, e.g. "Espresso KDS"
 * @param {Record<string, {push?: boolean, pull?: boolean, channels?: string[]}>} collectionConfig
 *        keys are dotted "scope.collection" names from COLLECTIONS in client.js
 * @returns {Promise<{database: import("./client.js").Database, replicators: import("./client.js").Replicator[]}>}
 */
export async function startSync(pageLabel, collectionConfig) {
  const database = await openDatabase();
  const asConfig = window.APP_SERVICES_CONFIG;

  if (!asConfig || !asConfig.configured) {
    showBanner(
      "warning",
      "⚠ App Services isn't configured yet - set APP_SERVICES_BASE_URL, " +
        "APP_SERVICES_USERNAME and APP_SERVICES_PASSWORD in .env " +
        `(and restart the app). ${pageLabel} is running against the local, ` +
        "unsynced database only until then."
    );
    return { database, replicators: [] };
  }

  // Group requested collections by scope - one Replicator/endpoint each.
  const byScope = {};
  for (const [name, cfg] of Object.entries(collectionConfig)) {
    (byScope[scopeOf(name)] ??= {})[name] = cfg;
  }

  const scopeNames = Object.keys(byScope);
  const statuses = new Map(scopeNames.map((s) => [s, { status: "connecting" }]));
  const renderBanner = () => {
    const values = [...statuses.values()];
    const errored = values.find((s) => s.error);
    if (errored) {
      showBanner("error", `✗ Sync error: ${errored.error.message || errored.error}`);
    } else if (values.some((s) => s.status === "offline")) {
      showBanner("error", "✗ Offline - retrying connection to App Services...");
    } else if (values.every((s) => s.status === "idle")) {
      showBanner("ok", `✓ Synced live via App Services (${pageLabel}, ${scopeNames.length} endpoint${scopeNames.length > 1 ? "s" : ""})`);
    } else {
      showBanner("connecting", `Syncing ${pageLabel} (${scopeNames.join(", ")})...`);
    }
  };
  renderBanner();

  const replicators = scopeNames.map((scope) => {
    const url = asConfig.scopeUrls[scope];
    if (!url) {
      throw new Error(`No App Endpoint URL configured for scope "${scope}" - check window.APP_SERVICES_CONFIG.scopeUrls`);
    }

    const collections = {};
    for (const [name, cfg] of Object.entries(byScope[scope])) {
      collections[name] = {
        ...(cfg.push ? { push: { continuous: true } } : {}),
        ...(cfg.pull ? { pull: { continuous: true, channels: cfg.channels } } : {}),
      };
    }

    const replicator = new Replicator({
      database,
      url,
      credentials: { username: asConfig.username, password: asConfig.password },
      continuous: true,
      collections,
    });

    replicator.onStatusChange = (status) => {
      statuses.set(scope, status);
      if (status.error) console.error(`[cbl] replicator error (scope=${scope})`, status.error);
      renderBanner();
    };
    replicator.run().catch((err) => {
      statuses.set(scope, { status: "offline", error: err });
      console.error(`[cbl] replicator.run() failed (scope=${scope})`, err);
      renderBanner();
    });

    return replicator;
  });

  return { database, replicators };
}

/** Stops every Replicator returned by startSync() - call before starting a
 *  new set (e.g. when the selected store changes and channel filters need
 *  to be rescoped). */
export function stopAll(replicators) {
  for (const r of replicators) r.stop();
}
