import { useCallback, useState } from 'react';
import { fetchConfigStatus } from '../api.js';
import { usePolling } from './usePolling.js';

// Poll interval while the first-run gate may be showing. Kept short so the
// wizard reflects a just-saved setting (and the "you're all set" transition)
// quickly — the endpoint is a cheap env/settings.json read.
const REFRESH_INTERVAL_MS = 5_000;

// Single source of truth for the setup-mode gate. Returns the latest
// config-status plus a manual ``refresh`` the wizard calls right after it
// saves, so the "what's missing" list and the completion state update
// immediately instead of waiting for the next poll tick.
export function useConfigStatus() {
  const [status, setStatus] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await fetchConfigStatus());
    } catch (_) {
      // Defensive surface — retry on the next tick rather than tearing the
      // whole app down if a single poll fails.
    }
  }, []);

  usePolling(refresh, REFRESH_INTERVAL_MS);

  return { status, refresh };
}
