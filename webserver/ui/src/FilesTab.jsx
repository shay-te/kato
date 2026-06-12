import {
  useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import { Tree } from 'react-arborist';
import {
  fetchDiff,
  fetchFileTree,
  fetchRepoCommits,
  fetchTaskComments,
  recheckRepositoryPush,
  syncTaskRepositories,
} from './api.js';
import AddRepositoryModal from './components/AddRepositoryModal.jsx';
import CommitDiffModal from './components/CommitDiffModal.jsx';
import ContentSearchResults from './components/ContentSearchResults.jsx';
import DiffKindIcon from './components/DiffKindIcon.jsx';
import Icon from './components/Icon.jsx';
import StickyHeader from './components/StickyHeader.jsx';
import { useChatComposer } from './contexts/ChatComposerContext.jsx';
import {
  buildDiffFileTree,
  changedFileOpenTarget,
  countFileChangeStats,
  diffDisplayPath,
  isFileConflicted,
  parseRepoDiffs,
} from './diffModel.js';
import { toastResult } from './stores/toastStore.js';
import { copyRepoRelativePath } from './utils/clipboard.js';
import {
  activateTreeNode,
  attachIds,
  countRepoComments,
  folderContainsChange,
  matchTreeNode,
  normalizeTrees,
  repoCommentStatus,
} from './FilesTabHelpers.js';
import { cssEscapeAttr } from './utils/dom.js';
import { countNoun } from './utils/pluralize.js';
import { apiErrorMessage } from './utils/apiError.js';
import { REPOSITORY_TAG_PREFIX } from './utils/katoTags.js';
import {
  fileTreeCommentStatus,
  moreUrgentCommentStatus,
} from './utils/commentStatus.js';
import { useDismissOnOutsidePointerOrEscape } from './hooks/useDismissOnOutsidePointerOrEscape.js';


// Same auto-poll cadence as ChangesTab. Keeps the file tree in sync
// with disk when files change outside of Claude's tool flow (manual
// edits, pulls, syncs). Honors document visibility so a background
// kato tab doesn't keep hammering the server.
const AUTO_POLL_INTERVAL_MS = 5000;
// Depth-0 left inset (px) for the full (arborist) tree, so its top-level
// chevron aligns with the repo header chevron and the Changes tree.
const TREE_ROW_DEPTH0_INSET = 10;
const EMPTY_DIFF_META = new Map();
const EMPTY_COMMENT_META = new Map();
const EMPTY_STATS = { added: 0, deleted: 0 };

// repoKey -> Map(repo-relative file path -> { count, status }). A
// "thread" is a top-of-thread comment (``parent_id`` empty); replies
// don't add to the count, matching the Bitbucket 💬 N convention.
// ``status`` is the most-urgent kato_status across the file's open
// threads (see moreUrgentCommentStatus in utils/commentStatus.js), used
// to tint the badge. Outdated anchors still count: clicking the badge
// opens the file panel where those unanchored comments are shown.
export function buildFilesCommentMeta(comments) {
  const byRepo = new Map();
  for (const comment of comments || []) {
    if (String(comment?.parent_id || '')) { continue; }
    if (comment?.status === 'resolved') { continue; }
    const filePath = String(comment?.file_path || '').trim();
    if (!filePath) { continue; }
    const repoId = String(comment?.repo_id || '').trim();
    const key = repoId || '';
    let fileMap = byRepo.get(key);
    if (!fileMap) { fileMap = new Map(); byRepo.set(key, fileMap); }
    const prev = fileMap.get(filePath) || { count: 0, status: '' };
    fileMap.set(filePath, {
      count: prev.count + 1,
      status: moreUrgentCommentStatus(
        prev.status, fileTreeCommentStatus(comment),
      ),
    });
  }
  return byRepo;
}
export default function FilesTab({
  taskId,
  workspaceVersion = 0,
  focusFilterSignal = 0,
  focusFileTarget = null,
  openFile = null,
  onOpenFile,
}) {
  const { appendToInput } = useChatComposer();
  const [state, setState] = useState({
    status: 'loading',
    trees: [],
    diffMetaByRepo: new Map(),
    commentMetaByRepo: new Map(),
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
  const [showAllFiles, setShowAllFiles] = useState(false);
  const [pathMenu, setPathMenu] = useState(null);
  const inFlightRef = useRef(false);
  const containerRef = useRef(null);
  const filterInputRef = useRef(null);
  // Last scroll offset the operator left the tree at. The 5s auto-poll
  // and the ~1.2s workspace-version bump both replace ``state.trees``
  // with fresh objects; the commit that follows can momentarily drop
  // the scroll container's content height and the browser clamps
  // ``scrollTop`` back to 0, snapping the tree to the top mid-read. We
  // record the offset on every scroll and re-apply it in a layout
  // effect (below) after each data-driven render, before paint.
  const scrollTopRef = useRef(0);
  // Last focus-request id this effect acted on. The focus effect below
  // depends on data-refresh values (state.trees / diffMeta) so it
  // re-fires on every 5s poll + 1.2s workspace bump; without this guard
  // it cleared the search and re-expanded the repo every few seconds
  // (the operator's "the tree changes/scrolls by itself" report). Only
  // act when the operator actually clicks a new file (a new requestId).
  const handledFilesFocusRef = useRef(0);
  // Signature of the last file-tree/diff/comments payload committed.
  // The fetch effect re-runs every 5s (poll) + ~1.2s (workspaceVersion
  // bump) and used to rebuild + replace the whole tree state each time —
  // re-cloning every node (attachIds), re-sorting the changed tree
  // (buildDiffFileTree) and re-rendering every row — even when nothing
  // on disk changed. Skip the rebuild + setState when the bytes match.
  const fetchSigRef = useRef('');
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
    if (!focusFileTarget || state.status !== 'ready') { return; }
    // Skip re-fires from background refreshes (same request already
    // handled) — only a fresh operator click bumps requestId. The
    // data-refresh deps stay so a click that lands before the tree is
    // ready still applies once the file appears.
    if (focusFileTarget.requestId === handledFilesFocusRef.current) { return; }
    const targetPath = String(focusFileTarget.relativePath || '').trim();
    if (!targetPath) { return; }
    for (const repoTree of state.trees) {
      const repoKey = repoTree.repo_id || repoTree.cwd;
      const diffMeta = state.diffMetaByRepo.get(repoKey) || EMPTY_DIFF_META;
      if (!focusTargetMatchesRepo(focusFileTarget, repoTree, state.trees.length)) {
        continue;
      }
      if (!diffMeta.has(targetPath)) { continue; }
      handledFilesFocusRef.current = focusFileTarget.requestId;
      setQuery('');
      setShowAllFiles(false);
      setCollapsed((prev) => {
        if (!prev.has(repoKey)) { return prev; }
        const next = new Set(prev);
        next.delete(repoKey);
        return next;
      });
      break;
    }
  }, [focusFileTarget, state.status, state.trees, state.diffMetaByRepo]);

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
        : {
            status: 'loading', trees: [], diffMetaByRepo: new Map(),
            commentMetaByRepo: new Map(), error: '',
          }
    ));
    // Fetch the RAW payloads in parallel; the diff/comments fetches
    // degrade to null (decoration only — the tree still renders) while a
    // file-tree failure propagates to the error state below.
    Promise.all([
      fetchFileTree(taskId),
      fetchDiff(taskId).catch((err) => {
        console.warn('Failed to load file-tree diff metadata', err);
        return null;
      }),
      fetchTaskComments(taskId).catch(() => null),
    ])
      .then(([payload, diffPayload, commentResult]) => {
        if (cancelled) { return; }
        const commentsRaw = Array.isArray(commentResult?.body?.comments)
          ? commentResult.body.comments : [];
        // Unchanged bytes → keep the existing state object (and every
        // per-repo Map/Set identity) so the tree's useMemos and child
        // rows bail instead of re-cloning + re-rendering on an idle poll.
        const sig = JSON.stringify([payload, diffPayload, commentsRaw]);
        if (sig === fetchSigRef.current) { return; }
        fetchSigRef.current = sig;
        let diffMetaByRepo = new Map();
        if (diffPayload) {
          try { diffMetaByRepo = buildFilesDiffMeta(parseRepoDiffs(diffPayload)); }
          catch (err) { console.warn('Failed to parse file-tree diff metadata', err); }
        }
        setState({
          status: 'ready',
          trees: normalizeTrees(payload),
          diffMetaByRepo,
          commentMetaByRepo: buildFilesCommentMeta(commentsRaw),
          error: '',
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState((prev) => ({
          status: 'error',
          trees: prev.trees,
          diffMetaByRepo: prev.diffMetaByRepo,
          commentMetaByRepo: prev.commentMetaByRepo,
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
  // the new fetch is in flight. Also drop the saved scroll offset —
  // a different task's tree must open at the top, not wherever the
  // previous one was left.
  useEffect(() => {
    scrollTopRef.current = 0;
    // Drop the payload signature so the new task always rebuilds, even in
    // the unlikely event its first payload byte-matches the old task's.
    fetchSigRef.current = '';
    setState({
      status: 'loading',
      trees: [],
      diffMetaByRepo: new Map(),
      commentMetaByRepo: new Map(),
      error: '',
    });
  }, [taskId]);

  // Re-apply the saved scroll offset after a data refresh re-renders
  // the tree. Runs before paint so a clamped-to-0 scrollTop never
  // becomes visible. No-op when the browser already preserved the
  // position (set to the same value it's already at).
  useLayoutEffect(() => {
    const node = containerRef.current;
    if (!node) { return; }
    const saved = scrollTopRef.current;
    if (saved > 0 && node.scrollTop !== saved) {
      node.scrollTop = saved;
    }
  }, [state.trees, state.diffMetaByRepo, state.commentMetaByRepo]);

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
  // "Try again" on a read-only repo badge: re-test push access, then reload the
  // tree so the badge reflects the new state (cleared once push is granted).
  async function recheckPush(repoId) {
    const id = String(repoId || '').trim();
    if (!taskId || !id) { return; }
    try {
      await recheckRepositoryPush(taskId, id);
    } finally {
      setSyncTick((n) => n + 1);
    }
  }
  function openPathMenu(event, relativePath, repoId = '') {
    event.preventDefault();
    event.stopPropagation();
    const path = String(relativePath || '').trim();
    if (!path) { return; }
    setPathMenu({
      x: event.clientX,
      y: event.clientY,
      relativePath: path,
      repoId: String(repoId || '').trim(),
    });
  }
  function closePathMenu() {
    setPathMenu(null);
  }
  async function copyPathMenuRelativePath() {
    const repoId = pathMenu?.repoId;
    const path = String(pathMenu?.relativePath || '').trim();
    closePathMenu();
    await copyRepoRelativePath(repoId, path);
  }

  useDismissOnOutsidePointerOrEscape(pathMenu, closePathMenu);

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
    toastResult(formatSyncResult(result));
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
  const hasChangedFiles = useMemo(() => {
    for (const fileMeta of state.diffMetaByRepo.values()) {
      if (fileMeta.size > 0) { return true; }
    }
    return false;
  }, [state.diffMetaByRepo]);
  const allFilesButtonClass = [
    'files-tab-text-btn',
    showAllFiles ? 'active' : '',
  ].filter(Boolean).join(' ');
  const allFilesToggle = hasChangedFiles ? (
    <button
      type="button"
      className={allFilesButtonClass}
      data-tooltip={showAllFiles ? 'Showing all files' : 'Show all files'}
      aria-label={showAllFiles ? 'Showing all files' : 'Show all files'}
      aria-pressed={showAllFiles ? 'true' : 'false'}
      onClick={() => setShowAllFiles((prev) => !prev)}
    >
      All
    </button>
  ) : null;

  const toolbar = (
    <span className="files-tab-toolbar">
      {allFilesToggle}
      <button
        type="button"
        className="files-tab-icon-btn"
        data-tooltip={
          'Add repository — pick from kato\'s inventory, tag the '
          + `task with \`\`${REPOSITORY_TAG_PREFIX}<id>\`\`, and clone it into the `
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
          + `\`\`${REPOSITORY_TAG_PREFIX}<name>\`\` tags + description). Never removes `
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
    // Which repo's tree shows the selection. With an explicit repoId the
    // owner is that repo; a repo-LESS open file (e.g. the chat comment-jump
    // passes only a path) resolves to the FIRST repo whose changed set
    // contains the path — mirroring DiffPane's path-only fallback so the
    // tree and the centre pane agree, and so only ONE repo ever highlights
    // (the multi-repo double-highlight this derivation exists to prevent).
    const selectionRepoKey = resolveSelectionRepoKey(
      openFile, state.trees, state.diffMetaByRepo,
    );
    body = state.trees.map((repoTree) => {
      const repoKey = repoTree.repo_id || repoTree.cwd;
      const diffMeta = state.diffMetaByRepo.get(repoKey) || EMPTY_DIFF_META;
      const commentMeta = state.commentMetaByRepo.get(repoTree.repo_id)
        || state.commentMetaByRepo.get(repoKey)
        || state.commentMetaByRepo.get('')
        || EMPTY_COMMENT_META;
      return (
        <RepoTree
          key={repoKey}
          repoTree={repoTree}
          width={size.width}
          collapsed={collapsed.has(repoKey)}
          onToggle={() => toggleRepo(repoKey)}
          onPickFile={appendToInput}
          onOpenFile={onOpenFile}
          onOpenPathMenu={openPathMenu}
          onRecheckPush={recheckPush}
          searchTerm={deferredQuery}
          conflictedFiles={repoTree.conflictedFiles}
          changedFiles={repoTree.changedFiles}
          diffMeta={diffMeta}
          commentMeta={commentMeta}
          showAllFiles={showAllFiles}
          taskId={taskId}
          focusFileTarget={focusFileTarget}
          openFile={selectionRepoKey === repoKey ? openFile : null}
        />
      );
    });
  }

  const filterRow = (
    <div className="files-tab-filter">
      <span className="files-tab-filter-icon" aria-hidden="true">
        <Icon name="search" />
      </span>
      <input
        ref={filterInputRef}
        type="search"
        className="files-tab-filter-input"
        placeholder="Search files… (Cmd+P)"
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
  // Content (grep) results — only when there's a query. Shown above the
  // (filename-filtered) trees so a symbol like ``project_list`` is findable
  // by its CONTENT, not just by filename.
  const contentResults = deferredQuery.trim().length >= 2 ? (
    <ContentSearchResults
      taskId={taskId}
      query={deferredQuery}
      onOpenFile={onOpenFile}
    />
  ) : null;

  return (
    <div className="files-tab">
      {header}
      <div
        className="files-tab-body"
        ref={containerRef}
        onScroll={(e) => { scrollTopRef.current = e.currentTarget.scrollTop; }}
      >
        {contentResults}
        {body}
      </div>
      {pathMenu && (
        <div
          className="files-tab-context-menu"
          style={{ left: pathMenu.x, top: pathMenu.y }}
          onPointerDown={(event) => event.stopPropagation()}
          role="menu"
        >
          <button
            type="button"
            className="files-tab-context-menu-item"
            onClick={copyPathMenuRelativePath}
            role="menuitem"
          >
            Copy relative path
          </button>
        </div>
      )}
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
    // Canonical precedence (body.error → result.error → fallback) via
    // apiErrorMessage, matching every other error toast (DUP-11: aligned).
    return {
      kind: 'error',
      title: 'Sync repositories failed',
      message: apiErrorMessage(result, 'unknown error'),
    };
  }
  const added = body.added_repositories || [];
  const failed = body.failed_repositories || [];
  // The Claude CLI bakes its --add-dir set at spawn time, so a chat
  // tab that opened before the new clone landed CANNOT see it — the
  // operator must close + reopen the tab for the sandbox to widen.
  // Backend computes this flag by comparing the live session's
  // allowed_additional_dirs() against the freshly-provisioned paths.
  const restartNeeded = !!body.requires_session_restart;
  const restartHint = restartNeeded
    ? '\n⟳ Close and reopen the chat tab for Claude to see the new repo(s) — its sandbox is fixed at spawn time.'
    : '';
  if (failed.length) {
    const errs = failed
      .map((entry) => `${entry.repository_id}: ${entry.error}`)
      .join('\n');
    return {
      kind: added.length ? 'warning' : 'error',
      title: added.length ? 'Sync partially succeeded' : 'Sync failed',
      message: added.length
        ? `✓ added ${added.length} repo(s): ${added.join(', ')}\n✗ ${errs}${restartHint}`
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
    kind: restartNeeded ? 'warning' : 'success',
    title: restartNeeded
      ? `Added ${added.length} repository(ies) — restart chat tab`
      : `Added ${added.length} repository(ies)`,
    message: `✓ cloned: ${added.join(', ')}${restartHint}`,
  };
}

export function buildFilesDiffMeta(repoDiffs) {
  const byRepo = new Map();
  for (const repoDiff of repoDiffs || []) {
    const fileMeta = new Map();
    for (const file of repoDiff.files || []) {
      const path = diffDisplayPath(file);
      fileMeta.set(path, {
        file,
        kind: file.type || 'modify',
        stats: countFileChangeStats(file),
      });
    }
    const repoId = String(repoDiff.repo_id || '').trim();
    const cwd = String(repoDiff.cwd || '').trim();
    if (repoId) { byRepo.set(repoId, fileMeta); }
    if (cwd) { byRepo.set(cwd, fileMeta); }
  }
  return byRepo;
}


// Inline repo-header summary: ``+N −M`` git stats and a comment count.
// Returns null when there's nothing to show (clean repo, no comments).
function renderRepoHeaderStats(stats, commentCount, commentStatus = '') {
  const added = Number(stats?.added) || 0;
  const deleted = Number(stats?.deleted) || 0;
  if (!added && !deleted && !commentCount) { return null; }
  const commentClass = [
    'files-tab-repo-comments',
    commentStatus ? `is-${commentStatus}` : '',
  ].filter(Boolean).join(' ');
  return (
    <span className="files-tab-repo-stats">
      {added > 0 && <span className="files-tab-repo-added">+{added}</span>}
      {deleted > 0 && <span className="files-tab-repo-deleted">−{deleted}</span>}
      {commentCount > 0 && (
        <span
          className={commentClass}
          title={`${commentCount} open comment(s)`}
        >
          <Icon name="comment" />
          {commentCount}
        </span>
      )}
    </span>
  );
}


function RepoTree({
  repoTree, width, collapsed, onToggle, onPickFile,
  onOpenFile, onOpenPathMenu, onRecheckPush,
  searchTerm = '', conflictedFiles, changedFiles, diffMeta = EMPTY_DIFF_META,
  commentMeta = EMPTY_COMMENT_META,
  showAllFiles = false, taskId = '', focusFileTarget = null,
  openFile = null,
}) {
  const repoRef = useRef(null);
  // Last focus-request id we scrolled/expanded for. The focus effect
  // below lists changedTree.nodes + repoTree in its deps (both get new
  // identities on every poll), so it re-fires constantly; this guard
  // makes it act once per operator click instead of smooth-scrolling
  // the tree and re-opening folders every few seconds.
  const handledChangedFocusRef = useRef(0);
  const treeData = useMemo(() => {
    return attachIds(repoTree.tree, repoTree.cwd);
  }, [repoTree.tree, repoTree.cwd]);
  const heading = repoTree.repo_id || repoTree.cwd || 'repo';
  const repoId = String(repoTree.repo_id || '').trim();
  const changedFilesList = useMemo(() => {
    return Array.from(diffMeta.values())
      .map((meta) => meta.file)
      .filter(Boolean);
  }, [diffMeta]);
  const changedTree = useMemo(() => {
    return buildDiffFileTree(changedFilesList);
  }, [changedFilesList]);
  // Inline header summary: git +/− totals for this repo and its open
  // comment count, so the operator reads the repo's state without
  // expanding it. Built before the return to keep the JSX logic-free.
  const headerStats = renderRepoHeaderStats(
    changedTree.stats,
    countRepoComments(commentMeta),
    repoCommentStatus(commentMeta, moreUrgentCommentStatus),
  );
  const filteredChangedNodes = useMemo(() => {
    return filterChangedFileTree(changedTree.nodes, searchTerm);
  }, [changedTree.nodes, searchTerm]);
  const hasChangedFiles = changedTree.nodes.length > 0;
  // While filtering, expand by default so the operator sees every
  // matching descendant without clicking through ancestor folders.
  const isFiltering = !!searchTerm.trim();
  const treeHeight = Math.max(120, Math.min(treeData.length * 28 + 8, 800));
  const chevronName = collapsed ? 'chevron-right' : 'chevron-down';
  const [closedChangedFolders, setClosedChangedFolders] = useState(() => new Set());
  // Selection is DERIVED from the centre pane's open file — the single
  // source of truth — instead of per-repo local state. With local state,
  // each RepoTree kept its own stale "selected" row: in a multi-repo task
  // two repos could highlight simultaneously, and ArrowUp/ArrowDown
  // resumed from a row that wasn't the file actually on screen.
  const selectedChangedKey = useMemo(
    () => changedSelectionKeyFor(openFile, repoId, diffMeta),
    [openFile, repoId, diffMeta],
  );
  // Per-repo commit dropdown state. Populated lazily on first
  // open so we don't fetch ``/commits`` for every repo on every
  // file-tree refetch (would be 5+ extra HTTP calls per
  // workspace-version bump otherwise).
  const [commitsState, setCommitsState] = useState({
    status: 'idle', items: [], error: '',
  });
  const [commitMenuOpen, setCommitMenuOpen] = useState(false);
  const [activeCommit, setActiveCommit] = useState(null);

  useEffect(() => {
    if (!focusTargetMatchesRepo(focusFileTarget, repoTree, 1)) { return undefined; }
    // Act only on a fresh focus request. changedTree.nodes / repoTree in
    // the deps re-fire this on every background refresh; without this
    // the tree scrolled itself + re-opened folders every poll.
    const requestId = focusFileTarget?.requestId || 0;
    if (requestId === handledChangedFocusRef.current) { return undefined; }
    const targetPath = String(focusFileTarget?.relativePath || '').trim();
    if (!targetPath) { return undefined; }
    const focusInfo = findChangedFileFocusInfo(changedTree.nodes, targetPath);
    if (!focusInfo) { return undefined; }
    handledChangedFocusRef.current = requestId;
    // (Selection itself derives from openFile — the focus request always
    // names the file already open in the centre, so the highlight is
    // already on it; this effect's job is expand + scroll only.)
    setClosedChangedFolders((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const key of focusInfo.ancestorKeys) {
        if (next.delete(key)) { changed = true; }
      }
      return changed ? next : prev;
    });
    const timer = window.requestAnimationFrame(() => {
      const root = repoRef.current;
      if (!root) { return; }
      const selector = `[data-changed-file-path="${cssEscapeAttr(targetPath)}"]`;
      const row = root.querySelector(selector);
      if (row && typeof row.scrollIntoView === 'function') {
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });
    return () => window.cancelAnimationFrame(timer);
  }, [
    focusFileTarget,
    focusFileTarget?.requestId,
    searchTerm,
    changedTree.nodes,
    repoTree,
  ]);

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
  function toggleChangedFolder(key) {
    setClosedChangedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(key)) { next.delete(key); } else { next.add(key); }
      return next;
    });
  }
  function selectChangedFile(file, { focusComment = false } = {}) {
    // Opening the file IS selecting it — the highlight derives from the
    // openFile round-trip (App state), keeping every surface in sync.
    if (typeof onOpenFile === 'function') {
      onOpenFile({
        ...changedFileOpenTarget({ cwd: repoTree.cwd, repo_id: repoId }, file),
        focusComment,
      });
    }
  }
  // Visible files in render order (closed folders hide their children) —
  // the ArrowUp/ArrowDown walk order for keyboard navigation.
  const visibleChangedFiles = useMemo(
    () => listVisibleChangedFiles(filteredChangedNodes, closedChangedFolders),
    [filteredChangedNodes, closedChangedFolders],
  );
  function handleChangedTreeKeyDown(event) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') { return; }
    // Leave modifier combos to the browser/OS (Cmd+ArrowUp = scroll to
    // top on macOS, Alt/Shift selections, etc.) — only the bare arrows
    // drive the file walk.
    if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) { return; }
    event.preventDefault();
    const files = visibleChangedFiles;
    if (files.length === 0) { return; }
    const delta = event.key === 'ArrowDown' ? 1 : -1;
    const index = files.findIndex(
      (file) => changedFileSelectionKey(file) === selectedChangedKey,
    );
    // No current selection: ArrowDown starts at the first file,
    // ArrowUp at the last. At either end the selection stays put.
    // (Scope note: the walk covers THIS repo's tree — each repo's tree is
    // its own focusable widget; arrows don't cross repo boundaries.)
    const nextIndex = index === -1
      ? (delta > 0 ? 0 : files.length - 1)
      : Math.min(files.length - 1, Math.max(0, index + delta));
    if (nextIndex === index) { return; }
    const file = files[nextIndex];
    selectChangedFile(file);
    // Keep the newly selected row in view without yanking the tree —
    // 'nearest' only scrolls when the row is actually off-screen. The
    // kind attribute disambiguates a delete+add pair sharing one path.
    window.requestAnimationFrame(() => {
      const selector = `[data-changed-file-path="${cssEscapeAttr(diffDisplayPath(file))}"]`
        + `[data-changed-file-kind="${cssEscapeAttr(file.type || 'modify')}"]`;
      const row = repoRef.current?.querySelector(selector);
      if (row && typeof row.scrollIntoView === 'function') {
        row.scrollIntoView({ block: 'nearest' });
      }
    });
  }
  const changedTreeContent = hasChangedFiles && filteredChangedNodes.length > 0 ? (
    <ChangedFilesTree
      nodes={filteredChangedNodes}
      conflictedFiles={conflictedFiles}
      commentMeta={commentMeta}
      closedFolders={closedChangedFolders}
      selectedKey={selectedChangedKey}
      onToggleFolder={toggleChangedFolder}
      onSelectFile={selectChangedFile}
      onOpenPathMenu={onOpenPathMenu}
      onKeyDown={handleChangedTreeKeyDown}
      repoId={repoId}
    />
  ) : null;
  const emptyChangedSearch = hasChangedFiles && filteredChangedNodes.length === 0 ? (
    <p className="files-tab-message">No changed files match this search.</p>
  ) : null;
  let body;
  // A search query searches ALL files (Cmd+P), not just the changed set —
  // otherwise typing a filename that lives in an unchanged file shows "no
  // match" and the box reads as broken. Empty query → respect the toggle
  // (changed view by default).
  const showAllForSearch = showAllFiles || isFiltering;
  if (collapsed) {
    body = null;
  } else if (!showAllForSearch && changedTreeContent) {
    body = changedTreeContent;
  } else if (!showAllForSearch && emptyChangedSearch) {
    body = emptyChangedSearch;
  } else if (!showAllForSearch && !hasChangedFiles) {
    // Repo has nothing changed yet AND the operator is not in All mode
    // — show a placeholder instead of falling through to the full tree
    // (which read as "All mode is on" even when it wasn't).
    body = <p className="files-tab-message">Nothing changed yet.</p>;
  } else if (treeData.length === 0) {
    body = <p className="files-tab-message">No tracked files in this repo.</p>;
  } else {
    body = (
      <Tree
        data={treeData}
        width={width}
        height={treeHeight}
        rowHeight={28}
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
            onOpenPathMenu={onOpenPathMenu}
            conflictedFiles={conflictedFiles}
            changedFiles={changedFiles}
            diffMeta={diffMeta}
            commentMeta={commentMeta}
            repoId={repoId}
          />
        )}
      </Tree>
    );
  }
  return (
    <section className="files-tab-repo" ref={repoRef}>
      <StickyHeader
        as="header"
        className="files-tab-repo-header"
        onClick={onToggle}
      >
        <span className="files-tab-repo-chevron">
          <Icon name={chevronName} />
        </span>
        {/* Use the custom (opaque) data-tooltip instead of the native
            ``title``: the OS title tooltip renders with dark-mode
            vibrancy that bleeds the rows behind it through, which the
            operator read as "transparent". Anchored to the name span
            (not the whole header) so it never doubles up with the
            commits button's own tooltip. */}
        <span className="files-tab-repo-name" data-tooltip={repoTree.cwd}>{heading}</span>
        {headerStats}
        {repoTree.readOnly && (
          <button
            type="button"
            className="files-tab-repo-readonly tooltip-above tooltip-anchor-right"
            data-tooltip="Read-only: kato has no push permission for this repo. The agent can edit it for reference, but changes here are NOT pushed. Click to re-check push access."
            aria-label={`${heading} is read-only — re-check push access`}
            onClick={(event) => {
              event.stopPropagation();
              if (typeof onRecheckPush === 'function') { onRecheckPush(repoId); }
            }}
          >
            RO
          </button>
        )}
        {repoId && taskId && (
          <button
            type="button"
            className="files-tab-repo-commits-btn tooltip-end"
            onClick={toggleCommitMenu}
            aria-haspopup="listbox"
            aria-expanded={commitMenuOpen ? 'true' : 'false'}
            data-tooltip="Commit history — pick a commit on this repo's task branch to scope the Changes tab to that single commit's diff."
            aria-label={`View commit history for ${heading}`}
          >
            <Icon name="history" />
          </button>
        )}
      </StickyHeader>
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

function ChangedFilesTree({
  nodes, conflictedFiles, commentMeta = EMPTY_COMMENT_META,
  closedFolders, selectedKey, onToggleFolder, onSelectFile, onOpenPathMenu,
  onKeyDown,
  repoId = '',
}) {
  const rows = nodes.map((node) => (
    <ChangedFilesTreeNode
      key={node.key}
      node={node}
      depth={0}
      relativePath=""
      conflictedFiles={conflictedFiles}
      commentMeta={commentMeta}
      closedFolders={closedFolders}
      selectedKey={selectedKey}
      onToggleFolder={onToggleFolder}
      onSelectFile={onSelectFile}
      onOpenPathMenu={onOpenPathMenu}
      repoId={repoId}
    />
  ));
  return (
    <div className="files-changed-tree-wrap">
      {/* The repo's +/- totals now live inline in the repo header
          (renderRepoHeaderStats), so the old "Lines updated" summary row
          here would just duplicate them. */}
      {/* role/tabIndex: the tree itself is focusable so ArrowUp/ArrowDown
          move the file selection — clicking any row (a button) also puts
          focus inside, and the keydown bubbles to this container. */}
      <div
        className="diff-file-tree files-changed-tree"
        role="tree"
        tabIndex={0}
        onKeyDown={onKeyDown}
      >
        {rows}
      </div>
    </div>
  );
}

function ChangedFilesTreeNode({
  node, depth, relativePath, conflictedFiles, commentMeta = EMPTY_COMMENT_META,
  closedFolders, selectedKey, onToggleFolder, onSelectFile, onOpenPathMenu,
  repoId = '',
}) {
  if (node.kind === 'folder') {
    const isClosed = closedFolders.has(node.key);
    const folderPath = joinRelativePath(relativePath, node.name);
    const childRows = isClosed ? null : node.children.map((child) => (
      <ChangedFilesTreeNode
        key={child.key}
        node={child}
        depth={depth + 1}
        relativePath={folderPath}
        conflictedFiles={conflictedFiles}
        commentMeta={commentMeta}
        closedFolders={closedFolders}
        selectedKey={selectedKey}
        onToggleFolder={onToggleFolder}
        onSelectFile={onSelectFile}
        onOpenPathMenu={onOpenPathMenu}
        repoId={repoId}
      />
    ));
    const chevron = isClosed ? 'chevron-right' : 'chevron-down';
    // role=group around the child rows + aria-level on every row: child
    // rows are DOM siblings of the folder button, so without these the
    // accessibility tree flattens the hierarchy (every row reads as
    // level 1 and aria-expanded has no associated children).
    return (
      <div className="diff-file-tree-group">
        <button
          type="button"
          role="treeitem"
          aria-expanded={!isClosed}
          aria-level={depth + 1}
          className="diff-file-tree-row files-changed-tree-row is-folder"
          style={{ '--depth': depth }}
          onClick={() => onToggleFolder(node.key)}
          onContextMenu={(event) => onOpenPathMenu(event, folderPath, repoId)}
        >
          <span className="diff-file-tree-guide" />
          <span className="diff-file-tree-chevron"><Icon name={chevron} /></span>
          <span className="diff-file-tree-label files-changed-tree-folder">
            {node.name}
          </span>
        </button>
        {childRows && <div role="group">{childRows}</div>}
      </div>
    );
  }
  const file = node.file;
  const path = diffDisplayPath(file);
  const kind = file.type || 'modify';
  const selected = selectedKey === changedFileSelectionKey(file);
  const conflicted = isFileConflicted(file, conflictedFiles);
  const className = [
    'diff-file-tree-row',
    'files-changed-tree-row',
    'is-file',
    `kind-${kind}`,
    selected ? 'selected' : '',
    conflicted ? 'conflicted' : '',
  ].filter(Boolean).join(' ');
  const conflictBadge = conflicted ? (
    <span className="diff-file-row-conflict" aria-label="merge conflict">
      <Icon name="warning" />
    </span>
  ) : null;
  return (
    <button
      type="button"
      role="treeitem"
      aria-selected={selected}
      aria-level={depth + 1}
      className={className}
      style={{ '--depth': depth }}
      data-changed-file-path={path}
      data-changed-file-kind={kind}
      title={`Open ${path} in the centre diff`}
      onClick={() => onSelectFile(file)}
      onContextMenu={(event) => onOpenPathMenu(event, path, repoId)}
    >
      <span className="diff-file-tree-guide" />
      <DiffKindIcon kind={kind} extraClass="tree-row-kind" />
      {conflictBadge}
      <span className="diff-file-tree-label files-changed-tree-label">
        {node.name}
      </span>
      <CommentCountBadge
        count={commentMeta.get(path)?.count || 0}
        status={commentMeta.get(path)?.status || ''}
        onClick={() => onSelectFile(file, { focusComment: true })}
      />
      <FilesLineStats stats={node.stats} />
    </button>
  );
}

function Node({
  node, style, onPickFile, onOpenFile, conflictedFiles,
  changedFiles, diffMeta = EMPTY_DIFF_META,
  commentMeta = EMPTY_COMMENT_META, repoId = '', onOpenPathMenu,
}) {
  const isFolder = node.isInternal;
  const relativePath = String(node.data?.relativePath || '');
  const changeMeta = !isFolder ? diffMeta.get(relativePath) : null;
  const commentEntry = !isFolder ? commentMeta.get(relativePath) : null;
  const commentCount = commentEntry?.count || 0;
  const commentStatus = commentEntry?.status || '';
  function onActivate() {
    // Left-click a FILE: only open it in the editor pane. It must
    // NOT also paste the path into the chat composer — pasting is
    // the explicit RIGHT-click affordance (see onContextMenu).
    if (!isFolder) {
      if (typeof onOpenFile === 'function') {
        onOpenFile({
          absolutePath: String(node.data?.path || ''),
          relativePath: String(node.data?.relativePath || ''),
          repoId,
        });
      }
      return;
    }
    // Folder: expand / collapse.
    activateTreeNode(node);
  }
  function onContextMenu(event) {
    if (typeof onOpenPathMenu !== 'function') { return; }
    onOpenPathMenu(event, relativePath, repoId);
  }
  // Clicking the comment badge (not the name) opens the file's DIFF —
  // where comments live — and scrolls to the comment thread. The
  // all-files tree normally opens the plain editor (view:'file'),
  // which has no comments, so force the diff view here.
  function onOpenComment() {
    if (typeof onOpenFile !== 'function') { return; }
    onOpenFile({
      absolutePath: String(node.data?.path || ''),
      relativePath: String(node.data?.relativePath || ''),
      repoId,
      view: 'diff',
      focusComment: true,
    });
  }
  const isConflicted = !isFolder
    && conflictedFiles
    && conflictedFiles.size > 0
    && conflictedFiles.has(relativePath);
  // A file kato has touched on this branch (committed or not) —
  // same set the Changes tab shows. Conflict wins visually since
  // it's the more urgent signal, so only flag ``changed`` when the
  // file isn't already flagged conflicted.
  const isChanged = !isFolder
    && !isConflicted
    && (
      !!changeMeta
      || (
        changedFiles
        && changedFiles.size > 0
        && changedFiles.has(relativePath)
      )
    );
  const displayChangeMeta = changeMeta || (isChanged
    ? { kind: 'modify', stats: EMPTY_STATS }
    : null);
  // A folder inherits the "changed" tint when it (transitively)
  // holds a file kato touched on this branch — so the ancestor
  // chain lights up all the way up and the operator sees where the
  // edits live without expanding. ``relativePath`` is empty for a
  // synthetic repo root; ``folderContainsChange`` returns false for
  // it (no relative path can start with "/"), which is exactly the
  // "colour up to the root, but NOT the root of all" rule — the
  // repo container (the .files-tab-repo header, not a tree row)
  // never gets the tint.
  const folderChanged = isFolder
    && changedFiles
    && changedFiles.size > 0
    && folderContainsChange(relativePath, changedFiles);
  const rowClass = [
    'tree-row',
    node.isSelected ? 'selected' : '',
    isConflicted ? 'conflicted' : '',
    isChanged ? 'changed' : '',
    folderChanged ? 'changed-ancestor' : '',
  ].filter(Boolean).join(' ');
  const level = Number.isFinite(node.level) ? node.level : 0;
  // react-arborist owns ``paddingLeft`` (its indent). Add a fixed depth-0
  // inset so the top-level chevron lands in the SAME column as the repo
  // header chevron and the Changes tree (both inset 10px). Added to
  // arborist's per-level value so deeper levels stay indented.
  const arboristPadLeft = Number.parseFloat(style?.paddingLeft) || 0;
  const rowStyle = {
    ...style,
    paddingLeft: arboristPadLeft + TREE_ROW_DEPTH0_INSET,
    '--level': level,
  };
  const folderChevron = isFolder ? (
    <span className="tree-row-chevron">
      <Icon name={node.isOpen ? 'chevron-down' : 'chevron-right'} />
    </span>
  ) : null;
  // No generic folder / file icons — the all-files tree must look
  // exactly like the changed-files tree (chevron + name only). Only
  // the change-KIND marker (pencil/＋/－) stays, on changed files,
  // so both trees read identically.
  const fileSpacer = !isFolder ? (
    <span className="tree-row-chevron tree-row-chevron-placeholder" />
  ) : null;
  const fileIcon = !isFolder && displayChangeMeta ? (
    <DiffKindIcon kind={displayChangeMeta.kind} extraClass="tree-row-kind" />
  ) : null;
  const conflictBadge = isConflicted ? (
    <span className="tree-row-conflict" aria-label="merge conflict">
      <Icon name="warning" />
    </span>
  ) : null;
  const lineStats = displayChangeMeta ? (
    <FilesLineStats stats={displayChangeMeta.stats} />
  ) : null;
  // Tooltip: spell out left- vs right-click semantics so the
  // right-click affordance is discoverable. Conflict tooltip wins
  // when set since it's the more urgent signal.
  let tooltip;
  if (isConflicted) {
    tooltip = 'Merge conflict — needs resolution';
  } else if (isChanged) {
    tooltip = 'Modified on this task branch — right-click for path options';
  } else if (folderChanged) {
    tooltip = 'Contains files modified on this task branch — right-click for path options';
  } else if (isFolder) {
    tooltip = 'Click to expand · right-click for path options';
  } else {
    tooltip = 'Click to open · right-click for path options';
  }
  return (
    <div
      className={rowClass}
      style={rowStyle}
      onClick={onActivate}
      onContextMenu={onContextMenu}
      title={tooltip}
    >
      <span className="tree-row-level-guides" aria-hidden="true" />
      {folderChevron}
      {fileSpacer}
      {fileIcon}
      {conflictBadge}
      <span className="tree-row-name">{node.data.name}</span>
      <CommentCountBadge
        count={commentCount}
        status={commentStatus}
        onClick={onOpenComment}
      />
      {lineStats}
    </div>
  );
}

export function filterChangedFileTree(nodes, term) {
  const raw = String(term || '').trim().toLowerCase();
  if (!raw) { return nodes || []; }
  const matches = [];
  for (const node of nodes || []) {
    if (node.kind === 'folder') {
      const childMatches = filterChangedFileTree(node.children, raw);
      const folderMatches = String(node.name || '').toLowerCase().includes(raw);
      if (folderMatches || childMatches.length > 0) {
        matches.push({
          ...node,
          children: folderMatches ? node.children : childMatches,
        });
      }
    } else if (changedFileNodeMatches(node, raw)) {
      matches.push(node);
    }
  }
  return matches;
}

function changedFileNodeMatches(node, raw) {
  const name = String(node.name || '').toLowerCase();
  const path = diffDisplayPath(node.file).toLowerCase();
  return name.includes(raw) || path.includes(raw);
}

function changedFileSelectionKey(file) {
  return `${file.type || 'modify'}:${diffDisplayPath(file)}`;
}

// The selection key for THIS repo's changed tree, derived from the centre
// pane's open file. Empty when the open file belongs to another repo (an
// explicit repoId mismatch) or isn't one of this repo's changed files.
// ``openFile.kind`` (carried by changed-tree clicks) wins over the
// diffMeta lookup: the meta map is keyed by path only, so for a
// delete+add pair sharing one path it holds just the surviving entry —
// the clicked DELETE row would otherwise highlight as the ADD row.
function changedSelectionKeyFor(openFile, repoId, diffMeta) {
  const path = String(openFile?.relativePath || '').trim();
  if (!path) { return ''; }
  const targetRepo = String(openFile?.repoId || '').trim();
  if (targetRepo && targetRepo !== repoId) { return ''; }
  const kind = String(openFile?.kind || '').trim();
  if (kind) { return `${kind}:${path}`; }
  const meta = diffMeta.get(path);
  if (!meta || !meta.file) { return ''; }
  return changedFileSelectionKey(meta.file);
}

// Which repo's tree owns the selection highlight (see the call site for
// why repo-less open files must resolve to at most ONE repo). Returns
// the repo KEY (repo_id || cwd) or '' when nothing should highlight.
function resolveSelectionRepoKey(openFile, trees, diffMetaByRepo) {
  const path = String(openFile?.relativePath || '').trim();
  if (!path) { return ''; }
  const targetRepo = String(openFile?.repoId || '').trim();
  for (const tree of trees || []) {
    const repoKey = tree.repo_id || tree.cwd;
    if (targetRepo) {
      if (targetRepo === String(tree.repo_id || '').trim()) { return repoKey; }
      continue;
    }
    const meta = diffMetaByRepo?.get(repoKey);
    if (meta && meta.get(path)) { return repoKey; }
  }
  return '';
}

// Files visible in the changed tree, in render order — folders whose key
// is in ``closedFolders`` contribute nothing. This is the walk order for
// ArrowUp/ArrowDown keyboard navigation. Exported for unit tests.
export function listVisibleChangedFiles(nodes, closedFolders) {
  const files = [];
  for (const node of nodes || []) {
    if (node.kind === 'file') {
      files.push(node.file);
    } else if (!closedFolders?.has(node.key)) {
      files.push(...listVisibleChangedFiles(node.children, closedFolders));
    }
  }
  return files;
}

function joinRelativePath(parent, child) {
  const left = String(parent || '').replace(/\/+$/, '');
  const right = String(child || '').replace(/^\/+/, '');
  if (!left) { return right; }
  if (!right) { return left; }
  return `${left}/${right}`;
}

function focusTargetMatchesRepo(target, repoTree, repoCount) {
  if (!target) { return false; }
  const targetRepo = String(target.repoId || '').trim();
  if (!targetRepo) { return repoCount === 1; }
  return targetRepo === String(repoTree.repo_id || '').trim()
    || targetRepo === String(repoTree.cwd || '').trim();
}

function findChangedFileFocusInfo(nodes, targetPath, ancestors = []) {
  for (const node of nodes || []) {
    if (node.kind === 'file' && diffDisplayPath(node.file) === targetPath) {
      return { file: node.file, ancestorKeys: ancestors };
    }
    if (node.kind === 'folder') {
      const found = findChangedFileFocusInfo(
        node.children,
        targetPath,
        [...ancestors, node.key],
      );
      if (found) { return found; }
    }
  }
  return null;
}

// Bitbucket-style 💬 N on a tree row when the file has open comment
// threads. Renders nothing at 0 so clean files stay clean. ``status``
// (most-urgent kato_status across the file's threads) tints the badge
// to match the comment status pills. When ``onClick`` is supplied the
// badge is its own click target: clicking it opens the file's diff and
// scrolls to the comment (distinct from clicking the name, which just
// opens the file). The row underneath is a button/clickable div, so
// the handler stops propagation to avoid double-firing the row action.
function CommentCountBadge({ count, status = '', onClick }) {
  if (!count || count < 1) { return null; }
  const interactive = typeof onClick === 'function';
  const className = [
    'tree-row-comments',
    status ? `is-${status}` : '',
    interactive ? 'is-clickable' : '',
  ].filter(Boolean).join(' ');
  const threadLabel = `${countNoun(count, 'comment thread')} on this file`;
  function handleClick(event) {
    event.stopPropagation();
    onClick(event);
  }
  function handleKeyDown(event) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      event.stopPropagation();
      onClick(event);
    }
  }
  return (
    <span
      className={className}
      title={interactive ? `${threadLabel} — click to jump to the comment` : threadLabel}
      aria-label={interactive ? `Jump to ${countNoun(count, 'comment')}` : countNoun(count, 'comment')}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      onClick={interactive ? handleClick : undefined}
      onKeyDown={interactive ? handleKeyDown : undefined}
    >
      <Icon name="comment" />
      {count}
    </span>
  );
}

function FilesLineStats({ stats }) {
  const added = stats?.added > 0 ? (
    <span className="diff-line-stat is-add">{`+${stats.added}`}</span>
  ) : null;
  const deleted = stats?.deleted > 0 ? (
    <span className="diff-line-stat is-delete">{`-${stats.deleted}`}</span>
  ) : null;
  if (!added && !deleted) { return null; }
  return (
    <span className="diff-line-stats tree-row-line-stats">
      {added}
      {deleted}
    </span>
  );
}
