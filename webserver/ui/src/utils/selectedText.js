// The word the operator has highlighted, wherever they highlighted it.
//
// Both search shortcuts are "search for THIS": Ctrl+Shift+F greps the task's
// repos for it, Ctrl+F finds it in the open file. Neither is useful if it
// cannot see the selection, and the selection can live in two different
// places — the page (a diff row, a chat message, the tree) or inside Monaco,
// which renders its own selection and does NOT put it in the DOM selection.
//
// Returns '' when nothing is selected, which every caller treats as "just
// open the search empty" rather than an error.

// A selection spanning half a file is not a search term. Long enough for any
// real identifier or a short phrase; short enough that an accidental
// select-all cannot be sent to a grep endpoint.
const MAX_SEED_LENGTH = 200;

export function normalizeSeed(raw) {
  const text = String(raw || '').trim();
  if (!text || text.length > MAX_SEED_LENGTH) { return ''; }
  // A multi-line selection is almost always an accident of dragging past the
  // end of a line. Take the first non-empty line rather than sending newlines
  // to a search box that cannot represent them.
  const firstLine = text.split('\n').map((l) => l.trim()).find(Boolean) || '';
  return firstLine.length > MAX_SEED_LENGTH ? '' : firstLine;
}

/** The DOM selection, normalized. '' when there is none. */
export function pageSelection() {
  try {
    return normalizeSeed(window.getSelection?.()?.toString());
  } catch {
    // Some embedders throw on getSelection in detached documents.
    return '';
  }
}

/** Monaco's own selection for ``editor``, normalized. '' when there is none. */
export function editorSelection(editor) {
  try {
    const selection = editor?.getSelection?.();
    if (!selection || selection.isEmpty?.()) { return ''; }
    return normalizeSeed(editor.getModel?.()?.getValueInRange?.(selection));
  } catch {
    return '';
  }
}

/**
 * What the operator means by "this word", preferring the editor.
 *
 * Monaco keeps its selection out of the DOM, so when the cursor is in the
 * editor the page selection is usually empty (or still holds something the
 * operator selected minutes ago somewhere else). Asking the editor first
 * means a highlight in the file wins over a stale page selection.
 */
export function selectedSearchTerm(editor) {
  return editorSelection(editor) || pageSelection();
}
