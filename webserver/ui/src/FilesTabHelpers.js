import { basenameOf } from './utils/basenameOf.js';
import { fuzzyMatches } from './utils/fuzzyMatch.js';

export function normalizeTrees(payload) {
  const trees = Array.isArray(payload?.trees) ? payload.trees : null;
  if (trees && trees.length > 0) {
    return trees.map((entry) => {
      const cwd = String(entry?.cwd || '');
      const conflicts = Array.isArray(entry?.conflicted_files)
        ? entry.conflicted_files.map(String)
        : [];
      const changed = Array.isArray(entry?.changed_files)
        ? entry.changed_files.map(String)
        : [];
      return {
        repo_id: String(entry?.repo_id || '') || basenameOf(cwd),
        cwd,
        // Which branch the clone is on. Surfaced in the repo header because a
        // clone stranded on the default branch (a branch prep that failed)
        // looks exactly like a healthy one until its work fails to push.
        branch: String(entry?.branch || ''),
        tree: entry?.tree || [],
        conflictedFiles: new Set(conflicts),
        changedFiles: new Set(changed),
        // Repo kato can't push to (reference / no push permission). The tree
        // badges it so the operator knows edits there won't be published.
        readOnly: !!entry?.read_only,
        // False for the task folder, which is not a git repo: "changed" has
        // no meaning there, so the changed-files view can only ever claim it
        // is empty. Defaults TRUE so a real repo (and any payload from an
        // older backend) keeps the changed-files behaviour.
        hasDiff: entry?.has_diff !== false,
      };
    });
  }
  const legacyCwd = String(payload?.cwd || '');
  const legacyConflicts = Array.isArray(payload?.conflicted_files)
    ? payload.conflicted_files.map(String)
    : [];
  const legacyChanged = Array.isArray(payload?.changed_files)
    ? payload.changed_files.map(String)
    : [];
  return [{
    repo_id: basenameOf(legacyCwd),
    cwd: legacyCwd,
    tree: payload?.tree || [],
    conflictedFiles: new Set(legacyConflicts),
    changedFiles: new Set(legacyChanged),
  }];
}

// A tree node's path resolved against its repo, so it names ONE file on disk.
//
// Tree nodes are repo-RELATIVE ("Dockerfile"), so in a multi-repo task every
// repo's ``Dockerfile`` has the same one — and both the editor tab key and the
// file-content cache are keyed on the path. Openers that used the relative
// path as the ``absolutePath`` therefore focused ``ob-love-admin-backend/
// Dockerfile`` when the operator clicked ``email-core-lib/Dockerfile`` and
// served its cached content, and closing the tab didn't help because the cache
// entry outlived it: a different repo's file under the right file's name.
export function absolutePathForRepo(path, cwd) {
  const normalizedPath = String(path || '').replace(/\\/g, '/');
  const normalizedCwd = String(cwd || '').replace(/\\/g, '/').replace(/\/+$/, '');
  if (!normalizedCwd || !normalizedPath) { return normalizedPath; }
  // Already resolved (the task-folder tree, or a server that starts sending
  // absolute paths later) — never join twice.
  if (normalizedPath === normalizedCwd
      || normalizedPath.startsWith(normalizedCwd + '/')) {
    return normalizedPath;
  }
  // Any other absolute path belongs to something else entirely; leave it be
  // rather than fabricating a path inside this repo.
  if (normalizedPath.startsWith('/') || /^[A-Za-z]:\//.test(normalizedPath)) {
    return normalizedPath;
  }
  return `${normalizedCwd}/${normalizedPath}`;
}

