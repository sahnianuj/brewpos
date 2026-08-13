// Small helpers around Database.createQuery(), used by every page.
//
// Local queries use the same N1QL/SQL++ this project already writes for
// Capella Server - the FROM source is just the collection's dotted
// "scope.collection" name, backtick-quoted since it contains a dot
// (there's no bucket prefix locally - see COLLECTIONS in client.js).
import { showBanner } from "./replication.js";

/** Starts a live (reactive) local query: `onResults` fires once immediately
 *  with the current matching rows (via an explicit .execute() - NOT
 *  assumed from addChangeListener alone, since whether that fires on
 *  registration or only on subsequent changes wasn't confirmed until
 *  tested against a real endpoint, and it turned out to be the latter),
 *  then again every time the underlying data changes. Returns
 *  `{query, token}`; call `token.remove()` when the page/view no longer
 *  needs updates. */
export async function liveQuery(db, sql, params, onResults) {
  const query = db.createQuery(sql);
  if (params) query.parameters = params;

  try {
    onResults(await query.execute());
  } catch (err) {
    console.error("[cbl] initial query failed", sql, err);
    showBanner("error", `✗ Query failed: ${err.message || err}`);
    throw err;
  }

  const token = query.addChangeListener(onResults);
  return { query, token };
}

/** One-shot local query, for data a page just needs to load once (e.g. the
 *  store list for a dropdown). */
export async function runQuery(db, sql, params) {
  const query = db.createQuery(sql);
  if (params) query.parameters = params;
  try {
    return await query.execute();
  } catch (err) {
    console.error("[cbl] query failed", sql, err);
    showBanner("error", `✗ Query failed: ${err.message || err}`);
    throw err;
  }
}
