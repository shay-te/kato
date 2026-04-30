import { useEffect, useMemo, useRef, useState } from 'react';
import { Tree } from 'react-arborist';
import { fetchFileTree } from './api.js';
import { useChatComposer } from './contexts/ChatComposerContext.jsx';

export default function FilesTab({ taskId }) {
  const { appendToInput } = useChatComposer();
  const [state, setState] = useState({
    status: 'loading',
    tree: [],
    cwd: '',
    error: '',
  });
  const containerRef = useRef(null);
  const [size, setSize] = useState({ width: 320, height: 480 });

  useEffect(() => {
    if (!taskId) { return; }
    let cancelled = false;
    setState({ status: 'loading', tree: [], cwd: '', error: '' });
    fetchFileTree(taskId)
      .then((payload) => {
        if (cancelled) { return; }
        setState({
          status: 'ready',
          tree: payload.tree || [],
          cwd: payload.cwd || '',
          error: '',
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState({ status: 'error', tree: [], cwd: '', error: String(err) });
      });
    return () => { cancelled = true; };
  }, [taskId]);

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

  const treeData = useMemo(() => attachIds(state.tree), [state.tree]);

  return (
    <div className="files-tab">
      <header className="files-tab-header" title={state.cwd}>
        {state.cwd ? state.cwd : 'no cwd'}
      </header>
      <div className="files-tab-body" ref={containerRef}>
        {state.status === 'loading' && (
          <p className="files-tab-message">Loading files…</p>
        )}
        {state.status === 'error' && (
          <p className="files-tab-message error">{state.error}</p>
        )}
        {state.status === 'ready' && treeData.length === 0 && (
          <p className="files-tab-message">No tracked files in this repo.</p>
        )}
        {state.status === 'ready' && treeData.length > 0 && (
          <Tree
            data={treeData}
            width={size.width}
            height={size.height}
            rowHeight={22}
            indent={14}
            openByDefault={false}
            disableDrag
            disableDrop
            disableEdit
          >
            {(props) => <Node {...props} onPickFile={appendToInput} />}
          </Tree>
        )}
      </div>
    </div>
  );
}

// react-arborist needs a unique `id`; reuse `path`.
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
