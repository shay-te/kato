// Comments child store — a task's review/diff comments (all repos). One shared
// fetch feeds the file-tree 💬 badges, the diff-pane threads, the editor-pane
// threads, and the chat status tint. Adds the comment MUTATIONS on top of the
// generic engine, including an OPTIMISTIC delete that reconciles from the
// server. PRIVATE: only the parent (../index.js) wires and reads it.

import * as api from '../../../api.js';
import { apiErrorMessage } from '../../../utils/apiError.js';
import { createDataStore } from '../createDataStore.js';

// The engine treats a throw as an error (keeps last data). fetchTaskComments
// returns an ``{ok}`` envelope instead of throwing, so translate a non-ok
// result into a throw and hand back just the comments list (identity parse).
async function fetchComments(taskId) {
  const result = await api.fetchTaskComments(taskId);
  if (!result || !result.ok) {
    throw new Error(apiErrorMessage(result, 'failed to load comments'));
  }
  return Array.isArray(result.body?.comments) ? result.body.comments : [];
}

const base = createDataStore({ fetch: fetchComments, parse: (list) => list, empty: [] });

// Drop a comment AND its whole reply subtree from a list (returns the same
// reference when nothing matched, so applyLocal can no-op).
function removeSubtree(list, commentId) {
  const target = String(commentId || '');
  if (!target) { return list; }
  const drop = new Set([target]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const comment of list) {
      const id = String(comment?.id || '');
      const parentId = String(comment?.parent_id || '');
      if (!drop.has(id) && parentId && drop.has(parentId)) { drop.add(id); grew = true; }
    }
  }
  const next = list.filter((c) => !drop.has(String(c?.id || '')));
  return next.length === list.length ? list : next;
}

// Run a mutation, then reconcile from the server on success so every
// subscriber reflects the new state in one tick. Returns the raw API result
// (callers keep their own toast / spawn handling).
async function mutate(taskId, apiCall) {
  const result = await apiCall();
  if (result && result.ok) { base.load(taskId); }
  return result;
}

export const commentsChild = {
  ...base,
  create: (taskId, payload) => mutate(taskId, () => api.createTaskComment(taskId, payload || {})),
  resolve: (taskId, id) => mutate(taskId, () => api.resolveTaskComment(taskId, id)),
  reopen: (taskId, id) => mutate(taskId, () => api.reopenTaskComment(taskId, id)),
  retry: (taskId, id) => mutate(taskId, () => api.retryTaskComment(taskId, id)),
  edit: (taskId, id, patch) => mutate(taskId, () => api.editTaskComment(taskId, id, patch)),
  markAddressed: (taskId, id, sha = '') => (
    mutate(taskId, () => api.markTaskCommentAddressed(taskId, id, sha))
  ),
  // Optimistic delete: remove the subtree instantly (badge/thread vanish in
  // the same tick), then reconcile — a rejected delete restores the subtree
  // because applyLocal set the dedupe sig to the optimistic state.
  async remove(taskId, commentId) {
    base.applyLocal(taskId, (list) => removeSubtree(list, commentId));
    const result = await api.deleteTaskComment(taskId, commentId);
    base.load(taskId);
    return result;
  },
};
