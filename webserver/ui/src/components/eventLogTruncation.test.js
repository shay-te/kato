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

// ---------------------------------------------------------------------------
// Chunked reveal. The window used to be all-or-nothing: a 200-entry tail, or
// EVERY entry the moment the operator clicked "show earlier". On a long
// session that rendered thousands of bubbles in one frame — the freeze that
// made the scroll jump so violent. It grows a chunk at a time now.
// ---------------------------------------------------------------------------

test('computeEventLogWindow accepts a caller-supplied window size', () => {
  const entries = Array.from({ length: 500 }, (_, i) => ({ id: i }));
  const win = computeEventLogWindow(entries, false, null, 300);
  assert.equal(win.visible.length, 300);
  assert.equal(win.hidden, 200);
});

test('a grown window reveals more without reaching the end', () => {
  const entries = Array.from({ length: 500 }, (_, i) => ({ id: i }));
  const first = computeEventLogWindow(entries, false, null, 200);
  const second = computeEventLogWindow(entries, false, null, 300);
  assert.equal(first.hidden, 300);
  assert.equal(second.hidden, 200);
  // Still a window, not the whole history — that is the point of chunking.
  assert.ok(second.visible.length < entries.length);
});

test('growing past the end simply shows everything', () => {
  const entries = Array.from({ length: 500 }, (_, i) => ({ id: i }));
  const win = computeEventLogWindow(entries, false, null, 900);
  assert.equal(win.visible.length, 500);
  assert.equal(win.hidden, 0);
});

test('the newest entries are the ones kept', () => {
  // Revealing older history must extend BACKWARD; the tail is what the
  // operator is reading.
  const entries = Array.from({ length: 500 }, (_, i) => ({ id: i }));
  const win = computeEventLogWindow(entries, false, null, 300);
  assert.equal(win.visible[win.visible.length - 1].id, 499);
  assert.equal(win.visible[0].id, 200);
});

test('an absent or nonsense window size falls back to the default', () => {
  const entries = Array.from({ length: 500 }, (_, i) => ({ id: i }));
  assert.equal(computeEventLogWindow(entries, false).visible.length, 200);
  assert.equal(
    computeEventLogWindow(entries, false, null, 0).visible.length, 200,
  );
  assert.equal(
    computeEventLogWindow(entries, false, null, NaN).visible.length, 200,
  );
});
