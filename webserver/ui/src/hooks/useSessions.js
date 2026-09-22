import { useCallback, useState } from 'react';
import { fetchSessionList } from '../api.js';
import { usePolling } from './usePolling.js';

const REFRESH_INTERVAL_MS = 5000;

// ``loaded`` / ``reachable`` exist so the tab strip can tell an EMPTY kato
// from one it has not heard from yet.
//
// This hook swallows every failure — deliberately, since the next tick
// retries — and used to report only ``sessions``. An empty array therefore
// meant four different things at once: still loading, request failed, kato
// restarting, and genuinely no tasks. The strip rendered all four as the
// onboarding "No tabs yet. Click + Add task" copy, so a page reloaded while
// kato was booting (its webserver binds ~24s before it finishes starting)
// told the operator their tasks were gone. They were on disk the whole time.
//
// A poll that fails AFTER a good load deliberately leaves ``sessions`` alone:
// showing the last known tabs beats blanking the strip because one tick
// missed.
export function useSessions() {
  const [sessions, setSessions] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [reachable, setReachable] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchSessionList();
      if (Array.isArray(data)) {
        setSessions(data);
        setLoaded(true);
      }
      setReachable(true);
    } catch (_) {
      setReachable(false);
    }
  }, []);

  usePolling(refresh, REFRESH_INTERVAL_MS);

  return { sessions, refresh, loaded, reachable };
}
