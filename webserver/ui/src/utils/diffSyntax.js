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
  // Prism's JS-family grammars never tag a member-expression
  // property — ``foo.bar`` leaves ``bar`` as plain text, so JSX
  // props like ``planEntry.packageIncludes`` render uncoloured
  // while Bitbucket paints them. Add a ``property-access`` token:
  // an identifier that follows a ``.`` (lookbehind keeps the dot
  // as its own punctuation token). Inserted before ``punctuation``
  // — i.e. AFTER ``function`` — so ``arr.map(`` still wins as a
  // function call and only true property reads are caught.
  const propertyAccess = {
    'property-access': {
      pattern: /(\.\s*)#?[$A-Za-z_\xA0-￿][$\w\xA0-￿]*/,
      lookbehind: true,
    },
  };
  for (const lang of ['javascript', 'jsx', 'typescript', 'tsx']) {
    if (refractor.languages[lang]) {
      refractor.languages.insertBefore(lang, 'punctuation', propertyAccess);
    }
  }
  refractor.__katoLanguagesRegistered = true;
}

const refractorAdapter = {
  highlight(text, language) {
    const tree = refractor.highlight(text, language);
    return Array.isArray(tree) ? tree : (tree.children || []);
  },
};

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
    // React projects routinely put JSX in plain ``.js`` files (the
    // diffed repo here does). The ``jsx`` grammar is a strict
    // superset of ``javascript`` — non-JSX ``.js`` still tokenizes
    // identically — so always use it; otherwise ``<Package …/>``,
    // attr-names and the whole tag render as plain text the way
    // ``javascript`` leaves them. Matches what Bitbucket does.
    return 'jsx';
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

  // ONE HUNK AT A TIME — never all of them in a single pass.
  //
  // react-diff-view builds the text it hands the tokenizer from the hunk
  // lines alone, padding every collapsed gap with an EMPTY line
  // (``toTokenTrees`` → ``mapChanges``, which yields '' for a line no hunk
  // covers). So a file whose hunks skip lines 20-46 is tokenized as:
  //
  //     19:  /*
  //     20-46:                  <- the closing */ lived here; now blank
  //     47:  const promiseStatus = ...
  //
  // Prism therefore never sees the ``*/``, and paints the ENTIRE rest of
  // the file as comment — the operator's report was a diff where all the
  // real code rendered as a comment and stayed that way until they expanded
  // the gap far enough to reveal the terminator.
  //
  // Tokenizing each hunk separately means comment / string / template state
  // cannot cross a gap it was never allowed to see. The trade-off is the
  // inverse case — lines genuinely INSIDE a comment whose ``/*`` and ``*/``
  // are both hidden now render as code rather than as comment. That is the
  // strictly less harmful direction: it under-decorates a few lines instead
  // of blanking the whole file, and it cannot hide real code.
  //
  // The correct-in-every-case fix is to pass ``oldSource`` (react-diff-view
  // then highlights the whole file and patches it), which needs the full
  // file body in the diff payload — that payload is POLLED, so it is not a
  // trade this makes lightly.
  const combined = { old: [], new: [] };
  let tokenized = false;
  for (const hunk of hunks) {
    const partial = _tokenizeHunk(hunk, language);
    if (!partial) { continue; }
    tokenized = true;
    // Indices are ABSOLUTE line numbers already: each pass pads from line 1
    // up to its own hunk, so the hunk's lines land where they belong.
    _copyLines(combined.old, partial.old, hunk.oldStart, hunk.oldLines);
    _copyLines(combined.new, partial.new, hunk.newStart, hunk.newLines);
  }
  return tokenized ? combined : null;
}


// Tokenize a single hunk. Null when even the edits-only pass fails, which
// leaves that hunk on react-diff-view's plain-text rendering.
function _tokenizeHunk(hunk, language) {
  const options = { enhancers: [markEdits([hunk])] };
  if (language) {
    options.highlight = true;
    options.refractor = refractorAdapter;
    options.language = language;
  }
  try {
    return tokenize([hunk], options);
  } catch (_) {
    // Highlight pass blew up (refractor pukes on rare edge cases —
    // e.g. an unterminated string). Retry with edits only so we at
    // least keep the intra-line tint.
    try {
      return tokenize([hunk], { enhancers: [markEdits([hunk])] });
    } catch (_inner) {
      return null;
    }
  }
}


// Copy one hunk's line tokens into the combined array. Lines no hunk covers
// are deliberately left as HOLES: react-diff-view renders a line with no
// token entry from its raw text (``CodeCell``: ``tokens ? … : text``), so a
// hole degrades to plain text rather than to an empty cell.
function _copyLines(target, source, start, count) {
  if (!Array.isArray(source) || !start || !count) { return; }
  for (let line = start; line < start + count; line += 1) {
    const index = line - 1;
    if (index >= 0 && source[index] !== undefined) {
      target[index] = source[index];
    }
  }
}
