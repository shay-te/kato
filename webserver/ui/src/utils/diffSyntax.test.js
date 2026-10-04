import assert from 'node:assert/strict';
import test from 'node:test';

import { detectDiffLanguage, tokenizeHunks } from './diffSyntax.js';


test('detectDiffLanguage recognises common JS/TS variants', function () {
  assert.equal(detectDiffLanguage('src/App.jsx'), 'jsx');
  assert.equal(detectDiffLanguage('src/App.tsx'), 'tsx');
  assert.equal(detectDiffLanguage('src/types.ts'), 'typescript');
  // .js/.cjs/.mjs map to the JSX grammar (a strict superset of
  // javascript) so React-in-.js files get tag/attr-name colouring
  // instead of rendering the whole element as plain text.
  assert.equal(detectDiffLanguage('src/utils/helpers.js'), 'jsx');
  assert.equal(detectDiffLanguage('build/output.cjs'), 'jsx');
  assert.equal(detectDiffLanguage('build/output.mjs'), 'jsx');
});

test('tokenizeHunks tags JSX-in-.js: tag, attr-name, and member props', async function () {
  const { parseDiff } = await import('react-diff-view');
  const rawDiff = [
    'diff --git a/Billing.js b/Billing.js',
    'new file mode 100644',
    '--- /dev/null',
    '+++ b/Billing.js',
    '@@ -0,0 +1,1 @@',
    '+const c = (<Package packageIncludes={planEntry.packageIncludes} />);',
    '',
  ].join('\n');
  const files = parseDiff(rawDiff);
  const tokens = tokenizeHunks(files[0].hunks, 'Billing.js');
  const flattened = JSON.stringify(tokens.new);

  // JSX is understood (under the old ``javascript`` grammar the
  // whole element rendered as plain text)…
  assert.match(flattened, /"token","tag"/);
  assert.match(flattened, /"token","attr-name"/);
  // …and the member-expression property is now its own token.
  assert.match(flattened, /"token","property-access"/);
});

test('property-access does not cannibalise method calls', async function () {
  // Regression guard for the grammar precedence: ``property-access``
  // is inserted AFTER ``function`` so ``items.map(`` stays a
  // function call while only the bare read ``i.label`` becomes a
  // property. If the order regressed, ``map`` would lose its
  // ``function`` token.
  const { parseDiff } = await import('react-diff-view');
  const rawDiff = [
    'diff --git a/m.js b/m.js',
    'new file mode 100644',
    '--- /dev/null',
    '+++ b/m.js',
    '@@ -0,0 +1,1 @@',
    '+const labels = items.map((i) => i.label);',
    '',
  ].join('\n');
  const files = parseDiff(rawDiff);
  const flattened = JSON.stringify(tokenizeHunks(files[0].hunks, 'm.js').new);

  assert.match(flattened, /"token","function"/);        // items.map(
  assert.match(flattened, /"token","property-access"/);  // i.label
});

test('detectDiffLanguage recognises Python', function () {
  assert.equal(detectDiffLanguage('kato_core_lib/foo.py'), 'python');
});

