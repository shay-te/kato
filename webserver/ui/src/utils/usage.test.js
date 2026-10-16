import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatCount, formatWhen, sortUsageTasks, usageBar, usageParts,
} from './usage.js';
import { formatTokens } from './chatCost.js';

function task(id, usage) {
  return {
    task_id: id,
    usage: {
      input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0, turns: 0, last_at: '', ...usage,
    },
  };
}

test('sorts by the chosen column, largest first', function () {
  const tasks = [
    task('A', { total: 10, output: 9, turns: 1 }),
    task('B', { total: 30, output: 1, turns: 2 }),
    task('C', { total: 20, output: 5, turns: 3 }),
  ];
  assert.deepEqual(sortUsageTasks(tasks, 'total').map((t) => t.task_id), ['B', 'C', 'A']);
  assert.deepEqual(sortUsageTasks(tasks, 'output').map((t) => t.task_id), ['A', 'C', 'B']);
  assert.deepEqual(sortUsageTasks(tasks, 'turns').map((t) => t.task_id), ['C', 'B', 'A']);
});

test('sorts by recency using the last timestamp', function () {
  const tasks = [
    task('old', { last_at: '2026-01-01T00:00:00Z' }),
    task('new', { last_at: '2026-10-01T00:00:00Z' }),
    task('never', { last_at: '' }),
  ];
  assert.deepEqual(sortUsageTasks(tasks, 'recent').map((t) => t.task_id), ['new', 'old', 'never']);
});

test('ties keep a stable order and an unknown sort falls back to total', function () {
  const tasks = [task('B', { total: 5 }), task('A', { total: 5 }), task('C', { total: 9 })];
  assert.deepEqual(sortUsageTasks(tasks, 'nonsense').map((t) => t.task_id), ['C', 'A', 'B']);
  assert.deepEqual(sortUsageTasks(null, 'total'), []);
});

test('sorting does not mutate the list it was given', function () {
  const tasks = [task('A', { total: 1 }), task('B', { total: 2 })];
  sortUsageTasks(tasks, 'total');
  assert.deepEqual(tasks.map((t) => t.task_id), ['A', 'B']);
});

test('fresh is new input plus cache writes, and cache reads stay apart', function () {
  const parts = usageParts({
    input: 10, output: 20, cache_read: 1000, cache_write: 5, total: 1035,
  });
  assert.deepEqual(parts, { output: 20, fresh: 15, cacheRead: 1000, total: 1035 });
});

test('the busiest task fills the bar and the others scale against it', function () {
  const busiest = usageBar({
    input: 0, output: 10, cache_read: 90, cache_write: 0, total: 100,
  }, 100);
  assert.equal(busiest.output + busiest.fresh + busiest.cacheRead, 100);
  assert.equal(busiest.output, 10);

  const half = usageBar({
    input: 0, output: 5, cache_read: 45, cache_write: 0, total: 50,
  }, 100);
  assert.equal(half.output + half.fresh + half.cacheRead, 50);
  assert.equal(half.output, 5);
});

test('an empty task, or an empty list, draws no bar', function () {
  const none = { output: 0, fresh: 0, cacheRead: 0 };
  assert.deepEqual(usageBar({ total: 0 }, 100), none);
  assert.deepEqual(usageBar({ total: 10, output: 10 }, 0), none);
});

test('relative time reads naturally and falls back to a date', function () {
  const now = Date.parse('2026-10-16T12:00:00Z');
  assert.equal(formatWhen('2026-10-16T11:59:30Z', now), 'just now');
  assert.equal(formatWhen('2026-10-16T11:45:00Z', now), '15m ago');
  assert.equal(formatWhen('2026-10-16T07:00:00Z', now), '5h ago');
  assert.equal(formatWhen('2026-10-13T12:00:00Z', now), '3d ago');
  assert.equal(formatWhen('2026-07-10T06:19:30Z', now), '2026-07-10');
  assert.equal(formatWhen('', now), '');
  assert.equal(formatWhen('not a date', now), '');
});

test('a timestamp in the future reads as just now, not negative', function () {
  assert.equal(formatWhen('2026-10-17T00:00:00Z', Date.parse('2026-10-16T12:00:00Z')), 'just now');
});

test('counts are grouped and a missing count is zero', function () {
  assert.equal(formatCount(1435175128), '1,435,175,128');
  assert.equal(formatCount(undefined), '0');
});

test('token totals in the billions are shown as B, not a four-digit M', function () {
  assert.equal(formatTokens(1_435_175_128), '1.4B');
  assert.equal(formatTokens(12_000_000_000), '12B');
  // The context meter's window sizes are unchanged.
  assert.equal(formatTokens(1_000_000), '1.0M');
  assert.equal(formatTokens(200_000), '200k');
  assert.equal(formatTokens(950), '950');
});
