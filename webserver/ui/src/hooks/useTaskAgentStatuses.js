import { useEffect, useState } from 'react';
import { fetchTaskAgentStatus } from '../api.js';

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

  useEffect(() => {
    if (!taskId) { setRows([]); return undefined; }
    let cancelled = false;
    let timer = null;

    function tick() {
      fetchTaskAgentStatus(taskId)
        .then((body) => {
          if (cancelled) { return; }
          setRows(Array.isArray(body?.backends) ? body.backends : []);
        })
        // A failed poll is not an error state — the chips simply keep their
        // last value rather than flashing "unknown" on one dropped request.
        .catch(() => {})
        .finally(() => {
          if (!cancelled) { timer = window.setTimeout(tick, intervalMs); }
        });
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) { window.clearTimeout(timer); }
    };
  }, [taskId, intervalMs, resyncKey]);

  return rows;
}
