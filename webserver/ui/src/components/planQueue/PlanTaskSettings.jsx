import { useState } from 'react';
import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import RepositoryPickerModal from '../RepositoryPickerModal.jsx';
import { effectiveEffort, effectiveModel } from '../newTask/newTaskHelpers.js';
import { modelOptions, repoCoverage } from './planQueueHelpers.js';

// "Every task": what the plan's tasks share, above the list — the
// repositories they work in (one click puts a repository in or out of every
// task) and the model and effort they run on. A task can still pick its own
// repositories and model on its row. Both selects show the CONCRETE value that
// will run, never "Default".
//
// ``drafts``: ``useStepDrafts``'s result (its ``mayChange`` keeps started tasks
// out of a change to every task). ``choices``: ``useAgentChoices``'s result.
// ``model`` / ``effort``: what the tasks run on now; ``onPick({model|effort})``.
export default function PlanTaskSettings({ drafts, repositories, sourceTaskId, choices, model, effort, onPick }) {
  const [picking, setPicking] = useState(false);
  const steps = drafts.steps.filter(drafts.mayChange);
  const levels = choices.effort.levels;
  return (
    <section className="plan-every-task" aria-label="Every task">
      <span className="plan-every-task-title">Every task</span>
      <div className="plan-every-task-row" role="group" aria-label="Every task works in">
        <span className="plan-every-task-label">works in</span>
        {repositories.map((repo) => {
          const { state, count } = repoCoverage(steps, repo);
          const pressed = { all: 'true', some: 'mixed', none: 'false' }[state];
          const tip = state === 'all'
            ? `Every task works in ${repo}. Click to take it out of all of them.`
            : `Click to add ${repo} to every task${state === 'some' ? ` (${count} of ${steps.length} have it now)` : ''}.`;
          return (
            <button key={repo} type="button" aria-pressed={pressed} data-tooltip={tip}
              className={cx('plan-chip', state === 'all' && 'is-on', state === 'some' && 'is-partial')}
              onClick={() => drafts.toggleEverywhere(repo)}>
              {repo}
              {state === 'some' ? <span className="plan-chip-count">{count}/{steps.length}</span> : null}
            </button>
          );
        })}
        <button type="button" className="plan-action plan-every-task-add" onClick={() => setPicking(true)}
          data-tooltip="Pick another repository under your repository folder — every task will work in it too.">
          <Icon name="plus" /> Add repository
        </button>
      </div>
      {model ? (
        <div className="plan-every-task-row" role="group" aria-label="Every task runs on">
          <span className="plan-every-task-label">runs on</span>
          <select aria-label="Model every task runs on" value={model}
            data-tooltip="The model every task runs on, unless a task picks its own on its row."
            onChange={(event) => onPick({ model: event.target.value })}>
            {modelOptions(choices.models, model).map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
          {levels.length ? (
            <select aria-label="Effort every task runs at" value={effort}
              data-tooltip="How hard each task's agent thinks."
              onChange={(event) => onPick({ effort: event.target.value })}>
              {[...new Set([...levels, effort].filter(Boolean))].map((level) => (
                <option key={level} value={level}>{level} effort</option>
              ))}
            </select>
          ) : null}
        </div>
      ) : null}
      {picking ? (
        <RepositoryPickerModal
          ariaLabel="Add a repository to every task"
          title={<>Add a repository to every task of {sourceTaskId}</>}
          helpText="Pick one or more of the repositories kato knows under your repository folder. Every task of
            this split will work in them too — handy for shared libraries they all build on. You can still take
            one out of a single task on its row."
          alreadyAttachedIds={new Set(repositories)}
          allAttachedText="Every repository kato knows is already in this split."
          confirmLabel="Add to every task"
          busyLabel="Adding…"
          onPick={(repos) => { drafts.addRepositories(repos.map((repo) => repo.id)); setPicking(false); }}
          onClose={() => setPicking(false)}
        />
      ) : null}
    </section>
  );
}

// What the plan's tasks run on, with the operator's picks over the saved plan:
// ``{ model, effort }`` concrete for the pickers, and ``changes`` — only what
// the operator picked, for the save (a value left alone is not sent, so an
// edit never pins the configured default by accident).
export function useHowTasksRun(plan, choices) {
  const [picks, setPicks] = useState({});
  const model = picks.model ?? effectiveModel(plan.step_model, choices.models);
  const effort = picks.effort ?? effectiveEffort(plan.effort, choices.effort);
  return {
    model,
    effort,
    changes: picks,
    pick: (patch) => setPicks((current) => ({ ...current, ...patch })),
  };
}
