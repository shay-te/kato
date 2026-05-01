import { useCallback, useEffect, useState } from 'react';
import { approveTaskPush, fetchAwaitingPushApproval } from '../api.js';

const POLL_INTERVAL_MS = 5000;

export function usePushApproval(taskId) {
  const [awaiting, setAwaiting] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!taskId) { setAwaiting(false); return; }
    let cancelled = false;
    async function check() {
      try {
        const body = await fetchAwaitingPushApproval(taskId);
        if (!cancelled) {
          setAwaiting(!!body?.awaiting_push_approval);
        }
      } catch (_) {
        // Best-effort; UI keeps last known state.
      }
    }
    check();
    const handle = window.setInterval(check, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [taskId]);

  const approve = useCallback(async () => {
    if (!taskId || busy) { return null; }
    setBusy(true);
    const result = await approveTaskPush(taskId);
    setBusy(false);
    if (result.ok) { setAwaiting(false); }
    return result;
  }, [taskId, busy]);

  return { awaiting, busy, approve };
}
