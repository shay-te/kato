import { useEffect, useRef, useState } from 'react';
import { fetchTaskAgentStatus } from '../api.js';
import { usePolling } from './usePolling.js';

// Per-backend chat liveness for one task, polled.
//
// The header shows a status chip per agent. The ACTIVE agent's chip is driven
// by the live SSE stream (only it separates "sleeping" from "closed" and
// reacts within the turn); this fills in the ones the stream says nothing
// about — the parked conversations, whose subprocesses are deliberately left
// running when the operator switches tabs.
//
// Polled rather than streamed on purpose: a backgrounded agent's state
// changes on the order of seconds and does not deserve a second SSE
// connection per task.
//
// Polls through ``usePolling`` rather than its own ``setTimeout`` loop. The
// hand-rolled version was a fourth copy of that loop and, like the one inside
// usePolling, had drifted away from the ``document.hidden`` check — so a
// backgrounded window kept asking the server which agents were alive while
// nobody could see the chips.
const POLL_MS = 5000;

// ``resyncKey`` forces an immediate re-poll when it changes. Switching agent
// tabs resets the live SSE stream, so for a moment the active chip has
// nothing definite to say — and the OTHER chip was up to a poll interval
// behind. The operator saw switching tabs "affect who is working", because
// for those seconds neither chip was reporting the truth.
export function useTaskAgentStatuses(
  taskId, { intervalMs = POLL_MS, resyncKey = '' } = {},
) {
  const [rows, setRows] = useState([]);
  // Guards a resolved fetch from writing rows after the hook was torn down or
  // re-keyed onto a different task — the same protection the old loop's
  // ``cancelled`` flag gave, which the poller cannot express for us because
  // the request outlives the tick that issued it.
  const liveRef = useRef(0);
  // Guards against RESPONSES ARRIVING OUT OF ORDER within one task. The old
  // loop rescheduled in ``.finally()``, so exactly one request was ever in
  // flight; a fixed-cadence poller can have two, and a slow first response
  // landing after a fast second one would apply the OLDER chips and leave them
  // there until the next tick. The generation ref cannot catch this — both
  // requests belong to the same generation.
  const seqRef = useRef(0);

  useEffect(() => {
    liveRef.current += 1;
    if (!taskId) { setRows([]); }
    return () => { liveRef.current += 1; };
  }, [taskId, resyncKey]);

  usePolling(async () => {
    const generation = liveRef.current;
    seqRef.current += 1;
    const seq = seqRef.current;
    try {
      const body = await fetchTaskAgentStatus(taskId);
      // Stale by task, or overtaken by a later request for the same task.
      if (liveRef.current !== generation || seq !== seqRef.current) { return; }
      setRows(Array.isArray(body?.backends) ? body.backends : []);
    } catch {
      // A failed poll is not an error state — the chips keep their last value
      // rather than flashing "unknown" on one dropped request.
    }
  }, intervalMs, [taskId, resyncKey], { enabled: !!taskId });

  return rows;
}
