import assert from 'node:assert/strict';
import test from 'node:test';

import { detectDiffLanguage, tokenizeHunks } from './diffSyntax.js';


test('detectDiffLanguage recognises common JS/TS variants', function () {
  assert.equal(detectDiffLanguage('src/App.jsx'), 'jsx');
  assert.equal(detectDiffLanguage('src/App.tsx'), 'tsx');
  assert.equal(detectDiffLanguage('src/types.ts'), 'typescript');
  assert.equal(detectDiffLanguage('src/utils/helpers.js'), 'javascript');
  assert.equal(detectDiffLanguage('build/output.cjs'), 'javascript');
  assert.equal(detectDiffLanguage('build/output.mjs'), 'javascript');
});

test('detectDiffLanguage recognises Python', function () {
  assert.equal(detectDiffLanguage('kato_core_lib/foo.py'), 'python');
});

test('detectDiffLanguage recognises styling files', function () {
  assert.equal(detectDiffLanguage('src/app.css'), 'css');
  assert.equal(detectDiffLanguage('src/theme.scss'), 'css');
  assert.equal(detectDiffLanguage('src/legacy.less'), 'css');
});

test('detectDiffLanguage recognises data formats', function () {
  assert.equal(detectDiffLanguage('package.json'), 'json');
  assert.equal(detectDiffLanguage('config.yaml'), 'yaml');
  assert.equal(detectDiffLanguage('config.yml'), 'yaml');
  assert.equal(detectDiffLanguage('README.md'), 'markdown');
});

test('detectDiffLanguage recognises shell', function () {
  assert.equal(detectDiffLanguage('scripts/build.sh'), 'bash');
  assert.equal(detectDiffLanguage('scripts/build.bash'), 'bash');
});

test('detectDiffLanguage is case-insensitive', function () {
  assert.equal(detectDiffLanguage('SRC/APP.JSX'), 'jsx');
  assert.equal(detectDiffLanguage('Foo.PY'), 'python');
});

test('detectDiffLanguage returns empty for unknown / missing path', function () {
  assert.equal(detectDiffLanguage(''), '');
  assert.equal(detectDiffLanguage(null), '');
  assert.equal(detectDiffLanguage(undefined), '');
  assert.equal(detectDiffLanguage('Dockerfile'), '');
  assert.equal(detectDiffLanguage('binary.dat'), '');
});

test('tokenizeHunks returns null for empty hunks', function () {
  assert.equal(tokenizeHunks([], 'src/App.jsx'), null);
  assert.equal(tokenizeHunks(null, 'src/App.jsx'), null);
});

test('tokenizeHunks produces an old/new token pair for an intra-line edit', async function () {
  const { parseDiff } = await import('react-diff-view');
  const rawDiff = [
    'diff --git a/App.jsx b/App.jsx',
    'index aaaa..bbbb 100644',
    '--- a/App.jsx',
    '+++ b/App.jsx',
    '@@ -1,3 +1,3 @@',
    ' const App = () => {',
    '-  return {whenType: triggerNode};',
    '+  return { whenType: triggerNode };',
    ' };',
    '',
  ].join('\n');
  const files = parseDiff(rawDiff);
  const tokens = tokenizeHunks(files[0].hunks, 'App.jsx');
  // ``markEdits`` returns ``{ old, new }`` arrays; the Diff
  // component renders the brighter intra-line tint from these.
  assert.notEqual(tokens, null);
  assert.equal(typeof tokens, 'object');
  assert.equal(Array.isArray(tokens.old), true);
  assert.equal(Array.isArray(tokens.new), true);
});
