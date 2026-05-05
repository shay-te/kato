// Intra-line edit highlighting for the Changes tab.
//
// react-diff-view's ``markEdits`` enhancer compares paired old/new
// changes character-by-character and emits ``edit`` tokens for the
// segments that actually differ. The Diff renderer then paints
// those segments with a brighter background (driven by the
// ``--diff-code-{insert,delete}-edit-background-color`` CSS vars).
//
// The result matches Bitbucket's diff view: a whole line tinted
// red/green for added/removed, with the specific changed chars
// inside the line tinted brighter still — so on a line where only
// a few characters changed, the eye jumps straight to the edit.
//
// We tried the refractor path for full keyword/identifier
// highlighting; it kept fighting react-diff-view's tokenize
// signature. Inline edits alone get most of the readability win
// without the integration mess.

import { markEdits, tokenize } from 'react-diff-view';


// Detect language by file extension. Currently only used to flag
// known source files; reserved for future re-introduction of
// keyword-level highlighting if/when the integration stabilises.
export function detectDiffLanguage(path) {
  if (!path) { return ''; }
  const lower = String(path).toLowerCase();
  if (lower.endsWith('.jsx')) { return 'jsx'; }
  if (lower.endsWith('.tsx')) { return 'tsx'; }
  if (lower.endsWith('.ts')) { return 'typescript'; }
  if (lower.endsWith('.js') || lower.endsWith('.mjs') || lower.endsWith('.cjs')) {
    return 'javascript';
  }
  if (lower.endsWith('.py')) { return 'python'; }
  if (lower.endsWith('.css') || lower.endsWith('.scss') || lower.endsWith('.less')) {
    return 'css';
  }
  if (lower.endsWith('.json')) { return 'json'; }
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) { return 'markdown'; }
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) { return 'yaml'; }
  if (lower.endsWith('.sh') || lower.endsWith('.bash')) { return 'bash'; }
  return '';
}


// Run intra-line edit detection over ``hunks``. Returns the token
// pair the Diff component expects via its ``tokens`` prop, or
// ``null`` when there's nothing to mark or the call fails. ``null``
// makes the Diff fall back to the default plain-text rendering —
// safe in every error case.
export function tokenizeHunks(hunks /* , path */) {
  if (!hunks || hunks.length === 0) { return null; }
  try {
    return tokenize(hunks, { enhancers: [markEdits(hunks)] });
  } catch (_) {
    return null;
  }
}
