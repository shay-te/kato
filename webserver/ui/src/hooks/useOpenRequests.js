import { useEffect, useRef } from 'react';

// Runs ``open(taskId, request)`` for each NEW request from ``requests`` (a
// store shaped like ``createOpenRequests``: ``{taskId, seq, ...}``) about the
// task on screen — '' means that task. Only the task on screen: its centre
// tabs are the ones showing. A replay on re-subscribe (``seq`` already seen)
// opens nothing.
export function useOpenRequests(requests, activeTaskId, open) {
  const lastSeqRef = useRef(0);
  useEffect(() => requests.subscribe((request) => {
    if (request.seq === lastSeqRef.current) { return; }
    lastSeqRef.current = request.seq;
    const taskId = request.taskId || activeTaskId;
    if (!taskId || taskId !== activeTaskId) { return; }
    open(taskId, request);
  }), [requests, activeTaskId, open]);
}
