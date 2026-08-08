import { useEffect, useRef, useState } from 'react';
import { fetchSessionContextUsage } from '../api.js';

/**
 * Live context-window usage for the composer's meter.
 *
 * Refreshed when a TURN COMPLETES rather than on a timer: the CLI reports
 * usage once per turn, so a poll would re-fetch the same numbers all the way
 * through a ten-minute run and still be no fresher at the end of it.
 *
 * Also fetched on mount, so switching back to a task shows where its context
 * stands without waiting for the agent to be prompted again.
 *
 * Errors leave the previous reading in place instead of blanking the meter —
 * a dropped request is not evidence the window emptied.
 */
export function useContextUsage(taskId, turnInFlight) {
  const [usage, setUsage] = useState(null);
  const wasInFlight = useRef(false);

  useEffect(() => {
    setUsage(null);
    wasInFlight.current = false;
  }, [taskId]);

  useEffect(() => {
    const finishedATurn = wasInFlight.current && !turnInFlight;
    wasInFlight.current = !!turnInFlight;
    if (!taskId) { return undefined; }
    // Fetch on mount/task-switch and on each turn boundary; skip while a
    // turn is running (the number can't change until it ends).
    if (turnInFlight && !finishedATurn) { return undefined; }

    let cancelled = false;
    fetchSessionContextUsage(taskId)
      .then((next) => { if (!cancelled && next) { setUsage(next); } })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [taskId, turnInFlight]);

  return usage;
}
