import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';

// Whether the tasks menu (the rail of tasks on the left, Chrome-style) is
// EXPANDED. The rail itself is always there, showing each task's code;
// hovering it (or the kato logo at its top) expands it over the page, and
// leaving it collapses it again.
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

let open = false;
let timer = null;
const pubsub = createPubSub(() => open);

function set(next) {
  if (open === next) { return; }
  open = next;
  pubsub.emit();
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
  return open;
}

export function useTaskMenuOpen() {
  const [value, setValue] = useState(open);
  useEffect(() => pubsub.subscribe(setValue), []);
  return value;
}

// Test-only: back to collapsed, nothing pending.
export function _resetTaskMenu() {
  cancelPending();
  open = false;
}
