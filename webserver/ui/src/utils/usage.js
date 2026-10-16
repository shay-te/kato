// What the Usage dialog needs to read a list of per-task token totals.
//
// The server sends each task's totals as four raw counters (input, output,
// cache_read, cache_write). Three of them mean different things to the person
// reading, so they are regrouped here, once, rather than per component:
//
//   output      what the model WROTE
//   fresh       what was sent to it new: fresh input plus cache writes
//   cache read  context the model re-read on every turn
//
// Cache reads are usually >90% of the raw total, because each turn re-reads
// the whole conversation. Folding them into "input" would make every task look
// like it fed the model a billion tokens of new material; keeping them apart
// is the whole point of the breakdown.

export const USAGE_SORTS = [
  { id: 'total', label: 'Most tokens', pick: (task) => task.usage.total },
  { id: 'output', label: 'Most output', pick: (task) => task.usage.output },
  { id: 'recent', label: 'Most recent', pick: (task) => Date.parse(task.usage.last_at) || 0 },
  { id: 'turns', label: 'Most turns', pick: (task) => task.usage.turns },
];

const DEFAULT_SORT = USAGE_SORTS[0];

// Largest first. Ties fall back to the task id so the order never shuffles
// between two refreshes of identical data.
export function sortUsageTasks(tasks, sortId) {
  const sort = USAGE_SORTS.find((entry) => entry.id === sortId) || DEFAULT_SORT;
  return [...(tasks || [])].sort((left, right) => {
    const delta = sort.pick(right) - sort.pick(left);
    if (delta !== 0) { return delta; }
    return String(left.task_id).localeCompare(String(right.task_id));
  });
}

// The three groups a reader cares about, from one usage record.
export function usageParts(usage) {
  return {
    output: usage.output,
    fresh: usage.input + usage.cache_write,
    cacheRead: usage.cache_read,
    total: usage.total,
  };
}

// Bar geometry, as percentages of the track. The whole bar is scaled to the
// busiest task (so two bars compare), and each segment takes its share of that
// task's own total.
export function usageBar(usage, maxTotal) {
  if (!(maxTotal > 0) || !(usage.total > 0)) {
    return { output: 0, fresh: 0, cacheRead: 0 };
  }
  const parts = usageParts(usage);
  const scale = (usage.total / maxTotal) * 100 / usage.total;
  return {
    output: parts.output * scale,
    fresh: parts.fresh * scale,
    cacheRead: parts.cacheRead * scale,
  };
}

export function formatCount(value) {
  return Number(value || 0).toLocaleString('en-US');
}

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;

// "just now" / "5m ago" / "3h ago" / "4d ago", then the date. '' for a task
// that has no timestamp (never ran), rather than inventing one.
export function formatWhen(iso, nowMs = Date.now()) {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) { return ''; }
  const seconds = Math.max(0, Math.round((nowMs - then) / 1000));
  if (seconds < MINUTE) { return 'just now'; }
  if (seconds < HOUR) { return `${Math.floor(seconds / MINUTE)}m ago`; }
  if (seconds < DAY) { return `${Math.floor(seconds / HOUR)}h ago`; }
  if (seconds < 30 * DAY) { return `${Math.floor(seconds / DAY)}d ago`; }
  return new Date(then).toISOString().slice(0, 10);
}
