import { useEffect } from 'react';
import { modalOrDrawerOpen } from '../utils/modalOpen.js';

/**
 * Ctrl+Shift+P (Cmd+Shift+P on macOS) opens the task palette.
 *
 * MOVED here from Ctrl+Shift+F. That key is the "search wider" gesture in
 * VS Code muscle memory, and kato has a real global search to put on it —
 * the workspace content search in the Files pane. Highlighting a symbol and
 * pressing Ctrl+Shift+F should grep the task's repos for it, not open a task
 * switcher; the operator reported exactly that mismatch.
 *
 * Ctrl+Shift+P is the natural home: VS Code puts its command palette there,
 * so "a palette of things to jump to" is already the meaning in everyone's
 * fingers.
 *
 * NOT Ctrl+P. That key is taken: ``RightPane`` binds Ctrl/Cmd+P to focus the
 * workspace FILE filter, so putting the palette on it double-bound the key —
 * both handlers fired and the operator got the palette on top of a focused
 * file search. Ctrl+P is also VS Code's "Go to File".
 *
 * Caveat worth knowing: Firefox binds Ctrl+Shift+P to "new private window"
 * at the browser level, which a page cannot override. On Firefox the
 * palette is still reachable from the tab-strip button.
 *
 * The shortcut stands down when a modal or the settings drawer is open
 * (that surface owns the keyboard), and — unlike the Tab task-cycling
 * shortcut — it deliberately DOES fire while focus is in a text field.
 * The composer is where the operator's cursor spends nearly all its
 * time, so a "go to task" gesture that refused to work there would
 * refuse to work when it is actually wanted. That is safe precisely
 * because Ctrl/Cmd+Shift is held: a bare keystroke would be typing,
 * this cannot be.
 */
export function useTaskPaletteShortcut(onOpen) {
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'p' && event.key !== 'P') { return; }
      if (!(event.metaKey || event.ctrlKey)) { return; }
      if (!event.shiftKey || event.altKey) { return; }
      if (modalOrDrawerOpen()) { return; }
      // Claim it before anything else (Firefox has no binding here, but a
      // future browser one should not silently win over the operator's).
      event.preventDefault();
      onOpen();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onOpen]);
}
