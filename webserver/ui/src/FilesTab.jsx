import { useEffect, useMemo, useRef, useState } from 'react';
import { Tree } from 'react-arborist';
import { fetchFileTree, syncTaskRepositories } from './api.js';
import Icon from './components/Icon.jsx';
import { useChatComposer } from './contexts/ChatComposerContext.jsx';
import { toast } from './stores/toastStore.js';
import {
  activateTreeNode,
  attachIds,
  matchTreeNode,
  normalizeTrees,
} from './FilesTabHelpers.js';

export default function FilesTab({
  taskId,
  workspaceVersion = 0,
  focusFilterSignal = 0,
}) {
  const { appendToInput } = useChatComposer();
  const [state, setState] = useState({
    status: 'loading',
    trees: [],
    error: '',
  });
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [query, setQuery] = useState('');
  // Bumped after a successful repo-sync so the file tree re-fetches
  // and the newly-cloned folders appear without waiting for the
  // next tool-result-driven workspaceVersion bump.
  const [syncTick, setSyncTick] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const containerRef = useRef(null);
  const filterInputRef = useRef(null);
  const [size, setSize] = useState({ width: 320, height: 480 });

  // Cmd/Ctrl+P from the parent flips the right pane to Files (already
  // handled in RightPane) and bumps ``focusFilterSignal``; on every
  // bump we focus + select the input so the operator's first
  // keystroke after the shortcut goes into the filter, not somewhere
  // else.
  useEffect(() => {
    if (focusFilterSignal === 0) { return; }
    const node = filterInputRef.current;
    if (!node) { return; }
    node.focus();
    node.select();
  }, [focusFilterSignal]);

  // Reset the filter when switching tasks — every task has its own
  // file tree, so a stale query from the previous task would be
  // confusing if the same string doesn't match anything in the new
  // tree.
  useEffect(() => {
    setQuery('');
  }, [taskId]);

  useEffect(() => {
    if (!taskId) { return; }
    let cancelled = false;
    // Only flip to ``loading`` on the FIRST fetch for this taskId.
    // Subsequent refetches (driven by workspaceVersion bumps every 1.2s
    // during active tool use) keep the existing tree visible until the
    // new payload arrives — otherwise the tab body flashes "Loading…"
    // between every turn.
    setState((prev) => (
      prev.status === 'ready' || prev.status === 'error'
        ? prev
        : { status: 'loading', trees: [], error: '' }
    ));
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
        setState((prev) => ({
          status: 'error',
          trees: prev.trees,
          error: String(err),
        }));
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion, syncTick]);

  // Blank state on task switch so we don't show stale data while
  // the new fetch is in flight.
  useEffect(() => {
    setState({ status: 'loading', trees: [], error: '' });
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

  // Sync icon: re-resolve the task's repositories from YouTrack /
  // Jira / etc. tags + description, and clone any that aren't yet on
  // disk. Pure additive — repos already cloned (or repos no longer
  // on the task) stay untouched. Lets the operator add a
  // ``kato:repo:<name>`` tag and pull the new repo into the
  // workspace from the UI without re-running the whole task.
  async function onSyncRepositories() {
    if (!taskId || syncing) { return; }
    setSyncing(true);
    const result = await syncTaskRepositories(taskId);
    setSyncing(false);
    const { title, message, kind } = formatSyncResult(result);
    toast.show({
      kind,
      title,
      message,
      durationMs: kind === 'error' ? 12000 : 7000,
    });
    // Bump the local sync-tick so the file tree refetches and any
    // newly-cloned repos render. Even on a no-op sync the refetch
    // is harmless and keeps the tree in sync with disk.
    if (result.ok) { setSyncTick((n) => n + 1); }
  }

  const toolbar = (
    <span className="files-tab-toolbar">
      <button
        type="button"
        className="files-tab-icon-btn"
        data-tooltip={
          'Sync repositories — clone any repos this task touches '
          + 'that aren’t in the workspace yet (driven by '
          + '``kato:repo:<name>`` tags + description). Never removes '
          + 'a repo from disk; purely additive.'
        }
        aria-label="Sync task repositories"
        onClick={onSyncRepositories}
        disabled={syncing || !taskId}
      >
        <Icon name="refresh" spin={syncing} />
      </button>
      {repoIds.length > 1 && (
        <>
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
        </>
      )}
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
          searchTerm={query}
          conflictedFiles={repoTree.conflictedFiles}
        />
      );
    });
  }

  const filterRow = (
    <div className="files-tab-filter">
      <input
        ref={filterInputRef}
        type="search"
        className="files-tab-filter-input"
        placeholder="Search files… (Cmd+P / Ctrl+P)"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') { setQuery(''); } }}
        aria-label="Search files in this task's workspace"
        spellCheck={false}
        autoComplete="off"
      />
      {query && (
        <button
          type="button"
          className="files-tab-filter-clear"
          onClick={() => setQuery('')}
          aria-label="Clear search"
          title="Clear (Esc)"
        >
          ×
        </button>
      )}
    </div>
  );

  const header = (
    <header className="files-tab-header">
      {filterRow}
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

// Render the sync-repos result into a toast title + message. Three
// outcomes the operator cares about, mapped to kind / wording:
//   * already in sync — green, "no missing repos"
//   * added N — green, lists the names so the operator can see what
//     showed up in the tree
//   * partial / failed — red or amber, surfaces the error
function formatSyncResult(result) {
  const body = (result && result.body) || {};
  if (!result || !result.ok) {
    return {
      kind: 'error',
      title: 'Sync repositories failed',
      message: (result && result.error) || body.error || 'unknown error',
    };
  }
  const added = body.added_repositories || [];
  const failed = body.failed_repositories || [];
  if (failed.length) {
    const errs = failed
      .map((entry) => `${entry.repository_id}: ${entry.error}`)
      .join('\n');
    return {
      kind: added.length ? 'warning' : 'error',
      title: added.length ? 'Sync partially succeeded' : 'Sync failed',
      message: added.length
        ? `✓ added ${added.length} repo(s): ${added.join(', ')}\n✗ ${errs}`
        : `✗ ${errs}`,
    };
  }
  if (added.length === 0) {
    return {
      kind: 'success',
      title: 'Repositories already in sync',
      message: 'No missing repositories — the workspace already has every repo this task touches.',
    };
  }
  return {
    kind: 'success',
    title: `Added ${added.length} repository(ies)`,
    message: `✓ cloned: ${added.join(', ')}`,
  };
}


function RepoTree({
  repoTree, width, collapsed, onToggle, onPickFile,
  searchTerm = '', conflictedFiles,
}) {
  const treeData = useMemo(() => {
    return attachIds(repoTree.tree, repoTree.cwd);
  }, [repoTree.tree, repoTree.cwd]);
  const heading = repoTree.repo_id || repoTree.cwd || 'repo';
  // While filtering, expand by default so the operator sees every
  // matching descendant without clicking through ancestor folders.
  const isFiltering = !!searchTerm.trim();
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
        openByDefault={isFiltering}
        searchTerm={searchTerm}
        searchMatch={matchTreeNode}
        disableDrag
        disableDrop
        disableEdit
      >
        {(props) => (
          <Node
            {...props}
            onPickFile={onPickFile}
            conflictedFiles={conflictedFiles}
          />
        )}
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

function Node({ node, style, onPickFile, conflictedFiles }) {
  const isFolder = node.isInternal;
  function onActivate() {
    activateTreeNode(node, onPickFile);
  }
  const isConflicted = !isFolder
    && conflictedFiles
    && conflictedFiles.size > 0
    && conflictedFiles.has(node.data.relativePath);
  const rowClass = [
    'tree-row',
    node.isSelected ? 'selected' : '',
    isConflicted ? 'conflicted' : '',
  ].filter(Boolean).join(' ');
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
      title={isConflicted ? 'Merge conflict — needs resolution' : undefined}
    >
      <span className="tree-row-icon"><Icon name={iconName} /></span>
      <span className="tree-row-name">{node.data.name}</span>
      {isConflicted && (
        <span className="tree-row-conflict" aria-label="merge conflict">⚠</span>
      )}
    </div>
  );
}
