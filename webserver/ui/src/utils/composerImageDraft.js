// Per-task composer IMAGE-attachment persistence — the companion to the
// text-only draft in composerDraft.js.
//
// Text drafts live in localStorage (small, synchronous). Image attachments are
// base64 data URLs that blow the localStorage quota, so they live in
// IndexedDB (idbStore.js) instead. That makes pasted/dropped images survive
// BOTH a tab switch and a full page reload, matching the text draft's
// durability — previously they were dropped on every tab switch.
//
// We persist only the Anthropic image PART ({media_type, data}); the throwaway
// preview URL is a pure data: URL the caller rebuilds from the part on read, so
// nothing un-serializable (object URLs) is ever stored. Async by nature — the
// composer hydrates after mount.

import { idbGet, idbSet, idbDelete } from './idbStore.js';

export const IMAGE_DRAFT_PREFIX = 'kato.composer.images.';

export function imageDraftKey(taskId) {
  return taskId ? `${IMAGE_DRAFT_PREFIX}${taskId}` : '';
}

// Persist the attachment parts for a task. An empty list removes the entry.
// Best-effort (the underlying idb ops never throw).
export function writeImageDraft(taskId, parts) {
  const key = imageDraftKey(taskId);
  if (!key) { return Promise.resolve(); }
  if (Array.isArray(parts) && parts.length > 0) {
    // Store a plain clone so we never hand IndexedDB a live React object.
    return idbSet(key, parts.map((part) => ({ media_type: part.media_type, data: part.data })));
  }
  return idbDelete(key);
}

// Returns the persisted parts for a task (``[]`` when none / unavailable).
export async function readImageDraft(taskId) {
  const key = imageDraftKey(taskId);
  if (!key) { return []; }
  const parts = await idbGet(key);
  if (!Array.isArray(parts)) { return []; }
  // Defensive: keep only well-formed parts so a corrupt entry can't crash the
  // composer's preview render.
  return parts.filter((p) => p && p.media_type && p.data);
}

export function clearImageDraft(taskId) {
  return writeImageDraft(taskId, []);
}
