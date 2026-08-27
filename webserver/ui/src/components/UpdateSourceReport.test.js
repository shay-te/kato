// The Update-source report is read to answer one question: what didn't work?
//
// It used to answer it last. Successes came first, the one failing repo was
// buried below them, and its raw git output ran unindented straight into the
// next repo's line — so there was no visible boundary between "this repo's
// error" and "the next repo". On a multi-repo task that is a wall of text.

import test from 'node:test';
import assert from 'node:assert/strict';
import { formatUpdateSourceResult } from './sessionHeaderFormatters.js';

const BLOCKED = {
  repository_id: 'ob-love-admin-client',
  blocked: true,
  warning: 'local changes would be overwritten (src/index.js)\n'
    + 'Detail: failed to fast-forward UNA-2959\n'
    + 'error: Your local changes would be overwritten by merge:\n'
    + '    src/index.js',
};

function report(body) {
  return formatUpdateSourceResult({ ok: true, body });
}

test('a blocked repo is the FIRST thing in the message', () => {
  const { message } = report({
    updated: true,
    updated_repositories: ['a', 'b', 'c'],
    warnings: [BLOCKED],
  });
  const firstLine = message.split('\n')[0];
  assert.match(firstLine, /need your attention/);
  assert.ok(
    message.indexOf('ob-love-admin-client') < message.indexOf('source updated'),
    'the failing repo must come before the successes',
  );
});

test('the title carries the verdict on its own', () => {
  // The title is all that survives a glance.
  assert.match(
    report({ updated: true, updated_repositories: ['a'], warnings: [BLOCKED] }).title,
    /partially updated — 1 problem/,
  );
  assert.match(
    report({ updated: false, warnings: [BLOCKED] }).title,
    /not updated — 1 problem/,
  );
  assert.equal(
    report({ updated: true, updated_repositories: ['a'] }).title,
    'Source updated',
  );
});

test('failed repos are counted as problems too, and named', () => {
  const { title, message } = report({
    updated: true,
    updated_repositories: ['a'],
    failed_repositories: [{ repository_id: 'pay-core-lib', error: 'no remote' }],
  });
  assert.match(title, /1 problem/);
  assert.match(message, /✗ pay-core-lib/);
  assert.match(message, /no remote/);
});

test('problems from both sources are counted together', () => {
  const { title } = report({
    updated: true,
    updated_repositories: ['a'],
    warnings: [BLOCKED],
    failed_repositories: [{ repository_id: 'x', error: 'boom' }],
  });
  assert.match(title, /2 problems/);
});

test('raw git output is indented, so it reads as DETAIL', () => {
  const { message } = report({ updated: true, warnings: [BLOCKED] });
  // The headline names the repo; every following detail line is indented,
  // which is what marks where this repo's error ends.
  assert.match(message, /^ {6}Detail: failed to fast-forward/m);
  assert.match(message, /^ {6}error: Your local changes/m);
});

test('an ordinary note is NOT promoted to a problem', () => {
  // "switched X and pulled" is progress, not something to act on.
  const { title, message } = report({
    updated: true,
    updated_repositories: ['a'],
    warnings: [{ repository_id: 'b', warning: 'switched b and pulled' }],
  });
  assert.equal(title, 'Source updated');
  assert.match(message, /• switched b and pulled/);
});

test('a clean run has no attention block at all', () => {
  const { message } = report({
    updated: true, updated_repositories: ['a', 'b'],
  });
  assert.doesNotMatch(message, /needs your attention/);
  assert.match(message, /✓ source updated for 2 repo\(s\)/);
});

test('a run that did nothing still says so', () => {
  assert.match(report({ updated: false }).message, /no source repositories updated/);
});

test('skipped repos stay in the progress half', () => {
  const { message } = report({
    updated: true,
    updated_repositories: ['a'],
    skipped_repositories: [{ repository_id: 'core-lib', reason: 'no changes' }],
  });
  assert.match(message, /• skipped core-lib: no changes/);
});

test('a request-level failure still reports as an error', () => {
  const out = formatUpdateSourceResult({ ok: false, error: 'offline' });
  assert.equal(out.title, 'Update source failed');
});
