import test from 'node:test';
import assert from 'node:assert/strict';

import {
  importSpecifierAt,
  importTargetAt,
  reposFromTrees,
  resolveImportTarget,
} from './importNavigation.js';

const CLIENT = {
  repoId: 'ob-love-admin-client',
  cwd: '/wk/UNA-1/ob-love-admin-client',
  paths: new Set([
    'src/App.jsx',
    'src/components/Table/QueryPhrase.js',
    'src/components/Table/index.js',
    'src/utils/format.ts',
    'package.json',
  ]),
};
const UI = {
  repoId: 'ob-love-ui',
  cwd: '/wk/UNA-1/ob-love-ui',
  paths: new Set(['src/index.js', 'src/TestButton.js']),
};
const BACKEND = {
  repoId: 'ob-love-admin-backend',
  cwd: '/wk/UNA-1/ob-love-admin-backend',
  paths: new Set([
    'admin_core_lib/data_layers/service/lead.py',
    'admin_core_lib/helpers/__init__.py',
    'admin_core_lib/helpers/dates.py',
    'celery_main.py',
  ]),
};
const EMAIL_LIB = {
  repoId: 'email-core-lib',
  cwd: '/wk/UNA-1/email-core-lib',
  paths: new Set(['email_core_lib/client/sender.py', 'email_core_lib/__init__.py']),
};

const REPOS = [CLIENT, UI, BACKEND, EMAIL_LIB];

// ---------------------------------------------------------------------------
// importSpecifierAt — only ever the specifier, never a stray word
// ---------------------------------------------------------------------------

test('reads the quoted specifier the caret sits inside', () => {
  const line = "import Table from './components/Table';";
  // caret inside './components/Table'
  assert.deepEqual(
    importSpecifierAt(line, line.indexOf('components') + 2),
    { specifier: './components/Table', kind: 'js' },
  );
});

test('a caret outside the quotes is not an import click', () => {
  const line = "import Table from './components/Table';";
  // On the word ``Table`` (the imported NAME): a name alone doesn't say which
  // file it came from, and guessing would open the wrong one — this workspace
  // has many same-named files across repos.
  assert.equal(importSpecifierAt(line, line.indexOf('Table') + 1), null);
});

test('handles require, dynamic import and re-export lines', () => {
  const req = "const x = require('./utils/format');";
  assert.equal(importSpecifierAt(req, req.indexOf('utils') + 1).specifier, './utils/format');
  const dyn = "const m = await import('./components/Table');";
  assert.equal(importSpecifierAt(dyn, dyn.indexOf('components') + 1).specifier, './components/Table');
  const re = "export { Button } from '@ob-love/ui';";
  assert.equal(importSpecifierAt(re, re.indexOf('ob-love') + 1).specifier, '@ob-love/ui');
});

test('a non-import line with a string is ignored', () => {
  const line = "const label = './components/Table';";
  assert.equal(importSpecifierAt(line, line.indexOf('components') + 1), null);
});

test('reads python dotted module paths from both forms', () => {
  const from = 'from admin_core_lib.helpers.dates import parse';
  assert.deepEqual(
    importSpecifierAt(from, from.indexOf('helpers') + 1),
    { specifier: 'admin_core_lib.helpers.dates', kind: 'python' },
  );
  const plain = 'import admin_core_lib.helpers.dates';
  assert.equal(
    importSpecifierAt(plain, plain.indexOf('dates') + 1).specifier,
    'admin_core_lib.helpers.dates',
  );
  // Caret on ``parse`` (the imported name) is not the module path.
  assert.equal(importSpecifierAt(from, from.indexOf('parse') + 1), null);
});

// ---------------------------------------------------------------------------
// resolveImportTarget — JS
// ---------------------------------------------------------------------------

test('relative JS import resolves with an extension guess', () => {
  assert.deepEqual(
    resolveImportTarget({
      specifier: './components/Table/QueryPhrase',
      fromRepoId: CLIENT.repoId,
      fromRelativePath: 'src/App.jsx',
      repos: REPOS,
    }),
    {
      repoId: 'ob-love-admin-client',
      relativePath: 'src/components/Table/QueryPhrase.js',
      absolutePath: '/wk/UNA-1/ob-love-admin-client/src/components/Table/QueryPhrase.js',
    },
  );
});

test('a directory import falls back to its index file', () => {
  const hit = resolveImportTarget({
    specifier: './components/Table',
    fromRepoId: CLIENT.repoId,
    fromRelativePath: 'src/App.jsx',
    repos: REPOS,
  });
  assert.equal(hit.relativePath, 'src/components/Table/index.js');
});

test('".." segments are resolved, not pasted', () => {
  const hit = resolveImportTarget({
    specifier: '../../utils/format',
    fromRepoId: CLIENT.repoId,
    fromRelativePath: 'src/components/Table/QueryPhrase.js',
    repos: REPOS,
  });
  assert.equal(hit.relativePath, 'src/utils/format.ts');
});

test('a relative import never escapes into another repo', () => {
  // Same file name exists in ob-love-ui; a relative path must not find it.
  assert.equal(
    resolveImportTarget({
      specifier: './TestButton',
      fromRepoId: CLIENT.repoId,
      fromRelativePath: 'src/App.jsx',
      repos: REPOS,
    }),
    null,
  );
});

