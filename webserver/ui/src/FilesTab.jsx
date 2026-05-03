import { useEffect, useMemo, useRef, useState } from 'react';
import { Tree } from 'react-arborist';
import { fetchFileTree } from './api.js';
import Icon from './components/Icon.jsx';
import { useChatComposer } from './contexts/ChatComposerContext.jsx';

export default function FilesTab({ taskId, workspaceVersion = 0 }) {
  const { appendToInput } = useChatComposer();
  const [state, setState] = useState({
    status: 'loading',
    trees: [],
    error: '',
  });
  const [collapsed, setCollapsed] = useState(() => new Set());
  const containerRef = useRef(null);
  const [size, setSize] = useState({ width: 320, height: 480 });

  useEffect(() => {
    if (!taskId) { return; }
    let cancelled = false;
    setState({ status: 'loading', trees: [], error: '' });
    fetchFileTree(taskId)
      .then((payload) => {
        if (cancelled) { return; }
        setState({
          status: 'ready',
          trees: normalizeTrees(payload),
          error: '',
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState({ status: 'error', trees: [], error: String(err) });
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion]);

  useEffect(() => {
    const node = containerRef.current;
    if (!node || typeof ResizeObserver === 'undefined') { return; }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) { return; }
      setSize({
        width: Math.max(160, Math.floor(entry.contentRect.width)),
        height: Math.max(200, Math.floor(entry.contentRect.height)),
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const repoIds = useMemo(() => {
    return state.trees.map((entry) => entry.repo_id || entry.cwd);
  }, [state.trees]);

  function toggleRepo(repoKey) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(repoKey)) { next.delete(repoKey); } else { next.add(repoKey); }
      return next;
    });
  }
  function collapseAll() { setCollapsed(new Set(repoIds)); }
  function expandAll() { setCollapsed(new Set()); }

  const showToolbar = repoIds.length > 1;
  const toolbar = showToolbar && (
    <span className="files-tab-toolbar">
      <button
        type="button"
        className="files-tab-icon-btn"
        data-tooltip="Expand all repositories — show every file in every workspace."
        aria-label="Expand all repositories"
        onClick={expandAll}
      >
        <Icon name="plus" />
      </button>
      <button
        type="button"
        className="files-tab-icon-btn"
        data-tooltip="Collapse all repositories — keep only the repository names visible."
        aria-label="Collapse all repositories"
        onClick={collapseAll}
      >
        <Icon name="minus" />
      </button>
    </span>
  );

  let body;
  if (state.status === 'loading') {
    body = <p className="files-tab-message">Loading files…</p>;
  } else if (state.status === 'error') {
    body = <p className="files-tab-message error">{state.error}</p>;
  } else if (state.trees.length === 0) {
    body = <p className="files-tab-message">No tracked files in this task.</p>;
  } else {
    body = state.trees.map((repoTree) => {
      const repoKey = repoTree.repo_id || repoTree.cwd;
      return (
        <RepoTree
          key={repoKey}
          repoTree={repoTree}
          width={size.width}
          collapsed={collapsed.has(repoKey)}
          onToggle={() => toggleRepo(repoKey)}
          onPickFile={appendToInput}
        />
      );
    });
  }

  const header = showToolbar && (
    <header className="files-tab-header">
      {toolbar}
    </header>
  );
  return (
    <div className="files-tab">
      {header}
      <div className="files-tab-body" ref={containerRef}>
        {body}
      </div>
    </div>
  );
}

// Normalize the multi-repo wire shape (with optional legacy single-repo
// fallback) into a list of per-repo tree blocks.
function normalizeTrees(payload) {
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
  // Legacy server: only ``cwd`` + ``tree`` at the top level.
  const legacyCwd = String(payload?.cwd || '');
  return [{
    repo_id: basenameOf(legacyCwd),
    cwd: legacyCwd,
    tree: payload?.tree || [],
  }];
}

function basenameOf(path) {
  if (!path) { return ''; }
  const trimmed = path.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
}

function RepoTree({ repoTree, width, collapsed, onToggle, onPickFile }) {
  const treeData = useMemo(() => {
    return attachIds(repoTree.tree, repoTree.cwd);
  }, [repoTree.tree, repoTree.cwd]);
  const heading = repoTree.repo_id || repoTree.cwd || 'repo';
  const treeHeight = Math.max(120, Math.min(treeData.length * 22 + 8, 800));
  const chevronName = collapsed ? 'chevron-right' : 'chevron-down';
  let body;
  if (collapsed) {
    body = null;
  } else if (treeData.length === 0) {
    body = <p className="files-tab-message">No tracked files in this repo.</p>;
  } else {
    body = (
      <Tree
        data={treeData}
        width={width}
        height={treeHeight}
        rowHeight={22}
        indent={14}
        openByDefault={false}
        disableDrag
        disableDrop
        disableEdit
      >
        {(props) => <Node {...props} onPickFile={onPickFile} />}
      </Tree>
    );
  }
  return (
    <section className="files-tab-repo">
      <header
        className="files-tab-repo-header"
        title={repoTree.cwd}
        onClick={onToggle}
      >
        <span className="files-tab-repo-chevron">
          <Icon name={chevronName} />
        </span>
        <span className="files-tab-repo-name">{heading}</span>
      </header>
      {body}
    </section>
  );
}

function attachIds(nodes, cwd = '') {
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

function relativePathForRepo(path, cwd) {
  const normalizedPath = String(path || '').replace(/\\/g, '/');
  const normalizedCwd = String(cwd || '').replace(/\\/g, '/').replace(/\/+$/, '');
  const cwdPrefix = normalizedCwd + '/';
  if (normalizedCwd && normalizedPath.startsWith(cwdPrefix)) {
    return normalizedPath.slice(cwdPrefix.length);
  }
  return normalizedPath.replace(/^\/+/, '');
}

function Node({ node, style, onPickFile }) {
  const isFolder = node.isInternal;
  function onActivate() {
    if (isFolder) {
      node.toggle();
      return;
    }
    if (typeof onPickFile === 'function') {
      onPickFile(node.data.relativePath);
    }
  }
  const rowClass = 'tree-row' + (node.isSelected ? ' selected' : '');
  let iconName;
  if (!isFolder) {
    iconName = 'file';
  } else if (node.isOpen) {
    iconName = 'folder-open';
  } else {
    iconName = 'folder';
  }
  return (
    <div
      className={rowClass}
      style={style}
      onClick={onActivate}
    >
      <span className="tree-row-icon"><Icon name={iconName} /></span>
      <span className="tree-row-name">{node.data.name}</span>
    </div>
  );
}
