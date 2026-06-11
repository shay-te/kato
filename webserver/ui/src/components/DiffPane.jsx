import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchDiff, fetchTaskComments } from '../api.js';
import {
  diffDisplayPath,
  diffFileKey,
  isFileConflicted,
  parseRepoDiffs,
} from '../diffModel.js';
import { useChatComposer } from '../contexts/ChatComposerContext.jsx';
import { apiErrorMessage } from '../utils/apiError.js';
import DiffFileWithComments from './DiffFileWithComments.jsx';

const EMPTY_COMMENTS = [];

// Stable per-file anchor id. The left Changes list passes the same
// (repoId, relativePath) so the centre can match the selected file.
// Exported for unit tests.
export function diffAnchorKey(repoId, path) {
  return `${repoId || ''}::${path}`;
}

/**
 * Centre-column diff viewer. Renders ONLY the selected file's diff —
 * the left Changes list is the navigation surface: clicking a file
 * there swaps this pane to that file (it does NOT stack every changed
 * file any more; the operator reads one file at a time).
 *
 * ``openFile`` shape: ``{ taskId, relativePath, repoId, view:'diff' }``
 * — ``relativePath``/``repoId`` select WHICH file's diff renders.
 */
export default function DiffPane({
  openFile,
  workspaceVersion = 0,
  onCommentSpawned,
  onFocusFileInTree,
  onCommentsChanged,
  onViewStateChange,
}) {
  const taskId = openFile?.taskId || '';
  const repoId = openFile?.repoId || '';
  const relativePath = openFile?.relativePath || openFile?.absolutePath || '';
  const openRequestId = openFile?.openRequestId || 0;
  const focusComment = !!openFile?.focusComment;
  const restoreViewState = !!openFile?.restoreViewState;

  const [state, setState] = useState({
    status: 'loading', repoDiffs: [], error: '',
  });
  // Comments for the selected file's repo only — one file is on
  // screen, so one repo's comments are all the pane needs.
  const [comments, setComments] = useState({
    loading: true, error: '', byFile: new Map(),
  });
  const [commentsTick, setCommentsTick] = useState(0);
  // Signature of the last comments payload we committed. The comments
  // poll re-fires on every diff refresh (workspaceVersion bumps ~1.2s
  // during tool use); without this guard each fire built a brand-new
  // Map even when nothing changed, giving the file box a new
  // ``comments`` prop identity and re-rendering the whole diff. Skip
  // the setState when the payload is unchanged so the memoized file
  // box can bail.
  const commentsSigRef = useRef('');
  const diffSigRef = useRef('');

  const { appendToInput } = useChatComposer();
  const bodyRef = useRef(null);
  const onViewStateChangeRef = useRef(onViewStateChange);
  const fileNodeRef = useRef(null);
  // Guard for the scroll-to-comment-thread effect below. It must
  // depend on ``comments`` (the thread only exists once comments load),
  // but ``comments`` also changes on every poll — a status flip the
  // poll picks up would otherwise re-scroll the pane to the thread while
  // the operator is reading. Marked handled only once we hit the real
  // thread, so the file→thread upgrade still works but poll re-fires
  // don't. Starts at -1 so the first open (openRequestId 0) still scrolls.
  const lastCommentScrolledRequestRef = useRef(-1);
  useEffect(() => {
    onViewStateChangeRef.current = onViewStateChange;
  }, [onViewStateChange]);

  useEffect(() => {
    if (!taskId) {
      setState({ status: 'error', repoDiffs: [], error: 'No task bound.' });
      return undefined;
    }
    let cancelled = false;
    setState((prev) => (
      prev.status === 'ready'
        ? prev
        : { status: 'loading', repoDiffs: [], error: '' }
    ));
    // Fetch the whole changeset (no repoId filter): the selected file is
    // located across repos below, and an unfiltered payload keeps the
    // signature guard effective across selection changes.
    fetchDiff(taskId)
      .then((payload) => {
        if (cancelled) { return; }
        const sig = JSON.stringify([taskId, payload]);
        if (sig === diffSigRef.current) { return; }
        diffSigRef.current = sig;
        setState({
          status: 'ready', repoDiffs: parseRepoDiffs(payload), error: '',
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState({ status: 'error', repoDiffs: [], error: String(err) });
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion]);

  const totalFiles = useMemo(
    () => state.repoDiffs.reduce((n, r) => n + (r.files?.length || 0), 0),
    [state.repoDiffs],
  );

  // Locate the selected file: exact (repoId, path) anchor match first,
  // then a path-only match if the repo wasn't carried (or went stale).
  const selected = useMemo(() => {
    if (!relativePath) { return null; }
    const targetKey = diffAnchorKey(repoId, relativePath);
    let pathOnly = null;
    for (const repo of state.repoDiffs) {
      for (const file of repo.files || []) {
        const path = diffDisplayPath(file);
        if (diffAnchorKey(repo.repo_id, path) === targetKey) {
          return { repo, file, path };
        }
        if (!pathOnly && path === relativePath) {
          pathOnly = { repo, file, path };
        }
      }
    }
    return pathOnly;
  }, [state.repoDiffs, repoId, relativePath]);
  const selectedRepoId = selected?.repo.repo_id || '';

  // Selection moved to a file in a DIFFERENT repo: drop the previous
  // repo's comments immediately. Without this, repo A's threads render on
  // repo B's file (same relative path = same byFile key) for the whole
  // fetch round-trip, and the signature guard alone wouldn't help — it
  // only suppresses identical payloads, not cross-repo staleness.
  const commentsRepoRef = useRef('');
  useEffect(() => {
    if (commentsRepoRef.current === selectedRepoId) { return; }
    commentsRepoRef.current = selectedRepoId;
    commentsSigRef.current = '';
    setComments({ loading: true, error: '', byFile: new Map() });
  }, [selectedRepoId]);

  // One comments fetch for the selected file's repo. Re-runs when a
  // comment mutation bumps ``commentsTick`` or the diff refreshes.
  useEffect(() => {
    if (!taskId || state.status !== 'ready' || !selectedRepoId) { return undefined; }
    let cancelled = false;
    fetchTaskComments(taskId, selectedRepoId)
      .catch(() => ({ ok: false, error: 'failed to load comments' }))
      .then((result) => {
        if (cancelled) { return; }
        // Identical payload to last time → keep the existing state object
        // (and its per-file comment arrays) so referential equality holds
        // and the memoized file box skips re-rendering.
        const sig = JSON.stringify([selectedRepoId, result]);
        if (sig === commentsSigRef.current) { return; }
        commentsSigRef.current = sig;
        const byFile = new Map();
        if (result.ok) {
          const list = Array.isArray(result.body?.comments)
            ? result.body.comments : [];
          for (const comment of list) {
            const p = String(comment.file_path || '');
            if (!byFile.has(p)) { byFile.set(p, []); }
            byFile.get(p).push(comment);
          }
        }
        setComments({
          loading: false,
          error: result.ok ? '' : apiErrorMessage(result, 'failed to load comments'),
          byFile,
        });
      });
    return () => { cancelled = true; };
  }, [taskId, state.status, selectedRepoId, commentsTick, workspaceVersion]);

  const bumpComments = useCallback(() => {
    setCommentsTick((n) => n + 1);
    if (typeof onCommentsChanged === 'function') {
      onCommentsChanged();
    }
  }, [onCommentsChanged]);

  // When the operator clicked a file's comment badge (not the name),
  // scroll to the file's first comment thread. Comments load
  // asynchronously, so this also depends on ``comments`` — it re-fires
  // once the threads render and centres the first one.
  useEffect(() => {
    if (restoreViewState || !focusComment || state.status !== 'ready' || !selected) { return; }
    // Already centred this open request on its thread → don't re-scroll
    // when a later comments poll changes the comments identity.
    if (openRequestId === lastCommentScrolledRequestRef.current) { return; }
    const fileNode = fileNodeRef.current;
    if (!fileNode) { return; }
    const thread = fileNode.querySelector('.diff-file-comment-thread');
    if (thread && typeof thread.scrollIntoView === 'function') {
      // Mark handled only when we reached the actual thread — until the
      // comments load we keep retrying so the final landing is the thread.
      lastCommentScrolledRequestRef.current = openRequestId;
      thread.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [
    restoreViewState, focusComment, state.status, selected,
    openRequestId, comments,
  ]);

  useEffect(() => {
    if (state.status !== 'ready') { return; }
    const node = bodyRef.current;
    const scrollTop = Number(openFile?.diffScrollTop);
    if (!node || !Number.isFinite(scrollTop) || scrollTop <= 0) { return; }
    node.scrollTop = scrollTop;
  }, [state.status, openRequestId, openFile?.diffScrollTop]);

  // Reset the pane to the TOP whenever the rendered file SWAPS. The scroll
  // container (.diff-pane-body) survives selection changes — only the inner
  // file card remounts — so without this, opening file B after reading deep
  // into file A lands mid-file (browser keeps/clamps the old scrollTop), and
  // the clamp's scroll event would then persist file A's leftover offset
  // into file B's remembered view state. Initialized on mount WITHOUT
  // resetting, so the diffScrollTop restore effect above (same file,
  // restoreViewState tab return) still wins. A focusComment open also
  // skips the reset — the thread-scroll effect above owns positioning
  // there, and an instant scrollTop=0 would ABORT its in-flight smooth
  // scroll (a comment-badge click on a same-repo file would land at the
  // top of the file instead of on the thread).
  const renderedFileKeyRef = useRef(null);
  const selectedFileKey = selected
    ? diffAnchorKey(selected.repo.repo_id, selected.path) : '';
  useEffect(() => {
    if (state.status !== 'ready' || !selectedFileKey) { return; }
    const previousKey = renderedFileKeyRef.current;
    renderedFileKeyRef.current = selectedFileKey;
    if (previousKey === null || previousKey === selectedFileKey) { return; }
    if (focusComment) { return; }
    const node = bodyRef.current;
    if (node) { node.scrollTop = 0; }
    const notify = onViewStateChangeRef.current;
    if (typeof notify === 'function') { notify({ diffScrollTop: 0 }); }
  }, [state.status, selectedFileKey, focusComment]);

  function handleBodyScroll(event) {
    const notify = onViewStateChangeRef.current;
    if (typeof notify !== 'function') { return; }
    notify({ diffScrollTop: event.currentTarget.scrollTop || 0 });
  }

  if (state.status === 'loading') {
    return (
      <div className="diff-pane">
        <p className="changes-tab-message">Computing diff…</p>
      </div>
    );
  }
  if (state.status === 'error') {
    return (
      <div className="diff-pane">
        <p className="changes-tab-message error">{state.error}</p>
      </div>
    );
  }
  if (totalFiles === 0) {
    return (
      <div className="diff-pane">
        <p className="changes-tab-message">No changes on this task branch.</p>
      </div>
    );
  }
  if (!selected) {
    // The changeset is non-empty but the selected file isn't in it any
    // more (e.g. Claude reverted it between the click and the refresh).
    return (
      <div className="diff-pane">
        <p className="changes-tab-message">
          No changes in {relativePath || 'this file'}.
        </p>
      </div>
    );
  }

  const conflicted = isFileConflicted(selected.file, selected.repo.conflictedFiles);
  return (
    <div className="diff-pane">
      <div className="diff-pane-body" ref={bodyRef} onScroll={handleBodyScroll}>
        <div
          key={diffFileKey(selected.file)}
          className="diff-pane-file"
          data-diff-key={diffAnchorKey(selected.repo.repo_id, selected.path)}
          ref={fileNodeRef}
        >
          <DiffFileWithComments
            file={selected.file}
            initiallyExpanded
            forceExpandToken={openRequestId}
            conflicted={!!conflicted}
            repoId={selected.repo.repo_id}
            repoCwd={selected.repo.cwd}
            taskId={taskId}
            onAddToChat={appendToInput}
            onFocusInTree={onFocusFileInTree}
            comments={comments.byFile.get(selected.path) || EMPTY_COMMENTS}
            commentsLoading={!!comments.loading}
            commentsError={comments.error || ''}
            onMutated={bumpComments}
            onCommentSpawned={onCommentSpawned}
          />
        </div>
      </div>
    </div>
  );
}
