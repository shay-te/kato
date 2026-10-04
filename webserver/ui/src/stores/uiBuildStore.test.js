// Does this window know it is running an old bundle?
//
// The store only answers WHETHER. These pin the two ways that answer could be
// wrong: calling a window stale when it cannot know (no id to compare), and
// acting on a build that is still being written.

import test from 'node:test';
import assert from 'node:assert/strict';

import { uiBuildStore, STABLE_MS } from './uiBuildStore.js';

function fresh(loaded) {
  uiBuildStore.resetForTest(loaded);
  const seen = [];
  const unsubscribe = uiBuildStore.subscribe((value) => seen.push(value));
  return { seen, unsubscribe };
}

test('a window on the current build is never stale', () => {
  const { seen, unsubscribe } = fresh('100-100-100');
  uiBuildStore.observe('100-100-100', 0);
  uiBuildStore.observe('100-100-100', STABLE_MS * 5);
  assert.equal(uiBuildStore.staleFor(), '');
  assert.deepEqual(seen, ['']);          // only the fire-on-subscribe
  unsubscribe();
});

test('a different build becomes stale only once it has stopped changing', () => {
  // A rebuild writes its files seconds apart, so the id moves more than once
  // on the way. Reloading on the first change would load a half-written
  // bundle and then reload again for the rest of it.
  const { seen, unsubscribe } = fresh('100-100-100');
  uiBuildStore.observe('200-100-100', 0);            // app.js written
  assert.equal(uiBuildStore.staleFor(), '');
  uiBuildStore.observe('200-200-100', 3000);         // then the vite css
  uiBuildStore.observe('200-200-200', 5000);         // then the sass css
  uiBuildStore.observe('200-200-200', 5000 + STABLE_MS - 1);
  assert.equal(uiBuildStore.staleFor(), '');
  uiBuildStore.observe('200-200-200', 5000 + STABLE_MS);
  assert.equal(uiBuildStore.staleFor(), '200-200-200');
  assert.deepEqual(seen, ['', '200-200-200']);
  unsubscribe();
});

test('staying stale does not notify again on every poll', () => {
  const { seen, unsubscribe } = fresh('100');
  uiBuildStore.observe('200', 0);
  uiBuildStore.observe('200', STABLE_MS);
  uiBuildStore.observe('200', STABLE_MS * 2);
  uiBuildStore.observe('200', STABLE_MS * 3);
  assert.deepEqual(seen, ['', '200']);
  unsubscribe();
});

test('a newer build while already stale replaces the target, after settling', () => {
  const { unsubscribe } = fresh('100');
  uiBuildStore.observe('200', 0);
  uiBuildStore.observe('200', STABLE_MS);
  uiBuildStore.observe('300', STABLE_MS + 1000);
  assert.equal(uiBuildStore.staleFor(), '200');
  uiBuildStore.observe('300', STABLE_MS * 2 + 1000);
  assert.equal(uiBuildStore.staleFor(), '300');
  unsubscribe();
});

test('the bundle going back to what this window runs clears the staleness', () => {
  const { seen, unsubscribe } = fresh('100');
  uiBuildStore.observe('200', 0);
  uiBuildStore.observe('200', STABLE_MS);
  uiBuildStore.observe('100', STABLE_MS + 1);
  assert.equal(uiBuildStore.staleFor(), '');
  assert.deepEqual(seen, ['', '200', '']);
  unsubscribe();
});

test('with nothing to compare, the window is never called stale', () => {
  // An older server sends no header; an older page carries no <meta>.
  fresh('100');
  for (const missing of ['', null, undefined, '   ']) {
    uiBuildStore.observe(missing, 0);
    uiBuildStore.observe(missing, STABLE_MS * 2);
  }
  assert.equal(uiBuildStore.staleFor(), '');

  fresh('');
  uiBuildStore.observe('200', 0);
  uiBuildStore.observe('200', STABLE_MS * 2);
  assert.equal(uiBuildStore.staleFor(), '');
});
