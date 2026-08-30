// Per-task view-data cache — the ONE public entry point for the app.
//
// Architecture: a PARENT orchestrator (createTaskCache) over PRIVATE per-type
// child stores (one file per data type under ./slices). App code imports only
// from here — the read hooks (useTaskDiff, useTaskComments, …) and the
// lifecycle/mutation actions. It never touches a child store. The parent is
// the single place that knows the cross-type rules: LRU retention of the last
// N viewed tasks, one poller on the active task, and eviction fan-out.
//
// New child stores are wired in here as each data type is migrated.

import { useEffect } from 'react';

import { agentStatusStore } from '../agentStatusStore.js';
import { isAgentActive } from '../../utils/agentStatus.js';
import { createTaskCache } from './createTaskCache.js';
import { diffChild } from './slices/diffChild.js';
import { commentsChild } from './slices/commentsChild.js';
import { treeChild } from './slices/treeChild.js';
import { publishChild } from './slices/publishChild.js';
import { pullRequestChild } from './slices/pullRequestChild.js';

const RETAIN = 5;
const POLL_INTERVAL_MS = 5000;
// Cadence once the task's agent is asleep. Each polled tick costs ~13 git
// subprocesses PER REPO on the server (``/files`` walks the tree, resolves the
// diff base and scans for conflicts; ``/diff`` checks out the branch, resolves
// the base, reads the remote and diffs) — about 150 a minute per repo, almost
// all of it re-deriving a byte-identical answer the store then discards on an
// unchanged signature. A sleeping agent cannot be the thing that changed them.
const IDLE_POLL_INTERVAL_MS = 30000;

// PRIVATE children — one single-concern store per data type.
const children = {
  diff: diffChild,
  comments: commentsChild,
  tree: treeChild,
  publish: publishChild,
  pullRequest: pullRequestChild,
};

// Types the active-task poller revalidates each 5s tick (SWR background
// refresh). publish/pullRequest are EXCLUDED (no polling — the PR lookup is a
// live provider call that tripped rate limits); they revalidate on activate +
// on button actions only.
const POLLED_TYPES = ['diff', 'comments', 'tree'];

const cache = createTaskCache({
  children,
  retain: RETAIN,
  polledTypes: POLLED_TYPES,
  intervalMs: POLL_INTERVAL_MS,
  idleIntervalMs: IDLE_POLL_INTERVAL_MS,
  // Liveness comes from the SAME place every other agent-status surface reads
  // it — the active task's SessionDetail publishes its live SSE state to
  // agentStatusStore, and utils/agentStatus.js owns the derivation. Re-deriving
  // "is it working" from the polled session fields here would be a second
  // definition of agent liveness, which is the bug that store exists to
  // prevent.
  isTaskLive: (taskId) => isAgentActive(agentStatusStore.getStatus(taskId)),
});

export const {
  setActiveTask,
  forgetTask,
  revalidate,
  registerOnEvict,
} = cache;

// Test-only: reset the retained singleton between cases so data / in-flight
// promises don't bleed across tests (the old ref-counted stores tore down on
// unmount; these don't). We SELF-REGISTER the reset on a global set that
// vitest's global ``afterEach`` drains — so only test files that actually load
// this store get reset, with no forced import in the setup file. No-op in prod.
export const resetTaskCache = cache.reset;
if (typeof globalThis !== 'undefined') {
  (globalThis.__TASK_CACHE_RESETS__ = globalThis.__TASK_CACHE_RESETS__ || new Set())
    .add(cache.reset);
}

// ---- Public read hooks (the ONLY way app code reads task data) ----

// A task with no cached entry yet reads as LOADING (status 'idle') so a pane
// shows its spinner for the frame before the fetch lands — never a false
// "empty". Load-on-read: each hook ensures its type is fetched whenever a
// component reads it (idempotent — single-flight coalesces with the active-
// task revalidate/poll).

export function useTaskDiff(taskId) {
  useEffect(() => {
    if (taskId) { revalidate(taskId, ['diff']); }
  }, [taskId]);
  return diffChild.use(taskId, (sl) => ({
    repoDiffs: sl.data,
    loading: sl.status === 'idle' || sl.status === 'loading',
    error: sl.error,
  }));
}

// FilesTab uses 'loading' | 'ready' | 'error' — map the store's 'idle' (no
// entry yet) to 'loading' so the first visit shows the spinner.
export function useTaskTree(taskId) {
  useEffect(() => {
    if (taskId) { revalidate(taskId, ['tree']); }
  }, [taskId]);
  return treeChild.use(taskId, (sl) => ({
    trees: sl.data,
    status: sl.status === 'idle' ? 'loading' : sl.status,
    error: sl.error,
  }));
}

// Git-button state (local, fast). ``ready``/``error`` come ONLY from this
// child — a failing PR fetch (below) must never disable the git buttons.
export function useTaskPublishState(taskId) {
  useEffect(() => {
    if (taskId) { revalidate(taskId, ['publish']); }
  }, [taskId]);
  return publishChild.use(taskId, (sl) => ({
    hasWorkspace: sl.data.hasWorkspace,
    hasChangesToPush: sl.data.hasChangesToPush,
    ready: sl.status === 'ready',
    error: sl.status === 'error',
  }));
}

// PR-existence + open-PR link. Best-effort (live provider call), kept off the
// git-button path so its loading/error never gates the buttons.
export function useTaskPullRequestState(taskId) {
  useEffect(() => {
    if (taskId) { revalidate(taskId, ['pullRequest']); }
  }, [taskId]);
  return pullRequestChild.use(taskId, (sl) => ({
    hasPullRequest: sl.data.hasPullRequest,
    pullRequestUrls: sl.data.pullRequestUrls,
  }));
}

// ``enabled: false`` opts a surface out entirely (no fetch, empty snapshot) —
// the chat uses it so an ordinary transcript issues no comment requests.
const EMPTY_COMMENTS_SNAPSHOT = { comments: [], loading: false, error: '' };

export function useTaskComments(taskId, { enabled = true } = {}) {
  const active = !!(taskId && enabled);
  useEffect(() => {
    if (active) { revalidate(taskId, ['comments']); }
  }, [active, taskId]);
  const snapshot = commentsChild.use(active ? taskId : '', (sl) => ({
    comments: sl.data,
    loading: sl.status === 'idle' || sl.status === 'loading',
    error: sl.error,
  }));
  return active ? snapshot : EMPTY_COMMENTS_SNAPSHOT;
}

// ---- Comment mutations (single source of truth for comment writes) ----
export const createComment = commentsChild.create;
export const resolveComment = commentsChild.resolve;
export const reopenComment = commentsChild.reopen;
export const retryComment = commentsChild.retry;
export const removeComment = commentsChild.remove;
export const editComment = commentsChild.edit;
export const markCommentAddressed = commentsChild.markAddressed;
