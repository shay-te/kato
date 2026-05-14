import { useEffect, useState } from 'react';
import Editor from '@monaco-editor/react';
import { fetchFileContent } from '../api.js';

/**
 * Read-only Monaco editor that lives in the middle column.
 *
 * Driven by a single ``openFile`` prop — when it changes (from a
 * click in the FilesTab), the pane refetches the file via
 * /api/sessions/<task_id>/file and renders it with VS-Code dark
 * theme and language-appropriate syntax highlighting.
 *
 * Why read-only: kato writes files on the operator's behalf; the
 * editor is for *seeing* what the agent produced, not for editing.
 * Letting the operator type into it would create two sources of
 * truth — the in-browser buffer + the actual file on disk — that
 * we'd then have to reconcile. Out of scope for now.
 *
 * ``openFile`` shape: ``{ taskId, absolutePath, relativePath }``.
 * Both paths arrive together so the title can show the relative
 * one (compact) while the fetch uses the absolute one (less
 * ambiguous server-side).
 */
export default function EditorPane({ openFile }) {
  const [state, setState] = useState({
    loading: false,
    error: '',
    content: '',
    binary: false,
    tooLarge: false,
  });

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
        // The server returns 200 with ``too_large: true`` for
        // files past the cap, and 200 with ``binary: true`` for
        // detected binary content — those aren't error states,
        // they're alternate happy paths the editor renders as
        // placeholder messages.
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
        // ``fetchJson`` throws on non-ok responses (404 from a
        // pre-restart kato, 403 from a path that escaped the
        // workspace root, 500 if the file disappeared mid-read).
        // Without this catch the rejection was unhandled and the
        // editor was stuck on its Loading placeholder.
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
        }}
      />
    );
  }

  return (
    <section id="editor-pane">
      <header className="editor-pane-header">
        <span className="editor-pane-path" title={openFile.absolutePath}>
          {openFile.relativePath || openFile.absolutePath}
        </span>
        <span className="editor-pane-readonly-pill">read-only</span>
      </header>
      <div className="editor-pane-body">
        {body}
      </div>
    </section>
  );
}

// Map a file path to a Monaco language id. Monaco ships with a
// long built-in list; we only need to translate uncommon
// extensions. For most cases the default fallback (``text/plain``
// dispatched by Monaco itself when ``language`` is unset) works,
// but providing the language hint gives tighter highlighting.
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
