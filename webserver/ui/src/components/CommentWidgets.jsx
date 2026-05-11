import { useEffect, useRef, useState } from 'react';
import { formatRelativeTime } from '../utils/relativeTime.js';
import { commentSubmitLock } from '../stores/commentSubmitLock.js';
import { toast } from '../stores/toastStore.js';

// Bubble + thread builder + form, shared between the file-level
// comments panel and the per-line widget rendered through
// react-diff-view's ``widgets`` prop. Lives in its own module so
// both consumers reuse the exact wording (load-bearing for how
// kato pushes replies on remote-sourced threads).

export function buildThreads(comments) {
  const byId = new Map();
  for (const comment of comments) {
    byId.set(comment.id, { ...comment, replies: [] });
  }
  const roots = [];
  for (const comment of byId.values()) {
    if (comment.parent_id && byId.has(comment.parent_id)) {
      byId.get(comment.parent_id).replies.push(comment);
    } else {
      roots.push(comment);
    }
  }
  roots.sort((a, b) => (a.created_at_epoch || 0) - (b.created_at_epoch || 0));
  for (const root of roots) {
    root.replies.sort((a, b) => (a.created_at_epoch || 0) - (b.created_at_epoch || 0));
  }
  return roots.map((root) => ({ root, replies: root.replies }));
}


export function CommentBubble({
  comment, isRoot,
  onResolve, onReopen, onDelete, onReply, onMarkAddressed,
}) {
  const sourceLabel = comment.source === 'remote' ? 'REMOTE' : 'LOCAL';
  const sourceTitle = comment.source === 'remote'
    ? 'Pulled from the source git platform (Bitbucket / GitHub PR review). Resolving locally syncs back to the source thread.'
    : 'Local kato comment — kato runs on this immediately if idle.';
  const ago = comment.created_at_epoch
    ? formatRelativeTime((Date.now() / 1000) - comment.created_at_epoch)
    : '';
  const author = comment.author || (comment.source === 'remote' ? 'remote' : 'operator');
  const isResolved = comment.status === 'resolved';
  const katoStatus = comment.kato_status;
  const showMarkAddressed = (
    isRoot
    && typeof onMarkAddressed === 'function'
    && katoStatus !== 'addressed'
  );

  return (
    <div
      className={[
        'diff-file-comment',
        isRoot ? 'is-root' : 'is-reply',
        comment.source === 'remote' ? 'is-remote' : 'is-local',
      ].filter(Boolean).join(' ')}
    >
      <header className="diff-file-comment-head">
        <span className="diff-file-comment-author">{author}</span>
        <span
          className={[
            'diff-file-comment-source',
            comment.source === 'remote' ? 'is-remote' : 'is-local',
          ].join(' ')}
          title={sourceTitle}
        >
          {sourceLabel}
        </span>
        {ago && <span className="diff-file-comment-ago">{ago}</span>}
        {isRoot && katoStatus && katoStatus !== 'idle' && (
          <span
            className={`diff-file-comment-kato-status is-${katoStatus}`}
            title={describeKatoStatus(comment)}
          >
            {katoStatusLabel(katoStatus)}
          </span>
        )}
      </header>
      {isResolved && isRoot && (
        <div className="diff-file-comment-resolved-banner inline">
          ✓ {comment.resolved_by || 'operator'} resolved this thread
          {comment.resolved_at_epoch ? (
            <> · {formatRelativeTime((Date.now() / 1000) - comment.resolved_at_epoch)}</>
          ) : null}
        </div>
      )}
      <div className="diff-file-comment-body">
        {comment.body || '(empty comment)'}
      </div>
      <footer className="diff-file-comment-actions">
        {typeof onReply === 'function' && (
          <button type="button" onClick={onReply} className="diff-file-comment-action">
            Reply
          </button>
        )}
        {isRoot && !isResolved && typeof onResolve === 'function' && (
          <button type="button" onClick={onResolve} className="diff-file-comment-action">
            Resolve
          </button>
        )}
        {isRoot && isResolved && typeof onReopen === 'function' && (
          <button type="button" onClick={onReopen} className="diff-file-comment-action">
            Reopen
          </button>
        )}
        {showMarkAddressed && (
          <button
            type="button"
            onClick={onMarkAddressed}
            className="diff-file-comment-action"
            title={
              comment.source === 'remote'
                ? 'Mark addressed locally + post the "Kato addressed" reply on the source git platform.'
                : 'Mark this comment as addressed by kato.'
            }
          >
            Mark addressed
          </button>
        )}
        {comment.source === 'local' && typeof onDelete === 'function' && (
          <button
            type="button"
            onClick={onDelete}
            className="diff-file-comment-action danger"
          >
            Delete
          </button>
        )}
      </footer>
    </div>
  );
}


