import { Fragment, useMemo, useState } from 'react';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { useDelayedFlag } from '../hooks/useDelayedFlag.js';
import { useUsage } from '../hooks/useUsage.js';
import { formatTokens } from '../utils/chatCost.js';
import {
  USAGE_SORTS, costLabel, formatCost, formatCount, formatSpan, formatWhen, hasCost,
  hasModelSpans, modelChips, sortUsageTasks, unpricedLabels, usageBarPercent, usageCostParts,
  usageModels, usageParts, usageShare,
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
//
// A row leads with its token total and one plain bar (tokens against the
// biggest task). What it is worth in dollars — priced on the server, per
// message, per model — is in the details, where a model switch and the three
// groups can be itemised.
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
        {data && tasks.length > 0 ? (
          <p className="usage-caption">
            Each bar compares a task&apos;s tokens with the biggest task&apos;s — the longest bar.
            Open a task for its models and what it cost.
          </p>
        ) : null}

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
              grandTotal={data?.totals?.total || 0}
              open={openIds.has(task.task_id)}
              onToggle={() => toggle(task.task_id)}
            />
          ))}
        </ul>

        <p className="usage-foot">
          Counted from the Claude transcripts on this machine, every chat a task has had.
          Cache reads are the conversation being re-read on each turn — they dominate the
          total but are not new material.
          {data?.pricing_as_of
            ? ` Costs, in each task's details, are estimates at Anthropic's published API rates (as of ${data.pricing_as_of}), worked out message by message for the model that answered it. A subscription is not billed per token, so read one as what the work would cost through the API, not as what you paid.`
            : ''}
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
  const tasks = `${taskCount} task${taskCount === 1 ? '' : 's'}`;
  return (
    <div className="usage-totals">
      <Tile
        label="Total tokens"
        value={parts.total}
        hint={hasCost(totals) ? `${tasks} · ≈ ${costLabel(totals)} at API rates` : tasks}
      />
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

// The models a task ran on, in the order it first used them: a switch reads as
// "Opus 5.5 → Sonnet 5.5". Each chip carries the model's own id on hover.
function ModelChips({ chips }) {
  if (chips.shown.length === 0) { return null; }
  return (
    <span className="usage-model-chips">
      {chips.shown.map((model, index) => (
        <Fragment key={model.id}>
          {index > 0 ? <span className="usage-model-arrow" aria-hidden="true">→</span> : null}
          <span
            className="usage-model-chip"
            title={`${model.id || 'unknown'} · ${formatCount(model.turns)} turns`}
          >
            {model.label}
          </span>
        </Fragment>
      ))}
      {chips.more > 0 ? <span className="usage-model-more">+{chips.more}</span> : null}
    </span>
  );
}

// What the task cost, itemised: the money lives here, not on the row.
function CostSummary({ usage }) {
  const cost = usageCostParts(usage);
  return (
    <p className="usage-cost">
      <span>Estimated cost <strong>{costLabel(usage)}</strong> at API rates</span>
      <span className="usage-cost-split">
        {formatCost(cost.output)} written by the model · {formatCost(cost.fresh)} new input
        · {formatCost(cost.cacheRead)} re-read from cache
      </span>
    </p>
  );
}

function UsageRow({ task, maxTotal, grandTotal, open, onToggle }) {
  const { usage } = task;
  const tokens = usageParts(usage);
  const percent = usageBarPercent(usage, maxTotal);
  const when = formatWhen(usage.last_at);
  const chips = modelChips(usage);
  const models = usageModels(usage);
  const unpriced = unpricedLabels(usage);
  const priced = hasCost(usage);
  const timed = hasModelSpans(models);
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
        <span className="usage-row-total" title={`${formatCount(tokens.total)} tokens`}>
          {formatTokens(tokens.total)}
        </span>
        <span
          className="usage-bar"
          aria-hidden="true"
          title={
            `${formatCount(tokens.total)} tokens — ${usageShare(usage, grandTotal)} of everything`
            + ` counted, ${Math.round(percent)}% of the biggest task`
          }
        >
          <span
            className="usage-bar-fill"
            style={{ width: `${percent}%`, minWidth: percent > 0 ? '2px' : 0 }}
          />
        </span>
        <span className="usage-row-meta">
          <ModelChips chips={chips} />
          <span>{formatTokens(tokens.output)} output</span>
          <span>{formatTokens(tokens.fresh)} new input</span>
          <span>{formatTokens(tokens.cacheRead)} cache read</span>
          <span>{formatCount(usage.turns)} turns</span>
          {task.status ? <span>{task.status}</span> : null}
          {when ? <span>{when}</span> : null}
        </span>
      </button>
      {open ? (
        <div className="usage-row-detail" id={bodyId}>
          {priced ? <CostSummary usage={usage} /> : null}
          {models.length === 0 ? (
            <p className="usage-note">No model calls recorded.</p>
          ) : (
            <table className="usage-models">
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  {timed ? <th scope="col">Used</th> : null}
                  <th scope="col">Turns</th>
                  <th scope="col">Output</th>
                  <th scope="col">New input</th>
                  <th scope="col">Cache read</th>
                  {priced ? <th scope="col">Cost</th> : null}
                </tr>
              </thead>
              <tbody>
                {models.map((model) => (
                  <tr key={model.id}>
                    <th scope="row" title={model.id || 'unknown'}>
                      {model.label}
                      {priced && !model.priced
                        ? <span className="usage-unpriced">no price</span> : null}
                    </th>
                    {timed ? <td>{formatSpan(model.firstAt, model.lastAt) || '—'}</td> : null}
                    <td>{formatCount(model.turns)}</td>
                    <td>{formatTokens(model.output)}</td>
                    <td>{formatTokens(model.fresh)}</td>
                    <td>{formatTokens(model.cacheRead)}</td>
                    {priced ? <td>{model.priced ? formatCost(model.cost) : '—'}</td> : null}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {priced && unpriced.length ? (
            <p className="usage-note">
              No price on file for {unpriced.join(', ')}: its tokens are counted but its cost is
              not, so the cost above is a minimum.
            </p>
          ) : null}
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
