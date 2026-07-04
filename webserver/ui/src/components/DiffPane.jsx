import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  diffDisplayPath,
  diffFileKey,
  isFileConflicted,
} from '../diffModel.js';
import { useChatComposer } from '../contexts/ChatComposerContext.jsx';
import { commentStore } from '../stores/commentStore.js';
import { useTaskComments } from '../hooks/useTaskComments.js';
import { diffStore } from '../stores/diffStore.js';
import { useTaskDiff } from '../hooks/useTaskDiff.js';
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
  onOpenFile,
}) {
  const taskId = openFile?.taskId || '';
  const repoId = openFile?.repoId || '';
  const relativePath = openFile?.relativePath || openFile?.absolutePath || '';
  const openRequestId = openFile?.openRequestId || 0;
  const focusComment = !!openFile?.focusComment;
  const restoreViewState = !!openFile?.restoreViewState;

  // The changeset comes from the shared ``diffStore`` (single source of
  // truth) — the SAME parsed diff the Files-tree badges read, so the tree
  // and this pane can never drift out of sync, and there's one fetch + one
  // poll instead of two. The store keeps ``repoDiffs`` referentially stable
  // across idle polls, so the memos below (selected file, totalFiles) bail.
  const { repoDiffs, loading: diffLoading, error: diffError } = useTaskDiff(taskId);
  let status = 'ready';
  let errorText = '';
  if (!taskId) { status = 'error'; errorText = 'No task bound.'; }
  else if (diffError) { status = 'error'; errorText = diffError; }
  else if (diffLoading) { status = 'loading'; }
  // Comments come from the shared ``commentStore`` (single source of
  // truth) — the same always-current list the Files-tree badges read, so
  // a mutation here (delete / resolve / reply) updates both surfaces in
  // the same tick. The store keeps the array identity stable across idle
  // polls, so the ``byFile`` memo below only rebuilds when the bytes
  // actually change and the memoized file box can still bail.
  const { comments: allComments, loading: commentsLoading, error: commentsError } =
    useTaskComments(taskId);

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

  // A Claude turn / git op (workspaceVersion bump) changed the tree outside
  // the poll cadence — reconcile the shared store now. Coalesced by its
  // single-flight guard; an unchanged payload emits nothing.
  useEffect(() => {
    if (taskId) { diffStore.poke(taskId); }
  }, [taskId, workspaceVersion]);

  const totalFiles = useMemo(
    () => repoDiffs.reduce((n, r) => n + (r.files?.length || 0), 0),
    [repoDiffs],
  );

  // Locate the selected file: exact (repoId, path) anchor match first,
  // then a path-only match if the repo wasn't carried (or went stale).
  const selected = useMemo(() => {
    if (!relativePath) { return null; }
    const targetKey = diffAnchorKey(repoId, relativePath);
    let pathOnly = null;
    for (const repo of repoDiffs) {
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
  }, [repoDiffs, repoId, relativePath]);
  const selectedRepoId = selected?.repo.repo_id || '';

  // Threads for the selected file's repo, grouped by file path. Derived
  // synchronously from the one shared comment list — filtered to the
  // selected repo (case-insensitive, matching the backend's per-repo
  // query) so a selection moving to a file in a DIFFERENT repo re-filters
  // instantly instead of flashing repo A's threads on repo B's file for a
  // fetch round-trip. The store keeps ``allComments`` referentially
  // stable across idle polls, so this map (and its per-file arrays) only
  // rebuilds on a real change and the memoized file box can bail.
  const byFile = useMemo(() => {
    const map = new Map();
    const repoKey = selectedRepoId.toLowerCase();
    if (!repoKey) { return map; }
    for (const comment of allComments) {
      if (String(comment?.repo_id || '').toLowerCase() !== repoKey) { continue; }
      const path = String(comment?.file_path || '');
      if (!map.has(path)) { map.set(path, []); }
      map.get(path).push(comment);
    }
    return map;
  }, [allComments, selectedRepoId]);

  // A Claude turn / repo sync (workspaceVersion bump) can add or
  // re-status comments outside a UI mutation — reconcile the shared
  // store so the threads reflect it. Coalesced by the store's
  // single-flight guard; a no-op payload emits nothing.
  useEffect(() => {
    if (taskId) { commentStore.poke(taskId); }
  }, [taskId, workspaceVersion]);

  const bumpComments = useCallback(() => {
    // The store already reconciles comment data on every mutation; this
    // only forwards the "comments changed" signal to the parent (App
    // bumps workspaceVersion so the diff itself refetches — kato may have
    // re-touched the file while addressing the thread).
    if (typeof onCommentsChanged === 'function') {
      onCommentsChanged();
    }
  }, [onCommentsChanged]);

  // When the operator clicked a file's comment badge (not the name),
  // scroll to the file's first comment thread. Comments load
  // asynchronously, so this also depends on ``comments`` — it re-fires
  // once the threads render and centres the first one.
  useEffect(() => {
    if (restoreViewState || !focusComment || status !== 'ready' || !selected) { return; }
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
    restoreViewState, focusComment, status, selected,
    openRequestId, byFile,
  ]);

  useEffect(() => {
    if (status !== 'ready') { return; }
    const node = bodyRef.current;
    const scrollTop = Number(openFile?.diffScrollTop);
    if (!node || !Number.isFinite(scrollTop) || scrollTop <= 0) { return; }
    node.scrollTop = scrollTop;
  }, [status, openRequestId, openFile?.diffScrollTop]);

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
    if (status !== 'ready' || !selectedFileKey) { return; }
    const previousKey = renderedFileKeyRef.current;
    renderedFileKeyRef.current = selectedFileKey;
    if (previousKey === null || previousKey === selectedFileKey) { return; }
    if (focusComment) { return; }
    const node = bodyRef.current;
    if (node) { node.scrollTop = 0; }
    const notify = onViewStateChangeRef.current;
    if (typeof notify === 'function') { notify({ diffScrollTop: 0 }); }
  }, [status, selectedFileKey, focusComment]);

  function handleBodyScroll(event) {
    const notify = onViewStateChangeRef.current;
    if (typeof notify !== 'function') { return; }
    notify({ diffScrollTop: event.currentTarget.scrollTop || 0 });
  }

  if (status === 'loading') {
    return (
      <div className="diff-pane">
        <p className="changes-tab-message">Computing diff…</p>
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className="diff-pane">
        <p className="changes-tab-message error">{errorText}</p>
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
            onOpenAsFile={
              typeof onOpenFile === 'function' && openFile?.absolutePath
                ? () => onOpenFile({
                  absolutePath: openFile.absolutePath,
                  relativePath: openFile.relativePath || selected.path,
                  repoId: selected.repo.repo_id,
                  view: 'file',
                })
                : undefined
            }
            comments={byFile.get(selected.path) || EMPTY_COMMENTS}
            commentsLoading={!!commentsLoading}
            commentsError={commentsError || ''}
            onMutated={bumpComments}
            onCommentSpawned={onCommentSpawned}
          />
        </div>
      </div>
    </div>
  );
}
