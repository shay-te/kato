import { test, beforeEach, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';

import {
  HOVER_EXPAND_CLOSE_GRACE_MS,
  HOVER_EXPAND_OPEN_DELAY_MS,
  createHoverExpand,
} from './hoverExpand.js';

beforeEach(() => { mock.timers.enable({ apis: ['setTimeout'] }); });
afterEach(() => { mock.timers.reset(); });

test('a pointer that only passes over does not open it', () => {
  const panel = createHoverExpand();
  panel.openSoon();
  mock.timers.tick(HOVER_EXPAND_OPEN_DELAY_MS - 1);
  panel.closeSoon();
  mock.timers.tick(HOVER_EXPAND_OPEN_DELAY_MS + HOVER_EXPAND_CLOSE_GRACE_MS);
  assert.equal(panel.isOpen(), false);
});

test('it opens once pointed at for the delay, and closes after the grace once left', () => {
  const panel = createHoverExpand();
  const seen = [];
  panel.subscribe((open) => seen.push(open));
  panel.openSoon();
  mock.timers.tick(HOVER_EXPAND_OPEN_DELAY_MS);
  assert.equal(panel.isOpen(), true);
  panel.closeSoon();
  mock.timers.tick(HOVER_EXPAND_CLOSE_GRACE_MS - 1);
  assert.equal(panel.isOpen(), true);
  mock.timers.tick(1);
  assert.equal(panel.isOpen(), false);
  assert.deepEqual(seen, [false, true, false]); // the replay on subscribe, then each change
});

test('coming back within the grace keeps it open', () => {
  const panel = createHoverExpand();
  panel.open();
  panel.closeSoon();
  mock.timers.tick(HOVER_EXPAND_CLOSE_GRACE_MS - 1);
  panel.openSoon();
  mock.timers.tick(HOVER_EXPAND_CLOSE_GRACE_MS * 2);
  assert.equal(panel.isOpen(), true);
});

test('open and close act at once and call off anything pending', () => {
  const panel = createHoverExpand({ openDelayMs: 50, closeGraceMs: 50 });
  panel.openSoon();
  panel.close();
  mock.timers.tick(100);
  assert.equal(panel.isOpen(), false);
  panel.open();
  assert.equal(panel.isOpen(), true);
});

test('two panels are independent', () => {
  const a = createHoverExpand();
  const b = createHoverExpand();
  a.open();
  assert.equal(b.isOpen(), false);
});
