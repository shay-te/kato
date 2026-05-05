// Builds the chat-composer fragment produced by the Changes-tab
// right-click handler. Lives in its own module so the wording is
// importable from the .test.js suite (Node's test runner can't load
// .jsx). The fragment is what Claude reads as the operator's prompt;
// treat changes here as content review.

const _SELECTION_BYTE_LIMIT = 8 * 1024;


export function buildChatFragmentFromSelection(path, repoId = '') {
  const safePath = String(path || '').trim();
  if (!safePath) { return ''; }
  const repoPrefix = repoId ? `${repoId}:` : '';
  const selectedText = _readSelectionText();
  if (!selectedText) {
    // No selection → just the file reference. The operator may
    // be pointing Claude at the file before typing the actual
    // ask ("rename foo to bar in this file").
    return `\`${repoPrefix}${safePath}\``;
  }
  // Selection present → Claude treats this as "act on these
  // specific lines" rather than free-form context. The diff
  // ``+/-`` markers are preserved (when the operator selected
  // diff body lines) so additions vs deletions are unambiguous.
  return (
    `In \`${repoPrefix}${safePath}\` the following diff lines:\n`
    + '```\n'
    + selectedText
    + '\n```'
  );
}


function _readSelectionText() {
  if (typeof window === 'undefined' || typeof window.getSelection !== 'function') {
    return '';
  }
  const selection = window.getSelection();
  if (!selection) { return ''; }
  const text = String(selection.toString() || '').trim();
  // Bound the selection length so a runaway "select-all" doesn't
  // dump megabytes into the chat composer. 8 KB is generous for
  // any real "ask Claude about these lines" interaction.
  if (text.length > _SELECTION_BYTE_LIMIT) {
    return `${text.slice(0, _SELECTION_BYTE_LIMIT)}\n… (selection truncated)`;
  }
  return text;
}
