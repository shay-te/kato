// Syntax + intra-line edit highlighting for the Changes tab.
//
// Two layers feed react-diff-view's ``tokens`` prop:
//
//   1. **refractor** (Prism's lightweight tokenizer) tags every
//      identifier / keyword / string / comment with Prism token
//      classes, which our CSS paints in the dark-mode palette. This
//      is what makes ``function``, string literals, etc. stand out
//      the way they do in any normal source viewer.
//
//   2. **markEdits** walks the paired old/new lines and tags the
//      specific characters that changed within each line — those
//      get the brighter intra-line tint (matches Bitbucket).
//
// Languages are registered eagerly at module load. The set is
// limited to what kato realistically diffs (JS/TS/JSX/TSX, Python,
// CSS family, JSON, Markdown, YAML, Bash). Unrecognised extensions
// fall back to edit-only highlighting (still readable, just plain).

import { markEdits, tokenize } from 'react-diff-view';
import { refractor } from 'refractor/core';

import bash from 'refractor/bash';
import css from 'refractor/css';
import javascript from 'refractor/javascript';
import json from 'refractor/json';
import jsx from 'refractor/jsx';
import markdown from 'refractor/markdown';
import python from 'refractor/python';
import scss from 'refractor/scss';
import tsx from 'refractor/tsx';
import typescript from 'refractor/typescript';
import yaml from 'refractor/yaml';

// One-time registration. Refractor is a module-level singleton; the
// extra ``refractor.registered`` guard means HMR re-imports don't
// re-register and log warnings.
if (!refractor.__katoLanguagesRegistered) {
  refractor.register(bash);
  refractor.register(css);
  refractor.register(javascript);
  refractor.register(json);
  refractor.register(jsx);
  refractor.register(markdown);
  refractor.register(python);
  refractor.register(scss);
  refractor.register(tsx);
  refractor.register(typescript);
  refractor.register(yaml);
  refractor.__katoLanguagesRegistered = true;
}

// Detect language by file extension. Returns a refractor-registered
// name when we have a tokenizer for it, '' otherwise (which makes
// :func:`tokenizeHunks` skip the syntax-highlight pass).
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
  if (lower.endsWith('.scss') || lower.endsWith('.sass')) { return 'scss'; }
  if (lower.endsWith('.css') || lower.endsWith('.less')) { return 'css'; }
  if (lower.endsWith('.json')) { return 'json'; }
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) { return 'markdown'; }
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) { return 'yaml'; }
  if (lower.endsWith('.sh') || lower.endsWith('.bash')) { return 'bash'; }
  return '';
}


// Run intra-line edit detection (+ optional syntax highlighting)
// over ``hunks``. Returns the token pair the Diff component expects
// via its ``tokens`` prop, or ``null`` when there's nothing to mark
// or the call fails. ``null`` makes the Diff fall back to the
// default plain-text rendering — safe in every error case.
export function tokenizeHunks(hunks, path) {
  if (!hunks || hunks.length === 0) { return null; }
  const language = detectDiffLanguage(path);
  const options = { enhancers: [markEdits(hunks)] };
  if (language) {
    options.refractor = refractor;
    options.language = language;
  }
  try {
    return tokenize(hunks, options);
  } catch (_) {
    // Highlight pass blew up (refractor pukes on rare edge cases —
    // e.g. unterminated strings spanning multiple hunks). Retry
    // with edits only so we at least keep the intra-line tint.
    try {
      return tokenize(hunks, { enhancers: [markEdits(hunks)] });
    } catch (_inner) {
      return null;
    }
  }
}
