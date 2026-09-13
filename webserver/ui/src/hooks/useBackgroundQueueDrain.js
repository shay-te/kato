// Delivers queued prompts for tasks that are NOT on screen.
//
// Operator: "feels like i need to move to the tab to make the agent start..."
// The focused task's chat (SessionDetail) drains its own queue off precise
// ``result`` events. Every other task had no drain at all, so its next prompt
// waited for someone to open the tab. This hook is that missing drain, mounted
// once in App for the whole app.
//
// Division of labour, so there is never a double send:
//   * the FOCUSED task belongs to SessionDetail — skipped here, and forgotten
//     by the engine so switching away is not mistaken for its turn ending;
//   * every OTHER task is observed on each session poll, through the single
//     status derivation, and the engine in ../stores/queueDrain.js decides
//     when a turn has genuinely ended.
//
// A failed send is never silent: the prompt goes back to the FRONT of that
// task's queue and the operator gets a sticky toast naming the task. The next
// genuine turn ending retries it.

import { useEffect, useRef } from 'react';

import { postChatMessage } from '../api.js';
import { activeBackendStore } from '../stores/activeBackendStore.js';
import { createQueueDrain, QUEUE_DRAIN_DECISION } from '../stores/queueDrain.js';
import { toast } from '../stores/toastStore.js';
import { deriveAgentStatus, isBusyAgentKind } from '../utils/agentStatus.js';
import {
  hydrateQueuedMessages,
  persistQueuedMessages,
  readQueuedMessages,
  writeQueuedMessages,
} from '../utils/queuedMessagesStore.js';

// Take the next prompt off ``taskId``'s queue and send it. Exported for the
// hook's tests; App uses the hook.
export async function deliverNextQueued(taskId, session) {
  // After a reload the in-memory queue is cold — the durable copy is the
  // truth until something has been read.
  const items = readQueuedMessages(taskId).length
    ? readQueuedMessages(taskId)
    : await hydrateQueuedMessages(taskId);
  if (!items.length) { return false; }
  const [next, ...rest] = items;
  // Removed BEFORE the await, so a poll tick landing while the request is in
  // flight cannot see this prompt again.
  writeQueuedMessages(taskId, rest);
  persistQueuedMessages(taskId, rest);
  const backend = activeBackendStore.get(taskId) || String(session?.agent_backend || '');
  const result = await postChatMessage(taskId, next.text, next.images || [], backend);
  if (result && result.ok) { return true; }
  // Put it back where it was — at the front, ahead of anything queued since.
  const restored = [next, ...readQueuedMessages(taskId)];
  writeQueuedMessages(taskId, restored);
  persistQueuedMessages(taskId, restored);
  toast.show({
    kind: 'error',
    title: `${taskId}: queued prompt was not sent`,
    message: (result && result.error)
      || 'the server did not accept it — it is back at the front of the queue',
    durationMs: 0,
  });
  return false;
}

export function useBackgroundQueueDrain({ sessions, activeTaskId, agentStatuses }) {
  const drainRef = useRef(null);
  if (!drainRef.current) { drainRef.current = createQueueDrain(); }
  // Tasks with a send in flight. A turn-ending edge that arrives while the
  // previous send is still settling must not start a second one.
  const sendingRef = useRef(new Set());

  // The focused task's chat owns its drain. Dropping it from the engine means
  // it re-seeds when it goes to the background, instead of reading the switch
  // itself as a busy -> idle edge.
  useEffect(() => {
    if (activeTaskId) { drainRef.current.forget(activeTaskId); }
  }, [activeTaskId]);

  useEffect(() => {
    const drain = drainRef.current;
    for (const session of Array.isArray(sessions) ? sessions : []) {
      const taskId = String(session?.task_id || '');
      if (!taskId || taskId === activeTaskId) { continue; }
      const { kind } = deriveAgentStatus(
        session, (agentStatuses && agentStatuses[taskId]) || null, false,
      );
      const decision = drain.observe(taskId, isBusyAgentKind(kind));
      if (decision !== QUEUE_DRAIN_DECISION.DELIVER) { continue; }
      if (sendingRef.current.has(taskId)) { continue; }
      sendingRef.current.add(taskId);
      deliverNextQueued(taskId, session)
        .finally(() => { sendingRef.current.delete(taskId); });
    }
  }, [sessions, activeTaskId, agentStatuses]);
}
