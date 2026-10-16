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
//
// A row leads with its TOKEN total, and its bar is one plain length: this task's
// tokens against the biggest task's (the longest bar). It is not progress toward
// a limit — kato knows of none — and it is not split into colours, because the
// split is nearly the same for every task (cache re-reads are ~96% of the
// tokens), so the colours only made one thin sliver to puzzle over. The split is
// written out beside the bar and itemised in the details.
//
// Dollars are the DETAILS: the server prices every message at its own model's
// published rate (``usage.cost``, in the same three groups) because the groups
// cost wildly different amounts per token. It is an API-equivalent ESTIMATE —
// a subscription is not billed per token — and it is optional: a server that
// does not price yet sends no ``cost``, and the dialog simply omits it.

export const USAGE_SORTS = [
  { id: 'total', label: 'Most tokens', pick: (task) => task.usage.total },
  { id: 'cost', label: 'Highest cost', pick: (task) => usageCostParts(task.usage).total },
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

// The dollars behind one usage record, in the same three groups as the tokens.
// A record from a server that does not price yet reads as zero, not as NaN.
export function usageCostParts(usage) {
  const cost = usage.cost || {};
  return {
    output: cost.output || 0,
    fresh: cost.fresh || 0,
    cacheRead: cost.cache_read || 0,
    total: cost.total || 0,
  };
}

// How long a task's bar is, as a percentage of the track: its tokens against
// the biggest task's. The biggest fills the track, so two bars compare. A task
// with no tokens, or a list with none, draws nothing.
export function usageBarPercent(usage, maxTotal) {
  if (!(maxTotal > 0) || !(usage.total > 0)) { return 0; }
  return (usage.total / maxTotal) * 100;
}

// A task's share of everything counted, as a whole percent — "<1%" for a real
// but tiny share rather than a zero.
export function usageShare(usage, grandTotal) {
  if (!(grandTotal > 0) || !(usage.total > 0)) { return '0%'; }
  const percent = (usage.total / grandTotal) * 100;
  return percent < 1 ? '<1%' : `${Math.round(percent)}%`;
}

// Whether the server priced this record at all. An older server sends no
// ``cost``; the dialog then leaves dollars out instead of showing a false $0.
export function hasCost(usage) {
  return Boolean(usage.cost) && typeof usage.cost === 'object';
}

// "$0.43", "$12.40", "$1,234" — and "<$0.01" rather than a zero for a real,
// tiny cost, so a task that did something never reads as free.
export function formatCost(dollars) {
  const value = Number(dollars) || 0;
  if (value <= 0) { return '$0'; }
  if (value < 0.01) { return '<$0.01'; }
  if (value < 100) { return `$${value.toFixed(2)}`; }
  return `$${Math.round(value).toLocaleString('en-US')}`;
}

// The figure a row or tile shows. When a model has no price on file its tokens
// are counted but its cost is not, so the total is a floor: "$12.40+", and "—"
// when nothing at all could be priced. Never a confident zero.
export function costLabel(usage) {
  const partial = (usage.unpriced_models || []).length > 0;
  const total = usageCostParts(usage).total;
  if (partial && total <= 0) { return '—'; }
  return partial ? `${formatCost(total)}+` : formatCost(total);
}

// The unpriced models by the names the server gave them.
export function unpricedLabels(usage) {
  return (usage.unpriced_models || []).map((id) => usage.models?.[id]?.label || id);
}

// The models a task ran on, in the order it first used them — so a switch
// reads as a sequence ("Opus 5.5 → Sonnet 5.5"). Never reformats a model id:
// the server names each one (``label``).
export function usageModels(usage) {
  const entries = Object.entries(usage.models || {});
  // A task that ran on ONE model used it for exactly the task's span, so an
  // older server that sends times only per task still has the right answer.
  const solo = entries.length === 1;
  return entries
    .map(([id, slot]) => ({
      id,
      label: slot.label || id || 'unknown',
      priced: slot.priced !== false,
      turns: slot.turns || 0,
      output: slot.output || 0,
      fresh: (slot.input || 0) + (slot.cache_write || 0),
      cacheRead: slot.cache_read || 0,
      total: slot.total || 0,
      cost: slot.cost?.total || 0,
      firstAt: slot.first_at || (solo ? usage.first_at : '') || '',
      lastAt: slot.last_at || (solo ? usage.last_at : '') || '',
    }))
    .sort((left, right) => (
      compareFirstUse(left.firstAt, right.firstAt)
      || right.total - left.total
      || left.id.localeCompare(right.id)
    ));
}

// Whether any model has a time to show — otherwise the "Used" column would be
// a column of blanks, and is left out.
export function hasModelSpans(models) {
  return models.some((model) => Boolean(model.firstAt || model.lastAt));
}

// A model with no timestamp sorts after the ones that have one.
function compareFirstUse(left, right) {
  if (left === right) { return 0; }
  if (!left) { return 1; }
  if (!right) { return -1; }
  return left < right ? -1 : 1;
}

// The first few models for a row's chips, and how many are left out.
export function modelChips(usage, limit = 3) {
  const models = usageModels(usage);
  return { shown: models.slice(0, limit), more: Math.max(0, models.length - limit) };
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

// "5d ago → 2d ago" for how long a model was used; one side when they match,
// '' when neither is known.
export function formatSpan(firstIso, lastIso, nowMs = Date.now()) {
  const from = formatWhen(firstIso, nowMs);
  const to = formatWhen(lastIso, nowMs);
  if (!from || from === to) { return to || from; }
  return to ? `${from} → ${to}` : from;
}
