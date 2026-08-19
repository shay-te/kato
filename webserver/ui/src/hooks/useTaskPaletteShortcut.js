import { useEffect } from 'react';
import { modalOrDrawerOpen } from '../utils/modalOpen.js';

/**
 * Ctrl+Shift+F (Cmd+Shift+F on macOS) opens the task palette.
 *
 * NOT Ctrl+P. That key was already taken: ``RightPane`` binds Ctrl/Cmd+P
 * to focus the workspace FILE filter, so putting the task palette on it
 * double-bound the key — both handlers fired, and the operator got the
 * palette on top of a focused file search. Ctrl+P is also what VS Code
 * uses for "Go to File", so file search is the meaning already in
 * everyone's fingers; taking it for tasks fights that.
 *
 * Ctrl+Shift+F is the "search wider" gesture in the same muscle memory
 * (VS Code: search across all files), it is unbound in Chrome, Edge,
 * Firefox and Safari, and it is unbound anywhere in kato.
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
      if (event.key !== 'f' && event.key !== 'F') { return; }
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
