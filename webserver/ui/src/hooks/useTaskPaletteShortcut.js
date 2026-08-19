import { useEffect } from 'react';
import { modalOrDrawerOpen } from '../utils/modalOpen.js';

/**
 * Ctrl+P (Cmd+P on macOS) opens the task palette.
 *
 * WHY THIS KEY, given it is the browser's Print shortcut:
 *
 * It is the same trade VS Code makes for "Go to File", and for the same
 * reason — inside an app that is mostly a code surface, the muscle
 * memory for Ctrl+P is "find a thing", not "print this page". Printing
 * a live agent session is not a thing anyone does; jumping to a task is
 * something you do constantly. It is also cleanly overridable:
 * ``preventDefault`` on the keydown suppresses the print dialog in every
 * browser, which is NOT true of every shortcut.
 *
 * Ctrl+Shift+P is deliberately NOT used. It is free in Chrome and Edge,
 * but in Firefox it opens a private window at the browser-chrome level,
 * where a page cannot intercept it — so the palette would silently fail
 * for Firefox operators with no way for kato to detect or explain it.
 * Ctrl+P works everywhere.
 *
 * The shortcut stands down when a modal or the settings drawer is open
 * (that surface owns the keyboard), and — unlike the Tab task-cycling
 * shortcut — it deliberately DOES fire while focus is in a text field.
 * The composer is where the operator's cursor spends nearly all its
 * time, so a "go to task" gesture that refuses to work there would
 * refuse to work when it is actually wanted. That is safe precisely
 * because Ctrl/Cmd is held: a bare keystroke would be typing, this
 * cannot be.
 */
export function useTaskPaletteShortcut(onOpen) {
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'p' && event.key !== 'P') { return; }
      if (!(event.metaKey || event.ctrlKey)) { return; }
      // Let Ctrl+Shift+P through to the browser untouched — claiming it
      // would break Firefox's private window for no gain.
      if (event.shiftKey || event.altKey) { return; }
      if (modalOrDrawerOpen()) { return; }
      // Claim it before the browser opens its print dialog.
      event.preventDefault();
      onOpen();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onOpen]);
}
