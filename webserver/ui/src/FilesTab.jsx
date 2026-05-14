import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { Tree } from 'react-arborist';
import { fetchFileTree, fetchRepoCommits, syncTaskRepositories } from './api.js';
import AddRepositoryModal from './components/AddRepositoryModal.jsx';
import CommitDiffModal from './components/CommitDiffModal.jsx';
import Icon from './components/Icon.jsx';
import { useChatComposer } from './contexts/ChatComposerContext.jsx';
import { toast } from './stores/toastStore.js';
import {
  activateTreeNode,
  attachIds,
  matchTreeNode,
  normalizeTrees,
} from './FilesTabHelpers.js';


// Same auto-poll cadence as ChangesTab. Keeps the file tree in sync
// with disk when files change outside of Claude's tool flow (manual
// edits, pulls, syncs). Honors document visibility so a background
// kato tab doesn't keep hammering the server.
const AUTO_POLL_INTERVAL_MS = 5000;

export default function FilesTab({
  taskId,
  workspaceVersion = 0,
  focusFilterSignal = 0,
  onOpenFile,
}) {
  const { appendToInput } = useChatComposer();
  const [state, setState] = useState({
    status: 'loading',
    trees: [],
    error: '',
  });
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [query, setQuery] = useState('');
  // The input itself stays bound to ``query`` (controlled, no input
  // lag), but the tree filter reads ``deferredQuery`` so the
  // potentially expensive node walk in ``matchTreeNode`` runs in a
  // lower-priority render. On a huge workspace, typing into the
  // filter previously walked every tree node on each keystroke and
  // janked the input.
  const deferredQuery = useDeferredValue(query);
  // Bumped after a successful repo-sync OR the auto-poll. Both
  // funnel into the fetch effect's dep array so the tree re-renders
  // when either fires.
  const [syncTick, setSyncTick] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [addModalOpen, setAddModalOpen] = useState(false);
  const inFlightRef = useRef(false);
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
    inFlightRef.current = true;
    // Only flip to ``loading`` on the FIRST fetch for this taskId.
    // Subsequent refetches (driven by workspaceVersion bumps every 1.2s
    // during active tool use, or the auto-poll every 5s, or the
    // refresh button) keep the existing tree visible until the new
    // payload arrives — otherwise the tab body flashes "Loading…"
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
      })
      .finally(() => {
        if (cancelled) { return; }
        inFlightRef.current = false;
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion, syncTick]);

  // Auto-poll while the tab is mounted so external changes (manual
  // edits, pulls, the sync button on a different kato tab) appear
  // without waiting for a Claude tool event to bump
  // ``workspaceVersion``. Visibility-aware so a background tab
  // doesn't keep churning the file walker on the server.
  useEffect(() => {
    if (!taskId || typeof window === 'undefined') { return undefined; }
    let timerId = null;
    function tick() {
      if (typeof document !== 'undefined' && document.hidden) { return; }
      if (inFlightRef.current) { return; }
      setSyncTick((n) => n + 1);
    }
    timerId = window.setInterval(tick, AUTO_POLL_INTERVAL_MS);
    return () => {
      if (timerId !== null) { window.clearInterval(timerId); }
    };
  }, [taskId]);


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

  // Tracks repos already in the workspace so the "+ Add repository"
  // picker filters them out — same source the file tree uses, so no
  // extra fetch needed.
  const attachedRepoIds = useMemo(() => {
    const set = new Set();
    for (const tree of state.trees) {
      const id = String(tree?.repo_id || '').trim();
      if (id) { set.add(id.toLowerCase()); }
    }
    return set;
  }, [state.trees]);

  const toolbar = (
    <span className="files-tab-toolbar">
      <button
        type="button"
        className="files-tab-icon-btn"
        data-tooltip={
          'Add repository — pick from kato\'s inventory, tag the '
          + 'task with ``kato:repo:<id>``, and clone it into the '
          + 'workspace. Filters out repos already attached.'
        }
        aria-label="Add repository to task"
        onClick={() => setAddModalOpen(true)}
        disabled={!taskId}
      >
        <Icon name="folder-plus" />
      </button>
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
          onOpenFile={onOpenFile}
          searchTerm={deferredQuery}
          conflictedFiles={repoTree.conflictedFiles}
          taskId={taskId}
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
      {addModalOpen && (
        <AddRepositoryModal
          taskId={taskId}
          alreadyAttachedIds={attachedRepoIds}
          onClose={() => setAddModalOpen(false)}
          onAdded={() => {
            // Bump the sync tick so the file tree refetches and the
            // newly-cloned repo appears as a top-level entry without
            // waiting for the auto-poll.
            setSyncTick((n) => n + 1);
          }}
        />
      )}
    </div>
  );
}

