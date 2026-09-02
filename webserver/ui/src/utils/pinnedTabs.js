// Per-operator pinned-task persistence.
//
// Pinned tabs sort to the LEFT of the strip and scroll with everything else.
// (They used to be held there with ``position: sticky``; that could not work
// once the pinned cluster was wider than the strip, so the tabs piled up on
// each other. Ordering alone gives the same result with no way to overlap.)
// The operator pins from a small button inside the tab pill; toggle is
// purely client-side because it's a UI preference, not state the
// backend needs to know about (mirrors the composer-draft pattern).
//
// Ordering: pinned tabs render in the order they were pinned — the
// first task pinned sits leftmost. Clicking the pin on an ALREADY-pinned
// task unpins it; the ordering within the pinned group is changed by
// dragging, not by re-clicking.
//
// Pure functions only (no React, no DOM beyond the injectable
// ``storage`` arg). Tests pass a Map-backed fake so the logic
// stays exercisable without jsdom.

import { readStorageString, writeStorageItem } from './storage.js';
import { parseJsonOr } from './json.js';

export const PINNED_TABS_STORAGE_KEY = 'kato.tabs.pinned';

// The operator's manual left-to-right order for UNPINNED task tabs.
//
// Separate from the pinned list because the two are stored differently by
// nature: pin order IS the pinned array, while unpinned tabs otherwise just
// follow whatever order the server returned. Dragging one has to record that
// somewhere, or the next poll would put it straight back.
export const TAB_ORDER_STORAGE_KEY = 'kato.tabs.order';

// Read the pinned-task-id list from storage. Defensive against:
// missing storage, missing key, malformed JSON, non-array payload,
// non-string entries (filtered out), and duplicates (dedup, keeping
// first occurrence). Returns ``[]`` for any failure mode.
export function readPinnedIds(storage) {
  // Unavailable / throwing storage → null fallback, which is not an
  // array and falls into the ``[]`` return below — same as the old
  // explicit no-store / catch returns.
  const raw = readStorageString(PINNED_TABS_STORAGE_KEY, null, storage);
  const parsed = parseJsonOr(raw, null);
  if (!Array.isArray(parsed)) { return []; }
  const seen = new Set();
  const out = [];
  for (const entry of parsed) {
    const id = typeof entry === 'string' ? entry.trim() : '';
    if (!id || seen.has(id)) { continue; }
    seen.add(id);
    out.push(id);
  }
  return out;
}

// Replace the pinned-task-id list. Filters non-strings / blanks /
// dupes the same way readPinnedIds does so the round-trip is stable.
export function writePinnedIds(ids, storage) {
  const seen = new Set();
  const sanitized = [];
  for (const entry of ids || []) {
    const id = typeof entry === 'string' ? entry.trim() : '';
    if (!id || seen.has(id)) { continue; }
    seen.add(id);
    sanitized.push(id);
  }
  // ``JSON.stringify`` of an array is always a truthy string, so this
  // always setItem's. Quota / disabled-storage throws are swallowed —
  // pinning is a convenience; losing it beats crashing the strip.
  writeStorageItem(PINNED_TABS_STORAGE_KEY, JSON.stringify(sanitized), storage);
}

export function isPinned(taskId, ids) {
  if (!taskId) { return false; }
  return (ids || []).includes(taskId);
}

// Toggle the pinned state for ``taskId``. Returns the NEW id list
// (caller can re-render with it AND persist via writePinnedIds).
// Pinning a task appends it to the end of the pinned list (rightmost
// pinned position). Unpinning removes it. Returns a new array — never
// mutates the input.
export function togglePinned(taskId, ids) {
  const id = typeof taskId === 'string' ? taskId.trim() : '';
  if (!id) { return [...(ids || [])]; }
  const current = [...(ids || [])];
  const idx = current.indexOf(id);
  if (idx >= 0) {
    current.splice(idx, 1);
    return current;
  }
  current.push(id);
  return current;
}

export function readTabOrder(storage) {
  const raw = readStorageString(TAB_ORDER_STORAGE_KEY, null, storage);
  if (!raw) { return []; }
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.map((id) => String(id || '').trim()).filter(Boolean)
      : [];
  } catch (_err) {
    return [];
  }
}

export function writeTabOrder(ids, storage) {
  const sanitized = (Array.isArray(ids) ? ids : [])
    .map((id) => String(id || '').trim())
    .filter(Boolean);
  writeStorageItem(TAB_ORDER_STORAGE_KEY, JSON.stringify(sanitized), storage);
  return sanitized;
}

// Order ``sessions`` so pinned tasks come first (in pinned order)
// and everything else preserves its original order. Pinned ids that
// don't match any session are silently ignored (stale pin from a
// deleted task).
//
// ``manualOrder`` is the operator's own left-to-right arrangement of the
// UNPINNED tabs. Tasks it does not mention keep their server order and sort
// after the ones it does — so a task that appears while the operator has a
// custom arrangement lands at the end rather than shuffling everything they
// placed by hand.
export function orderByPinned(sessions, pinnedIds, manualOrder) {
  if (!Array.isArray(sessions) || sessions.length === 0) { return []; }
  if (!Array.isArray(pinnedIds) || pinnedIds.length === 0) {
    // Still apply the manual arrangement: nothing being pinned is the COMMON
    // case, and returning early here meant a drag-reorder was computed,
    // persisted, and then silently discarded on the very next render.
    return applyManualOrder([...sessions], manualOrder);
  }
  const byId = new Map();
  for (const session of sessions) {
    const id = String(session?.task_id || '').trim();
    if (id) { byId.set(id, session); }
  }
  const pinnedSet = new Set();
  const pinned = [];
  for (const id of pinnedIds) {
    const session = byId.get(id);
    if (session && !pinnedSet.has(id)) {
      pinned.push(session);
      pinnedSet.add(id);
    }
  }
  const rest = sessions.filter(
    (s) => !pinnedSet.has(String(s?.task_id || '').trim()),
  );
  return [...pinned, ...applyManualOrder(rest, manualOrder)];
}

function applyManualOrder(sessions, manualOrder) {
  if (!Array.isArray(manualOrder) || manualOrder.length === 0) {
    return sessions;
  }
  const rank = new Map();
  manualOrder.forEach((id, index) => {
    if (!rank.has(id)) { rank.set(id, index); }
  });
  // A stable sort with unranked tasks pushed to the end. Not a filter+concat:
  // that would drop any task the manual list does not mention, and the list
  // goes stale the moment a task is added.
  return [...sessions].sort((a, b) => {
    const left = rank.has(String(a?.task_id || '').trim())
      ? rank.get(String(a.task_id).trim()) : Number.MAX_SAFE_INTEGER;
    const right = rank.has(String(b?.task_id || '').trim())
      ? rank.get(String(b.task_id).trim()) : Number.MAX_SAFE_INTEGER;
    return left - right;
  });
}
