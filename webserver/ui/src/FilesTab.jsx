import { useEffect, useMemo, useRef, useState } from 'react';
import { Tree } from 'react-arborist';
import { fetchFileTree } from './api.js';
import { useChatComposer } from './contexts/ChatComposerContext.jsx';

export default function FilesTab({ taskId, workspaceVersion = 0 }) {
  const { appendToInput } = useChatComposer();
  const [state, setState] = useState({
    status: 'loading',
    trees: [],
    error: '',
  });
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

  return (
    <div className="files-tab">
      <div className="files-tab-body" ref={containerRef}>
        {state.status === 'loading' && (
          <p className="files-tab-message">Loading files…</p>
        )}
        {state.status === 'error' && (
          <p className="files-tab-message error">{state.error}</p>
        )}
        {state.status === 'ready' && state.trees.length === 0 && (
          <p className="files-tab-message">No tracked files in this task.</p>
        )}
        {state.status === 'ready' && state.trees.map((repoTree) => (
          <RepoTree
            key={repoTree.cwd || repoTree.repo_id}
            repoTree={repoTree}
            width={size.width}
            onPickFile={appendToInput}
          />
        ))}
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

function RepoTree({ repoTree, width, onPickFile }) {
  const treeData = useMemo(() => attachIds(repoTree.tree), [repoTree.tree]);
  const heading = repoTree.repo_id || repoTree.cwd || 'repo';
  const treeHeight = Math.max(120, Math.min(treeData.length * 22 + 8, 800));
  return (
    <section className="files-tab-repo">
      <header className="files-tab-repo-header" title={repoTree.cwd}>
        {heading}
      </header>
      {treeData.length === 0 ? (
        <p className="files-tab-message">No tracked files in this repo.</p>
      ) : (
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
      )}
    </section>
  );
}

function attachIds(nodes) {
  if (!Array.isArray(nodes)) { return []; }
  return nodes.map((node) => {
    const next = { ...node, id: node.path };
    if (Array.isArray(node.children)) {
      next.children = attachIds(node.children);
    }
    return next;
  });
}

function Node({ node, style, onPickFile }) {
  const isFolder = node.isInternal;
  function onActivate() {
    if (isFolder) {
      node.toggle();
      return;
    }
    if (typeof onPickFile === 'function') {
      onPickFile(node.data.path);
    }
  }
  return (
    <div
      className={'tree-row' + (node.isSelected ? ' selected' : '')}
      style={style}
      onClick={onActivate}
    >
      <span className="tree-row-icon">
        {isFolder ? (node.isOpen ? '▾' : '▸') : '·'}
      </span>
      <span className="tree-row-name">{node.data.name}</span>
    </div>
  );
}