// Render the sync-repos result into a toast title + message. Three
// outcomes the operator cares about, mapped to kind / wording:
//   * already in sync — green, "no missing repos"
//   * added N — green, lists the names so the operator can see what
//     showed up in the tree
//   * partial / failed — red or amber, surfaces the error
// Exported for tests. Pure mapping from a sync api result to the
// kind/title/message of the operator-facing toast.
export function formatSyncResult(result) {
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
  onOpenFile,
  searchTerm = '', conflictedFiles, taskId = '',
}) {
  const treeData = useMemo(() => {
    return attachIds(repoTree.tree, repoTree.cwd);
  }, [repoTree.tree, repoTree.cwd]);
  const heading = repoTree.repo_id || repoTree.cwd || 'repo';
  const repoId = String(repoTree.repo_id || '').trim();
  // While filtering, expand by default so the operator sees every
  // matching descendant without clicking through ancestor folders.
  const isFiltering = !!searchTerm.trim();
  const treeHeight = Math.max(120, Math.min(treeData.length * 22 + 8, 800));
  const chevronName = collapsed ? 'chevron-right' : 'chevron-down';
  // Per-repo commit dropdown state. Populated lazily on first
  // open so we don't fetch ``/commits`` for every repo on every
  // file-tree refetch (would be 5+ extra HTTP calls per
  // workspace-version bump otherwise).
  const [commitsState, setCommitsState] = useState({
    status: 'idle', items: [], error: '',
  });
  const [commitMenuOpen, setCommitMenuOpen] = useState(false);
  const [activeCommit, setActiveCommit] = useState(null);

  async function ensureCommitsLoaded() {
    if (!taskId || !repoId) { return; }
    if (commitsState.status === 'ready' || commitsState.status === 'loading') {
      return;
    }
    setCommitsState({ status: 'loading', items: [], error: '' });
    const result = await fetchRepoCommits(taskId, repoId, { limit: 50 });
    if (!result.ok) {
      setCommitsState({
        status: 'error', items: [],
        error: String(result.error || 'failed to load commits'),
      });
      return;
    }
    setCommitsState({
      status: 'ready',
      items: Array.isArray(result.body?.commits) ? result.body.commits : [],
      error: '',
    });
  }

  function toggleCommitMenu(event) {
    // Stop the click from bubbling to the header — header click
    // is "expand/collapse repo", which we explicitly DON'T want
    // when the operator clicks the commit-list icon.
    event.stopPropagation();
    if (!commitMenuOpen) { ensureCommitsLoaded(); }
    setCommitMenuOpen((prev) => !prev);
  }

  function pickCommit(commit) {
    setCommitMenuOpen(false);
    setActiveCommit(commit);
  }
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
            onOpenFile={onOpenFile}
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
        {repoId && taskId && (
          <button
            type="button"
            className="files-tab-repo-commits-btn"
            onClick={toggleCommitMenu}
            aria-haspopup="listbox"
            aria-expanded={commitMenuOpen ? 'true' : 'false'}
            data-tooltip="View changes from a commit on this repo's task branch"
            aria-label={`View commit history for ${heading}`}
          >
            <Icon name="commit" />
          </button>
        )}
      </header>
      {commitMenuOpen && (
        <CommitDropdown
          state={commitsState}
          onPick={pickCommit}
          onClose={() => setCommitMenuOpen(false)}
        />
      )}
      {body}
      {activeCommit && (
        <CommitDiffModal
          taskId={taskId}
          repoId={repoId}
          commit={activeCommit}
          onClose={() => setActiveCommit(null)}
        />
      )}
    </section>
  );
}


