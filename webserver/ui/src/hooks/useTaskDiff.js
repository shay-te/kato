import { useEffect, useState } from 'react';
import { diffStore, EMPTY_DIFF_SNAPSHOT } from '../stores/diffStore.js';

// React adapter for the single-source-of-truth ``diffStore``. Both the
// file-tree diff badges (FilesTab) and the centre diff pane read the
// task's changeset through this hook, so they render the same
// always-current parsed diff and never drift out of sync.
//
// Returns ``{ repoDiffs, loading, error }``. Pass a falsy taskId to opt
// out (empty snapshot, no subscription).
const LOADING_SNAPSHOT = { repoDiffs: [], loading: true, error: '' };

// A task with no cached entry yet reads as LOADING (not the neutral empty
// snapshot) so the pane shows "Computing diff…" for the frame before
// subscribe() fires — never a false "No changes".
function snapshotFor(taskId) {
  const snapshot = diffStore.getSnapshot(taskId);
  return snapshot === EMPTY_DIFF_SNAPSHOT ? LOADING_SNAPSHOT : snapshot;
}

export function useTaskDiff(taskId) {
  const active = !!taskId;
  const [state, setState] = useState(() => ({
    taskId: active ? taskId : '',
    snapshot: active ? snapshotFor(taskId) : EMPTY_DIFF_SNAPSHOT,
  }));
  useEffect(() => {
    if (!active) {
      setState({ taskId: '', snapshot: EMPTY_DIFF_SNAPSHOT });
      return undefined;
    }
    return diffStore.subscribe(taskId, (snapshot) => setState({ taskId, snapshot }));
  }, [taskId, active]);
  // On a task switch, don't return the previous task's changeset for a
  // render — read the store for the new task (loading until its fetch lands).
  if (!active) { return EMPTY_DIFF_SNAPSHOT; }
  if (state.taskId !== taskId) { return snapshotFor(taskId); }
  return state.snapshot;
}
