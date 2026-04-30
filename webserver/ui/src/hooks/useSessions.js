import { useCallback, useEffect, useState } from 'react';
import { fetchSessionList } from '../api.js';

const REFRESH_INTERVAL_MS = 5000;

export function useSessions() {
  const [sessions, setSessions] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchSessionList();
      if (Array.isArray(data)) { setSessions(data); }
    } catch (_) { /* next tick retries */ }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return { sessions, refresh };
}
