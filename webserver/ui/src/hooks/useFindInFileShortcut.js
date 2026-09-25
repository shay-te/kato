import { useEffect } from 'react';
import { modalOrDrawerOpen } from '../utils/modalOpen.js';
import { pageSelection } from '../utils/selectedText.js';

/**
 * Ctrl+F (Cmd+F on macOS) finds the highlighted word in the OPEN FILE.
 *
 * Monaco already binds Ctrl+F when it has focus, and does it well — it seeds
 * the find box from the editor's own selection. This hook exists for the
 * other case: the operator highlighted something and the cursor is not in
 * the editor (they clicked a diff row, a tree node, a chat message). The
 * browser's own find would then search the rendered PAGE, which for a
 * virtualised editor is only the lines currently on screen — so it silently
 * misses matches further down the file.
 *
 * So: if the event came from inside the editor, stand back and let Monaco
 * handle it natively. Otherwise focus the editor, seed the find box with
 * the page selection, and open it.
 *
 * Does nothing when no file is open — the browser keeps its own Ctrl+F,
 * which is the right behaviour on a screen with no editor.
 */
export function useFindInFileShortcut(editorRef, { enabled = true } = {}) {
  useEffect(() => {
    if (!enabled) { return undefined; }
    function onKeyDown(event) {
      if (event.key !== 'f' && event.key !== 'F') { return; }
      if (!(event.metaKey || event.ctrlKey)) { return; }
      // Shift is the GLOBAL search (useGlobalSearchShortcut); alt is nobody's.
      if (event.shiftKey || event.altKey) { return; }
      if (modalOrDrawerOpen()) { return; }
      const editor = editorRef?.current;
      if (!editor) { return; }
      // Inside the editor already — Monaco's own binding is better than
      // anything reimplemented here, and double-handling would fight it.
      if (event.target?.closest?.('.monaco-editor')) { return; }

      event.preventDefault();
      const seed = pageSelection();
      try {
        editor.focus?.();
        if (seed) { seedFindWidget(editor, seed); }
        editor.getAction?.('actions.find')?.run?.();
      } catch {
        // A Monaco upgrade can rename the action or the contribution. Failing
        // to open the find box must not swallow the operator's keystroke
        // silently forever, but it also must not throw into the key handler.
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [editorRef, enabled]);
}

// Put ``term`` in the find box before opening it.
//
// ``actions.find`` seeds from the EDITOR's selection, which is empty in the
// case this hook exists for (the selection is on the page, not in Monaco).
// The find controller is Monaco-internal, so every step is optional-chained
// and the whole thing is best-effort: a rename upstream degrades to "the
// find box opens empty", not to a crash. Same defensive shape as
// ``useFindWidgetEscape``, which reads the same contribution.
function seedFindWidget(editor, term) {
  const controller = editor.getContribution?.('editor.contrib.findController');
  controller?.setSearchString?.(term);
}
