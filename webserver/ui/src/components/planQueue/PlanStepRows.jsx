import { useState } from 'react';
import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import MarkdownContent from '../MarkdownContent.jsx';
import RepositoryPickerModal from '../RepositoryPickerModal.jsx';

// A split's tasks as rows to scan and edit — the proposal's editor, and the
// board's once the plan is approved (where a started task is locked).

// The drafts an editor holds, and the edits both editors make the same way.
// ``onEdit`` runs after each change (the proposal marks itself unsaved).
export function useStepDrafts(initial, onEdit = () => {}) {
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
      if (index >= 0 && at !== index) { return step; }
      const missing = ids.filter((id) => !step.repositories.includes(id));
      return missing.length ? { ...step, repositories: [...step.repositories, ...missing] } : step;
    }));
    onEdit();
  }

  function toggle(id) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) { next.delete(id); } else { next.add(id); }
      return next;
    });
  }

  return { steps, setSteps, open, setOpen, change, remove, toggle, added, addRepositories };
}

// Every task of a split, one row each. ``drafts`` is ``useStepDrafts``'s
// result; ``lockedText(step)`` names why a task may not change ('' = it may).
export function StepList({ drafts, rounds, repositories, lockedText = () => '' }) {
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
          onChange={(patch) => change(index, patch)}
          onAddRepositories={(ids) => drafts.addRepositories(ids, index)}
          onRemove={!lockedText(step) && steps.length > 1 ? () => remove(index) : null}
          locked={lockedText(step)}
        />
      ))}
    </ol>
  );
}

// One task: a row to scan, that opens to edit. ``locked``: a task that has
// started — shown as it is (its agent already has its brief), never edited.
export function StepRow({
  step, round, open, onToggle, others, repositories, onChange, onRemove, onAddRepositories, locked = '',
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
        <span className="plan-step-repos">
          {step.repositories.map((repo) => <span key={repo} className="plan-step-repo">{repo}</span>)}
        </span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} />
      </button>
      {open && locked ? (
        <div className="plan-step-body"><div className="plan-step-brief-text"><MarkdownContent>{step.brief}</MarkdownContent></div></div>
      ) : null}
      {open && !locked ? (
        <StepEditor step={step} others={others} repositories={repositories} onChange={onChange}
          onRemove={onRemove} onAddRepositories={onAddRepositories} />
      ) : null}
    </li>
  );
}

function StepEditor({ step, others, repositories, onChange, onRemove, onAddRepositories }) {
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const toggle = (list, value) => (list.includes(value) ? list.filter((entry) => entry !== value) : [...list, value]);
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
      {/* Where this task works: tick or untick the plan's repositories, or
          add one kato knows that the plan does not have yet. */}
      <div className="plan-step-field" role="group" aria-label={`${step.id} repositories`}>
        <span className="plan-step-field-label">Works in</span>
        {repositories.map((repo) => (
          <button key={repo} type="button" aria-pressed={step.repositories.includes(repo)}
            className={cx('plan-chip', step.repositories.includes(repo) && 'is-on')}
            onClick={() => onChange({ repositories: toggle(step.repositories, repo) })}>
            {repo}
          </button>
        ))}
        <button type="button" className="plan-action plan-step-add-repo" onClick={() => setPicking(true)}
          aria-label={`Add a repository to ${step.id}`}
          data-tooltip="Pick another repository under your repository folder for this task.">
          <Icon name="plus" /> Add repository
        </button>
      </div>
      {picking ? (
        <RepositoryPickerModal
          ariaLabel={`Add a repository to ${step.id}`}
          title={<>Add a repository to {step.id}</>}
          helpText="Pick one or more of the repositories kato knows under your repository folder. This task
            will work in them too; tick them in another task if it needs them as well."
          alreadyAttachedIds={new Set(step.repositories)}
          allAttachedText="This task already works in every repository kato knows."
          confirmLabel="Add to this task"
          busyLabel="Adding…"
          onPick={(repos) => { onAddRepositories(repos.map((repo) => repo.id)); setPicking(false); }}
          onClose={() => setPicking(false)}
        />
      ) : null}
      {onRemove ? (
        <button type="button" className="plan-action is-danger plan-step-remove" onClick={onRemove}
          aria-label={`Remove ${step.id}`} data-tooltip="Leave this task out of the split">
          <Icon name="trash" /> Remove this task
        </button>
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
  };
}
