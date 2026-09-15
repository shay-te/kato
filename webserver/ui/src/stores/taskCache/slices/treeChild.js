// File-tree child store — the task's workspace file tree (all repos), fetched
// from /files and normalized once. Hands out a referentially-stable ``trees``
// so the tree rows + memos bail on an unchanged poll. PRIVATE: only the parent
// (../index.js) wires and reads it.
//
// The SERVER caches the tree (webserver/kato_webserver/file_tree_cache.py). A
// first load — nothing on screen for the task yet: a reload, a first open —
// asks for that copy, so the pane paints at once instead of waiting on a git
// walk of every repo, and the store then follows up with a fresh build.
// Revalidates and polls always build fresh, so the copy only fills the gap.

import * as api from '../../../api.js';
import { normalizeTrees } from '../../../FilesTabHelpers.js';
import { createDataStore } from '../createDataStore.js';

// Tasks whose most recent answer came from the server's cache.
const servedFromCache = new Set();

async function fetchTree(taskId, { revalidating }) {
  const body = await api.fetchFileTree(taskId, { cached: !revalidating });
  // The flag describes THIS response, not the tree, so it is kept out of the
  // payload: the fresh answer that follows then compares byte-equal when
  // nothing changed, and the rows are not re-rendered for it.
  const { cache_hit: cacheHit, ...payload } = body || {};
  if (cacheHit === true) {
    servedFromCache.add(taskId);
  } else {
    servedFromCache.delete(taskId);
  }
  return payload;
}

export const treeChild = createDataStore({
  fetch: fetchTree,
  parse: normalizeTrees,
  empty: [],
  followUp: (taskId) => servedFromCache.has(taskId),
});