test('a bare package resolves to the sibling REPO in the same task', () => {
  // ``@ob-love/ui`` is the ob-love-ui clone next door — the cross-repo hop
  // that makes this worth doing at all.
  const hit = resolveImportTarget({
    specifier: '@ob-love/ui',
    fromRepoId: CLIENT.repoId,
    fromRelativePath: 'src/App.jsx',
    repos: REPOS,
  });
  assert.equal(hit.repoId, 'ob-love-ui');
  assert.equal(hit.relativePath, 'src/index.js');
});

test('a bare package subpath resolves inside that repo', () => {
  const hit = resolveImportTarget({
    specifier: '@ob-love/ui/TestButton',
    fromRepoId: CLIENT.repoId,
    fromRelativePath: 'src/App.jsx',
    repos: REPOS,
  });
  assert.equal(hit.relativePath, 'src/TestButton.js');
});

test('an unknown package is null, never a same-named file elsewhere', () => {
  assert.equal(
    resolveImportTarget({
      specifier: 'react',
      fromRepoId: CLIENT.repoId,
      fromRelativePath: 'src/App.jsx',
      repos: REPOS,
    }),
    null,
  );
});

// ---------------------------------------------------------------------------
// resolveImportTarget — Python
// ---------------------------------------------------------------------------

test('a python module resolves inside its own repo first', () => {
  const hit = resolveImportTarget({
    specifier: 'admin_core_lib.helpers.dates',
    kind: 'python',
    fromRepoId: BACKEND.repoId,
    fromRelativePath: 'celery_main.py',
    repos: REPOS,
  });
  assert.equal(hit.repoId, 'ob-love-admin-backend');
  assert.equal(hit.relativePath, 'admin_core_lib/helpers/dates.py');
});

test('a python module in ANOTHER repo of the task is found', () => {
  // The core libs are separate clones in the same task folder — without the
  // cross-repo search, every core-lib import would be a dead click.
  const hit = resolveImportTarget({
    specifier: 'email_core_lib.client.sender',
    kind: 'python',
    fromRepoId: BACKEND.repoId,
    fromRelativePath: 'celery_main.py',
    repos: REPOS,
  });
  assert.equal(hit.repoId, 'email-core-lib');
  assert.equal(hit.relativePath, 'email_core_lib/client/sender.py');
});

test('a package module resolves to its __init__.py', () => {
  const hit = resolveImportTarget({
    specifier: 'admin_core_lib.helpers',
    kind: 'python',
    fromRepoId: BACKEND.repoId,
    fromRelativePath: 'celery_main.py',
    repos: REPOS,
  });
  assert.equal(hit.relativePath, 'admin_core_lib/helpers/__init__.py');
});

test('relative python imports climb one level per extra dot', () => {
  const one = resolveImportTarget({
    specifier: '.dates',
    kind: 'python',
    fromRepoId: BACKEND.repoId,
    fromRelativePath: 'admin_core_lib/helpers/__init__.py',
    repos: REPOS,
  });
  assert.equal(one.relativePath, 'admin_core_lib/helpers/dates.py');

  const two = resolveImportTarget({
    specifier: '...helpers.dates',
    kind: 'python',
    fromRepoId: BACKEND.repoId,
    fromRelativePath: 'admin_core_lib/data_layers/service/lead.py',
    repos: REPOS,
  });
  assert.equal(two.relativePath, 'admin_core_lib/helpers/dates.py');
});

test('nothing matching is null — a dead click, not a wrong file', () => {
  assert.equal(
    resolveImportTarget({
      specifier: 'nope.does.not.exist',
      kind: 'python',
      fromRepoId: BACKEND.repoId,
      fromRelativePath: 'celery_main.py',
      repos: REPOS,
    }),
    null,
  );
  assert.equal(resolveImportTarget({ specifier: '', repos: REPOS }), null);
  assert.equal(resolveImportTarget({}), null);
});

// ---------------------------------------------------------------------------
// importTargetAt + reposFromTrees — the editor's entry points
// ---------------------------------------------------------------------------

test('importTargetAt reads the line and resolves in one call', () => {
  const line = "import QueryPhrase from './components/Table/QueryPhrase';";
  const hit = importTargetAt({
    lineText: line,
    column: line.lastIndexOf('QueryPhrase') + 2,
    fromRepoId: CLIENT.repoId,
    fromRelativePath: 'src/App.jsx',
    repos: REPOS,
  });
  assert.equal(
    hit.absolutePath,
    '/wk/UNA-1/ob-love-admin-client/src/components/Table/QueryPhrase.js',
  );
});

test('importTargetAt is null off an import', () => {
  assert.equal(
    importTargetAt({ lineText: 'const x = 1;', column: 3, repos: REPOS }),
    null,
  );
});

test('reposFromTrees flattens the Files-tab payload into a path index', () => {
  const repos = reposFromTrees([
    {
      repo_id: 'client',
      cwd: '/wk/UNA-1/client',
      tree: [
        { name: 'src', path: 'src', children: [{ name: 'App.jsx', path: 'src/App.jsx' }] },
        { name: 'package.json', path: 'package.json' },
      ],
    },
  ]);
  assert.equal(repos.length, 1);
  assert.equal(repos[0].repoId, 'client');
  assert.deepEqual([...repos[0].paths].sort(), ['package.json', 'src/App.jsx']);
});

test('reposFromTrees tolerates junk', () => {
  assert.deepEqual(reposFromTrees(null), []);
  assert.deepEqual(reposFromTrees([{}]), [{ repoId: '', cwd: '', paths: new Set() }]);
});
