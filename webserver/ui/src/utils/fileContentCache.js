// Module-level cache for EditorPane's fetched file content, keyed by
// (taskId, absolutePath). Deliberately NOT invalidated by a client-side
// heuristic (e.g. "the agent hasn't emitted a tool_result since") --
// the backend can change a file on disk through paths the frontend
// never observes an event for (a background branch sync, a merge, an
// operator editing the file directly outside kato), so any purely
// optimistic client invalidation signal can go stale silently. Instead
// every read sends the cache entry's known mtime back to the server;
// the server is the single source of truth and only skips re-sending
// the file's full content when ITS OWN stat() confirms the mtime is
// still current. See api.js's fetchFileContent and the
// /api/sessions/<task>/file backend route (returns `{unchanged: true}`
// on a match instead of re-reading + re-transferring the file).
//
// This still meaningfully speeds up "switch away from a task with a
// file tab open, switch back" -- the round trip becomes a cheap
// server-side stat() instead of a full disk read + decode + content
// transfer -- while never risking stale content.
//
// Pure Map operations, no React -- testable without jsdom, mirrors the
// TASK_STREAM_CACHE pattern in hooks/useSessionStream.js.

const MAX_ENTRIES = 50;

const CACHE = new Map();

export function fileContentCacheKey(taskId, absolutePath) {
  // '::' (not a plain space) -- Windows paths routinely contain spaces
  // in directory names, and a space-joined key could theoretically
  // collide across different (taskId, path) splits.
  return `${taskId}::${absolutePath}`;
}

export function readCachedFileContent(taskId, absolutePath) {
  const key = fileContentCacheKey(taskId, absolutePath);
  return CACHE.has(key) ? CACHE.get(key) : null;
}

export function writeCachedFileContent(taskId, absolutePath, value) {
  const key = fileContentCacheKey(taskId, absolutePath);
  // Re-inserting an existing key would keep its OLD insertion-order
  // position in a Map, defeating the "evict oldest" logic below --
  // drop it first so a re-write always counts as freshest.
  CACHE.delete(key);
  CACHE.set(key, value);
  while (CACHE.size > MAX_ENTRIES) {
    const oldestKey = CACHE.keys().next().value;
    CACHE.delete(oldestKey);
  }
}

// Drop every cached entry for a task -- used when a task is forgotten,
// so a later re-adopt under the same id can't serve another task's
// leftover file content.
export function clearFileContentCacheForTask(taskId) {
  const prefix = `${taskId}::`;
  for (const key of CACHE.keys()) {
    if (key.startsWith(prefix)) { CACHE.delete(key); }
  }
}

export function _clearFileContentCacheForTests() {
  CACHE.clear();
}
