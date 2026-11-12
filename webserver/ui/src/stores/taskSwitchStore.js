import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';

// "The operator just moved to another task with the keyboard" (Tab / Ctrl+Tab).
// The task switcher (the Cmd+Tab-style overlay) shows while those keep coming
// and fades out a moment after the last one — with the tasks menu closed, a
// keyboard switch otherwise happens with nothing on screen saying where you
// went.

export const TASK_SWITCHER_HOLD_MS = 900;

let seq = 0;
const pubsub = createPubSub(() => seq);

export function announceTaskSwitch() {
  seq += 1;
  pubsub.emit();
}

// True from a switch until ``TASK_SWITCHER_HOLD_MS`` after the latest one.
export function useTaskSwitcherVisible() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    let seen = seq;
    let timer = null;
    const unsubscribe = pubsub.subscribe((current) => {
      if (current === seen) { return; }  // the replay every subscribe gets
      seen = current;
      setVisible(true);
      clearTimeout(timer);
      timer = setTimeout(() => setVisible(false), TASK_SWITCHER_HOLD_MS);
    });
    return () => {
      clearTimeout(timer);
      unsubscribe();
    };
  }, []);
  return visible;
}
