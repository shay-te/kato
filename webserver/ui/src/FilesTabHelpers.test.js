import assert from 'node:assert/strict';
import test from 'node:test';

import {
  activateTreeNode,
  attachIds,
  matchTreeNode,
  normalizeTrees,
} from './FilesTabHelpers.js';


test('file activation appends the repository-relative path to chat input', function () {
  const tree = attachIds([
    {
      name: 'Source.js',
      path: '/workspace/client/src/Table/OrganizationPhoneNumbers/Source.js',
    },
  ], '/workspace/client');
  const pickedPaths = [];
  const fileNode = {
    isInternal: false,
    data: tree[0],
    toggle: function () {
      throw new Error('file nodes must not toggle');
    },
  };

  activateTreeNode(fileNode, function (path) {
    pickedPaths.push(path);
  });

  assert.deepEqual(pickedPaths, ['src/Table/OrganizationPhoneNumbers/Source.js']);
});

test('folder activation toggles without appending text to chat input', function () {
  let toggled = false;
  const pickedPaths = [];
  const folderNode = {
    isInternal: true,
    data: { relativePath: 'src' },
    toggle: function () {
      toggled = true;
    },
  };

  activateTreeNode(folderNode, function (path) {
    pickedPaths.push(path);
  });

  assert.equal(toggled, true);
  assert.deepEqual(pickedPaths, []);
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
