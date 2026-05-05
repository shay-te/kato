import { useEffect, useMemo, useRef, useState } from 'react';
import { parseDiff, Diff, Hunk } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { fetchDiff } from './api.js';
import Icon from './components/Icon.jsx';
import { tokenizeHunks } from './utils/diffSyntax.js';


// While the tab is open, re-poll the diff endpoint at this cadence
// even when no Claude tool events fire. The operator sees changes
// from external sources (manual edits, ``git pull``, the new sync
// icon) without having to click anything. Skipped when the document
// is hidden (background tab) so we don't burn server cycles when no
// one's looking. 5 seconds is the eyeball-noticeable threshold —
// faster makes git churn for no perceived benefit.
const AUTO_POLL_INTERVAL_MS = 5000;


export default function ChangesTab({
  taskId,
  workspaceVersion = 0,
}) {
  const [state, setState] = useState({
    status: 'loading',
    diffs: [],
    workspaceStatus: '',
    error: '',
  });
  const [collapsed, setCollapsed] = useState(() => new Set());
  // Bumped by the manual refresh button + the auto-poll interval.
  // Independent of ``workspaceVersion`` so neither path blocks the
  // other; the fetch effect just watches for any change.
  const [refreshTick, setRefreshTick] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  // Block overlapping fetches: if a previous poll is still in
  // flight when the next interval fires, skip — we'd rather miss
  // a tick than have two ``git diff`` runs racing against each
  // other and the response order being non-deterministic.
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (!taskId) { return; }
    let cancelled = false;
    inFlightRef.current = true;
    // Only flip to ``loading`` on the FIRST fetch for this taskId — i.e.
    // when there are no diffs to show yet. Subsequent refetches (driven
    // by workspaceVersion bumps every 1.2s during active tool use, or
    // the auto-poll, or the refresh button) keep the previous diff
    // visible until the new payload arrives, so the tab body doesn't
    // flash "Computing diff…" between every turn.
    setState((prev) => (
      prev.status === 'ready' || prev.status === 'error'
        ? prev
        : { status: 'loading', diffs: [], workspaceStatus: '', error: '' }
    ));
    fetchDiff(taskId)
      .then((payload) => {
        if (cancelled) { return; }
        setState({
          status: 'ready',
          diffs: parseRepoDiffs(payload),
          workspaceStatus: String(payload?.workspace_status || ''),
          error: '',
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState((prev) => ({
          // Preserve the previously-shown diff if we had one — a
          // transient fetch error shouldn't blank the body.
          status: 'error',
          diffs: prev.diffs,
          workspaceStatus: prev.workspaceStatus,
          error: String(err),
        }));
      })
      .finally(() => {
        if (cancelled) { return; }
        inFlightRef.current = false;
        setRefreshing(false);
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion, refreshTick]);

  // Auto-poll while the tab is mounted. Bumps ``refreshTick`` which
  // triggers the fetch effect above. Honors document visibility so a
  // background kato tab doesn't keep hammering the server. Cleared on
  // unmount and on taskId change.
  useEffect(() => {
    if (!taskId || typeof window === 'undefined') { return undefined; }
    let timerId = null;
    function tick() {
      if (typeof document !== 'undefined' && document.hidden) { return; }
      if (inFlightRef.current) { return; }
      setRefreshTick((n) => n + 1);
    }
    timerId = window.setInterval(tick, AUTO_POLL_INTERVAL_MS);
    return () => {
      if (timerId !== null) { window.clearInterval(timerId); }
    };
  }, [taskId]);

  function onRefresh() {
    if (!taskId || refreshing || inFlightRef.current) { return; }
    setRefreshing(true);
    setRefreshTick((n) => n + 1);
  }

  // When the operator switches to a different task, blank the previous
  // task's diff state so we don't show stale data while the new fetch
  // is in flight.
  useEffect(() => {
    setState({ status: 'loading', diffs: [], workspaceStatus: '', error: '' });
  }, [taskId]);

  const repoIds = useMemo(() => {
    return state.diffs.map((entry) => entry.repo_id || entry.cwd);
  }, [state.diffs]);

  function toggleRepo(repoKey) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(repoKey)) { next.delete(repoKey); } else { next.add(repoKey); }
      return next;
    });
  }
  function collapseAll() { setCollapsed(new Set(repoIds)); }
  function expandAll() { setCollapsed(new Set()); }

  const diffLabel = diffLabelForStatus(state.workspaceStatus);
  const toolbar = (
    <span className="changes-tab-toolbar">
      <button
        type="button"
        className="changes-tab-icon-btn"
        data-tooltip={
          'Refresh diff — re-runs ``git diff`` against origin/<base> '
          + 'in every repo. Auto-polls every 5s while the tab is '
          + 'open; click to force a refresh now.'
        }
        aria-label="Refresh diff"
        onClick={onRefresh}
        disabled={refreshing || !taskId}
      >
        <Icon name="refresh" spin={refreshing} />
      </button>
      {repoIds.length > 1 && (
        <>
          <button
            type="button"
            className="changes-tab-icon-btn"
            data-tooltip="Expand all repositories — show every changed file in every workspace."
            aria-label="Expand all repositories"
            onClick={expandAll}
          >
            <Icon name="plus" />
          </button>
          <button
            type="button"
            className="changes-tab-icon-btn"
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
    body = <p className="changes-tab-message">Computing diff…</p>;
  } else if (state.status === 'error') {
    body = <p className="changes-tab-message error">{state.error}</p>;
  } else if (state.diffs.length === 0) {
    body = <p className="changes-tab-message">No repositories for this task.</p>;
  } else {
    body = state.diffs.map((repoDiff) => {
      const repoKey = repoDiff.repo_id || repoDiff.cwd;
      return (
        <RepoDiff
          key={repoKey}
          repoDiff={repoDiff}
          collapsed={collapsed.has(repoKey)}
          onToggle={() => toggleRepo(repoKey)}
        />
      );
    });
  }

  // Header is always rendered now — even on a single-repo task
  // the operator wants the refresh icon, and the diff label
  // (when present) gives at-a-glance branch context.
  const header = (
    <header className="changes-tab-header">
      <span>{diffLabel}</span>
      {toolbar}
    </header>
  );
  return (
    <div className="changes-tab">
      {header}
      <div className="changes-tab-body">
        {body}
      </div>
    </div>
  );
}

// Maps the workspace status reported by /api/sessions/<id>/diff into a
// header label so a "still has a diff" tab doesn't look like uncommitted
// work after publish. Empty string for the active/in-flight case — the
// "Changes" tab title already says what the body is.
function diffLabelForStatus(status) {
  switch (String(status || '').toLowerCase()) {
    case 'review':
      return 'already pushed (PR open)';
    case 'done':
      return 'merged';
    case 'errored':
      return 'publish errored';
    case 'terminated':
      return 'terminated';
    default:
      return '';
  }
}

// Shape the wire payload into a uniform per-repo list. Handles both the
// new ``diffs: [...]`` envelope and the legacy single-repo flat shape.
function parseRepoDiffs(payload) {
  const diffs = Array.isArray(payload?.diffs) ? payload.diffs : null;
  if (diffs && diffs.length > 0) {
    return diffs.map((entry) => normalizeDiff(entry));
  }
  return [normalizeDiff(payload)];
}

function normalizeDiff(entry) {
  const raw = String(entry?.diff || '');
  const cwd = String(entry?.cwd || '');
  // Older server responses don't carry repo_id; derive from the cwd's
  // last path segment so the accordion still has a meaningful heading.
  const repoId = String(entry?.repo_id || '') || basenameOf(cwd);
  const conflicts = Array.isArray(entry?.conflicted_files)
    ? entry.conflicted_files.map(String)
    : [];
  return {
    repo_id: repoId,
    cwd,
    base: String(entry?.base || ''),
    head: String(entry?.head || ''),
    error: String(entry?.error || ''),
    files: raw ? parseDiff(raw) : [],
    conflictedFiles: new Set(conflicts),
  };
}

function basenameOf(path) {
  if (!path) { return ''; }
  const trimmed = path.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
}

function RepoDiff({ repoDiff, collapsed, onToggle }) {
  const heading = repoDiff.repo_id || repoDiff.cwd || 'repo';
  const chevronName = collapsed ? 'chevron-right' : 'chevron-down';
  return (
    <section className="diff-repo">
      <header className="diff-repo-header" onClick={onToggle}>
        <span className="diff-repo-chevron"><Icon name={chevronName} /></span>
        <span className="diff-repo-name">{heading}</span>
        {repoDiff.base && repoDiff.head && (
          <span className="diff-repo-range">
            <code>{repoDiff.base}</code> … <code>{repoDiff.head}</code>
          </span>
        )}
      </header>
      {!collapsed && (
        <div className="diff-repo-body">
          {repoDiff.error && (
            <p className="changes-tab-message error">{repoDiff.error}</p>
          )}
          {!repoDiff.error && repoDiff.files.length === 0 && (
            <p className="changes-tab-message">
              No changes between <code>{repoDiff.base}</code> and{' '}
              <code>{repoDiff.head}</code>.
            </p>
          )}
          {!repoDiff.error && repoDiff.files.map((file) => (
            <DiffFile
              key={diffFileKey(file)}
              file={file}
              conflicted={isFileConflicted(file, repoDiff.conflictedFiles)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function diffFileKey(file) {
  const oldPath = file.oldPath || '';
  const newPath = file.newPath || '';
  return `${file.type}:${oldPath}->${newPath}`;
}

function DiffFile({ file, conflicted = false }) {
  const path = file.newPath || file.oldPath || '(unknown)';
  // Run intra-line edit highlighting (markEdits enhancer). Memoized
  // on the hunks reference + path so workspace-poll re-renders
  // don't re-tokenize.
  const tokens = useMemo(
    () => tokenizeHunks(file.hunks || [], path),
    [file.hunks, path],
  );
  return (
    <section className="diff-file">
      <header className="diff-file-header">
        <span className="diff-file-type">{file.type}</span>
        <span className="diff-file-path">{path}</span>
        {conflicted && (
          <span
            className="diff-file-conflicted"
            title="This file has merge conflicts that must be resolved before it can be merged."
          >
            CONFLICTED
          </span>
        )}
      </header>
      <Diff
        viewType="unified"
        diffType={file.type}
        hunks={file.hunks || []}
        tokens={tokens}
      >
        {(hunks) => hunks.map((hunk) => (
          <Hunk key={hunk.content} hunk={hunk} />
        ))}
      </Diff>
    </section>
  );
}


function isFileConflicted(file, conflictedSet) {
  if (!conflictedSet || conflictedSet.size === 0) { return false; }
  const oldPath = file.oldPath || '';
  const newPath = file.newPath || '';
  return conflictedSet.has(oldPath) || conflictedSet.has(newPath);
}
