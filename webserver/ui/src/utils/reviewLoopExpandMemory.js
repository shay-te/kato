// Which rounds and self-check turns the operator left OPEN or CLOSED in a
// task's review loop view, across reloads and tab switches.
//
// Per task, for its LATEST loop only: the entry carries the loop's id, and an
// entry made for any other loop reads as nothing. "Run again" forgets it
// (``forgetLoopExpansion``), so a finished loop's choices neither linger nor
// leak into the next loop.
//
// The bounded per-task keyed map is ``taskKeyedStore.js`` — the same one the
// Files pane's repo memories use (``repoCollapseMemory``, ``taskRepoMemory``).
// Losing this costs one round re-opened, never any loop data.

import { createTaskKeyedStore } from './taskKeyedStore.js';

const store = createTaskKeyedStore('kato.reviewLoopExpanded.v1');

// ``{ "1": true, … }`` with anything but true / false dropped.
function flags(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) { return {}; }
  return Object.fromEntries(
    Object.entries(value).filter(([, open]) => typeof open === 'boolean'),
  );
}

// ``{ rounds, selfChecks }`` — item number → open — remembered for this loop;
// both empty when nothing is, or what is belongs to another loop.
export function loopExpansion(taskId, loopId) {
  const entry = store.readEntry(taskId);
  if (!loopId || !entry || entry.loopId !== loopId) { return { rounds: {}, selfChecks: {} }; }
  return { rounds: flags(entry.rounds), selfChecks: flags(entry.selfChecks) };
}

// Record what is open now — ``{ rounds }`` and / or ``{ selfChecks }``; the
// part not given keeps what this loop remembered.
export function rememberLoopExpansion(taskId, loopId, { rounds, selfChecks } = {}) {
  if (!loopId) { return; }
  const current = loopExpansion(taskId, loopId);
  store.writeEntry(taskId, {
    loopId,
    rounds: flags(rounds ?? current.rounds),
    selfChecks: flags(selfChecks ?? current.selfChecks),
  });
}

// A new loop is starting on this task: drop what the last one remembered.
export function forgetLoopExpansion(taskId) {
  store.writeEntry(taskId, {});
}
