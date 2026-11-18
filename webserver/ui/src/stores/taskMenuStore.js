import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';
import {
  HOVER_EXPAND_CLOSE_GRACE_MS,
  HOVER_EXPAND_OPEN_DELAY_MS,
  createHoverExpand,
} from './hoverExpand.js';

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
// Hovering open and closed is the shared ``createHoverExpand`` (the files tree
// beside a maximized centre does the same), with its timing: the expand waits
// ``TASK_MENU_OPEN_DELAY_MS``, the collapse ``TASK_MENU_CLOSE_GRACE_MS``. The
// kato logo expands at once — pointing at it is asking.

export const TASK_MENU_OPEN_DELAY_MS = HOVER_EXPAND_OPEN_DELAY_MS;
export const TASK_MENU_CLOSE_GRACE_MS = HOVER_EXPAND_CLOSE_GRACE_MS;

export const TASK_MENU_WIDTH_DEFAULT = 360;
export const TASK_MENU_WIDTH_MIN = 240;
export const TASK_MENU_WIDTH_MAX = 640;
const PINNED_KEY = 'kato.taskMenu.pinned.v1';
const WIDTH_KEY = 'kato.taskMenu.width.v1';

const hover = createHoverExpand({
  openDelayMs: TASK_MENU_OPEN_DELAY_MS,
  closeGraceMs: TASK_MENU_CLOSE_GRACE_MS,
});
let pinned = readStored(PINNED_KEY) === 'true';
let width = clampWidth(Number(readStored(WIDTH_KEY)) || TASK_MENU_WIDTH_DEFAULT);
// Expanded = hovered open OR pinned: a pinned menu ignores the pointer.
const expanded = () => hover.isOpen() || pinned;
const pubsub = createPubSub(expanded);
const pinnedPubsub = createPubSub(() => pinned);
const widthPubsub = createPubSub(() => width);
let shownExpanded = expanded();

// Subscribers hear only a change of EXPANDED, whichever of the two moved it.
function emitIfExpandedChanged() {
  if (expanded() === shownExpanded) { return; }
  shownExpanded = expanded();
  pubsub.emit();
}
hover.subscribe(emitIfExpandedChanged);

export const openTaskMenu = hover.open;
export const openTaskMenuSoon = hover.openSoon;
export const closeTaskMenuSoon = hover.closeSoon;
export const closeTaskMenu = hover.close;

export function isTaskMenuOpen() {
  return expanded();
}

export function useTaskMenuOpen() {
  const [value, setValue] = useState(expanded());
  useEffect(() => pubsub.subscribe(setValue), []);
  return value;
}

// The kato logo's click: pinned open (docked, resizable), or back to the
// hover-only menu — collapsed, since the pointer is on the logo it just left
// pinned and leaving would collapse it anyway.
export function toggleTaskMenuPinned() {
  pinned = !pinned;
  writeStored(PINNED_KEY, pinned ? 'true' : 'false');
  pinnedPubsub.emit();
  hover.close();
  emitIfExpandedChanged();
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
  hover.reset();
  pinned = false;
  width = TASK_MENU_WIDTH_DEFAULT;
  shownExpanded = false;
}
