import { useState } from 'react';
import {
  approvePlanQueue,
  discardPlanQueue,
  editPlanQueueSteps,
  revisePlanQueue,
} from '../../api.js';
import { useBusyAction } from '../../hooks/useBusyAction.js';
import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import MarkdownContent from '../MarkdownContent.jsx';
import RepositoryPickerModal from '../RepositoryPickerModal.jsx';
import { planAction } from './planQueueActions.js';
import { StepList, asDraft, useStepDrafts } from './PlanStepRows.jsx';
import { repoCoverage, roundsSummary, stepRounds, withRepoEverywhere } from './planQueueHelpers.js';

// The proposed split, for the operator to approve — like a plan in plan mode.
//
// Built to be SCANNED, not read: one line says the whole split ("17 tasks · 5
// rounds · 4 start right away"), each task is one row (its round, id, title,
// what it waits for, where it works), and a row opens to edit it. The agent's
// notes wait behind a button; what each action does is in its tooltip, not in
// paragraphs; and the action bar stays at the bottom of the view while the
// operator scrolls. An open row still shows its brief in full: it is exactly
// what that task's agent will be told.
export default function PlanProposal({ plan, onPlan, onDiscarded }) {
  const [dirty, setDirty] = useState(false);
  const drafts = useStepDrafts(() => plan.steps.map(asDraft), () => setDirty(true));
  const { steps, setSteps, open, setOpen } = drafts;
  const [feedback, setFeedback] = useState('');
  const [showNotes, setShowNotes] = useState(false);
  // Repositories the operator added (from kato's inventory), kept by the
  // drafts and saved with the edits.
  const { added } = drafts;
  const [picking, setPicking] = useState(false);
  // Each released task opens in Plan mode and codes only once its plan is
  // approved in its chat. On by default: nothing runs that was not planned.
  const [planFirst, setPlanFirst] = useState(true);
  const repositories = [...new Set([...plan.repository_ids, ...added])];
  const rounds = stepRounds(steps);

  // One repository for every task at once — e.g. a shared core library each
  // of them builds on. Anywhere it is missing, it is added; when every task
  // has it already, it comes out of all of them.
  function everyTask(repo) {
    setSteps((current) => withRepoEverywhere(current, repo, repoCoverage(current, repo).state !== 'all'));
    setDirty(true);
  }

  function addRepositories(repos) {
    drafts.addRepositories(repos.map((repo) => repo.id));
    setPicking(false);
  }


  const allOpen = steps.length > 0 && steps.every((step) => open.has(step.id));
  const toggleAll = () => setOpen(allOpen ? new Set() : new Set(steps.map((step) => step.id)));

  const [busy, run] = useBusyAction(async (work) => work());

  async function saveEdits() {
    const body = await planAction(
      () => editPlanQueueSteps(plan.plan_id, steps, added), 'The changes were not saved',
    );
    if (body?.plan_id) { setDirty(false); onPlan(body); }
    return body;
  }

  // ``shared``: the steps become sub-tasks of the plan's ticket on the tracker
  // (only offered when the tracker can hold them) — the operator's choice.
  const approve = (startNow, shared = false) => run(async () => {
    if (dirty && !(await saveEdits())) { return; }
    const body = await planAction(
      () => approvePlanQueue(plan.plan_id, { run: startNow, shared, planFirst }), 'The split was not approved',
    );
    if (body?.plan_id) { onPlan(body); }
  });

  const revise = () => run(async () => {
    const body = await planAction(() => revisePlanQueue(plan.plan_id, feedback), 'The revision was not started');
    if (body?.plan_id) { onPlan(body); }
  });

  const discard = () => run(async () => {
    const body = await planAction(() => discardPlanQueue(plan.plan_id), 'The split was not discarded');
    if (body) { onDiscarded(); }
  });

  const tips = actionTips(plan, steps.length);
  return (
    <div className="plan-proposal">
      <div className="plan-proposal-overview">
        <p className="plan-proposal-summary">
          {roundsSummary(steps, rounds)}
          <span className="plan-proposal-steer"> · to change it, tell the agent in the chat</span>
        </p>
        <button type="button" className="plan-action" onClick={toggleAll}
          data-tooltip={allOpen ? 'Close every task' : 'Open every task to read or edit it'}>
          {allOpen ? 'Close all' : 'Open all'}
        </button>
      </div>
      {plan.notes ? (
        <AgentNotes notes={plan.notes} open={showNotes} onToggle={() => setShowNotes((value) => !value)} />
      ) : null}
      <EveryTaskRepositories steps={steps} repositories={repositories} onToggle={everyTask}
        onAdd={() => setPicking(true)} />
      {picking ? (
        <RepositoryPickerModal
          ariaLabel="Add a repository to every task"
          title={<>Add a repository to every task of {plan.source_task_id}</>}
          helpText="Pick one or more of the repositories kato knows under your repository folder. Every task of
            this split will work in them too — handy for shared libraries they all build on. You can still take
            one out of a single task by opening that task."
          alreadyAttachedIds={new Set(repositories)}
          allAttachedText="Every repository kato knows is already in this split."
          confirmLabel="Add to every task"
          busyLabel="Adding…"
          onPick={addRepositories}
          onClose={() => setPicking(false)}
        />
      ) : null}
      <StepList drafts={drafts} rounds={rounds} repositories={repositories} />
      <div className="plan-proposal-revise">
        <textarea
          aria-label="What should change"
          placeholder="Or write here what should change — e.g. merge the two UI tasks"
          value={feedback}
          onChange={(event) => setFeedback(event.target.value)}
        />
        <button type="button" className="plan-action" disabled={busy || !feedback.trim()} onClick={revise}
          data-tooltip={tips.revise}>
          Revise
        </button>
      </div>
      <div className="plan-proposal-actions">
        <button type="button" className="plan-action is-danger plan-proposal-discard" disabled={busy}
          onClick={discard} data-tooltip={tips.discard}>
          Discard
        </button>
        <label className="plan-proposal-plan-first" data-tooltip={tips.planFirst}>
          <input type="checkbox" checked={planFirst} disabled={busy}
            onChange={(event) => setPlanFirst(event.target.checked)} />
          Plan each task first
        </label>
        {dirty ? (
          <button type="button" className="plan-action" disabled={busy} onClick={() => run(saveEdits)}
            data-tooltip={tips.save}>
            Save changes
          </button>
        ) : null}
        {plan.can_share ? (
          <>
            <button type="button" className="plan-action" disabled={busy} onClick={() => approve(true)}
              data-tooltip={tips.local}>
              Keep them in kato only
            </button>
            <button type="button" className="plan-action" disabled={busy}
              onClick={() => approve(false, true)} data-tooltip={tips.create}>
              Create {tips.count}
            </button>
            <button type="button" className="plan-action is-primary" disabled={busy}
              onClick={() => approve(true, true)} data-tooltip={tips.createAndRun}>
              Create {tips.count} and start
            </button>
          </>
        ) : (
          <>
            <button type="button" className="plan-action" disabled={busy} onClick={() => approve(false)}
              data-tooltip={tips.approve}>
              Approve
            </button>
            <button type="button" className="plan-action is-primary" disabled={busy} onClick={() => approve(true)}
              data-tooltip={tips.approveAndRun}>
              Approve and run
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// What each action does, said where the operator points at it.
function actionTips(plan, stepCount) {
  const count = `${stepCount} sub-task${stepCount === 1 ? '' : 's'}`;
  const source = plan.source_task_id;
  const shared = `Creates ${count} of ${source} on the tracker, unassigned, plus one comment on ${source} `
    + 'listing them and who waits for whom — so a teammate (and their kato) sees the same plan and can '
    + 'take a sub-task by assigning it to themselves. Nothing is ever deleted.';
  return {
    count,
    revise: 'Sends what you wrote to the agent in the chat; its new split replaces this one.',
    discard: 'Throw this split away. Nothing has been created yet.',
    save: 'Keep your edits without approving yet.',
    planFirst: 'Each task opens in Plan mode when it starts: its agent writes a plan, and codes only once '
      + 'you approve that plan in its chat. Untick to let tasks start coding right away.',
    local: 'Nothing goes to the tracker: the tasks run as kato\'s own local tasks, starting now — '
      + 'each waits until the tasks before it are merged and done.',
    create: `${shared} Starts nothing yet — press Run on the board when you are ready.`,
    createAndRun: `${shared} Then starts: every sub-task that waits for nothing is assigned to kato now; `
      + 'the rest follow once the ones they wait for are merged and done.',
    approve: 'Accept the split without starting anything — press Run on the board when you are ready.',
    approveAndRun: 'Accept the split and start: every task that waits for nothing starts now; the rest '
      + 'follow once the ones they wait for are merged and done.',
  };
}

// What the agent says about its split (conflicts it expects, work outside these
// repositories) — a collapsed section by default: one row with a faint first
// line, opened only when the operator wants the detail.
function AgentNotes({ notes, open, onToggle }) {
  const firstLine = String(notes).split('\n').find((line) => line.trim()) || '';
  return (
    <section className={cx('plan-proposal-notes', open && 'is-open')}>
      <button type="button" className="plan-proposal-notes-head" aria-expanded={open}
        aria-label="Agent's notes" onClick={onToggle}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} />
        <span className="plan-proposal-notes-title">Agent&apos;s notes</span>
        {open ? null : <span className="plan-proposal-notes-preview">{firstLine}</span>}
      </button>
      {open ? <div className="plan-proposal-notes-body"><MarkdownContent>{notes}</MarkdownContent></div> : null}
    </section>
  );
}

// "Every task works in": each of the plan's repositories, filled when every
// task works there, half when some do (with how many), empty when none do.
function EveryTaskRepositories({ steps, repositories, onToggle, onAdd }) {
  return (
    <div className="plan-proposal-repos" role="group" aria-label="Every task works in">
      <span className="plan-step-field-label">Every task works in</span>
      {repositories.map((repo) => {
        const { state, count } = repoCoverage(steps, repo);
        const pressed = { all: 'true', some: 'mixed', none: 'false' }[state];
        const tip = state === 'all'
          ? `Every task works in ${repo}. Click to take it out of all of them.`
          : `Click to add ${repo} to every task${state === 'some' ? ` (${count} of ${steps.length} have it now)` : ''}.`;
        return (
          <button key={repo} type="button" aria-pressed={pressed} data-tooltip={tip}
            className={cx('plan-chip', state === 'all' && 'is-on', state === 'some' && 'is-partial')}
            onClick={() => onToggle(repo)}>
            {repo}
            {state === 'some' ? <span className="plan-chip-count">{count}/{steps.length}</span> : null}
          </button>
        );
      })}
      <button type="button" className="plan-action plan-proposal-add-repo" onClick={onAdd}
        data-tooltip="Pick another repository under your repository folder — every task will work in it too.">
        <Icon name="plus" /> Add repository
      </button>
    </div>
  );
}
