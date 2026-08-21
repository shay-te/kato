// Per-task chat-input draft persistence.
//
// SessionDetail keys MessageForm on ``activeTaskId``, so React unmounts
// the composer when the operator switches tabs and the in-memory
// textarea value is dropped. Mirroring every keystroke to localStorage
// (and reading it back on mount) is what makes the draft survive
// tab switches — the same behavior VS Code's chat composer has.
//
// Pure functions only (no React, no DOM imports beyond the injectable
// ``storage`` arg). Keeps the module unit-testable in node:test without
// jsdom.

import { readStorageString, writeStorageItem } from './storage.js';

export const DRAFT_STORAGE_PREFIX = 'kato.composer.draft.';

export function draftStorageKey(taskId) {
  return taskId ? `${DRAFT_STORAGE_PREFIX}${taskId}` : '';
}

export const COMMENT_DRAFT_PREFIX = 'kato.comment.draft.';

// Draft-storage key for an inline review-comment form. ``lineSegment`` is
// the gutter line key (or the literal 'file' for the file-level form);
// ``replyTo`` is the id of the comment being replied to, or falsy for a
// top-level (root) comment. Centralised here so the gutter form and the
// file-level form can't drift in prefix/separator and silently split a draft.
export function commentDraftKey(taskId, repoId, path, lineSegment, replyTo) {
  return `${COMMENT_DRAFT_PREFIX}${taskId}|${repoId}|${path}|${lineSegment}|${replyTo || 'root'}`;
}

export const ASK_QUESTION_DRAFT_PREFIX = 'kato.askquestion.draft.';

// Draft-storage key for one AskUserQuestion answer form, keyed by the
// permission request id. The form is inside a modal that other UI can tear
// down (a poll blip, a second ask arriving, a reload), and re-picking every
// radio button plus retyping the "Other" text is exactly the work nobody
// wants to redo — so the partial answer is mirrored here.
export function askQuestionDraftKey(requestId) {
  return requestId ? `${ASK_QUESTION_DRAFT_PREFIX}${requestId}` : '';
}

// Generic key-based variants. Used by callers that own their own
// key shape (e.g. CommentForm: ``comment.<task>.<repo>.<path>.<line>.<replyTo>``).
// The ``taskId``-shaped helpers below are thin wrappers that just
// supply the chat-composer prefix.
export function readDraftByKey(key, storage) {
  if (!key) { return ''; }
  // ``readStorageString`` swallows the private-browsing / quota /
  // disabled-storage throws and falls back to '' — same blank-draft
  // behavior the composer needs.
  return readStorageString(key, '', storage);
}

export function writeDraftByKey(key, value, storage) {
  if (!key) { return; }
  // Truthy value → setItem; falsy → removeItem. A failed write is
  // swallowed (best-effort): the next mount shows a blank composer,
  // not a crash.
  writeStorageItem(key, value, storage);
}

export function readDraft(taskId, storage) {
  return readDraftByKey(draftStorageKey(taskId), storage);
}

export function writeDraft(taskId, value, storage) {
  writeDraftByKey(draftStorageKey(taskId), value, storage);
}

export function clearDraft(taskId, storage) {
  writeDraft(taskId, '', storage);
}

// Per-task ultracode chip state. Same shape as the text draft: localStorage
// keyed by taskId, so the toggle survives tab switches and page reloads and
// each task keeps its own preference.
export const ULTRACODE_STORAGE_PREFIX = 'kato.composer.ultracode.';

export function ultracodeStorageKey(taskId) {
  return taskId ? `${ULTRACODE_STORAGE_PREFIX}${taskId}` : '';
}

// ``fallback`` is the value for a task that has never been toggled — the
// Settings → Chat default. Note the stored strings are 'on'/'off', NOT
// 'on'/'': an explicit OFF has to be distinguishable from "never chose", or
// turning the default on would silently re-enable the chip on every task the
// operator had deliberately turned it off for.
export function readUltracode(taskId, storage, fallback = false) {
  const stored = readDraftByKey(ultracodeStorageKey(taskId), storage);
  if (stored === 'on') { return true; }
  if (stored === 'off') { return false; }
  return !!fallback;
}

export function writeUltracode(taskId, on, storage) {
  writeDraftByKey(ultracodeStorageKey(taskId), on ? 'on' : 'off', storage);
}
