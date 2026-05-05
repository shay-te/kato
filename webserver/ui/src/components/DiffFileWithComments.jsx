import { useEffect, useMemo, useState } from 'react';
import {
  Diff,
  Hunk,
  computeNewLineNumber,
  getChangeKey,
} from 'react-diff-view';
import {
  createTaskComment,
  deleteTaskComment,
  fetchTaskComments,
  markTaskCommentAddressed,
  reopenTaskComment,
  resolveTaskComment,
} from '../api.js';
import { toast } from '../stores/toastStore.js';
import { tokenizeHunks } from '../utils/diffSyntax.js';
import {
  CommentBubble,
  CommentForm,
  buildThreads,
} from './CommentWidgets.jsx';

// One <Diff> + per-line comment threads + file-level thread, all
// in one component so the comments state is shared across the
// gutter widgets and the bottom panel. Wraps react-diff-view's
// ``widgets`` API: each comment with ``line >= 0`` becomes a
// widget keyed by ``getChangeKey`` of the matching change. Clicks
// on the line gutter open an inline new-comment form widget at
// that line. File-level comments (``line < 0``) live in the
// bottom panel below the diff.
export default function DiffFileWithComments({
  file, conflicted = false, repoId = '', taskId = '',
  onAddToChat,
  refreshTick = 0,
}) {
  const path = file.newPath || file.oldPath || '(unknown)';

  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Bumped on every successful mutation so the per-file fetch
  // re-runs immediately (no waiting on the parent's poll tick).
  const [localTick, setLocalTick] = useState(0);
  // ``activeLine`` is the line number where the inline new-comment
  // form is currently open. ``-1`` is the file-level panel below
  // the diff. ``null`` means no inline form is open.
  const [activeLine, setActiveLine] = useState(null);
  const [replyTo, setReplyTo] = useState('');

  const tokens = useMemo(
    () => tokenizeHunks(file.hunks || [], path),
    [file.hunks, path],
  );

  useEffect(() => {
    if (!taskId || !repoId) { return undefined; }
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchTaskComments(taskId, repoId).then((result) => {
      if (cancelled) { return; }
      if (!result.ok) {
        setError(String(result.error || 'failed to load comments'));
        setComments([]);
        return;
      }
      const list = Array.isArray(result.body?.comments) ? result.body.comments : [];
      setComments(list.filter((c) => c.file_path === path));
    }).finally(() => {
      if (!cancelled) { setLoading(false); }
    });
    return () => { cancelled = true; };
  }, [taskId, repoId, path, refreshTick, localTick]);

  // Group comments by line so we can build the widgets dict and
  // the file-level panel separately. Line < 0 means "file-level."
  const { commentsByLine, fileLevelComments } = useMemo(() => {
    const byLine = new Map();
    const fileLevel = [];
    for (const comment of comments) {
      const ln = Number(comment.line);
      if (Number.isFinite(ln) && ln >= 0) {
        if (!byLine.has(ln)) { byLine.set(ln, []); }
        byLine.get(ln).push(comment);
      } else {
        fileLevel.push(comment);
      }
    }
    return { commentsByLine: byLine, fileLevelComments: fileLevel };
  }, [comments]);

  async function onSubmit(line, body, parentId = '') {
    const trimmed = String(body || '').trim();
    if (!trimmed) { return false; }
    const result = await createTaskComment(taskId, {
      repo: repoId,
      file_path: path,
      line,
      body: trimmed,
      parent_id: parentId,
    });
    if (!result.ok) {
      toast.show({
        kind: 'error',
        title: 'Could not add comment',
        message: (result.body && result.body.error) || result.error || 'add failed',
        durationMs: 8000,
      });
      return false;
    }
    const triggered = result.body?.triggered_immediately;
    toast.show({
      kind: 'success',
      title: 'Comment added',
      message: parentId
        ? '✓ reply posted (kato runs only on top-of-thread comments)'
        : (triggered
          ? '✓ kato is working on this comment now'
          : '✓ queued — kato will pick it up when the live agent goes idle'),
      durationMs: 5000,
    });
    setActiveLine(null);
    setReplyTo('');
    setLocalTick((n) => n + 1);
    return true;
  }

  async function onResolve(commentId) {
    const result = await resolveTaskComment(taskId, commentId);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Resolve failed',
        message: (result.body && result.body.error) || result.error || '',
      });
      return;
    }
    const remoteSync = result.body?.remote_sync;
    if (remoteSync && remoteSync.attempted) {
      const lines = [];
      if (remoteSync.reply_posted) {
        lines.push('✓ posted reply on the source git platform');
      }
      if (remoteSync.resolved) {
        lines.push('✓ resolved the source thread too');
      }
      const errs = [
        remoteSync.error, remoteSync.reply_error, remoteSync.resolve_error,
      ].filter(Boolean);
      if (errs.length) {
        lines.push(`⚠ source-platform sync had issues: ${errs.join('; ')}`);
      }
      if (lines.length) {
        toast.show({
          kind: errs.length ? 'warning' : 'success',
          title: 'Resolved',
          message: lines.join('\n'),
          durationMs: 6000,
        });
      }
    }
    setLocalTick((n) => n + 1);
  }

  async function onReopen(commentId) {
    const result = await reopenTaskComment(taskId, commentId);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Reopen failed',
        message: (result.body && result.body.error) || result.error || '',
      });
      return;
    }
    setLocalTick((n) => n + 1);
  }

  async function onDelete(commentId) {
    if (!window.confirm('Delete this comment? Replies will be removed too.')) {
      return;
    }
    const result = await deleteTaskComment(taskId, commentId);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Delete failed',
        message: (result.body && result.body.error) || result.error || '',
      });
      return;
    }
    setLocalTick((n) => n + 1);
  }

  async function onMarkAddressed(commentId, addressedSha = '') {
    const result = await markTaskCommentAddressed(taskId, commentId, addressedSha);
    if (!result.ok) {
      toast.show({
        kind: 'error', title: 'Mark addressed failed',
        message: (result.body && result.body.error) || result.error || '',
      });
      return;
    }
    const remote = result.body?.remote_reply;
    if (remote && remote.attempted) {
      if (remote.reply_posted) {
        toast.show({
          kind: 'success', title: 'Posted on source platform',
          message: '✓ "Kato addressed this review comment" reply posted',
          durationMs: 5000,
        });
      } else if (remote.error || remote.reply_error) {
        toast.show({
          kind: 'warning',
          title: 'Marked addressed locally',
          message: `Source-platform reply failed: ${remote.error || remote.reply_error}`,
          durationMs: 8000,
        });
      }
    }
    setLocalTick((n) => n + 1);
  }

  // Build the react-diff-view widgets dict. Each widget is keyed
  // by the change's stable id (``getChangeKey``) so the line
  // doesn't lose its widget when the diff re-tokenizes between
  // polls. Widget content is the threads at that line plus an
  // inline new-comment form when ``activeLine`` matches.
  const widgets = useMemo(() => {
    const out = {};
    for (const hunk of file.hunks || []) {
      for (const change of hunk.changes || []) {
        const lineNumber = computeNewLineNumber(change);
        if (lineNumber == null || lineNumber < 0) { continue; }
        const lineComments = commentsByLine.get(lineNumber);
        const isActive = activeLine === lineNumber;
        if (!lineComments && !isActive) { continue; }
        const key = getChangeKey(change);
        const threads = buildThreads(lineComments || []);
        out[key] = (
          <div className="diff-line-comments-host">
            {threads.map((thread) => (
              <article
                key={thread.root.id}
                className={[
                  'diff-file-comment-thread',
                  thread.root.status === 'resolved' ? 'is-resolved' : '',
                ].filter(Boolean).join(' ')}
              >
                <CommentBubble
                  comment={thread.root}
                  isRoot
                  onResolve={() => onResolve(thread.root.id)}
                  onReopen={() => onReopen(thread.root.id)}
                  onDelete={() => onDelete(thread.root.id)}
                  onReply={() => {
                    setActiveLine(lineNumber);
                    setReplyTo(thread.root.id);
                  }}
                  onMarkAddressed={() => onMarkAddressed(thread.root.id)}
                />
                {thread.replies.map((reply) => (
                  <CommentBubble
                    key={reply.id}
                    comment={reply}
                    isRoot={false}
                    onDelete={() => onDelete(reply.id)}
                    onReply={() => {
                      setActiveLine(lineNumber);
                      setReplyTo(thread.root.id);
                    }}
                  />
                ))}
              </article>
            ))}
            {isActive && (
              <CommentForm
                placeholder={
                  replyTo
                    ? 'Add a reply…'
                    : `Comment on line ${lineNumber}…`
                }
                onSubmit={(body) => onSubmit(lineNumber, body, replyTo)}
                onCancel={() => { setActiveLine(null); setReplyTo(''); }}
                replyMode={!!replyTo}
              />
            )}
          </div>
        );
      }
    }
    return out;
  }, [file.hunks, commentsByLine, activeLine, replyTo]);

  // Gutter click → open the inline form at that line.
  const gutterEvents = useMemo(() => ({
    onClick: ({ change }) => {
      const ln = computeNewLineNumber(change);
      if (ln == null || ln < 0) { return; }
      setActiveLine((current) => (current === ln ? null : ln));
      setReplyTo('');
    },
  }), []);

  // Right-click → paste path + selection into chat composer (the
  // existing affordance the operator already has).
  function onContextMenu(event) {
    if (typeof onAddToChat !== 'function') { return; }
    event.preventDefault();
    const fragment = buildChatFragmentFromSelection(path, repoId);
    if (fragment) { onAddToChat(fragment); }
  }

  const fileThreads = useMemo(
    () => buildThreads(fileLevelComments),
    [fileLevelComments],
  );

  return (
    <section
      className="diff-file"
      onContextMenu={onContextMenu}
      title="Click a line gutter to add an inline comment · right-click to paste path + selection into chat"
    >
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
        widgets={widgets}
        gutterEvents={gutterEvents}
      >
        {(hunks) => hunks.map((hunk) => (
          <Hunk key={hunk.content} hunk={hunk} />
        ))}
      </Diff>
      <div className="diff-file-comments">
        {loading && <p className="diff-file-comments-empty">Loading comments…</p>}
        {!loading && error && (
          <p className="diff-file-comments-empty error">{error}</p>
        )}
        {!loading && !error && fileThreads.length === 0 && commentsByLine.size === 0 && (
          <p className="diff-file-comments-empty">
            Click a diff line's gutter to add an inline comment, or use
            the form below for a file-level comment. Kato runs on it
            immediately if idle, or queues it.
          </p>
        )}
        {!loading && !error && fileThreads.map((thread) => (
          <article
            key={thread.root.id}
            className={[
              'diff-file-comment-thread',
              thread.root.status === 'resolved' ? 'is-resolved' : '',
            ].filter(Boolean).join(' ')}
          >
            <CommentBubble
              comment={thread.root}
              isRoot
              onResolve={() => onResolve(thread.root.id)}
              onReopen={() => onReopen(thread.root.id)}
              onDelete={() => onDelete(thread.root.id)}
              onReply={() => {
                setActiveLine(-1);
                setReplyTo(thread.root.id);
              }}
              onMarkAddressed={() => onMarkAddressed(thread.root.id)}
            />
            {thread.replies.map((reply) => (
              <CommentBubble
                key={reply.id}
                comment={reply}
                isRoot={false}
                onDelete={() => onDelete(reply.id)}
                onReply={() => {
                  setActiveLine(-1);
                  setReplyTo(thread.root.id);
                }}
              />
            ))}
          </article>
        ))}
        {(activeLine === -1 || fileThreads.length === 0) && (
          <CommentForm
            placeholder={
              replyTo
                ? 'Add a reply…'
                : 'Add a file-level comment…'
            }
            onSubmit={(body) => onSubmit(-1, body, activeLine === -1 ? replyTo : '')}
            onCancel={
              activeLine === -1
                ? () => { setActiveLine(null); setReplyTo(''); }
                : null
            }
            replyMode={!!replyTo && activeLine === -1}
          />
        )}
      </div>
    </section>
  );
}


// Lift the diff-selection chat fragment helper inline so the
// component can consume it without an extra import — kept narrow
// to avoid pulling the full ChangesTab dependency graph.
function buildChatFragmentFromSelection(path, repoId) {
  if (typeof window === 'undefined' || !window.getSelection) { return ''; }
  const safePath = String(path || '').trim();
  if (!safePath) { return ''; }
  const repoPrefix = repoId ? `${repoId}:` : '';
  const text = String(window.getSelection().toString() || '').trim();
  if (!text) { return `\`${repoPrefix}${safePath}\``; }
  const truncated = text.length > 8 * 1024
    ? `${text.slice(0, 8 * 1024)}\n… (selection truncated)`
    : text;
  return (
    `In \`${repoPrefix}${safePath}\` the following diff lines:\n`
    + '```\n'
    + truncated
    + '\n```'
  );
}
