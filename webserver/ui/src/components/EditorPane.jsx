import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import Editor from '@monaco-editor/react';
import {
  createTaskComment,
  deleteTaskComment,
  editTaskComment,
  fetchFileContent,
  fetchTaskComments,
  markTaskCommentAddressed,
  reopenTaskComment,
  resolveTaskComment,
} from '../api.js';
import {
  CommentForm,
  CommentThread,
  buildThreads,
  katoTriggeredMessage,
} from './CommentWidgets.jsx';
import Icon from './Icon.jsx';
import { useChatComposer } from '../contexts/ChatComposerContext.jsx';
import { toast } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { commentDraftKey } from '../utils/composerDraft.js';
import { copyRepoRelativePath } from '../utils/clipboard.js';
import { useDismissOnOutsidePointerOrEscape } from '../hooks/useDismissOnOutsidePointerOrEscape.js';
import { useMonacoViewZone } from '../hooks/useMonacoViewZone.js';

/**
 * Read-only Monaco editor that lives in the middle column.
 *
 * Driven by a single ``openFile`` prop — when it changes, the pane
 * refetches the file via /api/sessions/<task_id>/file and renders
 * it with VS-Code dark theme + syntax highlighting.
 *
 * Comments: the operator can right-click → "Add comment", or hover
 * a line and click the ``+`` glyph in the gutter, to attach a
 * review-style comment to that line. Comments are persisted via the
 * SAME ``/api/sessions/<task>/comments`` endpoints the Changes tab
 * uses (no parallel storage). Once submitted, kato auto-runs against
 * the comment when its turn ends (queued) or immediately if idle —
 * the ``kato_status`` badge above each bubble reflects that lifecycle.
 *
 * ``openFile`` shape:
 *   ``{ taskId, absolutePath, relativePath, repoId }``.
 */
