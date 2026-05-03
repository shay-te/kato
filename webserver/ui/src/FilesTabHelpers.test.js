import assert from 'node:assert/strict';
import test from 'node:test';

import { activateTreeNode, attachIds, normalizeTrees } from './FilesTabHelpers.js';


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
