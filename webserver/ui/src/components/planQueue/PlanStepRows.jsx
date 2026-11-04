import { useState } from 'react';
import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import MarkdownContent from '../MarkdownContent.jsx';
import RepositoryPickerModal from '../RepositoryPickerModal.jsx';
import { modelOptions, toggleRepoEverywhere } from './planQueueHelpers.js';

// A split's tasks as rows to scan and edit — the proposal's editor, and the
// board's once the plan is approved (where a started task is locked).

// The drafts an editor holds, and the edits both editors make the same way.
// ``onEdit`` runs after each change (the proposal marks itself unsaved).
// ``mayChange(step)``: a change made to every task skips the ones it refuses
// (a started task on the board stays exactly as it is).
export function useStepDrafts(initial, onEdit = () => {}, mayChange = () => true) {
  const [steps, setSteps] = useState(initial);
  const [open, setOpen] = useState(() => new Set());
  // Repositories the operator added from kato's inventory, beyond the plan's
  // own: saved with the edit, which makes them the plan's too.
  const [added, setAdded] = useState([]);

  function change(index, patch) {
    setSteps((current) => current.map((step, at) => (at === index ? { ...step, ...patch } : step)));
    onEdit();
  }

  // Taken out, and out of what every other task waits for.
  function remove(index) {
    const removedId = steps[index].id;
    setSteps((current) => current
      .filter((_, at) => at !== index)
      .map((step) => ({ ...step, depends_on: step.depends_on.filter((dep) => dep !== removedId) })));
    onEdit();
  }

  // ``ids`` into the task at ``index`` — or into every task, without one.
  function addRepositories(ids, index = -1) {
    setAdded((current) => [...current, ...ids.filter((id) => !current.includes(id))]);
    setSteps((current) => current.map((step, at) => {
      if (index >= 0 ? at !== index : !mayChange(step)) { return step; }
      const missing = ids.filter((id) => !step.repositories.includes(id));
      return missing.length ? { ...step, repositories: [...step.repositories, ...missing] } : step;
    }));
    onEdit();
  }

  // ``repo`` into every task that may change, or out of all of them.
  function toggleEverywhere(repo) {
    setSteps((current) => toggleRepoEverywhere(current, repo, mayChange));
    onEdit();
  }

  function toggle(id) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) { next.delete(id); } else { next.add(id); }
      return next;
    });
  }

  return { steps, setSteps, open, setOpen, change, remove, toggle, added, addRepositories, toggleEverywhere, mayChange };
}

// Every task of a split, one row each. ``drafts`` is ``useStepDrafts``'s
// result; ``lockedText(step)`` names why a task may not change ('' = it may).
// ``models``: what a task can run on; ``planModel``: what it runs on unless it
// picks its own.
export function StepList({ drafts, rounds, repositories, models = [], planModel = '', lockedText = () => '' }) {
  const { steps, open, toggle, change, remove } = drafts;
  return (
    <ol className="plan-proposal-steps">
      {steps.map((step, index) => (
        <StepRow
          key={step.id}
          step={step}
          round={rounds.get(step.id) || 0}
          open={open.has(step.id)}
          onToggle={() => toggle(step.id)}
          others={steps.filter((other) => other.id !== step.id).map((other) => other.id)}
          repositories={repositories}
          models={models}
          planModel={planModel}
          onChange={(patch) => change(index, patch)}
          onAddRepositories={(ids) => drafts.addRepositories(ids, index)}
          onRemove={!lockedText(step) && steps.length > 1 ? () => remove(index) : null}
          locked={lockedText(step)}
        />
      ))}
    </ol>
  );
}

// One task: a row to scan, that opens to edit. Where it works and the model
// it runs on are on the row itself, never behind the open. ``locked``: a task
// that has started — shown as it is (its agent already has its brief).
export function StepRow({
  step, round, open, onToggle, others, repositories, models = [], planModel = '', onChange, onRemove,
  onAddRepositories, locked = '',
}) {
  const waits = step.depends_on.length ? `waits for ${step.depends_on.join(', ')}` : 'starts right away';
  return (
    <li className={cx('plan-step', open && 'is-open')} data-step={step.id}>
      <button type="button" className="plan-step-head" aria-expanded={open}
        aria-label={`${open ? 'Close' : 'Open'} ${step.id}`} onClick={onToggle}>
        <span className={cx('plan-step-round', round === 1 && 'is-first', round === 0 && 'is-stuck')}
          title={round ? `Runs in round ${round}` : 'Waits for itself through other tasks'}>
          {round || '!'}
        </span>
        <code className="plan-step-id">{step.id}</code>
        <span className="plan-step-title">{step.title || 'Untitled'}</span>
        <span className="plan-step-waits">{locked || waits}</span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} />
      </button>
      <StepSetup step={step} repositories={repositories} models={models} planModel={planModel}
        locked={Boolean(locked)} onChange={onChange} onAddRepositories={onAddRepositories} />
      {open && locked ? (
        <div className="plan-step-body"><div className="plan-step-brief-text"><MarkdownContent>{step.brief}</MarkdownContent></div></div>
      ) : null}
      {open && !locked ? (
        <StepEditor step={step} others={others} onChange={onChange} onRemove={onRemove} />
      ) : null}
    </li>
  );
}

