import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';

// Whether the tasks menu (the vertical task list on the left, Chrome-style) is
// open. Hovering the kato icon opens it; leaving both the icon and the menu
// closes it. The two are separate elements — the icon in the header, the menu
// rendered by App — so they share this one switch instead of props.
//
// The close waits a moment: crossing the few pixels from the icon into the
// menu passes over neither, and closing at once made the menu vanish on the
// way to it. Entering either cancels a pending close.

export const TASK_MENU_CLOSE_GRACE_MS = 220;

let open = false;
let closeTimer = null;
const pubsub = createPubSub(() => open);

function set(next) {
  if (open === next) { return; }
  open = next;
  pubsub.emit();
}

function cancelClose() {
  if (closeTimer !== null) {
    clearTimeout(closeTimer);
    closeTimer = null;
  }
}

export function openTaskMenu() {
  cancelClose();
  set(true);
}

export function closeTaskMenuSoon() {
  cancelClose();
  closeTimer = setTimeout(() => {
    closeTimer = null;
    set(false);
  }, TASK_MENU_CLOSE_GRACE_MS);
}

export function closeTaskMenu() {
  cancelClose();
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

// Test-only: back to closed, no pending timer.
export function _resetTaskMenu() {
  cancelClose();
  open = false;
}
