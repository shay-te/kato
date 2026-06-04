import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  TOOL_DETAILS_HARD_CAP,
  computeEventLogWindow,
  computeToolDetailsRender,
} from './eventLogTruncation.js';

// These rules cap the worst-case DOM rendered by EventLog +
// ToolDetails. Pinned here so a future tweak (lower the threshold,
// raise the cap, change the unit) can't silently regress operator
// performance on long sessions or huge tool outputs.

test('computeToolDetailsRender keeps short output intact at any expansion state', () => {
  const lines = ['a', 'b', 'c'];
  assert.deepEqual(
    computeToolDetailsRender(lines, false),
    { visible: lines, overflowed: false },
  );
  assert.deepEqual(
    computeToolDetailsRender(lines, true),
    { visible: lines, overflowed: false },
  );
});

test('computeToolDetailsRender collapses to head when not expanded and over threshold', () => {
  const lines = Array.from({ length: 100 }, (_, i) => `line ${i}`);
  const result = computeToolDetailsRender(lines, false);
  assert.equal(result.visible.length, 40);
  assert.equal(result.visible[0], 'line 0');
  assert.equal(result.visible[39], 'line 39');
  assert.equal(result.overflowed, false);
});

test('computeToolDetailsRender hard-caps even when expanded so massive output cannot lock the browser', () => {
  const lines = Array.from({ length: 5000 }, (_, i) => `line ${i}`);
  const result = computeToolDetailsRender(lines, true);
  // Hard cap is 1000 — we never render more even with the operator
  // having clicked "show full output".
  assert.equal(result.visible.length, TOOL_DETAILS_HARD_CAP);
  assert.equal(result.overflowed, true);
});

test('computeToolDetailsRender does not flag overflow when expanded output fits under the cap', () => {
  const lines = Array.from({ length: 500 }, (_, i) => `line ${i}`);
  const result = computeToolDetailsRender(lines, true);
  assert.equal(result.visible.length, 500);
  assert.equal(result.overflowed, false);
});

test('computeEventLogWindow returns full list under threshold', () => {
  const entries = Array.from({ length: 50 }, (_, i) => ({ i }));
  const result = computeEventLogWindow(entries, false);
  assert.equal(result.visible.length, 50);
  assert.equal(result.hidden, 0);
});

test('computeEventLogWindow shows the most recent window when not showing all', () => {
  const entries = Array.from({ length: 1000 }, (_, i) => ({ i }));
  const result = computeEventLogWindow(entries, false);
  // Default window is the trailing 200 — i.e. the operator sees the
  // newest events, which is what a chat scrolled to bottom expects.
  assert.equal(result.visible.length, 200);
  assert.equal(result.visible[0].i, 800);
  assert.equal(result.visible[199].i, 999);
  assert.equal(result.hidden, 800);
});

test('computeEventLogWindow returns full list when showAll is set', () => {
  const entries = Array.from({ length: 1000 }, (_, i) => ({ i }));
  const result = computeEventLogWindow(entries, true);
  assert.equal(result.visible.length, 1000);
  assert.equal(result.hidden, 0);
});

test('computeEventLogWindow snaps the window start back to the latest turn boundary', () => {
  // The latest turn (a prompt + a long run of tool events) opens at index
  // 300 — well before the default 200-tail (start = 800). Without snapping,
  // the window would start mid-turn at 800 and the turn's "YOU ASKED"
  // header would be hidden (the reported "prompt missing until I click
  // show-older" bug). The boundary predicate must pull the start back to 300.
  const entries = Array.from({ length: 1000 }, (_, i) => ({ i, prompt: i === 300 }));
  const result = computeEventLogWindow(entries, false, (e) => !!e.prompt);
  assert.equal(result.visible[0].i, 300);
  assert.equal(result.visible[0].prompt, true);
  assert.equal(result.hidden, 300);
  assert.equal(result.visible.length, 700);
});

test('computeEventLogWindow keeps the plain tail when no boundary precedes the cut', () => {
  // Only prompt is at 950 — already inside the 200-tail. There's no
  // boundary at/before the cut (800), so nothing to snap to: the tail is
  // unchanged and the header at 950 is naturally on screen.
  const entries = Array.from({ length: 1000 }, (_, i) => ({ i, prompt: i === 950 }));
  const result = computeEventLogWindow(entries, false, (e) => !!e.prompt);
  assert.equal(result.visible[0].i, 800);
  assert.equal(result.hidden, 800);
  assert.equal(result.visible.length, 200);
});

test('computeEventLogWindow without a boundary predicate is the plain trailing window', () => {
  // Back-compat: the 2-arg form behaves exactly as before (no snapping).
  const entries = Array.from({ length: 1000 }, (_, i) => ({ i }));
  const result = computeEventLogWindow(entries, false);
  assert.equal(result.visible[0].i, 800);
  assert.equal(result.hidden, 800);
});
