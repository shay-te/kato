import assert from 'node:assert/strict';
import test from 'node:test';

import {
  findTreeNodeIdByRelativePath,
  activateTreeNode,
  attachIds,
  countRepoComments,
  folderContainsChange,
  groupContentMatchesByFile,
  matchTreeNode,
  normalizeTrees,
  repoCommentStatus,
  countVisibleTreeRows,
} from './FilesTabHelpers.js';
import { moreUrgentCommentStatus } from './utils/commentStatus.js';


test('countRepoComments sums the per-file counts', () => {
  const meta = new Map([
    ['a.py', { count: 2, status: 'open' }],
    ['b.py', { count: 1, status: 'open' }],
  ]);
  assert.equal(countRepoComments(meta), 3);
});

test('countRepoComments is 0 for empty / non-map input', () => {
  assert.equal(countRepoComments(new Map()), 0);
  assert.equal(countRepoComments(null), 0);
  assert.equal(countRepoComments(undefined), 0);
});

test('countRepoComments scoped to filePaths ignores comments on files not shown', () => {
  const meta = new Map([
    ['a.py', { count: 2, status: 'open' }],    // rendered as a tree row
    ['gone.py', { count: 1, status: 'open' }], // orphan — not in the tree
  ]);
  // The repo-header count must equal the SUM of the visible per-file badges:
  // the orphan on gone.py (no tree row → no badge) can't inflate it.
  assert.equal(countRepoComments(meta, ['a.py']), 2);
  // No shown file carries a comment → repo shows 0 (the operator's report:
  // "no comment on the file tree means no comments on the repo").
  assert.equal(countRepoComments(meta, ['x.py']), 0);
  assert.equal(countRepoComments(meta, []), 0);
  // A duplicated path is counted once.
  assert.equal(countRepoComments(meta, ['a.py', 'a.py']), 2);
});


// Repo header chip must tint to the same status the file-row chips do,
// so the header doesn't read as "different colour from the rows it
// summarises" (the original operator report — two PENDING comments,
// repo header was a static cyan).
test('repoCommentStatus picks the most-urgent status across files', () => {
  const meta = new Map([
    ['a.py', { count: 1, status: 'queued' }],
    ['b.py', { count: 1, status: 'in_progress' }],
  ]);
  // Precedence: failed > waiting > open > queued > in_progress > addressed.
  // queued beats in_progress.
  assert.equal(repoCommentStatus(meta, moreUrgentCommentStatus), 'queued');
});

test('repoCommentStatus scoped to filePaths only tints from the shown files', () => {
  const meta = new Map([
    ['a.py', { count: 1, status: 'addressed' }], // shown in the tree
    ['gone.py', { count: 1, status: 'failed' }], // orphan (would win if counted)
  ]);
  // Only the shown file counts → 'addressed'; the orphan's more-urgent
  // 'failed' is ignored because it renders no badge.
  assert.equal(repoCommentStatus(meta, moreUrgentCommentStatus, ['a.py']), 'addressed');
  assert.equal(repoCommentStatus(meta, moreUrgentCommentStatus, []), '');
});

test('repoCommentStatus returns the only present status when one is set', () => {
  const meta = new Map([
    ['a.py', { count: 2, status: 'queued' }],
    ['b.py', { count: 1, status: 'queued' }],
  ]);
  assert.equal(repoCommentStatus(meta, moreUrgentCommentStatus), 'queued');
});

test('repoCommentStatus is empty for empty / non-map input', () => {
  assert.equal(repoCommentStatus(new Map(), moreUrgentCommentStatus), '');
  assert.equal(repoCommentStatus(null, moreUrgentCommentStatus), '');
  assert.equal(repoCommentStatus(undefined, moreUrgentCommentStatus), '');
});


test('groupContentMatchesByFile groups lines per file, first-seen order', () => {
  const matches = [
    { repo_id: 'be', path: 'a.py', abs_path: '/wk/be/a.py', line: 3, text: 'x' },
    { repo_id: 'be', path: 'a.py', abs_path: '/wk/be/a.py', line: 9, text: 'y' },
    { repo_id: 'fe', path: 'b.js', abs_path: '/wk/fe/b.js', line: 1, text: 'z' },
  ];
  const groups = groupContentMatchesByFile(matches);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].repoId, 'be');
  assert.equal(groups[0].path, 'a.py');
  assert.equal(groups[0].absPath, '/wk/be/a.py');
  assert.deepEqual(groups[0].lines, [{ line: 3, text: 'x' }, { line: 9, text: 'y' }]);
  assert.equal(groups[1].repoId, 'fe');
  assert.equal(groups[1].lines.length, 1);
});

test('groupContentMatchesByFile tolerates empty / malformed input', () => {
  assert.deepEqual(groupContentMatchesByFile([]), []);
  assert.deepEqual(groupContentMatchesByFile(null), []);
  const groups = groupContentMatchesByFile([null, { repo_id: 'r', path: 'p' }]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].lines[0].line, 0);
});


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

test('normalizeTrees carries read_only through as readOnly', function () {
  const normalized = normalizeTrees({
    trees: [
      { repo_id: 'client', cwd: '/ws/client', tree: [], read_only: false },
      { repo_id: 'ext-lib', cwd: '/ws/ext-lib', tree: [], read_only: true },
    ],
  });
  assert.equal(normalized[0].readOnly, false);
  assert.equal(normalized[1].readOnly, true);
});