// Resolve a server tree into the rows react-arborist draws: each node gains
// the two paths the openers need, plus the ``id`` the tree keys rows on.
//
// A node's path is DERIVED here, from the names of the folders above it —
// the server sends ``{name, children?}`` and nothing else. It used to spell
// the path out on every node, which on a 27-repository task was over half of
// a 1.4 MB payload, re-sent and re-parsed on every five-second poll, to say
// what the shape of the tree already says.
//
// ``id`` is the repo-relative path: unique within the tree (each repo renders
// its own), stable across polls, and what ``findTreeNodeIdByRelativePath``
// hands back for the selection and reveal.
export function attachIds(nodes, cwd = '', prefix = '') {
  if (!Array.isArray(nodes)) { return []; }
  return nodes.map((node) => {
    const name = String(node?.name || '');
    const relativePath = prefix ? `${prefix}/${name}` : name;
    const next = {
      ...node,
      id: relativePath,
      relativePath,
      absolutePath: absolutePathForRepo(relativePath, cwd),
    };
    if (Array.isArray(node.children)) {
      next.children = attachIds(node.children, cwd, relativePath);
    }
    return next;
  });
}

// Resolve the all-files tree id (= the node's absolute path, see
// ``attachIds``) for a repo-relative file path. Lets the tree highlight +
// reveal the centre pane's open file when the operator switches to "All".
// Returns null when the file isn't in this repo's tree (e.g. a deleted
// file that only exists in the diff).
export function findTreeNodeIdByRelativePath(nodes, relativePath) {
  const target = String(relativePath || '').trim();
  if (!target || !Array.isArray(nodes)) { return null; }
  for (const node of nodes) {
    const isFolder = Array.isArray(node.children);
    if (!isFolder && node.relativePath === target) { return node.id || null; }
    if (isFolder) {
      const found = findTreeNodeIdByRelativePath(node.children, target);
      if (found) { return found; }
    }
  }
  return null;
}

// True when ``changedFiles`` holds a path that lives inside the
// folder at ``folderRelativePath`` (the folder itself or any
// descendant). Used to tint the whole ancestor chain of a changed
// file in the tree.
//
// An empty / falsy ``folderRelativePath`` is the synthetic repo
// root — it returns ``false`` so the root of all is never tinted
// ("colour up to the root, but not the root of all"). No relative
// file path starts with "/", so prefixing with the folder + "/"
// also naturally can't match against an empty root path.
export function folderContainsChange(folderRelativePath, changedFiles) {
  const folder = String(folderRelativePath || '');
  if (!folder) { return false; }
  if (!changedFiles || typeof changedFiles.forEach !== 'function') {
    return false;
  }
  const prefix = folder + '/';
  for (const changed of changedFiles) {
    const path = String(changed || '');
    if (path === folder || path.startsWith(prefix)) { return true; }
  }
  return false;
}

// Left-click activation. Folders expand/collapse. Files are a
// no-op here on purpose: a left-click on a file only OPENS it in
// the editor pane (handled by the caller via ``onOpenFile``) — it
// must NOT also paste the path into the chat composer. Pasting a
// path is the explicit RIGHT-click affordance instead.
export function activateTreeNode(node) {
  if (node.isInternal) {
    node.toggle();
  }
}

// Node-shaped adapter over the shared matcher: a tree node matches when
// the term hits its basename OR its relative path. The matcher itself
// (substring + separator-insensitive subsequence) lives in
// utils/fuzzyMatch.js so the task palette matches identically — two
// hand-rolled copies would drift and the operator would see one surface
// find "authpy" and the other not.
//
// Folders only need to match themselves: react-arborist already keeps
// the ancestors of any matching descendant visible.
export function matchTreeNode(node, term, options) {
  const data = node?.data || {};
  return fuzzyMatches(term, [data.name, data.relativePath], options);
}