function CommitDropdown({ state, onPick, onClose }) {
  // Light-touch "click outside" behaviour: a backdrop catches
  // outside clicks without trapping mouse events on the rest of
  // the page (a real popover library would be overkill for one
  // dropdown).
  return (
    <>
      <div
        className="files-tab-commit-backdrop"
        onClick={onClose}
        aria-hidden="true"
      />
      <ul className="files-tab-commit-menu" role="listbox">
        {state.status === 'loading' && (
          <li className="files-tab-commit-empty">Loading commits…</li>
        )}
        {state.status === 'error' && (
          <li className="files-tab-commit-empty error">{state.error}</li>
        )}
        {state.status === 'ready' && state.items.length === 0 && (
          <li className="files-tab-commit-empty">
            No commits on the task branch yet.
          </li>
        )}
        {state.status === 'ready' && state.items.map((commit) => (
          <li key={commit.sha}>
            <button
              type="button"
              role="option"
              className="files-tab-commit-row"
              onClick={() => onPick(commit)}
              aria-selected="false"
              title={commit.sha}
            >
              <code className="files-tab-commit-sha">{commit.short_sha}</code>
              <span className="files-tab-commit-subject">
                {commit.subject || '(no subject)'}
              </span>
              <span className="files-tab-commit-author">{commit.author}</span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

function Node({ node, style, onPickFile, onOpenFile, conflictedFiles }) {
  const isFolder = node.isInternal;
  function onActivate() {
    // Folder click → toggle expand. File click → also notify the
    // EditorPane (so the file opens in the middle Monaco view)
    // before the original "paste relative path into chat" behavior.
    // Both are useful: the editor preview + the chat reference.
    if (!isFolder && typeof onOpenFile === 'function') {
      onOpenFile({
        absolutePath: String(node.data?.path || ''),
        relativePath: String(node.data?.relativePath || ''),
      });
    }
    activateTreeNode(node, onPickFile);
  }
  // Right-click pastes the FULL absolute path of whatever was
  // clicked — file OR folder — into the chat composer. Folders
  // can't be left-click-pasted (left-click toggles them open),
  // and even for files the absolute form is what the operator
  // usually wants when they're going to ask Claude to operate on
  // a directory tree from elsewhere on disk. ``preventDefault``
  // suppresses the browser's native context menu so the operator
  // doesn't have to dismiss it after the paste lands.
  function onContextMenu(event) {
    event.preventDefault();
    if (typeof onPickFile !== 'function') { return; }
    const absolutePath = String(node.data?.path || '').trim();
    if (!absolutePath) { return; }
    onPickFile(absolutePath);
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
  // Tooltip: spell out left- vs right-click semantics so the
  // right-click affordance is discoverable. Conflict tooltip wins
  // when set since it's the more urgent signal.
  let tooltip;
  if (isConflicted) {
    tooltip = 'Merge conflict — needs resolution';
  } else if (isFolder) {
    tooltip = 'Click to expand · right-click to paste this folder’s path into the chat';
  } else {
    tooltip = 'Click to paste the relative path · right-click to paste the absolute path';
  }
  return (
    <div
      className={rowClass}
      style={style}
      onClick={onActivate}
      onContextMenu={onContextMenu}
      title={tooltip}
    >
      <span className="tree-row-icon"><Icon name={iconName} /></span>
      <span className="tree-row-name">{node.data.name}</span>
      {isConflicted && (
        <span className="tree-row-conflict" aria-label="merge conflict">⚠</span>
      )}
    </div>
  );
}
