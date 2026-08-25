import { useEffect, useState } from 'react';
import { fetchAgentVersion } from '../api.js';

// The configured agent CLI's version + capability flags (backend, version,
// up_to_date, supports_workflows, can_upgrade, …). Fetched once and cached
// module-wide, shared by every surface (the out-of-date banner AND the
// ultracode toggle gate). Refreshable so an in-app CLI upgrade updates ALL
// of them live — no page reload. Returns ``null`` until first load.

// Keyed by BACKEND, not global. Each agent tab has its own CLI, its own
// version and its own "out of date" — one shared slot meant the Codex tab
// showed Claude's answer, and a host configured for Claude could never
// surface a stale Codex CLI at all. '' is the configured backend.
const _cache = new Map();
const _inflight = new Map();
const _subs = new Set();

function _emit(key) {
  for (const cb of _subs) { cb(key); }
}

function _load(force, backend = '') {
  const key = String(backend || '');
  if (_cache.has(key) && !force) { return Promise.resolve(_cache.get(key)); }
  if (_inflight.has(key) && !force) { return _inflight.get(key); }
  const request = fetchAgentVersion(force, key)
    .then((body) => { _cache.set(key, body || {}); _emit(key); return _cache.get(key); })
    .catch(() => {
      if (!_cache.has(key)) { _cache.set(key, {}); }
      _emit(key);
      return _cache.get(key);
    })
    .finally(() => { _inflight.delete(key); });
  _inflight.set(key, request);
  return request;
}

// Re-probe and notify every consumer — call after an in-app upgrade so the
// banner clears and the ultracode toggle (re)appears without a reload.
export function refreshAgentVersion(backend = '') {
  // Every cached backend is re-probed: an upgrade can change which CLI is on
  // PATH, and leaving the other tab's banner stale is the bug this clears.
  const keys = _cache.size ? [..._cache.keys()] : [String(backend || '')];
  return Promise.all(keys.map((key) => _load(true, key)));
}

// Test-only: reset the module cache so each test fetches fresh.
export function resetAgentVersionCacheForTests() {
  _cache.clear();
  _inflight.clear();
  _subs.clear();
}

export function useAgentVersion(backend = '') {
  const key = String(backend || '');
  const [info, setInfo] = useState(() => _cache.get(key) ?? null);
  useEffect(() => {
    setInfo(_cache.get(key) ?? null);
    const cb = (changed) => {
      if (changed === key) { setInfo(_cache.get(key) ?? null); }
    };
    _subs.add(cb);
    _load(false, key).then((value) => setInfo(value));
    return () => { _subs.delete(cb); };
  }, [key]);
  return info;
}
