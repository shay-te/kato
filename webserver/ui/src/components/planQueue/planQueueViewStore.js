// "Open the plan board of task X" — from the task toolbar or the plan pane —
// and "task X's plan just changed".
//
// Tiny pub/subs instead of callbacks threaded through the header: App opens
// the board as a tab in the centre strip (``planQueueTab``), and every
// ``usePlanQueueSummary`` reads again when its task's plan changed, so the
// toolbar button shows a split the moment it starts instead of on its next
// poll.

import { createPubSub } from '../../stores/pubsub.js';

let request = { taskId: '', label: '', seq: 0 };
const opens = createPubSub(() => request);

export const planQueueView = {
  // ``label`` is the tab's name ("Split plan" / "Plan board").
  open(taskId, label = '') {
    request = { taskId: String(taskId || ''), label: String(label || ''), seq: request.seq + 1 };
    opens.emit();
  },
  // Fires once on subscribe with the current request (seq 0 = none yet).
  subscribe: opens.subscribe,
};

let change = { taskId: '', seq: 0 };
const changes = createPubSub(() => change);

export const planQueueChanges = {
  notify(taskId) {
    change = { taskId: String(taskId || ''), seq: change.seq + 1 };
    changes.emit();
  },
  // Fires once on subscribe with the last change (seq 0 = none yet).
  subscribe: changes.subscribe,
};
