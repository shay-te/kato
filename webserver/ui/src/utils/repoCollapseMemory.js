// Which repos the operator left COLLAPSED, per task, across reloads and task
// switches.
//
// The Files pane's collapse state was plain component state, so switching to
// another task and back — or reloading — re-expanded every repo. On a 26-repo
// task that means scrolling past everything you had deliberately folded away
// to reach the one you were actually working in.
//
// Per TASK, not global: the same repository is the point of one task and
// noise in another, and the pane always looks at exactly one task's
// workspace.
//
// The bounded per-task keyed map lives in ``taskKeyedStore.js``, shared with
// ``taskRepoMemory`` — which keeps a different hint about the same tasks.
// Losing this costs one expanded pane and never any file data.

import { createTaskKeyedStore } from './taskKeyedStore.js';

const store = createTaskKeyedStore('kato.repoCollapse.v1');

// The repo keys left collapsed for ``taskId``. ``[]`` when nothing is stored,
// which is the default pane with every repo expanded.
export function collapsedRepos(taskId) {
  const entry = store.readEntry(taskId);
  const stored = entry && Array.isArray(entry.collapsed) ? entry.collapsed : [];
  return stored.map(String).filter(Boolean);
}

// Record what is collapsed right now; takes the Set the pane holds.
//
// An EMPTY set is stored rather than skipped: "I expanded everything" is a
// choice the operator made on purpose, and dropping it would quietly restore
// whatever they had collapsed before.
export function rememberCollapsedRepos(taskId, collapsed) {
  store.writeEntry(taskId, {
    collapsed: [...(collapsed || [])].map(String).filter(Boolean).sort(),
  });
}
