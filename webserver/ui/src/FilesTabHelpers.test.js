import assert from 'node:assert/strict';
import test from 'node:test';

import {
  activateTreeNode,
  attachIds,
  matchTreeNode,
  normalizeTrees,
} from './FilesTabHelpers.js';


test('file activation is a no-op — left-click only opens, never pastes to chat', function () {
  const tree = attachIds([
    {
      name: 'Source.js',
      path: '/workspace/client/src/Table/OrganizationPhoneNumbers/Source.js',
    },
  ], '/workspace/client');
  let toggled = false;
  const fileNode = {
    isInternal: false,
    data: tree[0],
    toggle: function () {
      toggled = true;
    },
  };

  // No throw, no toggle — files do nothing on activate; opening is
  // the caller's job (onOpenFile), pasting is right-click only.
  activateTreeNode(fileNode);

  assert.equal(toggled, false);
});

test('folder activation toggles', function () {
  let toggled = false;
  const folderNode = {
    isInternal: true,
    data: { relativePath: 'src' },
    toggle: function () {
      toggled = true;
    },
  };

  activateTreeNode(folderNode);

  assert.equal(toggled, true);
});

test('multi-repository payload normalization keeps repo cwd for relative paths', function () {
  const normalized = normalizeTrees({
    trees: [
      {
        repo_id: 'client',
        cwd: '/workspace/client',
        tree: [{ name: 'App.jsx', path: '/workspace/client/src/App.jsx' }],
      },
    ],
  });
  const tree = attachIds(normalized[0].tree, normalized[0].cwd);

  assert.equal(normalized[0].repo_id, 'client');
  assert.equal(tree[0].relativePath, 'src/App.jsx');
});


// ----- matchTreeNode (filename / path filter for the search box) -----

function _node({ name, relativePath }) {
  return { data: { name, relativePath } };
}

test('matchTreeNode returns true for empty / whitespace-only term', function () {
  const node = _node({ name: 'App.jsx', relativePath: 'src/App.jsx' });
  assert.equal(matchTreeNode(node, ''), true);
  assert.equal(matchTreeNode(node, '   '), true);
  assert.equal(matchTreeNode(node, undefined), true);
});

test('matchTreeNode matches basename substring (case-insensitive)', function () {
  const node = _node({ name: 'App.jsx', relativePath: 'src/App.jsx' });
  assert.equal(matchTreeNode(node, 'app'), true);
  assert.equal(matchTreeNode(node, 'APP'), true);
  assert.equal(matchTreeNode(node, '.jsx'), true);
});

test('matchTreeNode matches relative-path substring even when basename misses', function () {
  // Search "src/auth" finds src/auth.py even though "src/auth" is
  // not a substring of just "auth.py".
  const node = _node({ name: 'auth.py', relativePath: 'src/auth.py' });
  assert.equal(matchTreeNode(node, 'src/auth'), true);
});

test('matchTreeNode rejects when neither basename nor path contains the term', function () {
  const node = _node({ name: 'App.jsx', relativePath: 'src/App.jsx' });
  assert.equal(matchTreeNode(node, 'matchnothing'), false);
});

test('matchTreeNode tolerates missing data fields', function () {
  assert.equal(matchTreeNode({}, 'anything'), false);
  assert.equal(matchTreeNode(null, 'anything'), false);
  assert.equal(matchTreeNode({ data: {} }, ''), true);
});

test('matchTreeNode: separator-insensitive ("fileservice" → file_service)', function () {
  const node = _node({
    name: 'file_service.py', relativePath: 'src/file_service.py',
  });
  assert.equal(matchTreeNode(node, 'fileservice'), true);
  assert.equal(matchTreeNode(node, 'file-service'), true);
  assert.equal(matchTreeNode(node, 'file.service'), true);
  assert.equal(matchTreeNode(node, 'FileService'), true);
});

