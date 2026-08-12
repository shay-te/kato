// Per-operator tab rename.
//
// The tab label is the ticket summary from YouTrack/Jira, which is often long
// and written for the tracker rather than for scanning a strip of tabs. This
// stores a LOCAL display override so the operator can shorten it.
//
// Local on purpose, exactly like ``pinnedTabs``: renaming a tab is a UI
// preference, not a change to the ticket. Writing back to the tracker would
// edit an issue other people read — a far bigger act than relabelling your own
// tab, and not what "rename this tab" implies.
//
// Pure functions only (no React, no DOM beyond the injectable ``storage``
// arg), so the logic stays testable without jsdom — same contract as
// ``pinnedTabs``.

import { readStorageString, writeStorageItem } from './storage.js';
import { parseJsonOr } from './json.js';

export const TAB_NAMES_STORAGE_KEY = 'kato.tabs.names';

// Longer than this and the override defeats its own purpose (the tab
// ellipsises anyway) — and it keeps a paste-accident out of storage.
export const MAX_TAB_NAME_LENGTH = 80;

// ``{taskId: label}`` from storage. Defensive against missing/throwing
// storage, malformed JSON, non-object payloads, and non-string values.
// Returns ``{}`` for every failure mode.
export function readTabNames(storage) {
  const raw = readStorageString(TAB_NAMES_STORAGE_KEY, null, storage);
  const parsed = parseJsonOr(raw, null);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) { return {}; }
  const out = {};
  for (const [taskId, label] of Object.entries(parsed)) {
    const id = String(taskId || '').trim();
    const name = typeof label === 'string' ? label.trim() : '';
    if (!id || !name) { continue; }
    out[id] = name.slice(0, MAX_TAB_NAME_LENGTH);
  }
  return out;
}

// Replace the whole map, sanitised the same way ``readTabNames`` reads it so
// the round-trip is stable.
export function writeTabNames(names, storage) {
  const sanitized = {};
  for (const [taskId, label] of Object.entries(names || {})) {
    const id = String(taskId || '').trim();
    const name = typeof label === 'string' ? label.trim() : '';
    if (!id || !name) { continue; }
    sanitized[id] = name.slice(0, MAX_TAB_NAME_LENGTH);
  }
  writeStorageItem(TAB_NAMES_STORAGE_KEY, JSON.stringify(sanitized), storage);
}

// Set (or, with a blank label, CLEAR) one task's override. Clearing is the
// "reset to the ticket summary" path — storing '' instead would render an
// empty tab, so a blank always means "drop the override".
export function setTabName(names, taskId, label) {
  const id = String(taskId || '').trim();
  if (!id) { return { ...(names || {}) }; }
  const name = String(label == null ? '' : label).trim();
  const next = { ...(names || {}) };
  if (!name) {
    delete next[id];
    return next;
  }
  next[id] = name.slice(0, MAX_TAB_NAME_LENGTH);
  return next;
}

// The label to render: the operator's override when set, else the ticket
// summary. Never returns a blank when a summary exists.
export function tabNameFor(names, taskId, fallback) {
  const id = String(taskId || '').trim();
  const override = id ? (names || {})[id] : '';
  return (typeof override === 'string' && override.trim())
    ? override
    : String(fallback == null ? '' : fallback);
}
