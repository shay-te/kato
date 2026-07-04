// Single source of truth for a task's diff / review comments.
//
// Before this store, THREE surfaces each fetched and cached the same
// comment list independently:
//   * the Files-tree 💬 badges          (FilesTab)
//   * the centre diff pane inline threads (DiffPane)
//   * the chat comment-run status tint   (useCommentStatusMap)
// They polled on different clocks, so a mutation on one surface left the
// others stale — deleting the last comment on a file cleared the inline
// thread instantly but left the file-tree badge lingering for up to 5s
// (its own poll hadn't come round yet).
//
// Now every surface subscribes to THIS store and derives what it needs
// (badge meta / by-file threads / status map) from one shared, always
// current comment array. Mutations go through the store too: it applies
// the change (optimistically for delete), re-fetches to reconcile, and
// emits — so all subscribers re-render in the same tick. No component
// owns comment state any more.
//
// Plain module-level pub/sub keyed by taskId (no React, no context) —
// the ``useTaskComments`` hook is the React adapter, and non-component
// code (mutation handlers) can call the store directly. Mirrors the
// ``toastStore`` pattern.

import {
  createTaskComment,
  deleteTaskComment,
  editTaskComment,
  fetchTaskComments,
  markTaskCommentAddressed,
  reopenTaskComment,
  resolveTaskComment,
  retryTaskComment,
} from '../api.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { createTaskCacheStore } from './taskCacheStore.js';

// Same cadence as the file-tree / changes auto-poll it replaces.
const POLL_INTERVAL_MS = 5000;
const EMPTY_COMMENTS = [];
export const EMPTY_COMMENTS_SNAPSHOT = {
  comments: EMPTY_COMMENTS, loading: false, error: '',
};

// Single-flight fetch of the task's FULL (all-repo) comment list. The
// unfiltered payload is the superset every surface needs — DiffPane
// filters to its selected repo client-side, the badges group by repo,
// the status map keys by file+line — so one request serves all three.
// Only swaps the array identity when the bytes actually changed, so
// subscribers' useMemos keep referential stability across idle polls.
function _load(entry) {
  if (entry.inFlight) { return entry.inFlight; }
  const taskId = entry.taskId;
  entry.inFlight = Promise.resolve()
    .then(() => fetchTaskComments(taskId))
    .catch(() => ({ ok: false, error: 'failed to load comments' }))
    .then((result) => {
      if (result && result.ok) {
        const list = Array.isArray(result.body?.comments)
          ? result.body.comments : [];
        const sig = JSON.stringify(list);
        const changed = sig !== entry.sig;
        if (changed) { entry.comments = list; entry.sig = sig; }
        const wasLoading = entry.loading;
        const hadError = !!entry.error;
        entry.loading = false;
        entry.error = '';
        if (changed || wasLoading || hadError) {
          _base.emit(entry, { comments: entry.comments, loading: false, error: '' });
        }
      } else {
        // Keep the last-known comments on error — a transient failure
        // shouldn't blank the diff / badges — just surface the message.
        const nextError = apiErrorMessage(result, 'failed to load comments');
        const changed = entry.loading || entry.error !== nextError;
        entry.loading = false;
        entry.error = nextError;
        if (changed) {
          _base.emit(entry, { comments: entry.comments, loading: false, error: nextError });
        }
      }
    })
    .finally(() => { entry.inFlight = null; });
  return entry.inFlight;
}

// Drop a comment and its whole reply subtree from the cache immediately,
// so the badge / thread vanish in the same tick as the delete click
// instead of waiting for the reconcile round-trip. A follow-up _load
// restores them if the server rejected the delete.
function _removeLocally(entry, commentId) {
  const target = String(commentId || '');
  if (!target) { return; }
  const drop = new Set([target]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const comment of entry.comments) {
      const id = String(comment?.id || '');
      const parentId = String(comment?.parent_id || '');
      if (!drop.has(id) && parentId && drop.has(parentId)) {
        drop.add(id);
        grew = true;
      }
    }
  }
  const next = entry.comments.filter((c) => !drop.has(String(c?.id || '')));
  if (next.length === entry.comments.length) { return; }
  entry.comments = next;
  entry.sig = JSON.stringify(next);
  _base.emit(entry, { comments: next, loading: entry.loading, error: entry.error });
}

// Per-task pub/sub + poll lifecycle (shared skeleton). This store adds the
// comment fetch (_load), the optimistic delete (_removeLocally), and the
// mutations below.
const _base = createTaskCacheStore({
  intervalMs: POLL_INTERVAL_MS,
  emptySnapshot: EMPTY_COMMENTS_SNAPSHOT,
  createEntryState: () => ({
    comments: EMPTY_COMMENTS,
    loading: true,
    error: '',
    sig: '',
    inFlight: null,
    snapshot: { comments: EMPTY_COMMENTS, loading: true, error: '' },
  }),
  load: _load,
});

// Run a mutation, then reconcile the cache from the server on success so
// every subscriber reflects the new state. Returns the raw API result so
// callers keep their own toast / spawn handling.
async function _mutate(taskId, apiCall) {
  const result = await apiCall();
  if (result && result.ok) {
    const entry = _base.tasks.get(taskId);
    if (entry) { _load(entry); }
  }
  return result;
}

export const commentStore = {
  subscribe: _base.subscribe,
  getSnapshot: _base.getSnapshot,
  poke: _base.poke,

  create(taskId, payload) {
    return _mutate(taskId, () => createTaskComment(taskId, payload || {}));
  },
  resolve(taskId, commentId) {
    return _mutate(taskId, () => resolveTaskComment(taskId, commentId));
  },
  reopen(taskId, commentId) {
    return _mutate(taskId, () => reopenTaskComment(taskId, commentId));
  },
  markAddressed(taskId, commentId, addressedSha = '') {
    return _mutate(taskId, () => markTaskCommentAddressed(taskId, commentId, addressedSha));
  },
  retry(taskId, commentId) {
    return _mutate(taskId, () => retryTaskComment(taskId, commentId));
  },
  edit(taskId, commentId, patch) {
    return _mutate(taskId, () => editTaskComment(taskId, commentId, patch));
  },

  // Delete removes optimistically first (instant badge/thread removal),
  // then reconciles — restoring the subtree if the server rejected it.
  async remove(taskId, commentId) {
    const entry = _base.tasks.get(taskId);
    if (entry) { _removeLocally(entry, commentId); }
    const result = await deleteTaskComment(taskId, commentId);
    if (entry) { _load(entry); }
    return result;
  },
};
