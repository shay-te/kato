import test from 'node:test';
import assert from 'node:assert/strict';
import {
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
