import { fetchLidAwake } from '../api.js';
import { useChangedState } from './useChangedState.js';
import { usePolling } from './usePolling.js';

const REFRESH_INTERVAL_MS = 15_000;

// Will closing the lid stop kato? Matches the server's own tick, so the header
// never lags a charger change by more than two of them.
export function useLidAwake() {
  const [status, setStatus] = useChangedState(null);

  usePolling(async () => {
    try {
      setStatus(await fetchLidAwake());
    } catch (_) {
      // An older backend has no such route; the header just shows nothing.
    }
  }, REFRESH_INTERVAL_MS);

  return status;
}
