export function normalizeTrees(payload) {
  const trees = Array.isArray(payload?.trees) ? payload.trees : null;
  if (trees && trees.length > 0) {
    return trees.map((entry) => {
      const cwd = String(entry?.cwd || '');
      return {
        repo_id: String(entry?.repo_id || '') || basenameOf(cwd),
        cwd,
        tree: entry?.tree || [],
      };
    });
  }
  const legacyCwd = String(payload?.cwd || '');
  return [{
    repo_id: basenameOf(legacyCwd),
    cwd: legacyCwd,
    tree: payload?.tree || [],
  }];
}

export function attachIds(nodes, cwd = '') {
  if (!Array.isArray(nodes)) { return []; }
  return nodes.map((node) => {
    const next = {
      ...node,
      id: node.path,
      relativePath: relativePathForRepo(node.path, cwd),
    };
    if (Array.isArray(node.children)) {
      next.children = attachIds(node.children, cwd);
    }
    return next;
  });
}

export function activateTreeNode(node, onPickFile) {
  if (node.isInternal) {
    node.toggle();
    return;
  }
  if (typeof onPickFile === 'function') {
    onPickFile(node.data.relativePath);
  }
}

function basenameOf(path) {
  if (!path) { return ''; }
  const trimmed = path.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
}

// Match a tree node against a free-text search term.
//
// react-arborist's default matcher only checks ``node.data.name``
// (the basename). That misses VS-Code-style "Cmd+P type-the-path"
// flows where the user wants ``src/auth.py`` to surface when they
// type "src/auth" or "auth.py". This matcher checks BOTH basename
// AND relative path, case-insensitive, substring match.
//
// Empty / whitespace-only term matches everything (renders the
// whole tree, same as no filter). Folders match when their name
// matches OR any descendant matches — but react-arborist already
// shows ancestors of matching descendants, so we only need to
// match nodes themselves here.
export function matchTreeNode(node, term) {
  const needle = String(term || '').trim().toLowerCase();
  if (!needle) { return true; }
  const data = node?.data || {};
  const name = String(data.name || '').toLowerCase();
  const relativePath = String(data.relativePath || '').toLowerCase();
  return name.includes(needle) || relativePath.includes(needle);
}

function relativePathForRepo(path, cwd) {
  const normalizedPath = String(path || '').replace(/\\/g, '/');
  const normalizedCwd = String(cwd || '').replace(/\\/g, '/').replace(/\/+$/, '');
  const cwdPrefix = normalizedCwd + '/';
  if (normalizedCwd && normalizedPath.startsWith(cwdPrefix)) {
    return normalizedPath.slice(cwdPrefix.length);
  }
  return normalizedPath.replace(/^\/+/, '');
}
