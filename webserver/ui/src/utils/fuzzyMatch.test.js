import assert from 'node:assert/strict';
import test from 'node:test';

import { fuzzyMatches, fuzzyRank, isSubsequence } from './fuzzyMatch.js';

// ---------------------------------------------------------------------------
// The lenient base behaviour every "type a few characters" surface shares
// ---------------------------------------------------------------------------

test('plain substring matches', function () {
  assert.equal(fuzzyMatches('auth', ['src/auth.py']), true);
  assert.equal(fuzzyMatches('nope', ['src/auth.py']), false);
});

test('separator-insensitive subsequence matches', function () {
  assert.equal(fuzzyMatches('fileservice', ['file_service.py']), true);
  assert.equal(fuzzyMatches('una2818', ['UNA-2818']), true);
});

test('isSubsequence checks order, not adjacency', function () {
  assert.equal(isSubsequence('abc', 'axbxc'), true);
  assert.equal(isSubsequence('cba', 'axbxc'), false);
  assert.equal(isSubsequence('', 'anything'), true);
  assert.equal(isSubsequence('toolong', 'short'), false);
});

test('fuzzyRank puts exact above prefix above substring above fuzzy', function () {
  assert.equal(fuzzyRank('auth', 'auth'), 0);
  assert.equal(fuzzyRank('auth', 'auth.py'), 1);
  assert.equal(fuzzyRank('auth', 'src/auth.py'), 2);
  assert.equal(fuzzyRank('auth', 'unrelated'), 3);
  assert.equal(fuzzyRank('', 'anything'), 3);
});

// ---------------------------------------------------------------------------
// The VS Code find-widget narrowings (Match case / Exact)
// ---------------------------------------------------------------------------

test('matchCase makes the comparison case-sensitive', function () {
  assert.equal(fuzzyMatches('Dockerfile', ['dockerfile.md']), true);
  assert.equal(fuzzyMatches('Dockerfile', ['dockerfile.md'], { matchCase: true }), false);
  assert.equal(fuzzyMatches('Dockerfile', ['Dockerfile'], { matchCase: true }), true);
});

test('exact drops the forgiving subsequence half', function () {
  // The subsequence half is what makes "authpy" find auth.py — and also what
  // drags loosely-related paths into a search for one specific file.
  assert.equal(fuzzyMatches('authpy', ['src/auth.py']), true);
  assert.equal(fuzzyMatches('authpy', ['src/auth.py'], { exact: true }), false);
  // A literal substring still matches with exact on.
  assert.equal(fuzzyMatches('auth.py', ['src/auth.py'], { exact: true }), true);
});

test('exact still matches a plain substring anywhere in the path', function () {
  assert.equal(fuzzyMatches('Table/Query', ['src/Table/QueryPhrase.js'], { exact: true }), true);
});

test('both toggles together are case-sensitive AND literal', function () {
  const opts = { matchCase: true, exact: true };
  assert.equal(fuzzyMatches('QueryPhrase', ['src/QueryPhrase.js'], opts), true);
  assert.equal(fuzzyMatches('queryphrase', ['src/QueryPhrase.js'], opts), false);
  assert.equal(fuzzyMatches('qphrase', ['src/QueryPhrase.js'], opts), false);
});

test('an empty term still matches everything with either toggle on', function () {
  assert.equal(fuzzyMatches('', ['anything'], { matchCase: true, exact: true }), true);
  assert.equal(fuzzyMatches('   ', ['anything'], { exact: true }), true);
});

test('defaults are unchanged — every existing caller stays lenient', function () {
  assert.equal(fuzzyMatches('authpy', ['src/auth.py'], {}), true);
  assert.equal(fuzzyMatches('authpy', ['src/auth.py']), true);
  assert.equal(fuzzyMatches('DOCKERFILE', ['dockerfile']), true);
});
