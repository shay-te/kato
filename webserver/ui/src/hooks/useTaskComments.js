import { useEffect, useState } from 'react';
import { commentStore, EMPTY_COMMENTS_SNAPSHOT } from '../stores/commentStore.js';

// React adapter for the single-source-of-truth ``commentStore``. Every
// surface that shows a task's diff / review comments (the Files-tree
// badges, the centre diff pane, the plain-file editor pane, the chat
// comment-run status tint) reads them through this hook, so they all
// render the same always-current list and a mutation on one is seen
// instantly by the others.
//
// Returns ``{ comments, loading, error }``. ``enabled: false`` opts a
// surface out entirely (no subscription, no poll, empty snapshot) — the
// chat uses it so an ordinary transcript with no comment-run prompt
// issues no comment requests at all.
export function useTaskComments(taskId, { enabled = true } = {}) {
  const active = !!(taskId && enabled);
  // Stamp the snapshot with the task it belongs to so a task switch
  // drops the previous task's comments in the SAME render (read the
  // store for the new task — empty until its own fetch lands) instead of
  // briefly showing the old task's threads until the effect re-runs.
  const [state, setState] = useState(() => ({
    taskId: active ? taskId : '',
    snapshot: active ? commentStore.getSnapshot(taskId) : EMPTY_COMMENTS_SNAPSHOT,
  }));
  useEffect(() => {
    if (!active) {
      setState({ taskId: '', snapshot: EMPTY_COMMENTS_SNAPSHOT });
      return undefined;
    }
    // subscribe() fires once immediately with the current snapshot, so
    // late mounters render the cached list without waiting for a poll.
    return commentStore.subscribe(taskId, (snapshot) => setState({ taskId, snapshot }));
  }, [taskId, active]);
  if (!active) { return EMPTY_COMMENTS_SNAPSHOT; }
  if (state.taskId !== taskId) { return commentStore.getSnapshot(taskId); }
  return state.snapshot;
}
