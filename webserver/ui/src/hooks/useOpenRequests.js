import { useEffect, useRef } from 'react';

// How long a request about a task that is not on screen waits for that task
// to become the active one.
const PENDING_MS = 3000;

// Runs ``open(taskId, request)`` for each NEW request from ``requests`` (a
// store shaped like ``createOpenRequests``: ``{taskId, seq, ...}``) about the
// task on screen — '' means that task. Only the task on screen: its centre
// tabs are the ones showing. A replay on re-subscribe (``seq`` already seen)
// opens nothing.
//
// A request about ANOTHER task is held, briefly: a task's row in the tasks
// menu switches to that task and then asks for its view in the same click,
// before the switch has rendered. It opens once that task is on screen; one
// that never comes to be the active task (within ``PENDING_MS``) is dropped.
export function useOpenRequests(requests, activeTaskId, open) {
  const lastSeqRef = useRef(0);
  const pendingRef = useRef(null);
  useEffect(() => {
    const pending = pendingRef.current;
    if (pending && pending.taskId === activeTaskId) {
      pendingRef.current = null;
      if (Date.now() - pending.at <= PENDING_MS) { open(activeTaskId, pending.request); }
    }
    return requests.subscribe((request) => {
      if (request.seq === lastSeqRef.current) { return; }
      lastSeqRef.current = request.seq;
      const taskId = request.taskId || activeTaskId;
      if (!taskId) { return; }
      if (taskId !== activeTaskId) {
        pendingRef.current = { taskId, request, at: Date.now() };
        return;
      }
      open(taskId, request);
    });
  }, [requests, activeTaskId, open]);
}
