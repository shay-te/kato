// FEATURE: queued prompts run on EVERY task, not just the one on screen.
//
// Operator: "feels like i need to move to the tab to make the agent start..."
//
// The steer queue is client-side (utils/queuedMessagesStore.js) and its only
// drain lived inside SessionDetail, which App mounts for the ACTIVE task
// alone. Queue a prompt, switch tabs, and when that task's turn ended nothing
// delivered the next one — until the operator opened the tab again.
//
// This file is the decision half: given a stream of "is this task busy?"
// observations, WHEN should the next queued prompt go? It holds no queue, sends
// nothing and knows nothing about React — so it is testable as plain data, and
// the one rule the operator cares most about is written down once:
//
//   ONE PROMPT PER TURN. "unless i steer it, kato will run one prompt at a
//   time." Six queued prompts once drained in seconds because a flag falling
//   was read as a turn ending.
//
// It is an EDGE detector, and two properties do all the work:
//
//   1. The first observation of a task only SEEDS. A task that is idle when
//      the app loads has not "just finished" — delivering then would fire
//      every queue in the app at once on reload.
//   2. Only busy -> idle is an ending. Idle -> idle is not, so the stale poll
//      reading right after a send (the server has not registered it yet) can
//      never release a second prompt: the reading that released the first one
//      already recorded "idle", and nothing more goes until a turn is seen
//      running and then stopping.
//
// An earlier version also carried a "delivered, awaiting start" latch.
// Mutation testing showed it could never change a decision — property 2
// already covers that case — so it was removed rather than kept as a guard
// that only looked protective.
//
// Busy-ness is an INPUT. Callers derive it through utils/agentStatus.js —
// the single agent-status derivation — and never from ``session.working``.

export const QUEUE_DRAIN_DECISION = Object.freeze({
  DELIVER: 'deliver',
  WAIT: 'wait',
});

export function createQueueDrain() {
  // taskId -> was it busy at the last observation?
  const lastBusy = new Map();

  // Feed one observation. Returns DELIVER exactly on a busy -> idle edge.
  function observe(taskId, busy) {
    if (!taskId) { return QUEUE_DRAIN_DECISION.WAIT; }
    const isBusy = !!busy;
    const seen = lastBusy.has(taskId);
    const wasBusy = lastBusy.get(taskId) === true;
    lastBusy.set(taskId, isBusy);
    if (!seen) { return QUEUE_DRAIN_DECISION.WAIT; }
    return wasBusy && !isBusy ? QUEUE_DRAIN_DECISION.DELIVER : QUEUE_DRAIN_DECISION.WAIT;
  }

  // Stop tracking a task — it became the focused one (its chat owns the drain
  // while mounted) or was forgotten. It re-seeds when next observed, so
  // leaving a tab never reads as that task's turn ending.
  function forget(taskId) {
    lastBusy.delete(taskId);
  }

  return { observe, forget };
}
