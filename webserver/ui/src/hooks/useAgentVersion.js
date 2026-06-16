import { useEffect, useState } from 'react';
import { fetchAgentVersion } from '../api.js';

// The configured agent CLI's version + capability flags (backend, version,
// up_to_date, supports_workflows, can_upgrade, …). Fetched once and cached
// module-wide, shared by every surface (the out-of-date banner AND the
// ultracode toggle gate). Refreshable so an in-app CLI upgrade updates ALL
// of them live — no page reload. Returns ``null`` until first load.

let _cache = null;
let _inflight = null;
const _subs = new Set();

function _emit() {
  for (const cb of _subs) { cb(_cache); }
}

function _load(force) {
  if (_cache && !force) { return Promise.resolve(_cache); }
  if (_inflight && !force) { return _inflight; }
  _inflight = fetchAgentVersion()
    .then((body) => { _cache = body || {}; _emit(); return _cache; })
    .catch(() => { _cache = _cache || {}; _emit(); return _cache; })
    .finally(() => { _inflight = null; });
  return _inflight;
}

// Re-probe and notify every consumer — call after an in-app upgrade so the
// banner clears and the ultracode toggle (re)appears without a reload.
export function refreshAgentVersion() {
  return _load(true);
}

// Test-only: reset the module cache so each test fetches fresh.
export function resetAgentVersionCacheForTests() {
  _cache = null;
  _inflight = null;
  _subs.clear();
}

export function useAgentVersion() {
  const [info, setInfo] = useState(_cache);
  useEffect(() => {
    const cb = (value) => setInfo(value);
    _subs.add(cb);
    _load(false).then((value) => setInfo(value));
    return () => { _subs.delete(cb); };
  }, []);
  return info;
}
