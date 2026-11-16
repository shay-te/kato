// "These tasks were deleted" — said by whichever delete ran, heard by App,
// which clears everything the UI keeps for each one in ONE place
// (``purgeForgottenTask``: its tabs — files, plan, review loop — its caches
// and its drafts).
//
// The deletes live in different corners of the UI: a task's ✕ (and Hand
// over's "also delete it from my tasks", which runs the same delete), the bulk
// delete, a plan main task taking its sub-tasks with it, and the plan board's
// card delete. Only the first two reached App's purge, so a deleted task's
// review loop tab, and every tab of a sub-task deleted with its plan or from
// the board, stayed open (2026-11-16: "I handed over/deleted a task, but the
// UI still shows its review loop tab").
//
// No replay on subscribe: a delete is an event, not a state a late
// subscriber should act on again.

import { createPubSub } from './pubsub.js';

let announced = [];
const pubsub = createPubSub(() => announced, { replay: false });

export const forgottenTasks = {
  announce(taskIds) {
    const ids = [...new Set((taskIds || []).map((id) => String(id || '')).filter(Boolean))];
    if (!ids.length) { return; }
    announced = ids;
    pubsub.emit();
  },
  subscribe: pubsub.subscribe,
};

// The tasks one delete took away, from ``forgetTaskWorkspace``'s answer: the
// task itself when the delete went through, and every sub-task its plan took
// with it that really was forgotten — one that was not is still there, and
// keeps its tabs. A failed delete can still have taken sub-tasks first.
export function forgottenByDelete(taskId, result) {
  const subTasks = (result && result.body && result.body.sub_tasks) || [];
  return [
    ...(result && result.ok && taskId ? [taskId] : []),
    ...subTasks.filter((entry) => entry && entry.forgotten).map((entry) => entry.task_id),
  ];
}
