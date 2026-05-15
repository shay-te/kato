import assert from 'node:assert/strict';
import test from 'node:test';

import {
  STICK_THRESHOLD_PX,
  isPinnedToBottom,
  scrollToBottom,
  stickToBottomIfPinned,
} from './scrollUtils.js';

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
