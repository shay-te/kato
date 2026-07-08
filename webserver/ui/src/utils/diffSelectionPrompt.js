// Builds the chat-composer fragment produced by the diff-pane right-click
// "Place in chat". Lives in its own module so the wording is importable from
// the .test.js suite (Node's test runner can't load .jsx). The fragment is
// what Claude reads as the operator's prompt; treat changes here as content
// review.
//
// It emits a compact FILE:LINE REFERENCE (e.g. `repo:src/app.js:L20-L45`),
// NOT the selected code itself — pasting the whole block made the prompt
// unreadable and just duplicated what Claude already reads from the file.

// Turn a resolved new-side line range into the reference string. Pure (no
// DOM) so it's unit-testable; ``range`` is ``{start, end}`` or null.
export function formatSelectionReference(path, repoId = '', range = null) {
  const safePath = String(path || '').trim();
  if (!safePath) { return ''; }
  const repoPrefix = repoId ? `${repoId}:` : '';
  if (!range || !Number.isFinite(range.start) || !Number.isFinite(range.end)) {
    // No line selection → just the file reference. The operator may be
    // pointing Claude at the whole file before typing the actual ask.
    return `\`${repoPrefix}${safePath}\``;
  }
  const ref = range.start === range.end
    ? `${repoPrefix}${safePath}:L${range.start}`
    : `${repoPrefix}${safePath}:L${range.start}-L${range.end}`;
  return `\`${ref}\``;
}

export function buildChatFragmentFromSelection(path, repoId = '') {
  return formatSelectionReference(path, repoId, selectedNewLineRange());
}

// The new-side line range covered by the current text selection, read off
// the react-diff-view gutters. Prefers the NEW-side line number per row (the
// last ``.diff-gutter`` in the row; falls back to the old number on
// pure-delete rows that have no new number). Returns null when there's no
// usable selection.
export function selectedNewLineRange() {
  if (typeof window === 'undefined' || typeof window.getSelection !== 'function') {
    return null;
  }
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return null;
  }
  const range = selection.getRangeAt(0);
  let root = range.commonAncestorContainer;
  if (root && root.nodeType !== 1) { root = root.parentElement; }
  if (!root || typeof root.closest !== 'function') { return null; }
  const scope = root.closest('.diff') || root.ownerDocument || root;
  const rows = typeof scope.querySelectorAll === 'function'
    ? scope.querySelectorAll('tr') : [];
  let min = Infinity;
  let max = -Infinity;
  for (const row of rows) {
    if (!selection.containsNode(row, true)) { continue; }
    const gutters = row.querySelectorAll('.diff-gutter');
    let lineNumber = NaN;
    for (let i = gutters.length - 1; i >= 0; i -= 1) {
      const value = parseInt((gutters[i].textContent || '').trim(), 10);
      if (Number.isFinite(value)) { lineNumber = value; break; }
    }
    if (Number.isFinite(lineNumber)) {
      min = Math.min(min, lineNumber);
      max = Math.max(max, lineNumber);
    }
  }
  if (!Number.isFinite(min)) { return null; }
  return { start: min, end: max };
}
