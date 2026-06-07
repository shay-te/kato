import { useCallback, useEffect, useState } from 'react';
import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';
import { fetchPendingPermissions, postSession } from '../api.js';

// Cross-task permission prompting.
//
// The per-task SSE stream only delivers a ``control_request`` to the browser
// tab that has THAT session open, so a permission ask on a backgrounded task
// would sit unanswered until the operator happened to click into it. This
// polls the global ``/api/permissions/pending`` feed and pops the modal for
// any pending ask on a task OTHER than the one in focus — the focused task is
// still handled instantly by its own SSE container in SessionDetail, with the
// chat audit bubbles. The modal titles itself with the task code (the feed
// stamps ``task_id``, surfaced by unpackPermissionEnvelope) so the operator
// knows which task is waiting.
//
// Remembered "Allow always"/"Deny always" decisions auto-resolve here too —
// we hand the ask to the same PermissionDecisionContainer the focused path
// uses, so a remembered ``mvn`` on a background task is approved silently
// instead of nagging.

const POLL_MS = 3000;

export default function GlobalPermissionContainer({ activeTaskId, toolMemory }) {
  const [pendingList, setPendingList] = useState([]);

  const refetch = useCallback(async () => {
    try {
      const body = await fetchPendingPermissions();
      setPendingList(Array.isArray(body?.pending) ? body.pending : []);
    } catch (_) { /* keep the last snapshot on a transient failure */ }
  }, []);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const body = await fetchPendingPermissions();
        if (alive) {
          setPendingList(Array.isArray(body?.pending) ? body.pending : []);
        }
      } catch (_) { /* keep the last snapshot */ }
    }
    tick();
    const handle = window.setInterval(tick, POLL_MS);
    return () => { alive = false; window.clearInterval(handle); };
  }, []);

  // The oldest pending ask that is NOT the focused task (the focused task's
  // own SSE container owns that one — instant, with chat audit bubbles).
  const current = pendingList.find(
    (entry) => entry && entry.task_id && entry.task_id !== activeTaskId,
  ) || null;

  const submit = useCallback(async ({ requestId, allow, rationale }) => {
    const taskId = current?.task_id;
    if (!taskId) { return false; }
    const result = await postSession(taskId, 'permission', {
      request_id: requestId,
      allow,
      rationale,
    });
    return !!result.ok;
  }, [current]);

  if (!current) { return null; }

  return (
    <PermissionDecisionContainer
      // Remount only when the actual task+request changes — a fresh poll
      // object for the SAME ask must not tear down a modal mid-decision.
      key={`${current.task_id}:${current.request_id}`}
      pending={current}
      onDismiss={refetch}
      onSubmit={submit}
      onAuditBubble={() => { /* cross-task: no focused chat to bubble into */ }}
      recallToolDecision={toolMemory.recall}
      rememberToolDecision={toolMemory.remember}
    />
  );
}
