import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import Editor from '@monaco-editor/react';
import { fetchFileContent } from '../api.js';
import { useFindWidgetEscape } from '../hooks/useFindWidgetEscape.js';
import { readCachedFileContent, writeCachedFileContent } from '../utils/fileContentCache.js';
import {
  useTaskComments,
  createComment,
  resolveComment,
  reopenComment,
  removeComment,
  editComment,
  markCommentAddressed,
} from '../stores/taskCache/index.js';
import {
  CommentForm,
  CommentThread,
  buildThreads,
  katoTriggeredMessage,
} from './CommentWidgets.jsx';
import { useChatComposer } from '../contexts/ChatComposerContext.jsx';
import { toast } from '../stores/toastStore.js';
import { commentDraftKey } from '../utils/composerDraft.js';
import { copyFileName, copyRepoRelativePath } from '../utils/clipboard.js';
import { useMonacoViewZone } from '../hooks/useMonacoViewZone.js';
import { markdownViewFor } from '../utils/markdownView.js';
import MarkdownContent from './MarkdownContent.jsx';

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
}) {
  const [state, setState] = useState({
    loading: false,
    error: '',
    content: '',
    binary: false,
    tooLarge: false,
  });
  // Comments come from the shared ``commentStore`` (single source of
  // truth) — the same always-current list the diff pane's threads and
  // the file-tree badges read, so a mutation here shows up there in the
  // same tick and vice-versa. No per-pane fetch/refresh loop any more.
  const { comments, loading: commentsLoading, error: commentsError } =
    useTaskComments(openFile?.taskId || '');
  // ``activeLine`` is the line number where the inline composer is
  // currently open. ``null`` means no composer.
  const [activeLine, setActiveLine] = useState(null);
  // Reply state inside the comment list panel. Map of {threadId: bool}.
  const [replyTo, setReplyTo] = useState('');

  const { appendToInput } = useChatComposer();
  const taskId = openFile?.taskId || '';
  const repoId = openFile?.repoId || '';
  const filePath = openFile?.relativePath || openFile?.absolutePath || '';

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

  // Comments scoped to the currently-open file. The store holds the
  // whole task's set (across repos + files); filtering client-side by
  // file path AND repo mirrors the old per-repo fetch (a same-named file
  // in another repo must not leak its threads onto this one). An empty
  // repoId means "any repo" — same as the endpoint's no-repo query.
  const fileComments = useMemo(() => {
    const repoKey = repoId.toLowerCase();
    return comments.filter((c) => (
      String(c.file_path || '') === filePath
      && (!repoKey || String(c.repo_id || '').toLowerCase() === repoKey)
    ));
  }, [comments, filePath, repoId]);
  // Hooks must be top-of-component (no conditional returns above
  // them), so build the threads list here even though it's only
  // rendered in the happy-path body below.
  const threads = useMemo(() => buildThreads(fileComments), [fileComments]);

  // A per-line composer is portaled into a Monaco view zone, which the
  // preview doesn't have — leaving ``activeLine`` set while previewing would
  // strand a half-typed comment in a zone nobody can see. File-level (-1)
  // renders inline in both views, so it survives the flip.
  const previewing = markdownViewFor(openFile) === 'preview';
  useEffect(() => {
    if (previewing) { setActiveLine((cur) => (cur !== null && cur >= 1 ? null : cur)); }
  }, [previewing]);

  async function onCommentSubmit(line, body, parentId = '') {
    if (!body.trim()) { return false; }
    const result = await createComment(taskId, {
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
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
    return true;
  }

  async function onResolve(comment) {
    const result = await resolveComment(taskId, comment.id);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Resolve failed', fallback: 'resolve failed', durationMs: 5000,
      });
    }
  }
  async function onReopen(comment) {
    const result = await reopenComment(taskId, comment.id);
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
    if (triggered && typeof onCommentSpawned === 'function') {
      onCommentSpawned();
    }
  }
  async function onDelete(comment) {
    const result = await removeComment(taskId, comment.id);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Delete failed', fallback: 'delete failed', durationMs: 5000,
      });
    }
  }
  async function onEdit(commentId, { body, katoStatus } = {}) {
    const result = await editComment(taskId, commentId, { body, katoStatus });
    // Check HTTP error AND envelope-level ``{ok: false}`` (validation
    // rejects). See the matching handler in DiffFileWithComments for
    // the rationale.
    if (!result.ok || result.body?.ok === false) {
      toast.errorFromResult(result, {
        title: 'Edit failed', fallback: 'edit failed', durationMs: 5000,
      });
      return false;
    }
    return true;
  }
  async function onMarkAddressed(comment) {
    const result = await markCommentAddressed(taskId, comment.id, '');
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

    // Moved here from the (removed) path-header context menu, which was
    // the only surface outside the Files tree offering it.
    editor.addAction({
      id: 'kato.copyFileName',
      label: 'Copy file name',
      contextMenuGroupId: 'kato',
      contextMenuOrder: 3,
      run: () => {
        const file = openFileRef.current;
        if (!file) { return; }
        const path = file.relativePath || file.absolutePath || '';
        if (!path) { return; }
        copyFileName(path);
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

  // Ctrl+F's find bar must always be dismissable with Escape — Monaco's own
  // binding needs editor focus, which leaves the bar pinned over the file the
  // moment focus is anywhere else. See the hook for the full mechanism.
  useFindWidgetEscape(editorRef);

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
    const { taskId, absolutePath } = openFile;
    let cancelled = false;
    // Show a cached copy INSTANTLY (no loading spinner) if we have one
    // for this exact file — e.g. re-opening a tab after switching tasks
    // away and back. This is only a display optimization, never trusted
    // as correct on its own: the fetch below still always runs and
    // sends the cached mtime back for the SERVER to verify against its
    // own stat() — a background branch sync, merge, or a direct edit
    // outside kato can change the file with no SSE event the client
    // would ever see, so only the server confirming the mtime still
    // matches can justify skipping the full re-read.
    const cached = readCachedFileContent(taskId, absolutePath);
    if (cached) {
      setState({
        loading: false, error: '',
        content: cached.content, binary: cached.binary, tooLarge: cached.tooLarge,
      });
    } else {
      setState((prev) => ({ ...prev, loading: true, error: '' }));
    }
    fetchFileContent(taskId, absolutePath, cached?.mtime || '')
      .then((body) => {
        if (cancelled) { return; }
        if (body?.unchanged) {
          // Server confirmed the mtime we sent still matches — the
          // cached copy already showing is correct, nothing to update.
          return;
        }
        const next = {
          content: body?.content || '',
          binary: !!body?.binary,
          tooLarge: !!body?.too_large,
        };
        setState({ loading: false, error: '', ...next });
        // too_large responses carry no content and no reliable mtime
        // to key a future skip-fetch on — never cache those.
        if (!next.tooLarge && body?.mtime) {
          writeCachedFileContent(taskId, absolutePath, { ...next, mtime: body.mtime });
        }
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
  // Rendered markdown instead of Monaco. Task-folder documents (plan.md,
  // pr_description.md) default here — they are prose written for the
  // operator, and raw ``##``/``|---|`` is not how you read a plan. The
  // switch lives on the file tab, beside the diff toggle.
  const showMarkdownPreview = previewing;

  // Built once and spread at BOTH render sites (Monaco's view zone and the
  // markdown preview) — two hand-copied prop lists is how one of the views
  // ends up silently missing a handler.
  const commentsProps = {
    commentsError, threads, activeLine, replyTo, setReplyTo, jumpToLine,
    onResolve, onReopen, onDelete, onMarkAddressed, onEdit, onCommentSubmit,
    setActiveLine, taskId, repoId, filePath,
  };

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
  } else if (showMarkdownPreview) {
    body = (
      <div className="editor-pane-markdown">
        <MarkdownContent>{state.content}</MarkdownContent>
        {/* Same threads the source view shows in its trailing Monaco view
            zone — without this a comment on a previewed file would look
            like it had been deleted. Line-anchored ones can't jump (there
            are no line numbers in rendered prose), so their anchor button
            is inert here; the operator flips to source to act on it. */}
        <EditorComments {...commentsProps} />
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

  // No path/read-only header strip. Every one of its affordances is
  // reachable without spending a full row on it: the file tab already
  // shows ``repoId/relativePath`` on hover, carries the diff ⇄ file
  // toggle next to its close button, and the editor is ALWAYS read-only
  // (``readOnly: true`` below is not conditional), so a permanent
  // "read-only" pill told the operator nothing they could act on. The
  // copy actions live in Monaco's own right-click menu and the Files
  // tree. See the ``kato.copy*`` actions in handleEditorMount.
  return (
    <section id="editor-pane">
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
          diff uses. The markdown preview renders the SAME component
          inline instead (no Monaco, so no view zone to portal into). */}
      {!showMarkdownPreview && commentsZoneNode && createPortal(
        <EditorComments insideEditor {...commentsProps} />,
        commentsZoneNode,
      )}
    </section>
  );
}


// The file's comment threads. Rendered in TWO places and therefore
// extracted: inside Monaco's trailing view zone for the source view, and
// straight after the rendered body in the markdown preview — where there
// is no Monaco at all, so a portal-only version would make every comment
// on a previewed file silently vanish.
function EditorComments({
  insideEditor, commentsError, threads, activeLine, replyTo, setReplyTo,
  jumpToLine, onResolve, onReopen, onDelete, onMarkAddressed, onEdit,
  onCommentSubmit, setActiveLine, taskId, repoId, filePath,
}) {
  return (
    <div
      className="editor-pane-comments-wrap"
      {...(insideEditor ? {
    // Monaco re-grabs focus on mousedown inside its own DOM; the
    // preview is plain DOM and needs no such guard.
    onMouseDown: (e) => e.stopPropagation(),
    onPointerDown: (e) => e.stopPropagation(),
      } : {})}
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
    </div>
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
