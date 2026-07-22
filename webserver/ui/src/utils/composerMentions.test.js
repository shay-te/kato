import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyMention,
  detectMentionQuery,
  filterMentionFiles,
  flattenTreeFiles,
  referenceFor,
} from './composerMentions.js';

const TREES = [
  {
    repo_id: 'client',
    cwd: '/ws/client',
    tree: [
      {
        path: '/ws/client/src',
        name: 'src',
        children: [
          { path: '/ws/client/src/app.js', name: 'app.js' },
          { path: '/ws/client/src/util.js', name: 'util.js' },
        ],
      },
      { path: '/ws/client/README.md', name: 'README.md' },
    ],
  },
  {
    repo_id: 'server',
    cwd: '/ws/server',
    tree: [{ path: '/ws/server/main.py', name: 'main.py' }],
  },
];

test('flattenTreeFiles flattens nested folders across repos to files only', () => {
  assert.deepEqual(flattenTreeFiles(TREES), [
    { repoId: 'client', relativePath: 'src/app.js', name: 'app.js' },
    { repoId: 'client', relativePath: 'src/util.js', name: 'util.js' },
    { repoId: 'client', relativePath: 'README.md', name: 'README.md' },
    { repoId: 'server', relativePath: 'main.py', name: 'main.py' },
  ]);
});

test('flattenTreeFiles is safe on empty / missing input', () => {
  assert.deepEqual(flattenTreeFiles(null), []);
  assert.deepEqual(flattenTreeFiles([]), []);
  assert.deepEqual(flattenTreeFiles([{ repo_id: 'x', tree: [] }]), []);
});

test('detectMentionQuery activates on @ at the start', () => {
  assert.deepEqual(detectMentionQuery('@app', 4), { active: true, query: 'app', start: 0 });
});

test('detectMentionQuery activates on @ after whitespace (path prefix allowed)', () => {
  assert.deepEqual(detectMentionQuery('open @src/ap', 12), {
    active: true, query: 'src/ap', start: 5,
  });
});

test('detectMentionQuery activates with an empty query the moment @ is typed', () => {
  assert.deepEqual(detectMentionQuery('@', 1), { active: true, query: '', start: 0 });
});

test('detectMentionQuery does NOT activate for an email-like @', () => {
  assert.equal(detectMentionQuery('user@host', 9).active, false);
});

test('detectMentionQuery does NOT activate once the token has whitespace', () => {
  assert.equal(detectMentionQuery('@foo bar', 8).active, false);
});

test('detectMentionQuery tracks the NEAREST @ before the caret', () => {
  assert.deepEqual(detectMentionQuery('@a @b', 5), { active: true, query: 'b', start: 3 });
});

test('detectMentionQuery is inactive when the caret is before the @', () => {
  assert.equal(detectMentionQuery('@abc', 0).active, false);
});

test('filterMentionFiles returns the head of the list (capped) for an empty query', () => {
  const files = flattenTreeFiles(TREES);
  assert.equal(filterMentionFiles(files, '', 2).length, 2);
  assert.deepEqual(filterMentionFiles(files, ''), files);
});

test('filterMentionFiles ranks a filename match above a path-only match', () => {
  const files = flattenTreeFiles(TREES);
  assert.equal(filterMentionFiles(files, 'app')[0].relativePath, 'src/app.js');
});

test('filterMentionFiles matches on a path segment', () => {
  const files = flattenTreeFiles(TREES);
  const paths = filterMentionFiles(files, 'src/util').map((f) => f.relativePath);
  assert.ok(paths.includes('src/util.js'));
});

test('filterMentionFiles returns nothing when there is no match', () => {
  assert.deepEqual(filterMentionFiles(flattenTreeFiles(TREES), 'zzzznope'), []);
});

test('referenceFor scopes the path by repo id', () => {
  assert.equal(
    referenceFor({ repoId: 'client', relativePath: 'src/app.js' }),
    'client/src/app.js',
  );
});

test('referenceFor falls back to the bare path with no repo id', () => {
  assert.equal(referenceFor({ repoId: '', relativePath: 'a/b.js' }), 'a/b.js');
});

test('applyMention replaces @query with a backtick-wrapped reference + space', () => {
  const { text, caret } = applyMention('see @app', 4, 8, 'client/src/app.js');
  assert.equal(text, 'see `client/src/app.js` ');
  assert.equal(caret, text.length);
});

test('applyMention preserves text after the caret', () => {
  const { text } = applyMention('a @u done', 2, 4, 'client/src/util.js');
  assert.equal(text, 'a `client/src/util.js` done');
});
