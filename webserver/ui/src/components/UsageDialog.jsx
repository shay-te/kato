import { useMemo, useState } from 'react';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { useDelayedFlag } from '../hooks/useDelayedFlag.js';
import { useUsage } from '../hooks/useUsage.js';
import { formatTokens } from '../utils/chatCost.js';
import {
  USAGE_SORTS, formatCount, formatWhen, sortUsageTasks, usageBar, usageParts,
} from '../utils/usage.js';
import { backendLabel } from './AgentBackendChip.jsx';
import ModalShell from './ModalShell.jsx';
import { toggledSet } from '../utils/toggledSet.js';

// How many tokens each task has consumed.
//
// Read from the agent's own transcripts on the host, so it counts every chat a
// task ever had — not just the one that is open. The figures are split three
// ways because they mean different things (see utils/usage.js): what the model
// wrote, what was sent to it fresh, and the context it re-read on every turn.
// The last is usually most of the total and the cheapest, so it is shown apart
// instead of being folded into "input".
export default function UsageDialog({ onClose }) {
  const { status, data, error, reload } = useUsage();
  const [sortId, setSortId] = useState(USAGE_SORTS[0].id);
  const [openIds, setOpenIds] = useState(() => new Set());
  useEscapeKey(onClose);
  // A fast read never paints a loader: this only turns on if it is slow.
  const showLoader = useDelayedFlag(status === 'loading', 250);

  const tasks = useMemo(() => sortUsageTasks(data?.tasks, sortId), [data, sortId]);
  const maxTotal = useMemo(
    () => tasks.reduce((most, task) => Math.max(most, task.usage.total), 0),
    [tasks],
  );

  function toggle(taskId) {
    setOpenIds((prev) => toggledSet(prev, taskId));
  }

  const refreshing = status === 'refreshing' || status === 'loading';

  return (
    <ModalShell ariaLabel="Token usage" title="Usage" extraClass="usage-dialog" onClose={onClose}>
      <div className="usage-body">
        {data ? <UsageTotals totals={data.totals} taskCount={tasks.length} /> : null}

        <div className="usage-toolbar">
          <label className="usage-sort">
            <span>Sort by</span>
            <select value={sortId} onChange={(event) => setSortId(event.target.value)}>
              {USAGE_SORTS.map((sort) => (
                <option key={sort.id} value={sort.id}>{sort.label}</option>
              ))}
            </select>
          </label>
          <button type="button" className="usage-refresh" onClick={reload} disabled={refreshing}>
            {refreshing ? 'Reading…' : 'Refresh'}
          </button>
        </div>

        {status === 'error' ? (
          <p className="usage-error" role="alert">
            Could not read usage: {error}
          </p>
        ) : null}
        {showLoader && !data ? (
          <p className="usage-note" role="status">Reading the agent transcripts…</p>
        ) : null}
        {data && tasks.length === 0 ? (
          <p className="usage-note">No task has used any tokens yet.</p>
        ) : null}

        <ul className="usage-list">
          {tasks.map((task) => (
            <UsageRow
              key={task.task_id}
              task={task}
              maxTotal={maxTotal}
              open={openIds.has(task.task_id)}
              onToggle={() => toggle(task.task_id)}
            />
          ))}
        </ul>

        <p className="usage-foot">
          Counted from the Claude transcripts on this machine, every chat a task has had.
          Cache reads are the conversation being re-read on each turn — they dominate the
          total but are not new material.
          {data?.uncounted_backends?.length
            ? ` ${agentNames(data.uncounted_backends)} chats are not counted.` : ''}
        </p>
      </div>
    </ModalShell>
  );
}

// "codex" -> "Codex", the way the rest of the UI names an agent.
function agentNames(backends) {
  return backends.map((backend) => backendLabel(backend) || backend).join(', ');
}

function UsageTotals({ totals, taskCount }) {
  const parts = usageParts(totals);
  return (
    <div className="usage-totals">
      <Tile label="Total tokens" value={parts.total} hint={`${taskCount} task${taskCount === 1 ? '' : 's'}`} />
      <Tile label="Written by the model" value={parts.output} hint="output" tone="output" />
      <Tile label="Sent as new input" value={parts.fresh} hint="input + cache writes" tone="fresh" />
      <Tile label="Re-read from cache" value={parts.cacheRead} hint="context, every turn" tone="cache" />
    </div>
  );
}

function Tile({ label, value, hint, tone = '' }) {
  return (
    <div className={`usage-tile${tone ? ` usage-tile--${tone}` : ''}`}>
      <span className="usage-tile-label">{label}</span>
      <strong className="usage-tile-value" title={formatCount(value)}>{formatTokens(value)}</strong>
      <span className="usage-tile-hint">{hint}</span>
    </div>
  );
}

// A segment that exists is always visible: the model's own output is a sliver
// next to billions of cache reads, and a sub-pixel bar would read as "none".
function BarSegment({ kind, percent }) {
  return (
    <span
      className={`usage-bar-seg usage-bar-seg--${kind}`}
      style={{ width: `${percent}%`, minWidth: percent > 0 ? '2px' : 0 }}
    />
  );
}

function UsageRow({ task, maxTotal, open, onToggle }) {
  const { usage } = task;
  const parts = usageParts(usage);
  const bar = usageBar(usage, maxTotal);
  const when = formatWhen(usage.last_at);
  const models = Object.entries(usage.models || {}).sort((a, b) => b[1].total - a[1].total);
  const bodyId = `usage-row-${task.task_id}`;

  return (
    <li className="usage-row">
      <button
        type="button"
        className="usage-row-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={onToggle}
      >
        <span className="usage-row-title">
          <code className="usage-row-id">{task.task_id}</code>
          <span className="usage-row-summary">{task.summary || 'No summary'}</span>
        </span>
        <span className="usage-row-total" title={formatCount(usage.total)}>
          {formatTokens(usage.total)}
        </span>
        <span className="usage-bar" aria-hidden="true">
          <BarSegment kind="output" percent={bar.output} />
          <BarSegment kind="fresh" percent={bar.fresh} />
          <BarSegment kind="cache" percent={bar.cacheRead} />
        </span>
        <span className="usage-row-meta">
          <span>{formatTokens(parts.output)} output</span>
          <span>{formatTokens(parts.fresh)} new input</span>
          <span>{formatTokens(parts.cacheRead)} cache read</span>
          <span>{formatCount(usage.turns)} turns</span>
          {task.status ? <span>{task.status}</span> : null}
          {when ? <span>{when}</span> : null}
        </span>
      </button>
      {open ? (
        <div className="usage-row-detail" id={bodyId}>
          {models.length === 0 ? (
            <p className="usage-note">No model calls recorded.</p>
          ) : (
            <table className="usage-models">
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col">Turns</th>
                  <th scope="col">Output</th>
                  <th scope="col">New input</th>
                  <th scope="col">Cache read</th>
                  <th scope="col">Total</th>
                </tr>
              </thead>
              <tbody>
                {models.map(([model, counts]) => (
                  <tr key={model}>
                    <th scope="row">{model || 'unknown'}</th>
                    <td>{formatCount(counts.turns)}</td>
                    <td>{formatTokens(counts.output)}</td>
                    <td>{formatTokens(counts.input + counts.cache_write)}</td>
                    <td>{formatTokens(counts.cache_read)}</td>
                    <td>{formatTokens(counts.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {task.chats ? (
            <p className="usage-note">
              {task.chats} chat{task.chats === 1 ? '' : 's'} counted
              {task.uncounted_backends?.length
                ? `; ${agentNames(task.uncounted_backends)} not counted` : ''}.
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
