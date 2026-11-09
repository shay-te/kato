// Persistence for the "New task" draft's attached files (a spec, a CSV, a
// PDF) — the companion to ``composerImageDraft.js``, one tier earlier.
//
// The New Task pane unmounts whenever the operator switches tabs (it is
// rendered only while that tab is active), so plain component state would
// drop the attachments on every switch while the rest of the draft — kept
// in ``newTaskDraftStore.js`` — survives even a reload. File objects are
// structured-clonable, so unlike the composer's images (which need the
// Anthropic API's base64 shape regardless of storage) these are stored as
// the raw ``File`` objects IndexedDB already knows how to hold — no
// encoding, no size inflation.
//
// There is only ONE new-task draft at a time (unlike the composer, which is
// per EXISTING task), so this uses a single fixed key.

import { idbGet, idbSet, idbDelete } from './idbStore.js';

export const NEW_TASK_ATTACHMENT_DRAFT_KEY = 'kato.newTaskDraft.attachments.v1';

// Best-effort (the underlying idb ops never throw). An empty list removes
// the entry, matching ``composerImageDraft``'s convention.
export function writeNewTaskAttachmentDraft(files) {
  if (Array.isArray(files) && files.length > 0) {
    return idbSet(NEW_TASK_ATTACHMENT_DRAFT_KEY, files);
  }
  return idbDelete(NEW_TASK_ATTACHMENT_DRAFT_KEY);
}

// Returns the persisted files (``[]`` when none / unavailable). Filters to
// well-formed ``File`` instances so a corrupt or stale entry can't crash the
// pane's render.
export async function readNewTaskAttachmentDraft() {
  const files = await idbGet(NEW_TASK_ATTACHMENT_DRAFT_KEY);
  if (!Array.isArray(files)) { return []; }
  return files.filter((file) => file instanceof File);
}

export function clearNewTaskAttachmentDraft() {
  return writeNewTaskAttachmentDraft([]);
}