export default function EditorPane({
  openFile,
  onCommentSpawned,
  onViewStateChange,
  // Flip the centre column back to the diff view — mirror of the
  // "view file" icon in the diff header. Wired by App.handleOpenFile
  // (view: 'diff').
  onOpenFile,
}) {
  const [state, setState] = useState({
    loading: false,
    error: '',
    content: '',
    binary: false,
    tooLarge: false,
  });
  const [comments, setComments] = useState([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentsError, setCommentsError] = useState('');
  // ``activeLine`` is the line number where the inline composer is
  // currently open. ``null`` means no composer.
  const [activeLine, setActiveLine] = useState(null);
  // Reply state inside the comment list panel. Map of {threadId: bool}.
  const [replyTo, setReplyTo] = useState('');

  const { appendToInput } = useChatComposer();
  const taskId = openFile?.taskId || '';
  const repoId = openFile?.repoId || '';
  const filePath = openFile?.relativePath || openFile?.absolutePath || '';

  // Right-click menu on the file-path HEADER. The Monaco editor body
  // already carries the same "Copy relative path" action in its native
  // right-click menu (registered in handleEditorMount); this adds the
  // identical action when the operator right-clicks the path label in
  // the header strip instead of the code. Mirrors the Files tree +
  // diff-file header menus — same helper, same copied ``repo:path``.
  const [pathMenu, setPathMenu] = useState(null);
  function openPathMenu(event) {
    event.preventDefault();
    event.stopPropagation();
    if (!filePath) { return; }
    setPathMenu({ x: event.clientX, y: event.clientY });
  }
  function closePathMenu() { setPathMenu(null); }
  async function copyHeaderRelativePath() {
    closePathMenu();
    await copyRepoRelativePath(repoId, filePath);
  }
  useDismissOnOutsidePointerOrEscape(pathMenu, closePathMenu);

  // Refs so Monaco actions (registered once) always read latest
  // values without closing over stale state.
  const openFileRef = useRef(openFile);
  const appendRef = useRef(appendToInput);
  const setActiveLineRef = useRef(setActiveLine);
  const onViewStateChangeRef = useRef(onViewStateChange);
  useEffect(() => { openFileRef.current = openFile; }, [openFile]);
  useEffect(() => { appendRef.current = appendToInput; }, [appendToInput]);
  useEffect(() => { onViewStateChangeRef.current = onViewStateChange; }, [onViewStateChange]);
  useEffect(() => { setActiveLineRef.current = setActiveLine; }, []);

  // Monaco editor instance + decoration ids for hover line +
  // glyph-margin ``+``. Stored as refs because the hover effect is
  // event-driven (mouse move) and shouldn't trigger React re-renders.
  const editorRef = useRef(null);
  const hoverDecorationsRef = useRef([]);
  // Inline new-comment composer is rendered INTO a Monaco "view
  // zone" anchored at the clicked line (GitHub / VS Code style) —
  // not at the bottom of the pane. ``zoneNode`` is the DOM node
  // Monaco owns; we portal the React composer into it. ``zoneRef``
  // holds the live IViewZone so its height can be reflowed as the
  // textarea grows. File-level comments (line === -1) have no
  // editor line to anchor to, so they fall back to a bottom block.
  // Two Monaco view zones. The inline composer is anchored at the
  // clicked line; the comments-at-end zone is anchored after the last
  // line so the discussion reads as a footer to the code and Monaco's
  // own scrollbar is the single scroll surface. Both share the
  // useMonacoViewZone hook (sizing, sticky pinning, reflow on content
  // change) — see the hook's docstring for the rationale.

  function reportEditorViewState() {
    const editor = editorRef.current;
    const notify = onViewStateChangeRef.current;
    if (!editor || typeof notify !== 'function') { return; }
    const saveViewState = editor.saveViewState;
    if (typeof saveViewState !== 'function') { return; }
    notify({ editorViewState: saveViewState.call(editor) });
  }

  // Comments scoped to the currently-open file. The /comments
  // endpoint returns the whole task's set (across repos + files);
  // filtering client-side keeps the request count low (one fetch
  // per file open vs. one per line interaction).
  const fileComments = useMemo(
    () => comments.filter((c) => String(c.file_path || '') === filePath),
    [comments, filePath],
  );
  // Hooks must be top-of-component (no conditional returns above
  // them), so build the threads list here even though it's only
  // rendered in the happy-path body below.
  const threads = useMemo(() => buildThreads(fileComments), [fileComments]);

  // Re-fetch the task's comment list. Used after every mutation so
  // the chip strip + bubbles reflect the new state without a poll.
  const refreshComments = useCallback(async () => {
    if (!taskId) {
      setComments([]); setCommentsError(''); return;
    }
    setCommentsLoading(true);
    try {
      const result = await fetchTaskComments(taskId, repoId);
      if (result.ok) {
        setComments(Array.isArray(result.body?.comments) ? result.body.comments : []);
        setCommentsError('');
      } else {
        setCommentsError(apiErrorMessage(result, 'failed to load comments'));
      }
    } finally {
      setCommentsLoading(false);
    }
  }, [taskId, repoId]);

  useEffect(() => { refreshComments(); }, [refreshComments]);

  async function onCommentSubmit(line, body, parentId = '') {
    if (!body.trim()) { return false; }
    const result = await createTaskComment(taskId, {
      repo: repoId,
      file_path: filePath,
      line,
      body: body.trim(),
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
      title: 'Comment added',
      message: parentId
        ? '✓ reply posted (kato runs only on top-of-thread comments)'
        : katoTriggeredMessage(triggered),
      durationMs: 5000,
    });
    setActiveLine(null);
    setReplyTo('');
    refreshComments();
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
    return true;
  }

  async function onResolve(comment) {
    const result = await resolveTaskComment(taskId, comment.id);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Resolve failed', fallback: 'resolve failed', durationMs: 5000,
      });
      return;
    }
    refreshComments();
  }
  async function onReopen(comment) {
    const result = await reopenTaskComment(taskId, comment.id);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Reopen failed', fallback: 'reopen failed', durationMs: 5000,
      });
      return;
    }
    const triggered = result.body?.triggered_immediately;
    toast.show({
      kind: 'success',
      title: 'Comment reopened',
      message: katoTriggeredMessage(triggered),
      durationMs: 5000,
    });
    refreshComments();
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
  }
  async function onDelete(comment) {
    const result = await deleteTaskComment(taskId, comment.id);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Delete failed', fallback: 'delete failed', durationMs: 5000,
      });
      return;
    }
    refreshComments();
  }
  async function onEdit(commentId, { body, katoStatus } = {}) {
    const result = await editTaskComment(taskId, commentId, { body, katoStatus });
    // Check HTTP error AND envelope-level ``{ok: false}`` (validation
    // rejects). See the matching handler in DiffFileWithComments for
    // the rationale.
    if (!result.ok || result.body?.ok === false) {
      toast.errorFromResult(result, {
        title: 'Edit failed', fallback: 'edit failed', durationMs: 5000,
      });
      return false;
    }
    refreshComments();
    return true;
  }
  async function onMarkAddressed(comment) {
    const result = await markTaskCommentAddressed(taskId, comment.id, '');
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Mark addressed failed',
        fallback: 'mark addressed failed',
        durationMs: 5000,
      });
      return;
    }
    toast.show({
      kind: 'success',
      title: 'Marked addressed',
      message: '✓ "Kato addressed this review comment" reply posted',
      durationMs: 5000,
    });
    refreshComments();
  }

  function handleEditorMount(editor, monaco) {
    editorRef.current = editor;
    if (openFileRef.current?.editorViewState
        && typeof editor.restoreViewState === 'function') {
      editor.restoreViewState(openFileRef.current.editorViewState);
    }
    if (typeof editor.onDidScrollChange === 'function') {
      editor.onDidScrollChange(reportEditorViewState);
    }
    if (typeof editor.onDidChangeCursorPosition === 'function') {
      editor.onDidChangeCursorPosition(reportEditorViewState);
    }
    // Keep the inline-composer host the same width as the editor's
    // visible content area whenever Monaco re-lays-out (panel resize,
    // toggling the minimap, etc.). The view zone otherwise spans the
    // full horizontal scroll range and the composer overflows the
    // panel — see the zone-creation effect for the matching logic.
    if (typeof editor.onDidLayoutChange === 'function') {
      editor.onDidLayoutChange((info) => {
        if (!info?.contentWidth) { return; }
        const w = `${info.contentWidth}px`;
        for (const host of [zoneNodeRef.current, commentsZoneNodeRef.current]) {
          if (!host) { continue; }
          host.style.width = w;
          host.style.maxWidth = w;
        }
      });
    }

    // Right-click → "Add to chat" pushes the selected line range
    // into the chat composer as ``file:N-M``.
    editor.addAction({
      id: 'kato.addSelectionToChat',
      label: 'Add to chat',
      contextMenuGroupId: 'kato',
      contextMenuOrder: 0,
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyA],
      run: (ed) => {
        const file = openFileRef.current;
        const append = appendRef.current;
        if (!file || typeof append !== 'function') { return; }
        const selection = ed.getSelection();
        const path = file.relativePath || file.absolutePath || '';
        if (!path) { return; }
        const repoPrefix = file.repoId ? `${file.repoId}:` : '';
        let reference;
        if (!selection || selection.isEmpty()) {
          const pos = ed.getPosition();
          reference = pos ? `${repoPrefix}${path}:${pos.lineNumber}` : `${repoPrefix}${path}`;
        } else if (selection.startLineNumber === selection.endLineNumber) {
          reference = `${repoPrefix}${path}:${selection.startLineNumber}`;
        } else {
          reference = `${repoPrefix}${path}:${selection.startLineNumber}-${selection.endLineNumber}`;
        }
        append(`${reference}\n`);
      },
    });

    // Right-click → "Add comment" opens the inline composer.
    editor.addAction({
      id: 'kato.addCommentOnSelection',
      label: 'Add comment',
      contextMenuGroupId: 'kato',
      contextMenuOrder: 1,
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyC],
      run: (ed) => {
        const pos = ed.getPosition();
        setActiveLineRef.current(pos ? pos.lineNumber : 1);
      },
    });

    // Right-click → "Copy relative path" copies the repo-relative
    // path (``repo:path``) to the clipboard — the SAME helper + format
    // the Files tree and the diff-file header already use, so a path
    // copied from any of the three surfaces is identical. No keybinding:
    // Cmd/Ctrl+Shift+P is Monaco's own command palette.
    editor.addAction({
      id: 'kato.copyRelativePath',
      label: 'Copy relative path',
      contextMenuGroupId: 'kato',
      contextMenuOrder: 2,
      run: () => {
        const file = openFileRef.current;
        if (!file) { return; }
        const path = file.relativePath || file.absolutePath || '';
        if (!path) { return; }
        copyRepoRelativePath(file.repoId || '', path);
      },
    });

    // Hover: highlight the active line + show a ``+`` glyph in the
    // gutter so the operator can click to add a comment on that
    // line. Decorations are managed via deltaDecorations so we
    // don't leak references across hover transitions.
    editor.onMouseMove((e) => {
      const line = e?.target?.position?.lineNumber || 0;
      if (!line) {
        hoverDecorationsRef.current = editor.deltaDecorations(
          hoverDecorationsRef.current, [],
        );
        return;
      }
      hoverDecorationsRef.current = editor.deltaDecorations(
        hoverDecorationsRef.current,
        [
          {
            range: new monaco.Range(line, 1, line, 1),
            options: {
              isWholeLine: true,
              className: 'kato-line-hover',
              glyphMarginClassName: 'kato-add-comment-glyph',
              glyphMarginHoverMessage: { value: 'Add comment on this line' },
            },
          },
        ],
      );
    });
    editor.onMouseLeave?.(() => {
      hoverDecorationsRef.current = editor.deltaDecorations(
        hoverDecorationsRef.current, [],
      );
    });
    // Click on the gutter glyph → open the composer for that line.
    editor.onMouseDown((e) => {
      const monacoTypes = monaco.editor.MouseTargetType;
      const t = e?.target?.type;
      const isGlyph = t === monacoTypes.GUTTER_GLYPH_MARGIN;
      if (!isGlyph) { return; }
      const line = e?.target?.position?.lineNumber || 0;
      if (line) {
        setActiveLineRef.current(line);
      }
    });
  }

  // Switching files must not leave a stale inline composer (or its
  // view zone) anchored on the previous file's line.
  useEffect(() => {
    setActiveLine(null);
    setReplyTo('');
  }, [filePath]);

  useEffect(() => {
    const editor = editorRef.current;
    const viewState = openFile?.editorViewState;
    if (!editor || !viewState || typeof editor.restoreViewState !== 'function') {
      return;
    }
    editor.restoreViewState(viewState);
  }, [openFile?.absolutePath, openFile?.editorViewState]);

  // Inline composer zone (anchored at the clicked line).
  const { zoneNode, zoneNodeRef } = useMonacoViewZone({
    editorRef,
    enabled: activeLine !== null && activeLine >= 1,
    afterLine: activeLine !== null && activeLine >= 1 ? activeLine : 0,
    seedHeight: 200,
    minHeight: 120,
  });
  useEffect(() => {
    if (activeLine === null || activeLine < 1) { return; }
    editorRef.current?.revealLineInCenterIfOutsideViewport?.(activeLine);
  }, [activeLine]);

  // Comments-at-end zone (anchored after the last line). The anchor
  // line is derived from ``state.content`` — counting newlines is
  // equivalent to ``editor.getModel().getLineCount()`` for the model
  // we just handed Monaco, but doesn't depend on the editor instance
  // being ready before this render.
  const lastLine = useMemo(() => {
    const text = state.content || '';
    if (!text) { return 1; }
    let count = 1;
    for (let i = 0; i < text.length; i += 1) {
      if (text.charCodeAt(i) === 10) { count += 1; }
    }
    return count;
  }, [state.content]);
  const wantCommentsZone = threads.length > 0 || activeLine === -1;
  const {
    zoneNode: commentsZoneNode,
    zoneNodeRef: commentsZoneNodeRef,
  } = useMonacoViewZone({
    editorRef,
    enabled: wantCommentsZone,
    afterLine: lastLine,
    seedHeight: 80,
    minHeight: 60,
    extraClassName: 'editor-pane-comments-zone-host',
  });

  // Scroll the editor to a line when the operator clicks a chip.
  function jumpToLine(line) {
    const editor = editorRef.current;
    if (!editor || !line) { return; }
    editor.revealLineInCenter(line);
    editor.setPosition({ lineNumber: line, column: 1 });
    editor.focus();
  }

  useEffect(() => {
    if (!openFile || !openFile.taskId || !openFile.absolutePath) {
      setState({
        loading: false, error: '', content: '',
        binary: false, tooLarge: false,
      });
      return undefined;
    }
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    fetchFileContent(openFile.taskId, openFile.absolutePath)
      .then((body) => {
        if (cancelled) { return; }
        setState({
          loading: false,
          error: '',
          content: body?.content || '',
          binary: !!body?.binary,
          tooLarge: !!body?.too_large,
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState({
          loading: false,
          error: String(err && err.message ? err.message : err) || 'failed to load file',
          content: '', binary: false, tooLarge: false,
        });
      });
    return () => { cancelled = true; };
  }, [openFile?.taskId, openFile?.absolutePath]);

  if (!openFile || !openFile.absolutePath) {
    return (
      <section id="editor-pane">
        <div className="editor-pane-empty">
          <p>Pick a file from the left tree to preview it here.</p>
          <p className="editor-pane-empty-hint">
            Files open read-only — kato is the one editing the
            workspace; this view is for seeing what the agent does.
          </p>
        </div>
      </section>
    );
  }

  const language = languageForPath(openFile.relativePath || openFile.absolutePath);

  let body;
  if (state.loading) {
    body = <div className="editor-pane-message">Loading…</div>;
  } else if (state.tooLarge) {
    body = (
      <div className="editor-pane-message">
        File is too large for the in-browser preview (max 1 MB).
      </div>
    );
  } else if (state.binary) {
    body = (
      <div className="editor-pane-message">
        Binary file — no text preview available.
      </div>
    );
  } else if (state.error) {
    body = (
      <div className="editor-pane-message editor-pane-message-error">
        {state.error}
      </div>
    );
  } else {
    body = (
      <Editor
        theme="vs-dark"
        language={language}
        value={state.content}
        path={openFile.absolutePath}
        onMount={handleEditorMount}
        options={{
          readOnly: true,
          domReadOnly: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 12,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          renderLineHighlight: 'none',
          smoothScrolling: true,
          automaticLayout: true,
          padding: { top: 8, bottom: 8 },
          guides: { indentation: true, bracketPairs: true },
          glyphMargin: true,
        }}
      />
    );
  }

  return (
    <section id="editor-pane">
      <header className="editor-pane-header" onContextMenu={openPathMenu}>
        <span className="editor-pane-path" title={openFile.absolutePath}>
          {openFile.relativePath || openFile.absolutePath}
        </span>
        <span className="editor-pane-readonly-pill">read-only</span>
        {typeof onOpenFile === 'function' && (
          <button
            type="button"
            className="diff-file-open-as-file is-icon tooltip-below"
            onClick={() => onOpenFile({
              absolutePath: openFile.absolutePath,
              relativePath: openFile.relativePath,
              repoId: openFile.repoId,
              view: 'diff',
            })}
            data-tooltip="View diff"
            aria-label="View diff"
          >
            <Icon name="diff" />
          </button>
        )}
      </header>
      {pathMenu && (
        <div
          className="diff-file-context-menu"
          style={{ left: pathMenu.x, top: pathMenu.y }}
          onPointerDown={(event) => event.stopPropagation()}
          onContextMenu={(event) => event.preventDefault()}
          role="menu"
        >
          <button
            type="button"
            className="diff-file-context-menu-item"
            onClick={copyHeaderRelativePath}
            role="menuitem"
          >
            Copy relative path
          </button>
        </div>
      )}
      <div className="editor-pane-body">
        {body}
      </div>
      {/* Line comment: rendered INLINE at the clicked line via a
          Monaco view zone (GitHub / VS Code style), portaled into
          the zone's DOM node. */}
      {activeLine !== null && activeLine >= 1 && zoneNode && createPortal(
        // ``stopPropagation`` on the pointer-down chain keeps Monaco
        // from re-grabbing focus the instant the operator clicks back
        // into the textarea. Monaco also sees ``suppressMouseDown`` on
        // the zone itself (see the zone-creation effect), but the React
        // synthetic-event bubble would still reach Monaco's host —
        // hence the belt-and-braces stopPropagation here.
        <div
          className="editor-pane-composer-wrap editor-pane-composer-inline"
          onMouseDown={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <header className="editor-pane-composer-head">
            Add comment on {openFile.relativePath || openFile.absolutePath}:{activeLine}
          </header>
          <CommentForm
            placeholder="What should kato do about this line?"
            onSubmit={(b) => onCommentSubmit(activeLine, b)}
            onCancel={() => setActiveLine(null)}
            draftKey={commentDraftKey(taskId, repoId, filePath, `line:${activeLine}`)}
          />
        </div>,
        zoneNode,
      )}
      {/* All file-level + per-line discussion lives in a single Monaco
          view zone anchored AFTER the last line — Monaco's scrollbar
          is the only scroller for the pane. Same ``CommentThread`` the
          diff uses. */}
      {commentsZoneNode && createPortal(
        <div
          className="editor-pane-comments-wrap"
          onMouseDown={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {commentsError && (
            <p className="editor-pane-message editor-pane-message-error">
              {commentsError}
            </p>
          )}
          {threads.map(({ root, replies }) => (
            <div key={root.id} className="editor-pane-comment-thread">
              <div className="editor-pane-comment-anchor">
                {root.line >= 0 ? (
                  <button
                    type="button"
                    className="editor-pane-comment-jump"
                    onClick={() => jumpToLine(root.line)}
                    title="Jump to this line in the editor"
                  >
                    line {root.line}
                  </button>
                ) : (
                  <span className="editor-pane-comment-jump is-file">file-level</span>
                )}
              </div>
              <CommentThread
                thread={{ root, replies }}
                onResolve={(id) => onResolve({ id })}
                onReopen={(id) => onReopen({ id })}
                onDelete={(id) => onDelete({ id })}
                onMarkAddressed={(id) => onMarkAddressed({ id })}
                onEdit={onEdit}
                onReply={(rootId) => setReplyTo(rootId)}
              />
              {replyTo === root.id && (
                <CommentForm
                  placeholder="Reply…"
                  replyMode
                  onSubmit={(b) => onCommentSubmit(root.line, b, root.id)}
                  onCancel={() => setReplyTo('')}
                  draftKey={commentDraftKey(taskId, repoId, filePath, `reply:${root.id}`)}
                />
              )}
            </div>
          ))}
          {activeLine === -1 && (
            <div className="editor-pane-comment-thread">
              <div className="editor-pane-comment-anchor">
                <span className="editor-pane-comment-jump is-file">file-level</span>
              </div>
              <CommentForm
                placeholder="What should kato do about this file?"
                onSubmit={(b) => onCommentSubmit(activeLine, b)}
                onCancel={() => setActiveLine(null)}
                draftKey={commentDraftKey(taskId, repoId, filePath, 'file')}
              />
            </div>
          )}
        </div>,
        commentsZoneNode,
      )}
    </section>
  );
}


// Map a file path to a Monaco language id. Monaco ships with a
// long built-in list; we only translate uncommon extensions.
function languageForPath(path) {
  if (!path) { return 'plaintext'; }
  const lower = String(path).toLowerCase();
  if (lower.endsWith('.tsx')) { return 'typescript'; }
  if (lower.endsWith('.jsx')) { return 'javascript'; }
  if (lower.endsWith('.ts')) { return 'typescript'; }
  if (lower.endsWith('.js') || lower.endsWith('.mjs') || lower.endsWith('.cjs')) {
    return 'javascript';
  }
  if (lower.endsWith('.py')) { return 'python'; }
  if (lower.endsWith('.scss')) { return 'scss'; }
  if (lower.endsWith('.less')) { return 'less'; }
  if (lower.endsWith('.css')) { return 'css'; }
  if (lower.endsWith('.html') || lower.endsWith('.htm')) { return 'html'; }
  if (lower.endsWith('.json')) { return 'json'; }
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) { return 'markdown'; }
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) { return 'yaml'; }
  if (lower.endsWith('.sh') || lower.endsWith('.bash')) { return 'shell'; }
  if (lower.endsWith('.go')) { return 'go'; }
  if (lower.endsWith('.rs')) { return 'rust'; }
  if (lower.endsWith('.java')) { return 'java'; }
  if (lower.endsWith('.rb')) { return 'ruby'; }
  if (lower.endsWith('.xml') || lower.endsWith('.svg')) { return 'xml'; }
  if (lower.endsWith('.sql')) { return 'sql'; }
  if (lower.endsWith('.dockerfile') || lower.endsWith('/dockerfile')) {
    return 'dockerfile';
  }
  return 'plaintext';
}
