import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';

// Whether the tasks menu (the rail of tasks on the left, Chrome-style) is
// EXPANDED. The rail itself is always there, showing each task's code;
// hovering it (or the kato logo at its top) expands it over the page, and
// leaving it collapses it again.
//
// A CLICK on the kato logo PINS it open instead (``toggleTaskMenuPinned``): it
// stays expanded whatever the pointer does, docked — the page moves aside
// rather than lying under it — and its width can be dragged
// (``setTaskMenuWidth``). Both are remembered per browser.
//
// Both waits are short on purpose:
//   - the expand waits ``TASK_MENU_OPEN_DELAY_MS``: the rail sits at the
//     screen's edge, and a pointer only passing over it must not throw a
//     panel over the page. The kato logo expands at once — pointing at it is
//     asking.
//   - the collapse waits ``TASK_MENU_CLOSE_GRACE_MS``: a pointer that slips
//     off the edge for a moment must not snap it shut. Entering again in time
//     keeps it open.

export const TASK_MENU_OPEN_DELAY_MS = 150;
export const TASK_MENU_CLOSE_GRACE_MS = 220;

export const TASK_MENU_WIDTH_DEFAULT = 360;
export const TASK_MENU_WIDTH_MIN = 240;
export const TASK_MENU_WIDTH_MAX = 640;
const PINNED_KEY = 'kato.taskMenu.pinned.v1';
const WIDTH_KEY = 'kato.taskMenu.width.v1';

let open = false;
let pinned = readStored(PINNED_KEY) === 'true';
let width = clampWidth(Number(readStored(WIDTH_KEY)) || TASK_MENU_WIDTH_DEFAULT);
let timer = null;
// Expanded = hovered open OR pinned: a pinned menu ignores the pointer.
const pubsub = createPubSub(() => open || pinned);
const pinnedPubsub = createPubSub(() => pinned);
const widthPubsub = createPubSub(() => width);

function set(next) {
  const wasExpanded = open || pinned;
  open = next;
  if ((open || pinned) !== wasExpanded) { pubsub.emit(); }
}

function cancelPending() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

function setSoon(next, delayMs) {
  cancelPending();
  timer = setTimeout(() => {
    timer = null;
    set(next);
  }, delayMs);
}

export function openTaskMenu() {
  cancelPending();
  set(true);
}

// Already open: stays open (and a pending collapse is called off).
export function openTaskMenuSoon() {
  if (open) {
    cancelPending();
    return;
  }
  setSoon(true, TASK_MENU_OPEN_DELAY_MS);
}

export function closeTaskMenuSoon() {
  setSoon(false, TASK_MENU_CLOSE_GRACE_MS);
}

export function closeTaskMenu() {
  cancelPending();
  set(false);
}

export function isTaskMenuOpen() {
  return open || pinned;
}

export function useTaskMenuOpen() {
  const [value, setValue] = useState(open || pinned);
  useEffect(() => pubsub.subscribe(setValue), []);
  return value;
}

// The kato logo's click: pinned open (docked, resizable), or back to the
// hover-only menu — collapsed, since the pointer is on the logo it just left
// pinned and leaving would collapse it anyway.
export function toggleTaskMenuPinned() {
  const wasExpanded = open || pinned;
  pinned = !pinned;
  cancelPending();
  open = false;
  writeStored(PINNED_KEY, pinned ? 'true' : 'false');
  pinnedPubsub.emit();
  if ((open || pinned) !== wasExpanded) { pubsub.emit(); }
}

export function isTaskMenuPinned() {
  return pinned;
}

export function useTaskMenuPinned() {
  const [value, setValue] = useState(pinned);
  useEffect(() => pinnedPubsub.subscribe(setValue), []);
  return value;
}

// The expanded width in px, kept between ``TASK_MENU_WIDTH_MIN`` and ``_MAX``.
export function setTaskMenuWidth(next) {
  const clamped = clampWidth(next);
  if (clamped === width) { return; }
  width = clamped;
  writeStored(WIDTH_KEY, String(width));
  widthPubsub.emit();
}

export function resetTaskMenuWidth() {
  setTaskMenuWidth(TASK_MENU_WIDTH_DEFAULT);
}

export function taskMenuWidth() {
  return width;
}

export function useTaskMenuWidth() {
  const [value, setValue] = useState(width);
  useEffect(() => widthPubsub.subscribe(setValue), []);
  return value;
}

export function clampWidth(value) {
  const number = Math.round(Number(value));
  if (!Number.isFinite(number)) { return TASK_MENU_WIDTH_DEFAULT; }
  return Math.min(TASK_MENU_WIDTH_MAX, Math.max(TASK_MENU_WIDTH_MIN, number));
}

// Remembered per browser — a convenience: storage that is blocked or full
// (a private window) just means the defaults.
function readStored(key) {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeStored(key, value) {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    // not remembered; it still works for this page
  }
}

// Test-only: back to collapsed, unpinned, default width, nothing pending.
export function _resetTaskMenu() {
  cancelPending();
  open = false;
  pinned = false;
  width = TASK_MENU_WIDTH_DEFAULT;
}
