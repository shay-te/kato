import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isImagePath,
  canToggleView,
  TASK_FOLDER_REPO_ID, isMarkdownPath, isTaskFolderRepo,
  defaultMarkdownView, markdownViewFor,
} from './markdownView.js';

test('recognises markdown extensions, case-insensitively', () => {
  for (const p of ['plan.md', 'A.MD', 'notes.markdown', 'x/y.mkd', 'r.mdown']) {
    assert.equal(isMarkdownPath(p), true, p);
  }
  for (const p of ['app.py', 'readme', 'md', 'a.mdx', '', null, undefined]) {
    assert.equal(isMarkdownPath(p), false, String(p));
  }
});

test('task-folder repo id matches the backend pseudo-repo', () => {
  assert.equal(TASK_FOLDER_REPO_ID, 'task files');
  assert.equal(isTaskFolderRepo('task files'), true);
  assert.equal(isTaskFolderRepo('  Task Files '), true);
  assert.equal(isTaskFolderRepo('kato_core_lib'), false);
  assert.equal(isTaskFolderRepo(''), false);
});

test('task files default to preview, repo files to source', () => {
  assert.equal(defaultMarkdownView({ repoId: 'task files' }), 'preview');
  assert.equal(defaultMarkdownView({ repoId: 'kato' }), 'source');
  assert.equal(defaultMarkdownView(null), 'source');
});

test('markdownViewFor is empty for a non-markdown file', () => {
  assert.equal(markdownViewFor({ repoId: 'task files', relativePath: 'a.py' }), '');
});

test('markdownViewFor falls back to the default when unset', () => {
  assert.equal(
    markdownViewFor({ repoId: 'task files', relativePath: 'plan.md' }), 'preview',
  );
  assert.equal(
    markdownViewFor({ repoId: 'kato', relativePath: 'README.md' }), 'source',
  );
});

test('an explicit choice beats the default in both directions', () => {
  assert.equal(
    markdownViewFor({ repoId: 'task files', relativePath: 'plan.md', mdView: 'source' }),
    'source',
  );
  assert.equal(
    markdownViewFor({ repoId: 'kato', relativePath: 'README.md', mdView: 'preview' }),
    'preview',
  );
});

test('a junk mdView value falls back to the default', () => {
  assert.equal(
    markdownViewFor({ repoId: 'task files', relativePath: 'plan.md', mdView: 'wat' }),
    'preview',
  );
});

test('absolutePath is used when relativePath is missing', () => {
  assert.equal(
    markdownViewFor({ repoId: 'task files', absolutePath: '/w/t/plan.md' }), 'preview',
  );
});

// "load svgs (with a swich on the tab to view the code), images and other
// assets" — the pane used to answer "Binary file — no text preview
// available" for exactly the files an operator most wants to look at.

const assetTab = (relativePath, extra = {}) => ({ relativePath, ...extra });

test('an SVG opens RENDERED and keeps the source switch', () => {
  // It is text, so it has a source worth reading — but nobody opens an icon
  // to read its path data first.
  assert.equal(markdownViewFor(assetTab('icons/logo.svg')), 'preview');
  assert.equal(canToggleView(assetTab('icons/logo.svg')), true);
});

test('flipping the switch on an SVG shows its code', () => {
  assert.equal(
    markdownViewFor(assetTab('icons/logo.svg', { mdView: 'source' })), 'source',
  );
});

test('a raster image renders with NO switch', () => {
  // An inert toggle is worse than none — a PNG has no source to show.
  for (const name of ['shot.png', 'a.JPG', 'b.gif', 'c.webp', 'd.ico']) {
    assert.equal(markdownViewFor(assetTab(name)), 'preview', name);
    assert.equal(canToggleView(assetTab(name)), false, name);
  }
});

test('an SVG in a repo still opens rendered, unlike markdown in a repo', () => {
  // Repo markdown opens as source because line numbers anchor comments; that
  // reasoning does not carry to an icon.
  assert.equal(
    markdownViewFor(assetTab('src/logo.svg', { repoId: 'client' })), 'preview',
  );
  assert.equal(
    markdownViewFor(assetTab('src/README.md', { repoId: 'client' })), 'source',
  );
});

test('ordinary source files are untouched', () => {
  assert.equal(markdownViewFor(assetTab('src/main.py')), '');
  assert.equal(canToggleView(assetTab('src/main.py')), false);
});

test('isImagePath covers both families', () => {
  assert.equal(isImagePath('a.svg'), true);
  assert.equal(isImagePath('a.png'), true);
  assert.equal(isImagePath('a.md'), false);
});
