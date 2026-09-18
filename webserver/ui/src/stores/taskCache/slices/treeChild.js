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
//
// Every fetch after the first carries the ETag of the tree on screen, so the
// usual answer to a poll is an empty 304: nothing crosses the wire and nothing
// is re-parsed. A tree that HAS changed arrives gzipped.

import * as api from '../../../api.js';
import { normalizeTrees } from '../../../FilesTabHelpers.js';
import { createDataStore, signed } from '../createDataStore.js';

// Tasks whose most recent answer came from the server's cache.
const servedFromCache = new Set();

async function fetchTree(taskId, { revalidating, signature }) {
  const result = await api.fetchFileTree(taskId, {
    cached: !revalidating,
    signature,
  });
  if (result.unchanged) {
    // The server confirmed the tree already on screen. Handing back the SAME
    // signature with no payload is how the store is told to keep what it has.
    servedFromCache.delete(taskId);
    return signed(null, signature);
  }
  // Whether this answer came from the cache describes the RESPONSE, not the
  // tree, so it arrives as a header rather than in the payload: the body then
  // stays byte-identical to the fresh build of the same tree, which is what
  // lets the follow-up be a 304.
  if (result.cacheHit) {
    servedFromCache.add(taskId);
  } else {
    servedFromCache.delete(taskId);
  }
  // No ETag (something stripped it) → unsigned, and the store falls back to
  // comparing the payload's own bytes.
  return result.etag ? signed(result.payload, result.etag) : result.payload;
}

export const treeChild = createDataStore({
  fetch: fetchTree,
  parse: normalizeTrees,
  empty: [],
  followUp: (taskId) => servedFromCache.has(taskId),
});
