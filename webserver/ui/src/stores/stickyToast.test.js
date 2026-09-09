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

test('a result that needs attention is sticky for every action, not just one', () => {
  // The rule moved INTO toastResult, so Merge / Pull / Push / Finish / Sync
  // all inherit it. It used to be per-caller, and the merge handler was the
  // one that did not opt in — it asked for 6000ms.
  const source = readFileSync(join(ROOT, 'stores/toastStore.js'), 'utf8');
  assert.match(source, /problemMs = 0, defaultMs = 7000/);
  assert.match(
    source,
    /const needsAttention = kind === 'error' \|\| kind === 'warning'/,
  );
  assert.match(source, /durationMs: needsAttention \? problemMs : defaultMs/);
});

test('the Update-source toast asks for no timeout even when it SUCCEEDS', () => {
  const source = readFileSync(
    join(ROOT, 'components/SessionHeader.jsx'), 'utf8',
  );
  const call = source.slice(
    source.indexOf('formatUpdateSourceResult(result), kind'),
  ).slice(0, 200);
  // The failure half is now the store's default. This override is for the
  // other half: a CLEAN sync is still a per-repo record and still the only
  // place it exists.
  assert.match(call, /defaultMs:\s*0/);
});

test('the merge toast goes through toastResult, so it inherits the rule', () => {
  // It used to be a raw toast.show with durationMs 6000 — the one action
  // that opted out, and the one the operator lost a report from.
  const source = readFileSync(
    join(ROOT, 'components/SessionHeader.jsx'), 'utf8',
  );
  assert.match(source, /toastResult\(formatMergeResult\(result, session\.task_id\)\)/);
  assert.doesNotMatch(source, /durationMs: merged\.kind/);
});

test('a CLEAN result still expires', () => {
  // Sticky is for outcomes that need a human, not a new default — a UI full
  // of toasts that never leave is worse than one that vanishes. Successes
  // keep both the plain push default and the toastResult default.
  const source = readFileSync(join(ROOT, 'stores/toastStore.js'), 'utf8');
  assert.match(source, /durationMs = 5000/);
  assert.match(source, /defaultMs = 7000/);
});