// How many rows the tree will actually draw, for sizing its viewport.
//
// The height used to come from the number of ROOT entries, which ignores the
// filter completely: searching in a large repo left an 800px-tall section
// showing nine matching files, and the operator scrolled past all that empty
// space to reach the next repo.
//
//   - filtering  → every matching node, plus the ancestors that must be
//                  drawn to reach it (the tree opens by default while
//                  filtering, so those ancestors are visible rows too);
//   - otherwise  → the roots, since folders start closed.
export function countVisibleTreeRows(nodes, term, options) {
  const query = String(term || '').trim();
  if (!query) { return Array.isArray(nodes) ? nodes.length : 0; }

  function countMatching(list) {
    if (!Array.isArray(list)) { return 0; }
    let rows = 0;
    for (const node of list) {
      const children = countMatching(node?.children);
      const self = matchTreeNode({ data: node || {} }, query, options);
      // A folder is drawn when it matches OR when it leads to a match.
      if (self || children > 0) { rows += 1 + children; }
    }
    return rows;
  }
  return countMatching(nodes);
}


// Group flat content-search matches ({repo_id, abs_path, path, line, text})
// by file, preserving first-seen order, so the results render as
// file → its matching lines. Pure — unit-tested without React.
export function groupContentMatchesByFile(matches) {
  const groups = [];
  const byKey = new Map();
  for (const match of matches || []) {
    if (!match) { continue; }
    const repoId = String(match.repo_id || '');
    const path = String(match.path || '');
    const key = `${repoId}::${path}`;
    let group = byKey.get(key);
    if (!group) {
      group = {
        key,
        repoId,
        path,
        absPath: String(match.abs_path || ''),
        lines: [],
      };
      byKey.set(key, group);
      groups.push(group);
    }
    group.lines.push({ line: Number(match.line) || 0, text: String(match.text || '') });
  }
  return groups;
}


// Total un-resolved comment count for a repo from its file→{count} meta
// map (buildFilesCommentMeta values). 0 when none. Pure.
//
// ``filePaths`` (optional): restrict the count to comments anchored to
// those files — the files the tree actually renders a row (and thus a
// per-file 💬 badge) for. This keeps the repo-header badge equal to the
// SUM of the per-file badges the operator can see, so a comment whose
// file isn't in the tree (anchor outdated, change reverted, path/repo
// mismatch) can't inflate the header past the visible badges ("no badge
// on any file → repo shows 0"). Omit to count every commented file.
export function countRepoComments(commentMeta, filePaths = null) {
  if (!commentMeta || typeof commentMeta.values !== 'function') { return 0; }
  if (filePaths) {
    let total = 0;
    const seen = new Set();
    for (const path of filePaths) {
      if (seen.has(path)) { continue; }
      seen.add(path);
      total += Number(commentMeta.get(path)?.count) || 0;
    }
    return total;
  }
  let total = 0;
  for (const entry of commentMeta.values()) {
    total += Number(entry?.count) || 0;
  }
  return total;
}

// Most-urgent kato_status across the repo's open comment threads, so the
// repo-header chip tints to the same colour as the per-file chips it is
// summarising (otherwise it stayed a fixed cyan and disagreed with what
// the operator saw on the rows underneath). Uses the same
// ``moreUrgentCommentStatus`` precedence the per-file aggregator uses,
// so the two stay in lockstep. Pure.
// ``filePaths`` scopes the status to the same visible-file set as
// countRepoComments (see there) so the header chip's tint agrees with the
// per-file badges actually on screen. Omit to consider every commented file.
export function repoCommentStatus(commentMeta, moreUrgentCommentStatus, filePaths = null) {
  if (!commentMeta || typeof commentMeta.values !== 'function') { return ''; }
  let best = '';
  const consider = (entry) => {
    const status = String(entry?.status || '').trim();
    if (!status) { return; }
    best = best ? moreUrgentCommentStatus(best, status) : status;
  };
  if (filePaths) {
    const seen = new Set();
    for (const path of filePaths) {
      if (seen.has(path)) { continue; }
      seen.add(path);
      const entry = commentMeta.get(path);
      if (entry) { consider(entry); }
    }
    return best;
  }
  for (const entry of commentMeta.values()) { consider(entry); }
  return best;
}