test('matchTreeNode: initialism / camel-hump ("TMPD" → TestMePleaseDude)', function () {
  const node = _node({
    name: 'TestMePleaseDude.tsx',
    relativePath: 'src/TestMePleaseDude.tsx',
  });
  assert.equal(matchTreeNode(node, 'TMPD'), true);
  assert.equal(matchTreeNode(node, 'tmpd'), true);
  // Out-of-order initials must NOT match.
  assert.equal(matchTreeNode(node, 'dptm'), false);
});

test('matchTreeNode: contains / ends-with, not only starts-with', function () {
  const node = _node({ name: 'auth.py', relativePath: 'src/auth.py' });
  assert.equal(matchTreeNode(node, 'authpy'), true);   // ends-with-ish
  assert.equal(matchTreeNode(node, 'thpy'), true);     // middle/contains
  assert.equal(matchTreeNode(node, 'srcauth'), true);  // path, separator-free
});

test('matchTreeNode: still rejects a genuine non-match', function () {
  const node = _node({ name: 'App.jsx', relativePath: 'src/App.jsx' });
  // No subsequence of these chars in order — must be false so the
  // fuzzy path doesn't turn into "match everything".
  assert.equal(matchTreeNode(node, 'zzqx'), false);
});


// ----- conflict surfacing through normalizeTrees -----

test('normalizeTrees carries conflicted_files into a Set on each tree', function () {
  const normalized = normalizeTrees({
    trees: [
      {
        repo_id: 'client',
        cwd: '/workspace/client',
        tree: [],
        conflicted_files: ['src/auth.py', 'src/cache.py'],
      },
    ],
  });
  assert.equal(normalized[0].conflictedFiles instanceof Set, true);
  assert.equal(normalized[0].conflictedFiles.has('src/auth.py'), true);
  assert.equal(normalized[0].conflictedFiles.has('src/cache.py'), true);
  assert.equal(normalized[0].conflictedFiles.has('src/other.py'), false);
});

test('normalizeTrees defaults conflictedFiles to an empty Set when missing', function () {
  const normalized = normalizeTrees({
    trees: [
      { repo_id: 'client', cwd: '/workspace/client', tree: [] },
    ],
  });
  assert.equal(normalized[0].conflictedFiles instanceof Set, true);
  assert.equal(normalized[0].conflictedFiles.size, 0);
});

test('normalizeTrees handles legacy single-repo payload with conflicted_files', function () {
  const normalized = normalizeTrees({
    cwd: '/workspace/client',
    tree: [],
    conflicted_files: ['src/legacy.py'],
  });
  assert.equal(normalized.length, 1);
  assert.equal(normalized[0].conflictedFiles.has('src/legacy.py'), true);
});

// ----- changed-file surfacing through normalizeTrees -----

test('normalizeTrees carries changed_files into a Set on each tree', function () {
  const normalized = normalizeTrees({
    trees: [
      {
        repo_id: 'client',
        cwd: '/workspace/client',
        tree: [],
        changed_files: ['src/app.py', 'README.md'],
      },
    ],
  });
  assert.equal(normalized[0].changedFiles instanceof Set, true);
  assert.equal(normalized[0].changedFiles.has('src/app.py'), true);
  assert.equal(normalized[0].changedFiles.has('README.md'), true);
  assert.equal(normalized[0].changedFiles.has('src/untouched.py'), false);
});

test('normalizeTrees defaults changedFiles to an empty Set when missing', function () {
  const normalized = normalizeTrees({
    trees: [
      { repo_id: 'client', cwd: '/workspace/client', tree: [] },
    ],
  });
  assert.equal(normalized[0].changedFiles instanceof Set, true);
  assert.equal(normalized[0].changedFiles.size, 0);
});

test('normalizeTrees handles legacy single-repo payload with changed_files', function () {
  const normalized = normalizeTrees({
    cwd: '/workspace/client',
    tree: [],
    changed_files: ['src/legacy_changed.py'],
  });
  assert.equal(normalized.length, 1);
  assert.equal(normalized[0].changedFiles instanceof Set, true);
  assert.equal(normalized[0].changedFiles.has('src/legacy_changed.py'), true);
});