test('normalizeTrees defaults readOnly to false when absent', function () {
  const normalized = normalizeTrees({
    trees: [{ repo_id: 'client', cwd: '/ws/client', tree: [] }],
  });
  assert.equal(normalized[0].readOnly, false);
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


// ----- folderContainsChange: ancestor-folder tint -----------------
// Files changed on the branch already render in a distinct colour;
// this lights up every ancestor folder up to (but NOT including) the
// repo root so the path to an edit is visible without expanding.

const _changed = new Set([
  'src/components/EventLog.jsx',
  'webserver/static/css/app.css',
]);

test('folderContainsChange: direct parent of a changed file → true', function () {
  assert.equal(folderContainsChange('src/components', _changed), true);
});

test('folderContainsChange: grandparent (up the whole chain) → true', function () {
  assert.equal(folderContainsChange('src', _changed), true);
  assert.equal(folderContainsChange('webserver/static', _changed), true);
});

test('folderContainsChange: unrelated folder → false', function () {
  assert.equal(folderContainsChange('src/utils', _changed), false);
  assert.equal(folderContainsChange('docs', _changed), false);
});

test('folderContainsChange: empty path (synthetic repo root) → false — "not the root of all"', function () {
  assert.equal(folderContainsChange('', _changed), false);
  assert.equal(folderContainsChange(null, _changed), false);
});

test('folderContainsChange: segment-boundary safe (src/app vs src/application)', function () {
  const c = new Set(['src/application.js']);
  // ``src/app`` must NOT match ``src/application.js`` — the prefix
  // check appends "/" so only true path segments match.
  assert.equal(folderContainsChange('src/app', c), false);
  assert.equal(folderContainsChange('src', c), true);
});

test('folderContainsChange: path recorded exactly against a dir entry → true', function () {
  assert.equal(
    folderContainsChange('src/components', new Set(['src/components'])),
    true,
  );
});

test('folderContainsChange: empty / missing changed set → false', function () {
  assert.equal(folderContainsChange('src', new Set()), false);
  assert.equal(folderContainsChange('src', null), false);
});


test('findTreeNodeIdByRelativePath resolves a nested file to its tree id', () => {
  const nodes = attachIds([
    {
      name: 'src',
      path: '/tmp/client/src',
      children: [
        { name: 'App.jsx', path: '/tmp/client/src/App.jsx' },
        {
          name: 'utils',
          path: '/tmp/client/src/utils',
          children: [{ name: 'dom.js', path: '/tmp/client/src/utils/dom.js' }],
        },
      ],
    },
  ], '/tmp/client');
  assert.equal(
    findTreeNodeIdByRelativePath(nodes, 'src/utils/dom.js'),
    '/tmp/client/src/utils/dom.js',
  );
  assert.equal(
    findTreeNodeIdByRelativePath(nodes, 'src/App.jsx'),
    '/tmp/client/src/App.jsx',
  );
});

test('findTreeNodeIdByRelativePath returns null for unknown / blank paths', () => {
  const nodes = attachIds(
    [{ name: 'a.js', path: '/tmp/client/a.js' }], '/tmp/client',
  );
  assert.equal(findTreeNodeIdByRelativePath(nodes, 'missing.js'), null);
  assert.equal(findTreeNodeIdByRelativePath(nodes, ''), null);
  assert.equal(findTreeNodeIdByRelativePath(null, 'a.js'), null);
});


// ----- countVisibleTreeRows (how tall a repo section should be) -----
//
// The height came from the number of ROOT entries, which ignores the filter:
// searching a large repo left an 800px-tall section showing nine matching
// files, and the operator scrolled past the empty space to reach the next
// repo.

const _TREE = [
  {
    name: 'src',
    children: [
      { name: 'auth.py' },
      { name: 'profile.py' },
      { name: 'nested', children: [{ name: 'deep_profile.py' }] },
    ],
  },
  { name: 'README.md' },
  { name: 'tests', children: [{ name: 'test_auth.py' }] },
];

test('countVisibleTreeRows: no filter counts only the roots', function () {
  // Folders start closed, so the roots are all that is drawn.
  assert.equal(countVisibleTreeRows(_TREE, ''), 3);
  assert.equal(countVisibleTreeRows(_TREE, '   '), 3);
});

test('countVisibleTreeRows: a filter counts matches AND their ancestors', function () {
  // "profile" matches src/profile.py and src/nested/deep_profile.py; the
  // folders leading to them are drawn too, because filtering opens them.
  //   src, src/profile.py, src/nested, src/nested/deep_profile.py
  assert.equal(countVisibleTreeRows(_TREE, 'profile'), 4);
});

test('countVisibleTreeRows: a filter matching nothing draws nothing', function () {
  assert.equal(countVisibleTreeRows(_TREE, 'zzzz-no-such-file'), 0);
});

test('countVisibleTreeRows: a matching FOLDER brings its subtree', function () {
  // The folder itself matches, so it is drawn; its children are visible
  // beneath it only when they match too.
  const rows = countVisibleTreeRows(_TREE, 'tests');
  assert.ok(rows >= 1, `expected the matching folder to be drawn, got ${rows}`);
});

test('countVisibleTreeRows: empty / malformed input is 0', function () {
  assert.equal(countVisibleTreeRows([], 'x'), 0);
  assert.equal(countVisibleTreeRows(null, ''), 0);
  assert.equal(countVisibleTreeRows(undefined, 'x'), 0);
});

test('countVisibleTreeRows: a filtered count never exceeds the whole tree', function () {
  // 8 nodes in total (3 roots + 3 under src + 1 under nested + 1 under
  // tests); a filter can only ever draw a subset of them.
  assert.ok(countVisibleTreeRows(_TREE, 'p') <= 8);
});