function StepEditor({ step, others, onChange, onRemove }) {
  const [editing, setEditing] = useState(false);
  const addable = others.filter((id) => !step.depends_on.includes(id));
  return (
    <div className="plan-step-body">
      <input
        className="plan-step-title-input"
        aria-label={`Title of ${step.id}`}
        value={step.title}
        onChange={(event) => onChange({ title: event.target.value })}
      />
      <div className="plan-step-brief">
        {editing ? (
          <textarea
            aria-label={`Brief of ${step.id}`}
            value={step.brief}
            onChange={(event) => onChange({ brief: event.target.value })}
          />
        ) : (
          <div className="plan-step-brief-text"><MarkdownContent>{step.brief}</MarkdownContent></div>
        )}
        <button type="button" className="plan-action plan-step-brief-toggle"
          onClick={() => setEditing((value) => !value)}
          data-tooltip={editing ? 'Back to the formatted text' : 'Edit the exact text this task\'s agent will get'}
          aria-label={editing ? `Show the text of ${step.id}` : `Edit the text of ${step.id}`}>
          <Icon name={editing ? 'eye' : 'edit'} /> {editing ? 'Show' : 'Edit text'}
        </button>
      </div>
      <div className="plan-step-field" role="group" aria-label={`${step.id} waits for`}>
        <span className="plan-step-field-label">Waits for</span>
        {step.depends_on.length ? step.depends_on.map((dep) => (
          <button key={dep} type="button" className="plan-chip is-on" aria-label={`Stop waiting for ${dep}`}
            data-tooltip={`Stop waiting for ${dep}`}
            onClick={() => onChange({ depends_on: step.depends_on.filter((entry) => entry !== dep) })}>
            {dep} <Icon name="xmark" />
          </button>
        )) : <span className="plan-step-none">nothing — starts right away</span>}
        {addable.length ? (
          <select className="plan-step-add" aria-label={`Add a task ${step.id} waits for`} value=""
            onChange={(event) => event.target.value && onChange({ depends_on: [...step.depends_on, event.target.value] })}>
            <option value="">+ Add…</option>
            {addable.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        ) : null}
      </div>
      {onRemove ? (
        <button type="button" className="plan-action is-danger plan-step-remove" onClick={onRemove}
          aria-label={`Remove ${step.id}`} data-tooltip="Leave this task out of the split">
          <Icon name="trash" /> Remove this task
        </button>
      ) : null}
    </div>
  );
}

// Where one task works and the model it runs on — on its row, one click away.
// A repository chip ticks it in or out; ``+`` adds one kato knows that the
// plan does not have yet. The model shows what WILL run: the task's own pick,
// else the plan's; picking the plan's again makes it follow the plan.
function StepSetup({ step, repositories, models, planModel, locked, onChange, onAddRepositories }) {
  const [picking, setPicking] = useState(false);
  const model = step.model || planModel;
  if (locked) {
    return (
      <div className="plan-step-setup is-locked">
        <span>works in {step.repositories.join(', ') || '—'}</span>
        {step.model ? <span> · runs on {step.model}</span> : null}
      </div>
    );
  }
  const toggle = (repo) => onChange({
    repositories: step.repositories.includes(repo)
      ? step.repositories.filter((entry) => entry !== repo)
      : [...step.repositories, repo],
  });
  const own = Boolean(step.model) && step.model !== planModel;
  return (
    <div className="plan-step-setup">
      <div className="plan-step-setup-repos" role="group" aria-label={`${step.id} repositories`}>
        {repositories.map((repo) => {
          const on = step.repositories.includes(repo);
          return (
            <button key={repo} type="button" aria-pressed={on} className={cx('plan-chip', on && 'is-on')}
              data-tooltip={on ? `${step.id} works in ${repo}. Click to take it out.` : `Click to add ${repo} to ${step.id}.`}
              onClick={() => toggle(repo)}>
              {repo}
            </button>
          );
        })}
        <button type="button" className="plan-chip plan-chip-add" onClick={() => setPicking(true)}
          aria-label={`Add a repository to ${step.id}`}
          data-tooltip="Add another repository kato knows to this task.">
          <Icon name="plus" />
        </button>
      </div>
      {model ? (
        <label className={cx('plan-step-setup-model', own && 'is-own')}
          data-tooltip={own ? `${step.id}'s own model. Pick ${planModel} to follow the plan again.`
            : 'The model this task runs on. Pick another for this task only.'}>
          <span className="plan-step-setup-label">model</span>
          <select aria-label={`Model of ${step.id}`} value={model}
            onChange={(event) => onChange({ model: event.target.value === planModel ? '' : event.target.value })}>
            {modelOptions(models, model).map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
      ) : null}
      {picking ? (
        <RepositoryPickerModal
          ariaLabel={`Add a repository to ${step.id}`}
          title={<>Add a repository to {step.id}</>}
          helpText="Pick one or more of the repositories kato knows under your repository folder. This task
            will work in them too; tick them on another task if it needs them as well."
          alreadyAttachedIds={new Set(step.repositories)}
          allAttachedText="This task already works in every repository kato knows."
          confirmLabel="Add to this task"
          busyLabel="Adding…"
          onPick={(repos) => { onAddRepositories(repos.map((repo) => repo.id)); setPicking(false); }}
          onClose={() => setPicking(false)}
        />
      ) : null}
    </div>
  );
}

export function asDraft(step) {
  return {
    id: step.id,
    title: step.title,
    brief: step.brief,
    depends_on: [...(step.depends_on || [])],
    repositories: [...(step.repositories || [])],
    model: step.model || '',
  };
}
