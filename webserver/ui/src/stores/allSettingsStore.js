// One owner for ``GET /api/all-settings``.
//
// Two things read that payload: the Settings drawer (for its tab list AND its
// cross-tab "find a setting" index) and whichever schema panel is open (for
// the fields it edits). They fetched it independently, and both did it badly:
//
//   * The panel is mounted with ``key={sectionId}``, so EVERY schema-tab click
//     remounted it and re-fetched the whole payload — then once more after
//     every save. The server resolves ~121 fields through an uncached
//     per-key settings read, so each of those is ~121 file reads and JSON
//     parses for one section's worth of data.
//   * The drawer's fetch is latched behind a ``schemaLoaded`` flag and the
//     drawer never unmounts (``open`` only drives a CSS transform), so it read
//     the payload once per PAGE LOAD. After a save, its search index kept
//     serving pre-save values until a full reload — quietly, with no way for
//     the operator to tell.
//
// So this is not only a caching story. The two readers have to share an
// invalidation, or the one that never remounts goes stale forever.
//
// Shaped like ``catalogStore`` (module cache, promise-valued, failures not
// cached) plus the subscription that stale index needs.

import { fetchAllSettings } from '../api.js';

let _promise = null;
const _subscribers = new Set();

// The shared payload promise, fetching on first use. ``force`` re-fetches and
// replaces the entry. A FAILED fetch is not cached, so the next caller
// retries — guarded so a stale rejection cannot clobber a newer entry.
export function loadAllSettings(force = false) {
  if (force || _promise === null) {
    const promise = Promise.resolve()
      .then(() => fetchAllSettings())
      .then((result) => {
        // An envelope that reports failure is not a cacheable answer either —
        // otherwise one flaky load leaves the drawer empty for the session.
        if (!result || !result.ok) {
          if (_promise === promise) { _promise = null; }
        }
        return result;
      })
      .catch((err) => {
        if (_promise === promise) { _promise = null; }
        throw err;
      });
    _promise = promise;
  }
  return _promise;
}

// Drop the cached payload and tell every reader to re-read. Called after a
// save: the panel that saved re-reads for its own fields, and the drawer —
// which would otherwise never look again — refreshes its search index.
export function invalidateAllSettings() {
  _promise = null;
  for (const notify of _subscribers) {
    try { notify(); } catch (_) { /* one reader must not break the others */ }
  }
}

// Subscribe to invalidations. Returns an unsubscribe function.
export function subscribeAllSettings(notify) {
  _subscribers.add(notify);
  return () => _subscribers.delete(notify);
}

// Test-only: forget the cache and every subscriber between cases.
export function _resetAllSettingsStore() {
  _promise = null;
  _subscribers.clear();
}
