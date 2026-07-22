// Pure logic for the composer's ``@`` file-mention autocomplete (type ``@`` in
// the message box → pick a workspace file → its repo-scoped path is inserted).
//
// Kept framework-free so it's trivially unit-testable: the component
// (ComposerMentionMenu) and MessageForm only do rendering + wiring on top of
// these functions. The inserted reference format — ``repoId/relativePath``
// wrapped in backticks — matches what clicking a file in the Files tab inserts
// (FilesTab.jsx), so the agent resolves an @-mention and a click identically.

import { attachIds, matchTreeNode } from '../FilesTabHelpers.js';
import { basenameOf } from './basenameOf.js';

// Flatten the normalized per-repo trees (see FilesTabHelpers.normalizeTrees)
// into a flat list of FILE entries the picker filters over. Folders are walked
// but not listed. ``attachIds`` gives each node its repo-relative path.
export function flattenTreeFiles(trees) {
  const files = [];
  for (const entry of Array.isArray(trees) ? trees : []) {
    const repoId = String(entry?.repo_id || '').trim();
    _collectFiles(attachIds(entry?.tree || [], entry?.cwd || ''), repoId, files);
  }
  return files;
}

function _collectFiles(nodes, repoId, out) {
  for (const node of Array.isArray(nodes) ? nodes : []) {
    if (Array.isArray(node.children)) {
      _collectFiles(node.children, repoId, out);
      continue;
    }
    const relativePath = String(node.relativePath || '').trim();
    if (relativePath) {
      out.push({
        repoId,
        relativePath,
        name: String(node.name || basenameOf(relativePath)),
      });
    }
  }
}

// The repo-scoped reference inserted for a picked file — identical to the
// Files-tab click format (FilesTab.jsx: ``repoId ? `${repoId}/${path}` : path``).
export function referenceFor(file) {
  const repoId = String(file?.repoId || '').trim();
  const relativePath = String(file?.relativePath || '').trim();
  return repoId ? `${repoId}/${relativePath}` : relativePath;
}

// Detect whether the caret sits inside an ``@mention`` token.
//
// Active only when an ``@`` appears at the start of the text or right after
// whitespace, with NO whitespace between it and the caret — so ``user@host``
// (email) and a finished ``@foo bar`` never trigger. The query is everything
// between the ``@`` and the caret (may contain ``/`` so a path prefix filters).
// Returns ``{ active, query, start }`` where ``start`` is the ``@`` index.
export function detectMentionQuery(text, caret) {
  const value = String(text || '');
  const pos = Math.max(0, Math.min(Number(caret) || 0, value.length));
  for (let i = pos - 1; i >= 0; i -= 1) {
    const ch = value[i];
    if (ch === '@') {
      const prev = i > 0 ? value[i - 1] : '';
      if (i === 0 || /\s/.test(prev)) {
        return { active: true, query: value.slice(i + 1, pos), start: i };
      }
      return _inactive();
    }
    if (/\s/.test(ch)) {
      return _inactive();
    }
  }
  return _inactive();
}

function _inactive() {
  return { active: false, query: '', start: -1 };
}

// Rank the files against ``query`` (empty query → the head of the list).
// name-exact > name-prefix > name-substring > path-substring > path-subsequence.
export function filterMentionFiles(files, query, limit = 50) {
  const list = Array.isArray(files) ? files : [];
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) {
    return list.slice(0, limit);
  }
  // Reuse the Files-tab matcher (substring + separator-insensitive subsequence)
  // for the match test, then rank filename hits above path-only hits.
  const matched = list.filter((file) => matchTreeNode({ data: file }, needle));
  matched.sort(
    (a, b) => _rank(a, needle) - _rank(b, needle)
      || String(a.relativePath).length - String(b.relativePath).length,
  );
  return matched.slice(0, limit);
}

// Lower is better. A filename hit beats a path-only / subsequence hit.
function _rank(file, needle) {
  const name = String(file?.name || '').toLowerCase();
  if (name === needle) { return 0; }
  if (name.startsWith(needle)) { return 1; }
  if (name.includes(needle)) { return 2; }
  return 3;
}

// Replace the ``@query`` span (``start``..``caret``) with the backtick-wrapped
// reference + a trailing space. Returns the new text and the new caret offset.
export function applyMention(text, start, caret, reference) {
  const value = String(text || '');
  const ref = String(reference || '').trim();
  const from = Math.max(0, Math.min(Number(start) || 0, value.length));
  const to = Math.max(from, Math.min(Number(caret) || 0, value.length));
  const after = value.slice(to);
  // Trailing space so the next word is separated — but not when whitespace
  // already follows (avoids a double space when mentioning mid-sentence).
  const needsSpace = after.length === 0 || !/^\s/.test(after);
  const insert = '`' + ref + '`' + (needsSpace ? ' ' : '');
  return {
    text: value.slice(0, from) + insert + after,
    caret: from + insert.length,
  };
}
