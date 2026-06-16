import { useEffect, useState } from 'react';
import { fetchAgentVersion } from '../api.js';

// The configured agent CLI's version + capability flags (backend, version,
// up_to_date, supports_workflows, …). Fetched ONCE and cached module-wide —
// the CLI version doesn't change while kato runs, and several surfaces want it
// (the out-of-date banner AND the ultracode toggle gate), so they share one
// request. Returns ``null`` until the first fetch resolves.

let _cache = null;
let _promise = null;

// Test-only: reset the module cache so each test fetches fresh.
export function resetAgentVersionCacheForTests() {
  _cache = null;
  _promise = null;
}

export function useAgentVersion() {
  const [info, setInfo] = useState(_cache);

  useEffect(() => {
    if (_cache) { setInfo(_cache); return undefined; }
    if (!_promise) {
      _promise = fetchAgentVersion()
        .then((body) => { _cache = body || {}; return _cache; })
        .catch(() => { _cache = {}; return _cache; });
    }
    let alive = true;
    _promise.then((value) => { if (alive) { setInfo(value); } });
    return () => { alive = false; };
  }, []);

  return info;
}