test('detectDiffLanguage recognises styling files', function () {
  assert.equal(detectDiffLanguage('src/app.css'), 'css');
  // SCSS gets its own refractor language pack (preserves
  // ``@include``, nesting, etc.); LESS isn't bundled and falls
  // back to plain CSS highlighting.
  assert.equal(detectDiffLanguage('src/theme.scss'), 'scss');
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

test('tokenizeHunks produces syntax token classes for recognised languages', async function () {
  const { parseDiff } = await import('react-diff-view');
  const rawDiff = [
    'diff --git a/App.jsx b/App.jsx',
    'new file mode 100644',
    '--- /dev/null',
    '+++ b/App.jsx',
    '@@ -0,0 +1,2 @@',
    '+const App = () => {',
    "+  return 'ready';",
    '',
  ].join('\n');
  const files = parseDiff(rawDiff);
  const tokens = tokenizeHunks(files[0].hunks, 'App.jsx');
  const flattened = JSON.stringify(tokens.new);

  assert.match(flattened, /"token","keyword"/);
  assert.match(flattened, /"token","string"/);
});


// A block comment opened just before a collapsed gap must not swallow the
// code after it.
//
// Operator report: "if there is a comment starting and he cuts the line after
// that, the rest of the code is also commented when it is not, and the diff is
// not viewable unless we expand the comment line until it is closed."
//
// react-diff-view pads every skipped line with '' before tokenizing, so a
// ``*/`` sitting in a collapsed gap is erased and Prism keeps the comment open
// to end of file. Here the ``/*`` is the last line of hunk one and its
// terminator lives in the 29 hidden lines before hunk two.
test('a comment opened before a collapsed gap does not swallow later code', async function () {
  const { parseDiff } = await import('react-diff-view');
  const rawDiff = [
    'diff --git a/PromiseAlertBanner.js b/PromiseAlertBanner.js',
    '--- a/PromiseAlertBanner.js',
    '+++ b/PromiseAlertBanner.js',
    '@@ -14,4 +14,4 @@',
    "-import { buildPromiseeTagProps } from './tagProps';",
    "+import { buildPromiseMemberTagProps } from './tagProps';",
    " import './PromiseAlertBanner.scss';",
    ' ',
    ' /*',
    '@@ -47,3 +47,3 @@',
    '     const promiseStatus = systemStore.constants.promise_status;',
    '-    const entityTypes = legacy;',
    '+    const entityTypes = systemStore.constants.promise_linked_entity_type;',
    "     const resolveLabel = i18n.t('promise.banner.resolve');",
    '',
  ].join('\n');
  const files = parseDiff(rawDiff);
  const tokens = tokenizeHunks(files[0].hunks, 'PromiseAlertBanner.js');

  // new line 47 — the first real code line after the gap.
  const afterGap = JSON.stringify(tokens.new[46]);
  assert.doesNotMatch(
    afterGap,
    /"token","comment"/,
    'code after a collapsed gap was tokenized as comment — the whole rest of '
    + 'the diff renders as a comment until the operator expands to the "*/".',
  );
  assert.match(
    afterGap,
    /"token","keyword"/,
    'the line after the gap should tokenize as real code (const is a keyword).',
  );
});

test('the comment opener itself is still tokenized as a comment', function () {
  // Guards the obvious wrong fix: switching comment highlighting off entirely
  // would also make the test above pass.
  const hunk = {
    content: '@@ -1,2 +1,2 @@',
    oldStart: 1,
    oldLines: 2,
    newStart: 1,
    newLines: 2,
    changes: [
      { type: 'normal', oldLineNumber: 1, newLineNumber: 1, content: ' /* a note' },
      { type: 'normal', oldLineNumber: 2, newLineNumber: 2, content: '    still the note */' },
    ],
  };
  const tokens = tokenizeHunks([hunk], 'note.js');
  assert.match(JSON.stringify(tokens.new[0]), /"token","comment"/);
});

test('each hunk keeps its tokens at its own absolute line numbers', async function () {
  // The per-hunk passes each pad from line 1, so a bad merge would stack every
  // hunk at the top of the file and mis-colour everything below the first.
  const { parseDiff } = await import('react-diff-view');
  const rawDiff = [
    'diff --git a/a.js b/a.js',
    '--- a/a.js',
    '+++ b/a.js',
    '@@ -1,1 +1,1 @@',
    '-const first = 1;',
    '+const first = 2;',
    '@@ -80,1 +80,1 @@',
    "-const second = 'x';",
    "+const second = 'y';",
    '',
  ].join('\n');
  const files = parseDiff(rawDiff);
  const tokens = tokenizeHunks(files[0].hunks, 'a.js');

  assert.match(JSON.stringify(tokens.new[0]), /first/, 'hunk one lost line 1');
  assert.match(JSON.stringify(tokens.new[79]), /second/, 'hunk two is not at line 80');
  // The gap is left as holes so react-diff-view falls back to raw text
  // instead of rendering those lines empty.
  assert.equal(tokens.new[40], undefined, 'gap lines should be holes, not empty token lists');
});
