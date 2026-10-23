import assert from 'node:assert/strict';
import test from 'node:test';

import {
  costLabel, formatCost, formatCount, formatDay, formatSpan, formatWhen, hasCost, hasModelSpans,
  modelChips, sortUsageTasks, taskCreatedDay, unpricedLabels, usageBarPercent, usageCostParts,
  usageModels, usageParts, usageRowKey, usageShare, visibleUsage,
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

// A cost record: ``total`` dollars, all of it in the group named (default: cache reads).
function cost(total, group = 'cache_read') {
  return { output: 0, fresh: 0, cache_read: 0, [group]: total, total };
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

test('ties keep a stable order and an unknown sort falls back to tokens', function () {
  // The row leads with tokens, so tokens is the order a person expects by default.
  const tasks = [
    task('B', { total: 5, cost: cost(9) }),
    task('A', { total: 5, cost: cost(9) }),
    task('C', { total: 9, cost: cost(1) }),
  ];
  assert.deepEqual(sortUsageTasks(tasks, 'nonsense').map((t) => t.task_id), ['C', 'A', 'B']);
  assert.deepEqual(sortUsageTasks(null, 'total'), []);
});

test('sorts by estimated cost, which is not the same order as tokens', function () {
  // The token-heavy task is mostly cheap cache reads; the other wrote more.
  const tasks = [
    task('tokens', { total: 900, cost: cost(2) }),
    task('dollars', { total: 100, cost: cost(40) }),
    task('unpriced', { total: 500 }),
  ];
  assert.deepEqual(sortUsageTasks(tasks, 'cost').map((t) => t.task_id), ['dollars', 'tokens', 'unpriced']);
  assert.deepEqual(sortUsageTasks(tasks, 'total').map((t) => t.task_id), ['tokens', 'unpriced', 'dollars']);
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

test('the biggest task fills the bar and the others scale against it', function () {
  assert.equal(usageBarPercent({ total: 572 }, 572), 100);
  assert.equal(usageBarPercent({ total: 286 }, 572), 50);
  assert.equal(usageBarPercent({ total: 143 }, 572), 25);
});

test('the bar is tokens only: it neither needs nor reads a cost', function () {
  // A server that does not price sends no cost; the bar must still be drawn.
  assert.equal(usageBarPercent({ total: 50 }, 100), 50);
  assert.equal(usageBarPercent({ total: 50, cost: cost(9999) }, 100), 50);
});

test('an empty task, or a list with nothing in it, draws no bar', function () {
  assert.equal(usageBarPercent({ total: 0 }, 100), 0);
  assert.equal(usageBarPercent({ total: 10 }, 0), 0);
  assert.equal(usageBarPercent({}, 100), 0);
});

test('a task share is a whole percent of everything counted, never a false zero', function () {
  assert.equal(usageShare({ total: 572 }, 2200), '26%');
  assert.equal(usageShare({ total: 1100 }, 2200), '50%');
  assert.equal(usageShare({ total: 1 }, 2200), '<1%');
  assert.equal(usageShare({ total: 0 }, 2200), '0%');
  assert.equal(usageShare({ total: 5 }, 0), '0%');
});

test('cost is only shown when the server priced the record', function () {
  assert.equal(hasCost({ cost: { total: 0 } }), true);
  assert.equal(hasCost({}), false);
  assert.equal(hasCost({ cost: null }), false);
  assert.equal(hasCost({ cost: 12 }), false);
});

test('cost parts read a record that has no cost as zero, not NaN', function () {
  assert.deepEqual(usageCostParts({}), { output: 0, fresh: 0, cacheRead: 0, total: 0 });
  assert.deepEqual(
    usageCostParts({ cost: { output: 1, fresh: 2, cache_read: 3, total: 6 } }),
    { output: 1, fresh: 2, cacheRead: 3, total: 6 },
  );
});

test('dollars read as cents under a hundred, whole dollars above, and never a false zero', function () {
  assert.equal(formatCost(0), '$0');
  assert.equal(formatCost(undefined), '$0');
  assert.equal(formatCost(0.004), '<$0.01');
  assert.equal(formatCost(0.43), '$0.43');
  assert.equal(formatCost(12.4), '$12.40');
  assert.equal(formatCost(99.994), '$99.99');
  assert.equal(formatCost(191.6), '$192');
  assert.equal(formatCost(1234.5), '$1,235');
});

test('a total that leaves a model out is shown as a minimum, never a confident figure', function () {
  assert.equal(costLabel({ cost: { total: 12.4 } }), '$12.40');
  assert.equal(costLabel({ cost: { total: 12.4 }, unpriced_models: ['claude-x'] }), '$12.40+');
  assert.equal(costLabel({ cost: { total: 0 }, unpriced_models: ['claude-x'] }), '—');
  assert.equal(costLabel({}), '$0');
});

test('unpriced models are named the way the server named them', function () {
  const usage = {
    unpriced_models: ['claude-x', 'claude-y'],
    models: { 'claude-x': { label: 'X 1' } },
  };
  assert.deepEqual(unpricedLabels(usage), ['X 1', 'claude-y']);
  assert.deepEqual(unpricedLabels({}), []);
});

test('models are listed in the order the task first used them, so a switch reads as a sequence', function () {
  const usage = {
    models: {
      'claude-sonnet-5-5': {
        label: 'Sonnet 5.5', turns: 579, output: 70, input: 3, cache_write: 4, cache_read: 900,
        total: 977, first_at: '2026-10-02T09:00:00Z', last_at: '2026-10-03T09:00:00Z',
        cost: { total: 8 },
      },
      'claude-opus-5-5': {
        label: 'Opus 5.5', turns: 420, output: 30, input: 1, cache_write: 2, cache_read: 500,
        total: 533, first_at: '2026-10-01T09:00:00Z', last_at: '2026-10-02T08:00:00Z',
        cost: { total: 20 }, priced: true,
      },
    },
  };
  const models = usageModels(usage);
  assert.deepEqual(models.map((m) => m.label), ['Opus 5.5', 'Sonnet 5.5']);
  assert.deepEqual(models[0], {
    id: 'claude-opus-5-5', label: 'Opus 5.5', priced: true, turns: 420, output: 30,
    fresh: 3, cacheRead: 500, total: 533, cost: 20,
    firstAt: '2026-10-01T09:00:00Z', lastAt: '2026-10-02T08:00:00Z',
  });
});

test('a model with no timestamp goes last, and a missing label falls back to the id', function () {
  const models = usageModels({
    models: {
      b: { turns: 1, total: 1 },
      a: { turns: 1, total: 99 },
      c: { turns: 1, total: 5, first_at: '2026-10-01T00:00:00Z' },
      '': { turns: 1, total: 1, first_at: '2026-10-02T00:00:00Z' },
    },
  });
  // Timed ones first (oldest first), then the untimed by size.
  assert.deepEqual(models.map((m) => m.id), ['c', '', 'a', 'b']);
  assert.equal(models.find((m) => m.id === '').label, 'unknown');
  assert.equal(models.find((m) => m.id === 'a').label, 'a');
});

test('a task on ONE model uses the task span when the server sent none per model', function () {
  // An older server sends first_at / last_at only per task; with one model the
  // model's span IS the task's span, so the "Used" cell is not left blank.
  const [only] = usageModels({
    first_at: '2026-10-10T00:00:00Z',
    last_at: '2026-10-12T00:00:00Z',
    models: { 'claude-opus-5-5': { turns: 999, total: 1000 } },
  });
  assert.equal(only.firstAt, '2026-10-10T00:00:00Z');
  assert.equal(only.lastAt, '2026-10-12T00:00:00Z');
});

test('the model\'s own times win over the task\'s', function () {
  const [only] = usageModels({
    first_at: '2026-10-01T00:00:00Z',
    last_at: '2026-10-30T00:00:00Z',
    models: { m: { turns: 1, first_at: '2026-10-10T00:00:00Z', last_at: '2026-10-12T00:00:00Z' } },
  });
  assert.equal(only.firstAt, '2026-10-10T00:00:00Z');
  assert.equal(only.lastAt, '2026-10-12T00:00:00Z');
});

test('with several models and no times per model, none is guessed from the task', function () {
  const models = usageModels({
    first_at: '2026-10-10T00:00:00Z',
    last_at: '2026-10-12T00:00:00Z',
    models: { a: { turns: 1, total: 2 }, b: { turns: 1, total: 1 } },
  });
  assert.deepEqual(models.map((m) => [m.firstAt, m.lastAt]), [['', ''], ['', '']]);
});

test('the Used column is only worth showing when some model has a time', function () {
  assert.equal(hasModelSpans([]), false);
  assert.equal(hasModelSpans([{ firstAt: '', lastAt: '' }]), false);
  assert.equal(hasModelSpans([{ firstAt: '', lastAt: '' }, { firstAt: '2026-10-10T00:00:00Z', lastAt: '' }]), true);
  assert.equal(hasModelSpans([{ firstAt: '', lastAt: '2026-10-10T00:00:00Z' }]), true);
});

test('a model is priced unless the server says it is not', function () {
  const models = usageModels({
    models: { known: { turns: 1 }, odd: { turns: 1, priced: false } },
  });
  assert.equal(models.find((m) => m.id === 'known').priced, true);
  assert.equal(models.find((m) => m.id === 'odd').priced, false);
});

test('a row shows the first few models and counts the rest', function () {
  const models = {};
  for (let index = 1; index <= 5; index += 1) {
    models[`m${index}`] = { turns: 1, total: 1, first_at: `2026-10-0${index}T00:00:00Z` };
  }
  const chips = modelChips({ models });
  assert.deepEqual(chips.shown.map((m) => m.id), ['m1', 'm2', 'm3']);
  assert.equal(chips.more, 2);
  assert.deepEqual(modelChips({ models }, 5).more, 0);
  assert.deepEqual(modelChips({}), { shown: [], more: 0 });
});

test('how long a model was used reads as a span, or one side when they match', function () {
  const now = Date.parse('2026-10-16T12:00:00Z');
  assert.equal(formatSpan('2026-10-13T12:00:00Z', '2026-10-16T11:00:00Z', now), '3d ago → 1h ago');
  assert.equal(formatSpan('2026-10-16T11:59:40Z', '2026-10-16T11:59:50Z', now), 'just now');
  assert.equal(formatSpan('', '2026-10-13T12:00:00Z', now), '3d ago');
  assert.equal(formatSpan('2026-10-13T12:00:00Z', '', now), '3d ago');
  assert.equal(formatSpan('', '', now), '');
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

// ----- when a task was made, and the tasks that were deleted -----

test('sorts by when the task was created, newest first', function () {
  const tasks = [
    { ...task('old', { first_at: '2026-10-09T00:00:00Z' }), created_at: '2026-08-01T00:00:00Z' },
    { ...task('new', {}), created_at: '2026-10-01T00:00:00Z' },
    task('unknown', {}),
  ];
  assert.deepEqual(sortUsageTasks(tasks, 'created').map((t) => t.task_id), ['new', 'old', 'unknown']);
});

test('a task the server gave no creation date falls back to its first turn', function () {
  const old = task('A', { first_at: '2026-09-01T00:00:00Z' });
  const newer = task('B', { first_at: '2026-09-20T00:00:00Z' });
  assert.deepEqual(sortUsageTasks([old, newer], 'created').map((t) => t.task_id), ['B', 'A']);
  assert.equal(taskCreatedDay(old), '2026-09-01');
});

test('a day is written absolutely, and is blank when there is no usable stamp', function () {
  assert.equal(formatDay('2026-09-28T23:59:59Z'), '2026-09-28');
  assert.equal(formatDay(''), '');
  assert.equal(formatDay('not a date'), '');
  assert.equal(taskCreatedDay(task('A', {})), '');
  assert.equal(
    taskCreatedDay({ ...task('A', { first_at: '2026-01-01T00:00:00Z' }), created_at: '2026-09-28T01:00:00Z' }),
    '2026-09-28',
  );
});

test('a deleted task and a live one with the same id are different rows', function () {
  const live = task('UNA-1', {});
  const gone = { ...task('UNA-1', {}), deleted_at: '2026-10-10T00:00:00Z' };
  assert.equal(usageRowKey(live), 'UNA-1');
  assert.notEqual(usageRowKey(gone), usageRowKey(live));
  assert.notEqual(
    usageRowKey(gone),
    usageRowKey({ ...gone, deleted_at: '2026-10-11T00:00:00Z' }),
  );
});

test('deleted tasks are in the list and the totals only when asked for', function () {
  const data = {
    tasks: [task('LIVE', { total: 10 })],
    deleted_tasks: [{ ...task('GONE', { total: 90 }), deleted_at: '2026-10-10T00:00:00Z' }],
    totals: { total: 10 },
    totals_with_deleted: { total: 100 },
  };
  const without = visibleUsage(data, false);
  assert.deepEqual(without.tasks.map((t) => t.task_id), ['LIVE']);
  assert.equal(without.totals.total, 10);
  const withDeleted = visibleUsage(data, true);
  assert.deepEqual(withDeleted.tasks.map((t) => t.task_id), ['LIVE', 'GONE']);
  assert.equal(withDeleted.totals.total, 100);
});

test('an older server that sends no history is still read', function () {
  const data = { tasks: [task('LIVE', { total: 10 })], totals: { total: 10 } };
  assert.deepEqual(visibleUsage(data, true).tasks.map((t) => t.task_id), ['LIVE']);
  assert.equal(visibleUsage(data, true).totals.total, 10);
  assert.deepEqual(visibleUsage(null, true), { tasks: [], totals: undefined });
});
