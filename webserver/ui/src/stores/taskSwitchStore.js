import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';

// Is the task switcher (the Cmd+Tab-style overlay) on screen? Only while the
// operator HOLDS Tab / Ctrl+Tab: a quick press switches the task with nothing
// else on screen (the tasks menu's highlight already moves), and a press still
// down ``TASK_SWITCHER_REVEAL_MS`` later shows where they are until they let go
// (``useTaskTabShortcuts`` + ``utils/keyHold.js``).

export const TASK_SWITCHER_REVEAL_MS = 300;

let shown = false;
const pubsub = createPubSub(() => shown);

export function setTaskSwitcherShown(next) {
  const value = !!next;
  if (value === shown) { return; }
  shown = value;
  pubsub.emit();
}

export function useTaskSwitcherVisible() {
  const [visible, setVisible] = useState(shown);
  useEffect(() => pubsub.subscribe(setVisible), []);
  return visible;
}
