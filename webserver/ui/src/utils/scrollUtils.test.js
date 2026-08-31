import assert from 'node:assert/strict';
import test from 'node:test';

import { STICK_THRESHOLD_PX, anchoredScrollTop, isNearTop, isPinnedToBottom, scrollToBottom, stickToBottomIfPinned } from './scrollUtils.js';

// Fake scroll container — just the three metrics the helpers read.
function node({ scrollHeight, clientHeight, scrollTop }) {
  return { scrollHeight, clientHeight, scrollTop };
}

test('isPinnedToBottom: exactly at the bottom is pinned', function () {
  assert.equal(
    isPinnedToBottom(node({ scrollHeight: 1000, clientHeight: 400, scrollTop: 600 })),
    true,
  );
});

test('isPinnedToBottom: within the slack threshold is still pinned', function () {
  // distance = 1000 - 400 - (600 - threshold) = threshold
  const scrollTop = 600 - STICK_THRESHOLD_PX;
  assert.equal(
    isPinnedToBottom(node({ scrollHeight: 1000, clientHeight: 400, scrollTop })),
    true,
  );
});

test('isPinnedToBottom: scrolled up beyond the slack is NOT pinned', function () {
  assert.equal(
    isPinnedToBottom(node({ scrollHeight: 1000, clientHeight: 400, scrollTop: 200 })),
    false,
  );
});

test('isPinnedToBottom: null node defaults to pinned (initial mount)', function () {
  assert.equal(isPinnedToBottom(null), true);
});

test('scrollToBottom sets scrollTop to scrollHeight', function () {
  const n = node({ scrollHeight: 1234, clientHeight: 400, scrollTop: 0 });
  scrollToBottom(n);
  assert.equal(n.scrollTop, 1234);
});

test('scrollToBottom on null is a safe no-op', function () {
  assert.doesNotThrow(() => scrollToBottom(null));
});

test('stickToBottomIfPinned scrolls when pinned and reports true', function () {
  const n = node({ scrollHeight: 1000, clientHeight: 400, scrollTop: 590 });
  const scrolled = stickToBottomIfPinned(n);
  assert.equal(scrolled, true);
  assert.equal(n.scrollTop, 1000);
});

test('stickToBottomIfPinned leaves position alone when scrolled up', function () {
  const n = node({ scrollHeight: 1000, clientHeight: 400, scrollTop: 100 });
  const scrolled = stickToBottomIfPinned(n);
  assert.equal(scrolled, false);
  assert.equal(n.scrollTop, 100); // untouched — operator is reading history
});

test('stickToBottomIfPinned on null is a safe no-op', function () {
  assert.equal(stickToBottomIfPinned(null), false);
});

// ---------------------------------------------------------------------------
// Reading upward: reveal older history without moving the text being read.
// ---------------------------------------------------------------------------

test('isNearTop is true only within the threshold', () => {
  assert.equal(isNearTop({ scrollTop: 0 }), true);
  assert.equal(isNearTop({ scrollTop: 50 }), true);
  assert.equal(isNearTop({ scrollTop: 51 }), false);
  assert.equal(isNearTop({ scrollTop: 4000 }), false);
});

test('isNearTop tolerates a missing node', () => {
  // A null container is "not near the top" — never trigger a reveal on a
  // log that is not mounted.
  assert.equal(isNearTop(null), false);
});

test('anchoredScrollTop shifts by exactly the height that was added', () => {
  // THE BUG. Prepending 800px moves every existing pixel down by 800. Leaving
  // scrollTop at 120 keeps the OFFSET, not the place — and since 120 < 800,
  // the browser clamps and the operator lands at the very top.
  assert.equal(anchoredScrollTop(120, 1000, 1800), 920);
});

test('anchoredScrollTop leaves the position alone when nothing grew', () => {
  assert.equal(anchoredScrollTop(120, 1000, 1000), 120);
});

test('anchoredScrollTop never scrolls backwards on a shrink', () => {
  // Content can shrink under it (a collapsing tool block). Applying a
  // negative delta would yank the reader upward for no reason.
  assert.equal(anchoredScrollTop(120, 1000, 600), 120);
});

test('anchoredScrollTop survives missing geometry', () => {
  assert.equal(anchoredScrollTop(undefined, undefined, undefined), 0);
  assert.equal(anchoredScrollTop(50, NaN, 900), 50);
});
