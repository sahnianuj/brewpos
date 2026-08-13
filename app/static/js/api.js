// Small formatting helpers shared by every page. Loaded as a plain
// (non-module) script in base.html, so these are just globals - available
// to the ES module page scripts too, since modules still see `window`.
//
// This used to also hold a fetch() wrapper for our own /api/* backend;
// that's gone now that every page talks to Couchbase Lite directly - see
// app/static/js/cbl/.

function money(n) {
  return "$" + Number(n).toFixed(2);
}

function timeAgo(isoString) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(isoString)) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m ago`;
}
