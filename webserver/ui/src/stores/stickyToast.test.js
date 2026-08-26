// The Update-source report must not disappear on a timer.
//
// It is a PER-REPO record — updated / skipped / blocked / failed, one line
// each — and on a task with many repos it is the only place that record
// exists. Any timeout can take the answer to "what actually synced?" away
// mid-read. It still closes on click, like every toast.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;

test('a zero duration means the store schedules no dismissal', () => {
  // The mechanism the sticky toast relies on: push() only sets a timer when
  // durationMs is positive.
  const source = readFileSync(join(ROOT, 'stores/toastStore.js'), 'utf8');
  assert.match(source, /if \(durationMs > 0\) \{/);
  assert.match(source, /setTimeout\(\(\) => toastStore\.dismiss\(id\), durationMs\)/);
});

test('toastResult passes its duration straight through per kind', () => {
  // So a caller can make BOTH the success and error variants sticky.
  const source = readFileSync(join(ROOT, 'stores/toastStore.js'), 'utf8');
  assert.match(
    source, /durationMs:\s*kind === 'error' \? errorMs : defaultMs/,
  );
});

test('the Update-source toast asks for no timeout at all', () => {
  const source = readFileSync(
    join(ROOT, 'components/SessionHeader.jsx'), 'utf8',
  );
  const call = source.slice(
    source.indexOf('formatUpdateSourceResult(result), kind'),
  ).slice(0, 200);
  // BOTH kinds — an errored run is exactly when the operator most needs to
  // read which repo failed.
  assert.match(call, /defaultMs:\s*0/);
  assert.match(call, /errorMs:\s*0/);
});

test('every OTHER toast still expires', () => {
  // Sticky is for this one report, not a new default — a UI full of toasts
  // that never leave is worse than one that vanishes.
  const source = readFileSync(join(ROOT, 'stores/toastStore.js'), 'utf8');
  assert.match(source, /durationMs = 5000/);
  assert.match(source, /errorMs = 12000, defaultMs = 7000/);
});
