import { useEffect } from 'react';
import { modalOrDrawerOpen } from '../utils/modalOpen.js';
import { pageSelection } from '../utils/selectedText.js';

/**
 * Ctrl+Shift+F (Cmd+Shift+F on macOS) searches the task's repos for the
 * highlighted word.
 *
 * This key used to open the task palette, which is the mismatch the operator
 * reported: highlighting a function name and pressing the "search
 * everywhere" chord gave them a task switcher. It now does what VS Code
 * trained everyone to expect — a content search across the workspace, seeded
 * with whatever is selected. The palette moved to Ctrl+Shift+P.
 *
 * ``getSeed`` is injected rather than read here because the selection may be
 * inside Monaco, which keeps its selection out of the DOM — the caller knows
 * whether an editor is mounted and can ask it first. Callers that have no
 * editor pass the page selection.
 *
 * Fires even while focus is in a text field, for the same reason the palette
 * does: the composer is where the cursor lives, and Ctrl/Cmd+Shift held down
 * cannot be mistaken for typing. It stands down for a modal or the settings
 * drawer, which own the keyboard while open.
 */
export function useGlobalSearchShortcut(onSearch, getSeed = pageSelection) {
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'f' && event.key !== 'F') { return; }
      if (!(event.metaKey || event.ctrlKey)) { return; }
      if (!event.shiftKey || event.altKey) { return; }
      if (modalOrDrawerOpen()) { return; }
      // Claim it before the browser's own "find again" variants.
      event.preventDefault();
      let seed = '';
      try {
        seed = typeof getSeed === 'function' ? getSeed() : '';
      } catch {
        // A seed that cannot be read is not a reason to swallow the gesture —
        // open the search empty and let the operator type.
        seed = '';
      }
      onSearch(seed);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onSearch, getSeed]);
}
