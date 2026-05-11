import { useEffect, useState } from 'react';
import { fetchSafetyState } from '../api.js';

const REFRESH_INTERVAL_MS = 30_000;

export function useSafetyState() {
  const [state, setState] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const next = await fetchSafetyState();
        if (!cancelled) { setState(next); }
      } catch (_) {
        // The banner is a defensive surface; silently retry on the next tick
        // rather than swallowing the rest of the UI.
      }
    }

    load();
    const handle = window.setInterval(load, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, []);

  return state;
}
