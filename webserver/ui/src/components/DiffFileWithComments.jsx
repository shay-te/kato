import { memo, useEffect, useMemo, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import {
  Decoration,
  Diff,
  Hunk,
  computeNewLineNumber,
  computeOldLineNumber,
  expandFromRawCode,
  getChangeKey,
} from 'react-diff-view';

// Encode/decode old-side (deleted) line numbers so they are stored
// alongside new-side line numbers without collision. Any value
// <= -(OLD_LINE_OFFSET + 1) is an old-side encoded number;
// -1 through -(OLD_LINE_OFFSET) are file-level comment sentinels.
const OLD_LINE_OFFSET = 2000;
function encodeOldLine(n) { return -(n + OLD_LINE_OFFSET); }
function decodeOldLine(encoded) { return (-encoded) - OLD_LINE_OFFSET; }
function isOldSideEncoded(n) { return n < -(OLD_LINE_OFFSET); }
import { fetchBaseFileContent } from '../api.js';
import { commentStore } from '../stores/commentStore.js';
import { toast } from '../stores/toastStore.js';
import { diffDisplayPath } from '../diffModel.js';
import { copyRepoRelativePath } from '../utils/clipboard.js';
import { useDismissOnOutsidePointerOrEscape } from '../hooks/useDismissOnOutsidePointerOrEscape.js';
import { useClampedPointMenu } from '../hooks/useClampedPointMenu.js';
import { commentDraftKey } from '../utils/composerDraft.js';
import { buildChatFragmentFromSelection } from '../utils/diffSelectionPrompt.js';
import { tokenizeHunks } from '../utils/diffSyntax.js';
import {
  CommentForm,
  CommentThread,
  buildThreads,
  katoTriggeredMessage,
} from './CommentWidgets.jsx';
import StickyHeader from './StickyHeader.jsx';
import {
  basePathForDiffFile,
  buildDiffRenderItems,
  expansionRangeForGap,
  pendingCommentExpansions,
  splitSourceLines,
} from './DiffExpansionHelpers.js';
import { isLargeFile } from './diffFileSize.js';
import DiffKindIcon from './DiffKindIcon.jsx';
import { countNoun } from '../utils/pluralize.js';

export function splitCommentsForDisplay(comments) {
  const byLine = new Map();
  const fileLevel = [];
  const allComments = Array.isArray(comments) ? comments : [];
  const byId = new Map();
  const rootById = new Map();
  const outdatedRoots = new Set();
  for (const comment of allComments) {
    byId.set(String(comment.id || ''), comment);
    if (!comment.parent_id && comment.outdated) {
      outdatedRoots.add(String(comment.id || ''));
    }
  }
  function rootIdOf(comment) {
    const seen = new Set();
    let current = comment;
    while (current?.parent_id && !seen.has(String(current.id || ''))) {
      seen.add(String(current.id || ''));
      const parent = byId.get(String(current.parent_id || ''));
      if (!parent) { break; }
      current = parent;
    }
    return String(current?.id || '');
  }
  for (const comment of allComments) {
    rootById.set(String(comment.id || ''), rootIdOf(comment));
  }
  for (const comment of allComments) {
    const ln = Number(comment.line);
    const isLineTarget = Number.isFinite(ln) && (ln >= 0 || isOldSideEncoded(ln));
    const isOutdatedThread = outdatedRoots.has(rootById.get(String(comment.id || '')));
    if (!comment.outdated && !isOutdatedThread && isLineTarget) {
      if (!byLine.has(ln)) { byLine.set(ln, []); }
      byLine.get(ln).push(comment);
    } else {
      fileLevel.push(comment);
    }
  }
  return { commentsByLine: byLine, fileLevelComments: fileLevel };
}

// Default ``initiallyExpanded`` resolver: per-file rule only.
function _defaultInitiallyExpanded(file) {
  return !isLargeFile(file);
}

function renderPathSegments(path) {
  const rawPath = String(path || '');
  const parts = rawPath.includes('/') && !rawPath.startsWith('/')
    ? rawPath.split('/').filter(Boolean)
    : [rawPath];
  return parts.map((part, index) => {
    const separator = index > 0 ? (
      <span className="diff-file-path-separator">/</span>
    ) : null;
    return (
      <span className="diff-file-path-part" key={`${part}-${index}`}>
        {separator}
        <span className="diff-file-path-segment">{part}</span>
      </span>
    );
  });
}

// One <Diff> + per-line comment threads + file-level thread, all
// in one component so the comments state is shared across the
// gutter widgets and the bottom panel. Wraps react-diff-view's
// ``widgets`` API: each comment with ``line >= 0`` becomes a
// widget keyed by ``getChangeKey`` of the matching change. Clicks
// on the line gutter open an inline new-comment form widget at
// that line. File-level comments (``line < 0``) live in the
// bottom panel below the diff.
// Memoized: a comments poll / workspace-version bump that re-renders
// DiffPane must NOT re-run the file box when nothing it shows changed.
// All inputs arrive as props (no context), and DiffPane keeps ``file``
// (parseDiffCached) + ``comments`` (unchanged-payload guard)
// referentially stable, so the default shallow compare lets it bail.
function DiffFileWithComments({
  file, conflicted = false, repoId = '', repoCwd = '', taskId = '',
  initiallyExpanded,
  forceExpandToken = 0,
  onAddToChat,
  onFocusInTree,
  // Swap the centre column from the diff view to a plain editor view of
  // this file. Wired by DiffPane → App.handleOpenFile (view: 'file').
  // Shown as a header icon when set.
  onOpenAsFile,
  comments = [],
  commentsLoading = false,
  commentsError = '',
  onMutated,
  onCommentSpawned,
}) {
  // Use the shared resolver, NOT ``file.newPath || file.oldPath``:
  // react-diff-view sets the missing side to ``/dev/null`` for pure
  // add/delete, so the naive form renders a deleted file's header as
  // "/dev/null" instead of its real (old) path.
  const path = diffDisplayPath(file);

  // ``activeLine`` is the line number where the inline new-comment
  // form is currently open. ``-1`` is the file-level panel below
  // the diff. ``null`` means no inline form is open.
  const [activeLine, setActiveLine] = useState(null);
  const [replyTo, setReplyTo] = useState('');
  // Auto-collapse big files. Rendering a 5K-line diff into the
  // DOM freezes the browser's paint loop and makes EVERY input on
  // the page lag (typing in the chat composer, opening the adopt
  // modal, etc.). Below the threshold the file expands by default
  // — the operator's normal flow is unchanged.
  // ``initiallyExpanded`` (passed by DiffPane, which always expands
  // its single selected file) overrides the per-file size rule; the
  // rule is the fallback for callers that don't pass it.
  const [expanded, setExpanded] = useState(() => (
    typeof initiallyExpanded === 'boolean'
      ? initiallyExpanded
      : _defaultInitiallyExpanded(file)
  ));
  const [renderedHunks, setRenderedHunks] = useState(() => file.hunks || []);
  // In-flight base-source fetch, so concurrent callers coalesce (see
  // ``loadBaseSourceLines``). Cleared on file switch below.
  const baseSourcePromiseRef = useRef(null);
  // Latest loaded base lines, read by ``loadBaseSourceLines`` so a caller
  // whose closure captured a pre-load ``baseSource`` still gets the lines
  // (no stale-closure "already loaded but I see null" race).
  const baseSourceLinesRef = useRef(null);
  const [baseSource, setBaseSource] = useState({
    status: 'idle',
    lines: null,
    error: '',
  });
  const [pathMenu, setPathMenu] = useState(null);

  // A stable CONTENT signature of the diff — same string across identical
  // 5s polls (``file.hunks`` gets a fresh array reference each poll even
  // when the bytes are unchanged), but different the instant the diff
  // actually changes (Claude edits the open file, a merge lands, …). It
  // must react to the actual TEXT, not just line counts/lengths: a Claude
  // edit that swaps a value on a line (``b`` → ``c``) keeps every length
  // identical, so a length-only signature would miss it and the open file
  // would go stale. Cheap FNV-1a hash over each change's content — one pass
  // over the hunks, tiny result.
  const hunksSignature = useMemo(() => {
    let hash = 0x811c9dc5;
    const mix = (text) => {
      for (let i = 0; i < text.length; i += 1) {
        hash ^= text.charCodeAt(i);
        hash = Math.imul(hash, 0x01000193);
      }
      hash ^= 0x7f; // separator, so "ab"+"c" ≠ "a"+"bc"
    };
    const hunks = file.hunks || [];
    for (const hunk of hunks) {
      mix(hunk.content || '');
      for (const change of hunk.changes || []) {
        mix(change.content || '');
      }
    }
    return (hash >>> 0).toString(16);
  }, [file.hunks]);

  // Reset the rendered hunks when the file identity changes (operator
  // switched files / the workspace changed) OR when the open file's diff
  // actually changed (``hunksSignature``). Keying on the SIGNATURE — not
  // the ``file.hunks`` array reference — is what lets the OPEN file update
  // LIVE when Claude edits it, WITHOUT wiping the operator's manual
  // expansions on every idle poll (unchanged bytes → same signature → no
  // reset). A genuine change resets to the fresh diff, which is exactly
  // what the operator wants to see.
  useEffect(() => {
    setRenderedHunks(file.hunks || []);
    setBaseSource({ status: 'idle', lines: null, error: '' });
    baseSourcePromiseRef.current = null;
    baseSourceLinesRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, repoId, repoCwd, taskId, hunksSignature]);

  useEffect(() => {
    if (forceExpandToken) { setExpanded(true); }
  }, [forceExpandToken]);

  // Tokenisation walks every hunk synchronously and is by far the
  // hottest first-paint cost on big diffs. Skip it entirely when
  // the file is collapsed; recompute lazily on expand.
  const tokens = useMemo(
    () => (expanded ? tokenizeHunks(renderedHunks, path) : null),
    [renderedHunks, path, expanded],
  );

  // Per-file gutter width: largest actual old/new line plus 1ch.
  // CSS padding supplies the rest, matching Bitbucket's tight columns.
  const gutterColWidth = useMemo(() => {
    let maxLine = 1;
    for (const hunk of renderedHunks || []) {
      const oldEnd = (hunk.oldStart || 0) + Math.max(0, hunk.oldLines || 0) - 1;
      const newEnd = (hunk.newStart || 0) + Math.max(0, hunk.newLines || 0) - 1;
      if (oldEnd > maxLine) { maxLine = oldEnd; }
      if (newEnd > maxLine) { maxLine = newEnd; }
    }
    return `${String(maxLine).length + 1}ch`;
  }, [renderedHunks]);

  function notifyMutated() {
    if (typeof onMutated === 'function') { onMutated(); }
  }

  // Group comments by display target: live anchors render inline;
  // outdated anchors move to the file-level panel.
  const { commentsByLine, fileLevelComments } = useMemo(() => {
    return splitCommentsForDisplay(comments);
  }, [comments]);

  // New-side line numbers that carry at least one OPEN (un-resolved)
  // comment. These are the threads that must never be buried inside a
  // collapsed gap. Resolved-only lines are intentionally excluded —
  // they don't need to force the diff open. Stable across renders
  // when the comment set is unchanged so the reveal effect below
  // doesn't thrash.
  const openCommentLines = useMemo(() => {
    const out = [];
    for (const [line, lineComments] of commentsByLine) {
      // Auto-reveal only works for new-side lines (react-diff-view expand logic).
      if (line < 0) { continue; }
      if (lineComments.some((c) => c.status !== 'resolved')) {
        out.push(line);
      }
    }
    return out.sort((a, b) => a - b);
  }, [commentsByLine]);

  // Never let auto-collapse bury a comment. A file can start collapsed
  // (the big-file perf rule) while carrying an open inline thread — and
  // a collapsed file renders NO inline comments at all (``widgets`` is
  // skipped, ``diffBody`` is null). Expand it once so the thread is on
  // screen, then let the new-side auto-reveal below pull the exact line
  // out of any collapsed gap. Ref-guarded + reset per file so a
  // deliberate manual collapse afterward sticks; we only force the
  // FIRST reveal. Resolved-only files are left collapsed (nothing to
  // surface). Old-side and truly-orphaned threads are handled by the
  // comments panel / reveal logic, not here.
  const autoExpandedForCommentsRef = useRef(false);
  // Once the operator manually toggles collapse/expand, the comment
  // auto-expand below must stop fighting them — a file they collapsed
  // STAYS collapsed while they read, even if it carries an open comment
  // and the diff refreshes underneath them.
  const userToggledExpandRef = useRef(false);
  useEffect(() => {
    autoExpandedForCommentsRef.current = false;
    userToggledExpandRef.current = false;
  }, [path, repoId, taskId]);
  useEffect(() => {
    if (
      userToggledExpandRef.current
      || autoExpandedForCommentsRef.current
      || expanded
    ) { return; }
    let hasOpenInline = false;
    for (const lineComments of commentsByLine.values()) {
      if (lineComments.some((c) => c.status !== 'resolved')) {
        hasOpenInline = true;
        break;
      }
    }
    if (hasOpenInline) {
      autoExpandedForCommentsRef.current = true;
      setExpanded(true);
    }
  }, [commentsByLine, expanded]);

  // Operator-driven collapse/expand. Records the interaction so the
  // comment auto-expand effect above never re-opens what they closed.
  function setExpandedByUser(value) {
    userToggledExpandRef.current = true;
    setExpanded(value);
  }

  async function onSubmit(line, body, parentId = '') {
    const trimmed = String(body || '').trim();
    if (!trimmed) { return false; }
    const result = await commentStore.create(taskId, {
      repo: repoId,
      file_path: path,
      line,
      body: trimmed,
      parent_id: parentId,
    });
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Could not add comment',
        fallback: 'add failed',
        durationMs: 8000,
      });
      return false;
    }
    const triggered = result.body?.triggered_immediately;
    toast.show({
      kind: 'success',
      title: parentId ? 'Reply posted' : 'Comment added',
      message: parentId
        ? (triggered
          ? '✓ kato is re-addressing this thread now'
          : '✓ thread re-queued — kato will pick up your reply when it goes idle')
        : katoTriggeredMessage(triggered),
      durationMs: 5000,
    });
    setActiveLine(null);
    setReplyTo('');
    notifyMutated();
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
    return true;
  }

  async function onResolve(commentId) {
    const result = await commentStore.resolve(taskId, commentId);
    if (!result.ok) {
      toast.errorFromResult(result, { title: 'Resolve failed', durationMs: 5000 });
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
    notifyMutated();
  }

  async function onReopen(commentId) {
    const result = await commentStore.reopen(taskId, commentId);
    if (!result.ok) {
      toast.errorFromResult(result, { title: 'Reopen failed', durationMs: 5000 });
      return;
    }
    const triggered = result.body?.triggered_immediately;
    toast.show({
      kind: 'success',
      title: 'Comment reopened',
      message: katoTriggeredMessage(triggered),
      durationMs: 5000,
    });
    notifyMutated();
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
  }

  async function onRetry(commentId) {
    const result = await commentStore.retry(taskId, commentId);
    if (!result.ok) {
      toast.errorFromResult(result, { title: 'Retry failed', durationMs: 5000 });
      return;
    }
    const triggered = result.body?.triggered_immediately;
    toast.show({
      kind: 'success',
      title: 'Comment re-queued',
      message: katoTriggeredMessage(triggered),
      durationMs: 5000,
    });
    notifyMutated();
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
  }

  async function onDelete(commentId) {
    if (!window.confirm('Delete this comment? Replies will be removed too.')) {
      return;
    }
    const result = await commentStore.remove(taskId, commentId);
    if (!result.ok) {
      toast.errorFromResult(result, { title: 'Delete failed', durationMs: 5000 });
      return;
    }
    notifyMutated();
  }

  async function onEdit(commentId, { body, katoStatus } = {}) {
    const result = await commentStore.edit(taskId, commentId, { body, katoStatus });
    // Surface BOTH layers: HTTP failure (404 if the route isn't
    // registered yet — i.e. kato hasn't been restarted since this
    // feature landed) AND envelope-level ``{ok: false}`` (the service
    // returns this on validation rejects: not-found / non-local /
    // status not in queued+editing). Without the body.ok check, a
    // validation refusal would silently look like success.
    if (!result.ok || result.body?.ok === false) {
      toast.errorFromResult(result, { title: 'Edit failed', durationMs: 5000 });
      return false;
    }
    notifyMutated();
    return true;
  }

  async function onMarkAddressed(commentId, addressedSha = '') {
    const result = await commentStore.markAddressed(taskId, commentId, addressedSha);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Mark addressed failed', durationMs: 5000,
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
    notifyMutated();
  }

  // Build the react-diff-view widgets dict. Each widget is keyed
  // by the change's stable id (``getChangeKey``) so the line
  // doesn't lose its widget when the diff re-tokenizes between
  // polls. Widget content is the threads at that line plus an
  // inline new-comment form when ``activeLine`` matches. Skipped
  // entirely when collapsed — the dict feeds into a <Diff> we are
  // not going to render anyway.
  const widgets = useMemo(() => {
    if (!expanded) { return {}; }
    const out = {};
    function buildWidget(changeKey, lineKey, isOldSide) {
      const lineComments = commentsByLine.get(lineKey);
      const isActive = activeLine === lineKey;
      if (!lineComments && !isActive) { return; }
      const threads = buildThreads(lineComments || []);
      const displayLine = isOldSide ? decodeOldLine(lineKey) : lineKey;
      out[changeKey] = (
        <div className="diff-line-comments-host">
          {threads.map((thread) => (
            <CommentThread
              key={thread.root.id}
              thread={thread}
              onResolve={onResolve}
              onReopen={onReopen}
              onDelete={onDelete}
              onMarkAddressed={onMarkAddressed}
              onRetry={onRetry}
              onEdit={onEdit}
              onReply={(rootId) => {
                setActiveLine(lineKey);
                setReplyTo(rootId);
              }}
            />
          ))}
          {isActive && (
            <CommentForm
              placeholder={
                replyTo
                  ? 'Add a reply…'
                  : isOldSide
                    ? `Comment on deleted line ${displayLine}…`
                    : `Comment on line ${displayLine}…`
              }
              onSubmit={(body) => onSubmit(lineKey, body, replyTo)}
              onCancel={() => { setActiveLine(null); setReplyTo(''); }}
              replyMode={!!replyTo}
              draftKey={commentDraftKey(taskId, repoId, path, lineKey, replyTo)}
            />
          )}
        </div>
      );
    }
    for (const hunk of renderedHunks) {
      for (const change of hunk.changes || []) {
        const newLn = computeNewLineNumber(change);
        if (newLn != null && newLn >= 0) {
          buildWidget(getChangeKey(change), newLn, false);
        } else if (change.type === 'delete') {
          const oldLn = computeOldLineNumber(change);
          if (oldLn != null && oldLn >= 0) {
            buildWidget(getChangeKey(change), encodeOldLine(oldLn), true);
          }
        }
      }
    }
    return out;
  }, [renderedHunks, commentsByLine, activeLine, replyTo, expanded]);

  // Gutter click → open the inline form at that line (new-side or old-side).
  const gutterEvents = useMemo(() => ({
    onClick: ({ change }) => {
      const newLn = computeNewLineNumber(change);
      if (newLn != null && newLn >= 0) {
        setActiveLine((current) => (current === newLn ? null : newLn));
        setReplyTo('');
        return;
      }
      if (change.type === 'delete') {
        const oldLn = computeOldLineNumber(change);
        if (oldLn != null && oldLn >= 0) {
          const encoded = encodeOldLine(oldLn);
          setActiveLine((current) => (current === encoded ? null : encoded));
          setReplyTo('');
        }
      }
    },
  }), []);

  function openPathMenu(event) {
    event.preventDefault();
    event.stopPropagation();
    setPathMenu({ x: event.clientX, y: event.clientY });
  }

  function closePathMenu() {
    setPathMenu(null);
  }

  function showPathInTree() {
    closePathMenu();
    if (typeof onFocusInTree === 'function') {
      onFocusInTree({ repoId, relativePath: path });
    }
  }

  function placePathInChat() {
    closePathMenu();
    if (typeof onAddToChat !== 'function') { return; }
    const fragment = buildChatFragmentFromSelection(path, repoId);
    if (fragment) { onAddToChat(fragment); }
  }

  async function copyHeaderRelativePath() {
    closePathMenu();
    await copyRepoRelativePath(repoId, path);
  }

  useDismissOnOutsidePointerOrEscape(pathMenu, closePathMenu);
  const { menuRef: pathMenuRef, style: pathMenuStyle } = useClampedPointMenu(pathMenu);

  const fileThreads = useMemo(
    () => buildThreads(fileLevelComments),
    [fileLevelComments],
  );

  // The file-level comment form is shown ONLY when the operator
  // explicitly opens it (activeLine === -1, set by Reply on a
  // thread OR the "Add file-level comment" entry button). Previously
  // the form auto-opened on every file that had no comments yet,
  // which planted an unrequested textarea + Add-comment button
  // under every clean file in a diff — visual noise that operators
  // never asked for. The entry button below still surfaces the form
  // when needed.
  const fileFormOpen = activeLine === -1;
  const fileFormReplyMode = !!replyTo && activeLine === -1;
  const conflictedBadge = conflicted ? (
    <span
      className="diff-file-conflicted"
      aria-label="merge conflict"
      title="This file has merge conflicts that must be resolved before it can be merged."
    >
      <Icon name="warning" />
    </span>
  ) : null;
  const collapseToggle = expanded ? (
    <button
      type="button"
      className="diff-file-collapse-toggle is-icon tooltip-below"
      onClick={() => setExpandedByUser(false)}
      data-tooltip="Collapse diff"
      aria-label="Collapse diff"
    >
      <Icon name="chevron-down" />
    </button>
  ) : (
    <button
      type="button"
      className="diff-file-collapse-toggle is-icon tooltip-below"
      onClick={() => setExpandedByUser(true)}
      data-tooltip="Expand diff"
      aria-label="Expand diff"
    >
      <Icon name="chevron-right" />
    </button>
  );

  function loadBaseSourceLines() {
    if (baseSourceLinesRef.current) { return Promise.resolve(baseSourceLinesRef.current); }
    // Coalesce concurrent callers onto ONE in-flight fetch. Without this the
    // eager load-on-expand (for the trailing gap) and a manual gap-expander
    // click race: the second caller used to see status 'loading' and get
    // ``null``, silently dropping its expansion.
    if (baseSourcePromiseRef.current) { return baseSourcePromiseRef.current; }
    const basePath = basePathForDiffFile(file, path);
    if (!basePath || basePath === '/dev/null') { return Promise.resolve(null); }
    setBaseSource({ status: 'loading', lines: null, error: '' });
    const promise = (async () => {
      try {
        const body = await fetchBaseFileContent(taskId, {
          repoId,
          repoCwd,
          path: basePath,
        });
        if (body.binary || body.too_large) {
          const error = body.too_large ? 'file too large' : 'binary file';
          setBaseSource({ status: 'error', lines: null, error });
          return null;
        }
        const lines = splitSourceLines(body.content || '');
        baseSourceLinesRef.current = lines;
        setBaseSource({ status: 'ready', lines, error: '' });
        return lines;
      } catch (err) {
        setBaseSource({ status: 'error', lines: null, error: String(err) });
        return null;
      } finally {
        baseSourcePromiseRef.current = null;
      }
    })();
    baseSourcePromiseRef.current = promise;
    return promise;
  }

  // Auto-reveal buried threads. A comment anchored to a line that
  // sits inside a collapsed "N hidden lines" gap gets no
  // react-diff-view widget, so the thread is invisible until the
  // operator manually clicks the ↑/↓ expanders enough times to drag
  // that line into a hunk — the "I have to load more just to see the
  // comment" trap. Here: whenever an open comment's line isn't
  // already rendered, pull the base file and expand a tight window
  // around it so open threads are ALWAYS on screen. Re-runs after
  // each expand (renderedHunks dep) until nothing is missing, and
  // again whenever the base source becomes available.
  useEffect(() => {
    if (!expanded || openCommentLines.length === 0) { return undefined; }
    const present = new Set();
    for (const hunk of renderedHunks) {
      for (const change of hunk.changes || []) {
        const ln = computeNewLineNumber(change);
        if (ln != null && ln >= 0) { present.add(ln); }
      }
    }
    const missing = openCommentLines.filter((ln) => !present.has(ln));
    if (missing.length === 0) { return undefined; }
    let cancelled = false;
    (async () => {
      const sourceLines = await loadBaseSourceLines();
      if (cancelled || !sourceLines) { return; }
      const ranges = pendingCommentExpansions(
        renderedHunks, missing, sourceLines.length,
      );
      if (ranges.length === 0) { return; }
      setRenderedHunks((current) => {
        let next = current;
        for (const range of ranges) {
          next = expandFromRawCode(next, sourceLines, range.start, range.end);
        }
        return next;
      });
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, openCommentLines, renderedHunks, baseSource]);

  // Eagerly load the base file's line count once the file is expanded so
  // the TRAILING gap (the "view more lines below the last hunk" expander)
  // can render — it needs the total line count to know how many lines sit
  // between the last hunk and EOF. Without this the bottom expander only
  // appeared after clicking some OTHER expander first (which is what
  // triggered the lazy load). Leading/middle gaps compute from the hunks
  // alone, so they were never affected. One fetch per file, cached.
  useEffect(() => {
    if (!expanded) { return undefined; }
    if (baseSource.status !== 'idle') { return undefined; }
    loadBaseSourceLines();
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, baseSource.status]);

  async function onExpandGap(event, gap, direction) {
    event.preventDefault();
    const range = expansionRangeForGap(gap, direction, event.shiftKey);
    if (!range) { return; }
    const sourceLines = await loadBaseSourceLines();
    if (!sourceLines) {
      toast.show({
        kind: 'warning',
        title: 'Could not expand context',
        message: baseSource.error || 'base file is not available yet',
        durationMs: 5000,
      });
      return;
    }
    setRenderedHunks((current) => (
      expandFromRawCode(current, sourceLines, range.start, range.end)
    ));
  }

  function renderGapDecoration(gap) {
    // ``loading`` only marks the buttons busy (aria-busy) — it must NOT
    // disable them. The base file is fetched eagerly on expand (so the
    // trailing gap can render), which means a fast operator would otherwise
    // hit a dead expander for the fetch's duration. ``loadBaseSourceLines``
    // coalesces, so a click mid-fetch just awaits the same load and then
    // expands.
    const loading = baseSource.status === 'loading';
    const label = countNoun(gap.count, 'hidden line');
    return (
      <Decoration
        key={gap.key}
        className="diff-context-expander"
        contentClassName="diff-context-expander-cell"
      >
        <div className="diff-context-expander-inner">
          <button
            type="button"
            className="diff-context-expander-btn"
            onClick={(event) => onExpandGap(event, gap, 'above')}
            aria-busy={loading}
            aria-label={`Show hidden lines above (${label})`}
            title="Show lines from the top of this hidden block. Shift-click shows all."
          >
            ↑
          </button>
          <span className="diff-context-expander-label">{label}</span>
          <button
            type="button"
            className="diff-context-expander-btn"
            onClick={(event) => onExpandGap(event, gap, 'below')}
            aria-busy={loading}
            aria-label={`Show hidden lines below (${label})`}
            title="Show lines from the bottom of this hidden block. Shift-click shows all."
          >
            ↓
          </button>
        </div>
      </Decoration>
    );
  }

  function renderDiffChildren(hunks) {
    const sourceLineCount = baseSource.lines ? baseSource.lines.length : 0;
    const items = buildDiffRenderItems(hunks, sourceLineCount);
    return items.map((item) => {
      if (item.type === 'gap') { return renderGapDecoration(item); }
      return <Hunk key={item.key} hunk={item.hunk} />;
    });
  }

  const diffBody = expanded ? (
    <Diff
      viewType="unified"
      diffType={file.type}
      hunks={renderedHunks}
      tokens={tokens}
      widgets={widgets}
      gutterEvents={gutterEvents}
    >
      {(hunks) => renderDiffChildren(hunks)}
    </Diff>
  ) : null;
  // The standalone "+ Add file-level comment" entry button and its
  // empty-state hint paragraph were removed on request — the diff
  // view no longer offers a file-level-comment entry point. Inline
  // gutter comments and replies to existing review threads (which
  // still set ``activeLine === -1`` via a thread's Reply) keep
  // working through ``fileLevelForm`` below.
  const fileLevelForm = fileFormOpen ? (
    <CommentForm
      placeholder={fileFormReplyMode ? 'Add a reply…' : 'Add a file-level comment…'}
      onSubmit={(body) => onSubmit(-1, body, fileFormReplyMode ? replyTo : '')}
      onCancel={
        activeLine === -1
          ? () => { setActiveLine(null); setReplyTo(''); }
          : null
      }
      replyMode={fileFormReplyMode}
      draftKey={commentDraftKey(taskId, repoId, path, 'file', fileFormReplyMode && replyTo)}
    />
  ) : null;
  const commentsLoadingMessage = commentsLoading && comments.length === 0 ? (
    <p className="diff-file-comments-empty">Loading comments…</p>
  ) : null;
  const commentsErrorMessage = !commentsLoading && commentsError ? (
    <p className="diff-file-comments-empty error">{commentsError}</p>
  ) : null;
  const commentThreads = !commentsError && fileThreads.length > 0 ? fileThreads.map((thread) => (
    <CommentThread
      key={thread.root.id}
      thread={thread}
      onResolve={onResolve}
      onReopen={onReopen}
      onDelete={onDelete}
      onMarkAddressed={onMarkAddressed}
      onRetry={onRetry}
      onEdit={onEdit}
      onReply={(rootId) => {
        setActiveLine(-1);
        setReplyTo(rootId);
      }}
    />
  )) : null;
  const commentsPanel = (
    commentsLoadingMessage
    || commentsErrorMessage
    || commentThreads
    || fileLevelForm
  ) ? (
    <div className="diff-file-comments">
      {commentsLoadingMessage}
      {commentsErrorMessage}
      {commentThreads}
      {fileLevelForm}
    </div>
  ) : null;
  const bodyContent = diffBody || commentsPanel ? (
    <div className="diff-file-body">
      {diffBody}
      {commentsPanel}
    </div>
  ) : null;
  const pathSegments = renderPathSegments(path);
  const focusPathButton = typeof onFocusInTree === 'function' ? (
    <button
      type="button"
      className="diff-file-path diff-file-path-button"
      onClick={() => onFocusInTree({ repoId, relativePath: path })}
      title="Show this file in the file tree"
    >
      {pathSegments}
    </button>
  ) : (
    <span className="diff-file-path">{pathSegments}</span>
  );
  const showInTreeDisabled = typeof onFocusInTree !== 'function';
  const placeInChatDisabled = typeof onAddToChat !== 'function';
  const pathContextMenu = pathMenu ? (
    <div
      ref={pathMenuRef}
      className="diff-file-context-menu"
      style={pathMenuStyle}
      onPointerDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
      role="menu"
    >
      <button
        type="button"
        className="diff-file-context-menu-item"
        onClick={showPathInTree}
        disabled={showInTreeDisabled}
        role="menuitem"
      >
        Show in tree
      </button>
      <button
        type="button"
        className="diff-file-context-menu-item"
        onClick={placePathInChat}
        disabled={placeInChatDisabled}
        role="menuitem"
      >
        Place in chat
      </button>
      <button
        type="button"
        className="diff-file-context-menu-item"
        onClick={copyHeaderRelativePath}
        role="menuitem"
      >
        Copy relative path
      </button>
    </div>
  ) : null;

  return (
    <section
      className={`diff-file ${expanded ? 'is-expanded' : 'is-collapsed'}`}
      style={{ '--diff-gutter-col-width': gutterColWidth }}
      onContextMenu={openPathMenu}
      title="Click a line gutter to add an inline comment · right-click for file actions"
    >
      <StickyHeader as="header" className="diff-file-header">
        {collapseToggle}
        <DiffKindIcon kind={file.type} />
        {conflictedBadge}
        {focusPathButton}
        {typeof onOpenAsFile === 'function' && (
          <button
            type="button"
            className="diff-file-open-as-file is-icon tooltip-below"
            onClick={onOpenAsFile}
            data-tooltip="View file (no diff)"
            aria-label="View file (no diff)"
          >
            <Icon name="file" />
          </button>
        )}
      </StickyHeader>
      {pathContextMenu}
      {bodyContent}
    </section>
  );
}

export default memo(DiffFileWithComments);
