import { useEffect, useMemo, useState } from 'react';
import { parseDiff, Diff, Hunk } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { fetchDiff } from './api.js';
import Icon from './components/Icon.jsx';
import { tokenizeHunks } from './utils/diffSyntax.js';

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

  useEffect(() => {
    if (!taskId) { return; }
    let cancelled = false;
    // Only flip to ``loading`` on the FIRST fetch for this taskId — i.e.
    // when there are no diffs to show yet. Subsequent refetches (driven
    // by workspaceVersion bumps every 1.2s during active tool use) keep
    // the previous diff visible until the new payload arrives, so the
    // tab body doesn't flash "Computing diff…" between every turn.
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
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion]);

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
  const showToolbar = repoIds.length > 1;
  const toolbar = showToolbar && (
    <span className="changes-tab-toolbar">
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

  const showHeader = !!diffLabel || showToolbar;
  const header = showHeader && (
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
