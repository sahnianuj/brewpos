// Shared write helpers. Every save uses LastWriteWins as its conflict
// handler - simplest possible policy for a demo with light concurrency;
// a production app would want something smarter (e.g. reject conflicting
// line-item status transitions rather than silently overwriting).
import { DocID, LastWriteWins } from "./client.js";

/** Creates or overwrites a document at `key` with `body`. */
export async function saveDoc(collection, key, body) {
  const doc = collection.createDocument(DocID(key), body);
  await collection.save(doc, LastWriteWins);
  return doc;
}

/** Saves an already-fetched CBLDocument back after mutating its properties
 *  in place (see orderLogic.js's advanceLineItem/completeOrder/cancelOrder,
 *  which mutate the document object you pass them). */
export async function saveExisting(collection, doc) {
  await collection.save(doc, LastWriteWins);
  return doc;
}

/** Loads a document, applies `mutate(doc)` to it in place, and saves it.
 *  No-ops (returns false) if the document doesn't exist - mirrors the
 *  Python inventory_repo.decrement_stock's "best effort" behavior for
 *  ingredients that aren't tracked at a given store. */
export async function mutateDoc(collection, key, mutate) {
  const doc = await collection.getDocument(DocID(key));
  if (!doc) return false;
  mutate(doc);
  await collection.save(doc, LastWriteWins);
  return true;
}

/** Decrements stock_levels.quantityOnHand for one ingredient at one store. */
export function decrementStock(stockLevelsCollection, storeId, ingredientId, amount) {
  if (amount <= 0) return;
  const key = `stock::${storeId}::${ingredientId}`;
  return mutateDoc(stockLevelsCollection, key, (doc) => {
    doc.quantityOnHand = (doc.quantityOnHand ?? 0) - amount;
  });
}
