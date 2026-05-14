// Bundle Monaco locally so the read-only file preview doesn't
// depend on the public CDN that ``@monaco-editor/react`` defaults
// to. Without this:
//   - the editor hangs on "Loading…" forever in offline /
//     locked-down environments (the operator saw exactly this);
//   - first-load is slow even when the CDN works.
//
// Vite's ``?worker`` import turns each Monaco worker source file
// into a real Worker constructor at build time, so the workers
// live in our own bundle, not on jsdelivr.

// Import the whole monaco-editor package (the package's
// ``exports`` map resolves to ``esm/vs/editor/editor.main.js``,
// which preloads every built-in language definition).
// Importing the api.js subpath alone skips the language registry,
// which is what was causing ``@monaco-editor/react`` to fall back
// to its CDN loader (and hang on "Loading…" forever).
import * as monaco from 'monaco-editor';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import CssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import HtmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import TsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';
import { loader } from '@monaco-editor/react';

// MonacoEnvironment is the global hook Monaco reads on every
// language switch to find the worker for that language. The
// fallback (``EditorWorker``) covers everything we didn't list:
// plain text, markdown, yaml, python, shell, etc. all go through
// the base editor worker (syntax highlighting only — no rich
// language service for those, which is fine for read-only
// preview).
self.MonacoEnvironment = {
  getWorker(_workerId, label) {
    switch (label) {
      case 'json':
        return new JsonWorker();
      case 'css':
      case 'scss':
      case 'less':
        return new CssWorker();
      case 'html':
      case 'handlebars':
      case 'razor':
        return new HtmlWorker();
      case 'typescript':
      case 'javascript':
        return new TsWorker();
      default:
        return new EditorWorker();
    }
  },
};

// Tell @monaco-editor/react to use the locally-imported monaco
// instance instead of fetching one off the CDN. Must run before
// any <Editor /> mounts.
loader.config({ monaco });