export function CommentForm({
  placeholder = 'Add a comment…',
  onSubmit,
  onCancel,
  replyMode = false,
}) {
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  // Mirror the global lock into local render state so the button
  // shows ``Submitting…`` and disables when ANY comment form is
  // mid-submit, not just this one. Without this an operator with
  // multiple comment forms open could fire two submits in parallel
  // and kato would race two review-fix runs against the same
  // workspace.
  const [globallyLocked, setGloballyLocked] = useState(commentSubmitLock.isBusy());
  const textareaRef = useRef(null);

  // Focus the textarea on mount so the operator can type
  // immediately after clicking the gutter / Reply.
  useEffect(() => {
    if (textareaRef.current) { textareaRef.current.focus(); }
  }, []);

  // Track the global lock so any other form's in-flight submit
  // disables this form's submit button too.
  useEffect(() => commentSubmitLock.subscribe(setGloballyLocked), []);

  async function submit() {
    const trimmed = draft.trim();
    if (!trimmed || busy) { return; }
    if (!commentSubmitLock.acquire()) {
      toast.show({
        kind: 'warning',
        title: 'Another comment is already being submitted',
        message: 'Wait for it to finish, then try again.',
        durationMs: 5000,
      });
      return;
    }
    setBusy(true);
    try {
      const ok = await onSubmit(trimmed);
      if (ok) { setDraft(''); }
    } finally {
      setBusy(false);
      commentSubmitLock.release();
    }
  }

  return (
    <div className="diff-file-comments-form">
      <textarea
        ref={textareaRef}
        className="diff-file-comments-textarea"
        placeholder={`${placeholder} (Cmd+Enter / Ctrl+Enter to submit)`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            submit();
          } else if (e.key === 'Escape' && typeof onCancel === 'function') {
            e.preventDefault();
            onCancel();
          }
        }}
        rows={3}
      />
      <div className="diff-file-comments-form-actions">
        {typeof onCancel === 'function' && (
          <button
            type="button"
            className="diff-file-comments-cancel"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
        )}
        <button
          type="button"
          className="diff-file-comments-submit"
          onClick={submit}
          disabled={busy || globallyLocked || !draft.trim()}
        >
          {(busy || globallyLocked) ? 'Submitting…' : (replyMode ? 'Reply' : 'Add comment')}
        </button>
      </div>
    </div>
  );
}


function katoStatusLabel(status) {
  switch (status) {
    case 'queued': return '⏳ queued';
    case 'in_progress': return '⟳ kato working';
    case 'addressed': return '✓ kato addressed';
    case 'failed': return '✗ kato failed';
    default: return status;
  }
}


function describeKatoStatus(comment) {
  switch (comment.kato_status) {
    case 'queued':
      return 'Kato will run an agent on this comment when the live turn ends.';
    case 'in_progress':
      return 'Kato is running an agent against this comment right now.';
    case 'addressed':
      return comment.kato_addressed_sha
        ? `Kato pushed a fix in commit ${comment.kato_addressed_sha.slice(0, 8)}.`
        : 'Kato addressed this comment.';
    case 'failed':
      return comment.kato_failure_reason
        ? `Kato could not address this comment: ${comment.kato_failure_reason}`
        : 'Kato could not address this comment.';
    default:
      return '';
  }
}
