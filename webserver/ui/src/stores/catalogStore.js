// Module-cache for the GLOBAL option catalogues (the model list, the effort
// levels). They're identical for every task and change only on a CLI upgrade
// (+ the explicit header Refresh), so caching the fetch here means a task
// switch — which remounts SessionDetail and resets useSessionOption's per-hook
// ``loadedRef`` — no longer refetches /models + /effort-levels every time.
// Mirrors useAgentVersion's module cache. Pure (no imports) so it's safe to
// reset from the global test setup.

const _cache = new Map(); // key -> Promise<result>

// Return a cached catalogue promise, fetching (and caching) on first use.
// ``force`` re-fetches and replaces the entry — for the on-demand refresh so a
// just-upgraded CLI's labels show without a reload. A FAILED fetch is not
// cached, so the next call retries (guarded against a stale reject clobbering a
// newer entry).
export function loadCatalog(key, fetcher, force = false) {
  if (force || !_cache.has(key)) {
    const promise = Promise.resolve()
      .then(() => fetcher(force))
      .catch((err) => {
        if (_cache.get(key) === promise) { _cache.delete(key); }
        throw err;
      });
    _cache.set(key, promise);
  }
  return _cache.get(key);
}

// Drop every cached catalogue (a global refresh forces a re-fetch).
export function clearCatalogCache() { _cache.clear(); }
