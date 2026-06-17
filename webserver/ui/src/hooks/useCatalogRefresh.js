// A tiny broadcast so the option catalogues (the model + effort pickers,
// via useSessionOption) can be re-fetched on demand — e.g. when the operator
// hits the header Refresh after upgrading the CLI — without a page reload or
// threading a refresh prop through App → SessionDetail → every picker.
//
// Mirrors the useAgentVersion pub/sub: callers subscribe, a single
// refreshCatalogs() bump notifies them all to re-fetch (forced).
const _subs = new Set();

// Notify every subscribed picker to re-fetch its catalogue (bypassing caches).
export function refreshCatalogs() {
  for (const cb of _subs) { cb(); }
}

export function subscribeCatalogRefresh(cb) {
  _subs.add(cb);
  return () => { _subs.delete(cb); };
}

// Test-only: drop all subscribers so cases don't leak across each other.
export function resetCatalogRefreshForTests() {
  _subs.clear();
}
