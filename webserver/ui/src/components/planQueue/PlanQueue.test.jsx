// Tests for the plan queue tab — split a task's plan (or take a split already
// written as files), approve the split, watch the board.
//
// What must hold:
// - it is a tab among the task's files, never a popup over the window: a
//   proposal takes minutes and nothing may wait on it;
// - the proposal is the trust boundary: every brief is shown in full and is
//   exactly what is saved; edits are saved BEFORE the approval they precede;
// - the board is read-only: every step lands in the column its status names,
//   no card can be dragged or edited, and only a STUCK card offers a way out;
// - the plan is polled only while its tab shows.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('../../api.js', () => ({
  fetchPlanQueues: vi.fn(),
  fetchPlanQueue: vi.fn(),
  fetchPlanQueueSplitFolders: vi.fn(),
  proposePlanQueue: vi.fn(),
  revisePlanQueue: vi.fn(),
  retryPlanQueueProposal: vi.fn(),
  editPlanQueueSteps: vi.fn(),
  approvePlanQueue: vi.fn(),
  controlPlanQueue: vi.fn(),
  discardPlanQueue: vi.fn(),
  planQueueStepAction: vi.fn(),
  takeOverPlanQueue: vi.fn(),
  fetchSharedPlanQueue: vi.fn(),
  fetchInventoryRepositories: vi.fn(),
}));

import {
  approvePlanQueue,
  controlPlanQueue,
  discardPlanQueue,
  editPlanQueueSteps,
  fetchPlanQueue,
  fetchInventoryRepositories,
  fetchPlanQueueSplitFolders,
  fetchPlanQueues,
  fetchSharedPlanQueue,
  planQueueStepAction,
  proposePlanQueue,
  retryPlanQueueProposal,
  revisePlanQueue,
  takeOverPlanQueue,
} from '../../api.js';
import { toast } from '../../stores/toastStore.js';
import PlanQueueButton from './PlanQueueButton.jsx';
import PlanQueueView from './PlanQueueView.jsx';
import { planQueueChanges, planQueueView } from './planQueueViewStore.js';

function step(id, overrides = {}) {
  return {
    id,
    title: `Title of ${id}`,
    brief: `The whole brief of ${id}.\nSecond line.`,
    depends_on: [],
    repositories: ['api'],
    status: 'blocked',
    reason_code: '',
    reason: '',
    task_id: '',
    released_at: 0,
    merge: null,
    ...overrides,
  };
}

function plan(overrides = {}) {
  return {
    plan_id: 'p1',
    source_task_id: 'UNA-1',
    title: 'Discount codes',
    status: 'proposed',
    reason: '',
    notes: '',
    repository_ids: ['api', 'web'],
    revision: 1,
    steps: [step('api'), step('ui', { depends_on: ['api'], repositories: ['web'] })],
    ...overrides,
  };
}

function summary(overrides = {}) {
  return {
    plan_id: 'p1', source_task_id: 'UNA-1', status: 'proposed',
    step_count: 2, counts: {}, updated_at: 1, ...overrides,
  };
}

const ok = (body) => Promise.resolve({ ok: true, body });

// The task has a plan queue in ``state``, and reading it answers ``detail``.
function serve(detail, state = detail.status) {
  fetchPlanQueues.mockResolvedValue({ plans: [summary({ status: state })] });
  fetchPlanQueue.mockResolvedValue(detail);
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchPlanQueues.mockResolvedValue({ plans: [] });
  fetchPlanQueue.mockResolvedValue(plan());
  fetchPlanQueueSplitFolders.mockResolvedValue({ folders: [] });
  fetchSharedPlanQueue.mockRejectedValue(new Error('404'));  // no plan on the tracker
});

describe('PlanQueueButton', () => {
  test('says "Split plan" before the task has a plan queue', async () => {
    render(<PlanQueueButton session={{ task_id: 'UNA-1' }} />);
    await waitFor(() => expect(fetchPlanQueues).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'Split plan' })).toBeInTheDocument();
  });

  test('switches to "Plan board" once it has one, with a dot while it runs', async () => {
    fetchPlanQueues.mockResolvedValue({ plans: [summary({ status: 'running', counts: { satisfied: 1 } })] });
    const { container } = render(<PlanQueueButton session={{ task_id: 'UNA-1' }} />);
    const button = await screen.findByRole('button', { name: 'Plan board' });
    expect(button).toHaveAttribute('data-tooltip', 'Plan board — Running · 1/2 done');
    expect(container.querySelector('.plan-queue-dot')).toBeTruthy();
  });

  test('a stopped plan offers a new split again, without the dot', async () => {
    fetchPlanQueues.mockResolvedValue({ plans: [summary({ status: 'cancelled' })] });
    const { container } = render(<PlanQueueButton session={{ task_id: 'UNA-1' }} />);
    await waitFor(() => expect(fetchPlanQueues).toHaveBeenCalled());
    expect(await screen.findByRole('button', { name: 'Split plan' })).toBeInTheDocument();
    expect(container.querySelector('.plan-queue-dot')).toBeNull();
  });

  test('ignores another task\'s plan', async () => {
    fetchPlanQueues.mockResolvedValue({ plans: [summary({ source_task_id: 'UNA-9' })] });
    render(<PlanQueueButton session={{ task_id: 'UNA-1' }} />);
    await waitFor(() => expect(fetchPlanQueues).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'Split plan' })).toBeInTheDocument();
  });

  test('the placeholder is inert and asks nothing', () => {
    render(<PlanQueueButton disabled />);
    expect(screen.getByRole('button', { name: 'Split plan' })).toBeDisabled();
    expect(fetchPlanQueues).not.toHaveBeenCalled();
  });

  test('a click opens its task\'s plan tab, named as the button', async () => {
    const seen = [];
    const unsubscribe = planQueueView.subscribe((request) => seen.push([request.taskId, request.label]));
    render(<PlanQueueButton session={{ task_id: 'UNA-1' }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Split plan' }));
    unsubscribe();
    expect(seen.at(-1)).toEqual(['UNA-1', 'Split plan']);
  });

  test('a blue dot while an agent proposes, an amber one while the split waits for review', async () => {
    fetchPlanQueues.mockResolvedValue({ plans: [summary({ status: 'proposing' })] });
    const { container, unmount } = render(<PlanQueueButton session={{ task_id: 'UNA-1' }} />);
    await waitFor(() => expect(container.querySelector('.plan-queue-dot.is-running')).toBeTruthy());
    unmount();
    fetchPlanQueues.mockResolvedValue({ plans: [summary({ status: 'proposed' })] });
    const second = render(<PlanQueueButton session={{ task_id: 'UNA-1' }} />);
    await waitFor(() => expect(second.container.querySelector('.plan-queue-dot.is-warn')).toBeTruthy());
  });
});

describe('PlanQueueView — before a split exists', () => {
  test('offers to propose one, then says an agent is proposing', async () => {
    proposePlanQueue.mockReturnValue(ok(plan({ status: 'proposing', steps: [] })));
    fetchPlanQueue.mockResolvedValue(plan({ status: 'proposing', steps: [] }));
    render(<PlanQueueView taskId="UNA-1" />);
    expect(screen.getByRole('region', { name: 'Split plan — UNA-1' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();  // a tab, never a popup over the window

    fireEvent.click(screen.getByRole('button', { name: 'Propose a split of plan.md' }));

    expect(await screen.findByText(/splitting the plan in this task's chat/)).toBeInTheDocument();
    expect(screen.getByText(/tell the agent there what to change/)).toBeInTheDocument();
    expect(proposePlanQueue).toHaveBeenCalledWith('UNA-1', { splitFolder: '' });
  });

  test('a proposal can be cancelled while it runs', async () => {
    serve(plan({ status: 'proposing', steps: [] }));
    controlPlanQueue.mockReturnValue(ok(plan({ status: 'propose_failed', reason: 'Cancelled.', steps: [] })));
    render(<PlanQueueView taskId="UNA-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Cancel the split' }));

    await waitFor(() => expect(controlPlanQueue).toHaveBeenCalledWith('p1', 'stop'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Cancelled.');
  });

  test('a failed proposal says why and can be tried again or thrown away', async () => {
    serve(plan({ status: 'propose_failed', reason: 'The agent wrote no <task-split> block.', steps: [] }));
    retryPlanQueueProposal.mockReturnValue(ok(plan({ status: 'proposing', steps: [] })));
    render(<PlanQueueView taskId="UNA-1" />);

    expect(await screen.findByRole('alert')).toHaveTextContent('no <task-split> block');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(retryPlanQueueProposal).toHaveBeenCalledWith('p1'));
  });

  test('a failed split can start over from the split folder in one click', async () => {
    serve(plan({ status: 'propose_failed', reason: 'kato restarted while the split was being worked out', steps: [] }));
    fetchPlanQueueSplitFolders.mockResolvedValue({ folders: [{ path: 'tasks', files: 17, context: 'README.md' }] });
    discardPlanQueue.mockReturnValue(ok({ ok: true }));
    proposePlanQueue.mockReturnValue(ok(plan({ plan_id: 'p2', status: 'proposing', steps: [] })));
    fetchPlanQueue.mockImplementation((id) => Promise.resolve(id === 'p2'
      ? plan({ plan_id: 'p2', status: 'proposing', steps: [] })
      : plan({ status: 'propose_failed', reason: 'kato restarted while the split was being worked out', steps: [] })));
    render(<PlanQueueView taskId="UNA-1" />);

    expect(await screen.findByRole('alert')).toHaveTextContent('kato restarted while the split was being worked out');
    expect(screen.getByText('Or start over:')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Use the split in tasks/' }));

    await waitFor(() => expect(proposePlanQueue).toHaveBeenCalledWith('UNA-1', { splitFolder: 'tasks' }));
    expect(discardPlanQueue.mock.invocationCallOrder[0]).toBeLessThan(proposePlanQueue.mock.invocationCallOrder[0]);
    expect(await screen.findByText(/splitting the plan in this task's chat/)).toBeInTheDocument();
  });

  test('starting over stops when the failed split cannot be thrown away', async () => {
    const shown = vi.spyOn(toast, 'errorFromResult').mockImplementation(() => {});
    serve(plan({ status: 'propose_failed', reason: 'it failed', steps: [] }));
    discardPlanQueue.mockResolvedValue({ ok: false, error: 'the plan is running' });
    render(<PlanQueueView taskId="UNA-1" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('it failed');
    fireEvent.click(screen.getByRole('button', { name: 'Propose a split of plan.md' }));
    await waitFor(() => expect(shown).toHaveBeenCalled());
    expect(proposePlanQueue).not.toHaveBeenCalled();
  });

  test('a refusal is shown, and the view stays where it was', async () => {
    const shown = vi.spyOn(toast, 'errorFromResult').mockImplementation(() => {});
    proposePlanQueue.mockResolvedValue({ ok: false, error: 'the task has no plan.md' });
    render(<PlanQueueView taskId="UNA-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Propose a split of plan.md' }));

    await waitFor(() => expect(shown).toHaveBeenCalledWith(
      { ok: false, error: 'the task has no plan.md' }, { title: 'The plan was not split' },
    ));
    expect(screen.getByRole('button', { name: 'Propose a split of plan.md' })).toBeInTheDocument();
  });

  test('a split already written as files is offered first', async () => {
    fetchPlanQueueSplitFolders.mockResolvedValue({ folders: [{ path: 'tasks', files: 17, context: 'README.md' }] });
    proposePlanQueue.mockReturnValue(ok(plan({ status: 'proposing', steps: [] })));
    fetchPlanQueue.mockResolvedValue(plan({ status: 'proposing', steps: [] }));
    render(<PlanQueueView taskId="UNA-1" />);

    const useFolder = await screen.findByRole('button', { name: 'Use the split in tasks/' });
    expect(useFolder).toHaveClass('is-primary');
    expect(screen.getByText(/17 files, one task each, kept exactly as written \(README.md is read for the order\)/))
      .toBeInTheDocument();
    // plan.md is still offered, as the quieter choice.
    expect(screen.getByRole('button', { name: 'Propose a split of plan.md' })).not.toHaveClass('is-primary');
    expect(fetchPlanQueueSplitFolders).toHaveBeenCalledWith('UNA-1');

    fireEvent.click(useFolder);

    await waitFor(() => expect(proposePlanQueue).toHaveBeenCalledWith('UNA-1', { splitFolder: 'tasks' }));
    expect(await screen.findByText(/splitting the plan in this task's chat/)).toBeInTheDocument();
  });

  test('a split stopped before it had any tasks offers a new split, not an empty board', async () => {
    serve(plan({ status: 'cancelled', reason: 'stopped by the operator', steps: [] }));
    fetchPlanQueueSplitFolders.mockResolvedValue({ folders: [{ path: 'tasks', files: 17, context: '' }] });
    render(<PlanQueueView taskId="UNA-1" />);

    expect(await screen.findByRole('button', { name: 'Use the split in tasks/' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Split plan — UNA-1' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Done' })).toBeNull();
  });

  test('without split folders, splitting plan.md is the main choice', async () => {
    fetchPlanQueueSplitFolders.mockRejectedValue(new Error('offline'));
    render(<PlanQueueView taskId="UNA-1" />);
    await waitFor(() => expect(fetchPlanQueueSplitFolders).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'Propose a split of plan.md' })).toHaveClass('is-primary');
    expect(screen.queryByRole('button', { name: /Use the split in/ })).toBeNull();
  });
});

// Opens a task's row in the proposal (the list starts closed: one line each).
async function openRow(id) {
  fireEvent.click(await screen.findByRole('button', { name: `Open ${id}` }));
}

describe('PlanQueueView — the proposal', () => {
  test('one line says the whole split; the agent\'s notes are a section, collapsed', async () => {
    serve(plan({ notes: 'Expect a conflict in App.js.\n\nAnd t11 needs audit-core-lib.' }));
    const { container } = render(<PlanQueueView taskId="UNA-1" />);
    expect(await screen.findByText('2 tasks · 2 rounds · 1 starts right away')).toBeInTheDocument();
    const notes = screen.getByRole('button', { name: 'Agent\'s notes' });
    expect(notes).toHaveAttribute('aria-expanded', 'false');
    // Collapsed: a faint first line only.
    expect(container.querySelector('.plan-proposal-notes-preview')).toHaveTextContent('Expect a conflict in App.js.');
    expect(screen.queryByText('And t11 needs audit-core-lib.')).toBeNull();
    fireEvent.click(notes);
    expect(notes).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('And t11 needs audit-core-lib.')).toBeInTheDocument();
    expect(container.querySelector('.plan-proposal-notes-preview')).toBeNull();
    fireEvent.click(notes);
    expect(screen.queryByText('And t11 needs audit-core-lib.')).toBeNull();
  });

  test('each task is one row to scan: its round, title, what it waits for, where it works', async () => {
    serve(plan());
    const { container } = render(<PlanQueueView taskId="UNA-1" />);
    await screen.findByRole('button', { name: 'Open api' });
    const ui = container.querySelector('[data-step="ui"]');
    expect(within(ui).getByText('Title of ui')).toBeInTheDocument();
    expect(within(ui).getByText('waits for api')).toBeInTheDocument();
    expect(within(ui).getByText('web')).toBeInTheDocument();
    expect(ui.querySelector('.plan-step-round')).toHaveTextContent('2');
    expect(container.querySelector('[data-step="api"] .plan-step-round')).toHaveClass('is-first');
    expect(screen.queryByLabelText('Title of api')).toBeNull();  // closed until opened
    fireEvent.click(screen.getByRole('button', { name: 'Open all' }));
    expect(screen.getByLabelText('Title of ui')).toHaveValue('Title of ui');
    fireEvent.click(screen.getByRole('button', { name: 'Close all' }));
    expect(screen.queryByLabelText('Title of ui')).toBeNull();
  });

  test('an open task shows its brief in full, exactly as it will be written', async () => {
    serve(plan());
    render(<PlanQueueView taskId="UNA-1" />);
    await openRow('api');
    expect(screen.getByText(/The whole brief of api\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit the text of api' }));
    expect(screen.getByLabelText('Brief of api')).toHaveValue('The whole brief of api.\nSecond line.');
    fireEvent.click(screen.getByRole('button', { name: 'Show the text of api' }));
    expect(screen.queryByLabelText('Brief of api')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Close api' }));
    expect(screen.queryByLabelText('Title of api')).toBeNull();
  });

  test('Approve and run saves the edits first, then approves', async () => {
    serve(plan());
    editPlanQueueSteps.mockReturnValue(ok(plan()));
    approvePlanQueue.mockReturnValue(ok(plan({ status: 'running' })));
    render(<PlanQueueView taskId="UNA-1" />);

    await openRow('api');
    fireEvent.change(screen.getByLabelText('Title of api'), { target: { value: 'The endpoint' } });
    fireEvent.click(screen.getByRole('button', { name: 'Approve and run' }));

    await waitFor(() => expect(approvePlanQueue).toHaveBeenCalledWith('p1', { run: true, shared: false }));
    const [, saved] = editPlanQueueSteps.mock.calls[0];
    expect(saved[0]).toEqual({
      id: 'api', title: 'The endpoint', brief: 'The whole brief of api.\nSecond line.',
      depends_on: [], repositories: ['api'],
    });
    expect(editPlanQueueSteps.mock.invocationCallOrder[0])
      .toBeLessThan(approvePlanQueue.mock.invocationCallOrder[0]);
  });

  test('Approve without edits saves nothing and does not run', async () => {
    serve(plan());
    approvePlanQueue.mockReturnValue(ok(plan({ status: 'approved' })));
    render(<PlanQueueView taskId="UNA-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));

    await waitFor(() => expect(approvePlanQueue).toHaveBeenCalledWith('p1', { run: false, shared: false }));
    expect(editPlanQueueSteps).not.toHaveBeenCalled();
  });

  test('every action says what it does, and looks like a button', async () => {
    serve(plan());
    render(<PlanQueueView taskId="UNA-1" />);
    for (const name of ['Discard', 'Approve', 'Approve and run', 'Revise']) {
      const button = await screen.findByRole('button', { name });
      expect(button).toHaveClass('plan-action');
      expect(button.getAttribute('data-tooltip')).toBeTruthy();
    }
    expect(screen.getByRole('button', { name: 'Approve and run' })).toHaveClass('is-primary');
    expect(screen.getByRole('button', { name: 'Discard' })).toHaveClass('is-danger');
  });

  test('a refused save stops the approval', async () => {
    vi.spyOn(toast, 'errorFromResult').mockImplementation(() => {});
    serve(plan());
    editPlanQueueSteps.mockResolvedValue({ ok: false, error: 'step ui waits for itself' });
    render(<PlanQueueView taskId="UNA-1" />);

    await openRow('ui');
    fireEvent.click(screen.getByRole('button', { name: 'Edit the text of ui' }));
    fireEvent.change(screen.getByLabelText('Brief of ui'), { target: { value: 'changed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));

    await waitFor(() => expect(editPlanQueueSteps).toHaveBeenCalled());
    expect(approvePlanQueue).not.toHaveBeenCalled();
    expect(toast.errorFromResult).toHaveBeenCalledWith(
      { ok: false, error: 'step ui waits for itself' }, { title: 'The changes were not saved' },
    );
  });

  test('removing a task also drops it from what the others wait for', async () => {
    serve(plan());
    editPlanQueueSteps.mockReturnValue(ok(plan()));
    render(<PlanQueueView taskId="UNA-1" />);

    await openRow('api');
    fireEvent.click(screen.getByRole('button', { name: 'Remove api' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(editPlanQueueSteps).toHaveBeenCalled());
    const [, saved] = editPlanQueueSteps.mock.calls[0];
    expect(saved.map((entry) => [entry.id, entry.depends_on])).toEqual([['ui', []]]);
    // The last task left cannot be removed.
    await openRow('ui');
    expect(screen.queryByRole('button', { name: 'Remove ui' })).toBeNull();
  });

  test('what a task waits for is listed, removed and added one by one', async () => {
    serve(plan());
    render(<PlanQueueView taskId="UNA-1" />);

    await openRow('api');
    const repos = within(screen.getByRole('group', { name: 'api repositories' }));
    fireEvent.click(repos.getByRole('button', { name: 'web' }));
    expect(repos.getByRole('button', { name: 'web' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(screen.getByRole('group', { name: 'api waits for' })).getByText(/nothing/)).toBeInTheDocument();

    await openRow('ui');
    fireEvent.click(screen.getByRole('button', { name: 'Stop waiting for api' }));
    expect(screen.queryByRole('button', { name: 'Stop waiting for api' })).toBeNull();
    fireEvent.change(screen.getByLabelText('Add a task ui waits for'), { target: { value: 'api' } });
    expect(screen.getByRole('button', { name: 'Stop waiting for api' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Add a task ui waits for')).toBeNull();  // nothing left to add
    fireEvent.change(screen.getByLabelText('Add a task api waits for'), { target: { value: '' } });
    expect(screen.queryByRole('button', { name: 'Stop waiting for ui' })).toBeNull();
  });

  test('one click puts a repository in every task — and takes it out again', async () => {
    serve(plan());
    editPlanQueueSteps.mockReturnValue(ok(plan()));
    render(<PlanQueueView taskId="UNA-1" />);
    const every = within(await screen.findByRole('group', { name: 'Every task works in' }));
    // api works only in api; ui only in web: each repository is in some tasks.
    expect(every.getByRole('button', { name: /api/ })).toHaveAttribute('aria-pressed', 'mixed');
    expect(every.getAllByText('1/2')).toHaveLength(2);  // one task of two has each

    fireEvent.click(every.getByRole('button', { name: /web/ }));
    expect(every.getByRole('button', { name: 'web' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(editPlanQueueSteps).toHaveBeenCalled());
    const [, saved] = editPlanQueueSteps.mock.calls[0];
    expect(saved.map((step) => step.repositories)).toEqual([['api', 'web'], ['web']]);

    fireEvent.click(every.getByRole('button', { name: 'web' }));
    expect(every.getByRole('button', { name: 'web' })).toHaveAttribute('aria-pressed', 'false');
  });

  test('Add repository picks one from the dev folder and puts it in every task', async () => {
    serve(plan());
    editPlanQueueSteps.mockReturnValue(ok(plan()));
    fetchInventoryRepositories.mockResolvedValue({ ok: true, body: { repositories: [
      { id: 'api', repo_slug: 'api' }, { id: 'core-lib', repo_slug: 'core-lib', local_path: '/dev/core-lib' },
    ] } });
    render(<PlanQueueView taskId="UNA-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Add repository' }));
    const row = await screen.findByText('core-lib', { selector: '.adopt-session-cwd' });
    expect(screen.queryByText('api', { selector: '.adopt-session-cwd' })).toBeNull();  // already in the split
    fireEvent.click(row);
    fireEvent.click(screen.getByRole('button', { name: 'Add to every task' }));

    const every = within(screen.getByRole('group', { name: 'Every task works in' }));
    expect(every.getByRole('button', { name: 'core-lib' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(editPlanQueueSteps).toHaveBeenCalled());
    const [, saved, added] = editPlanQueueSteps.mock.calls[0];
    expect(saved.map((step) => step.repositories)).toEqual([['api', 'core-lib'], ['web', 'core-lib']]);
    expect(added).toEqual(['core-lib']);
  });

  test('revise sends the feedback, and needs some', async () => {
    serve(plan());
    revisePlanQueue.mockReturnValue(ok(plan({ status: 'proposing', steps: [] })));
    render(<PlanQueueView taskId="UNA-1" />);

    const revise = await screen.findByRole('button', { name: 'Revise' });
    expect(revise).toBeDisabled();
    fireEvent.change(screen.getByLabelText('What should change'), { target: { value: 'merge the two' } });
    fireEvent.click(revise);

    await waitFor(() => expect(revisePlanQueue).toHaveBeenCalledWith('p1', 'merge the two'));
  });

  test('discard throws the split away and offers a new one', async () => {
    serve(plan());
    discardPlanQueue.mockReturnValue(ok({ plan_id: 'p1', discarded: true }));
    render(<PlanQueueView taskId="UNA-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));

    expect(await screen.findByRole('button', { name: 'Propose a split of plan.md' })).toBeInTheDocument();
    expect(discardPlanQueue).toHaveBeenCalledWith('p1');
  });
});

// One step in every state the board knows.
const BOARD = plan({
  status: 'running',
  steps: [
    step('blocked', { depends_on: ['active'], reason: 'waits for T-2' }),
    step('ready', { status: 'ready' }),
    step('active', { status: 'active', task_id: 'T-2', released_at: 10 }),
    step('review', { status: 'in_review', task_id: 'T-3', released_at: 10 }),
    step('merging', {
      status: 'awaiting_merge', task_id: 'T-4', released_at: 10,
      merge: { repos: [{ repository_id: 'api', state: 'open', url: 'https://git.example/pr/42' }] },
    }),
    step('nopr', {
      status: 'awaiting_merge', task_id: 'T-5', released_at: 10,
      reason: 'no pull request found — no changes? Count it as done',
    }),
    step('done', {
      status: 'satisfied', task_id: 'T-6', released_at: 10,
      merge: { repos: [{ repository_id: 'api', state: 'merged', url: '' }] },
    }),
    step('declined', {
      status: 'failed', reason_code: 'pr_declined', reason: 'the pull request was declined',
      task_id: 'T-7', released_at: 10,
    }),
    step('gone', { status: 'failed', reason_code: 'task_gone', reason: 'the task is gone', task_id: 'T-8' }),
    step('dropped', { status: 'cancelled' }),
  ],
});

// Waits for the board, then scopes queries to one of its columns.
async function column(name) {
  return within(await screen.findByRole('region', { name }));
}

function cardsIn(name) {
  return [...screen.getByRole('region', { name }).querySelectorAll('[data-step]')]
    .map((card) => card.getAttribute('data-step'));
}

describe('PlanQueueView — the board', () => {
  test('every step lands in the column its status names', async () => {
    serve(BOARD);
    render(<PlanQueueView taskId="UNA-1" />);
    expect(await screen.findByRole('region', { name: 'Plan board — UNA-1' })).toBeInTheDocument();

    expect(cardsIn('Waiting')).toEqual(['blocked', 'ready', 'dropped']);
    expect(cardsIn('In progress')).toEqual(['active', 'gone']);
    expect(cardsIn('In review')).toEqual(['review']);
    expect(cardsIn('Waiting for merge')).toEqual(['merging', 'nopr', 'declined']);
    expect(cardsIn('Done')).toEqual(['done']);
  });

  test('cards cannot be dragged or edited', async () => {
    serve(BOARD);
    const { container } = render(<PlanQueueView taskId="UNA-1" />);
    await screen.findByRole('region', { name: 'Done' });

    const board = container.querySelector('.plan-board');
    expect(board.querySelectorAll('[draggable="true"]')).toHaveLength(0);
    expect(board.querySelectorAll('input, textarea, select, [contenteditable]')).toHaveLength(0);
  });

  test('only a stuck card offers a way out', async () => {
    serve(BOARD);
    const { container } = render(<PlanQueueView taskId="UNA-1" />);
    await screen.findByRole('region', { name: 'Done' });

    const actionsOf = (id) => [...container.querySelectorAll(`[data-step="${id}"] .plan-card-action`)]
      .map((button) => button.textContent);
    expect(actionsOf('declined')).toEqual(['Retry', 'Count as done']);
    expect(actionsOf('gone')).toEqual(['Retry', 'Count as done']);
    expect(actionsOf('nopr')).toEqual(['Count as done']);
    for (const healthy of ['blocked', 'ready', 'active', 'review', 'merging', 'done', 'dropped']) {
      expect(actionsOf(healthy)).toEqual([]);
    }
  });

  test('a stuck card\'s action reaches its step', async () => {
    serve(BOARD);
    planQueueStepAction.mockReturnValue(ok(BOARD));
    render(<PlanQueueView taskId="UNA-1" />);

    fireEvent.click((await column('Waiting for merge')).getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(planQueueStepAction).toHaveBeenCalledWith('p1', 'declined', 'retry'));
  });

  test('cards show the code, why they wait, and their pull requests', async () => {
    serve(BOARD);
    render(<PlanQueueView taskId="UNA-1" />);

    const waiting = await column('Waiting');
    expect(waiting.getByText('waits for T-2')).toBeInTheDocument();
    expect(waiting.getByText('cancelled')).toBeInTheDocument();
    expect((await column('Waiting for merge')).getByRole('link', { name: 'api: open' }))
      .toHaveAttribute('href', 'https://git.example/pr/42');
    // A merged chip with no address is plain text, not a dead link.
    expect((await column('Done')).getByText('api: merged').tagName).toBe('SPAN');
  });

  test('a card\'s code opens its task; a step with no task yet shows its own id', async () => {
    serve(BOARD);
    const onOpenTask = vi.fn();
    render(<PlanQueueView taskId="UNA-1" onOpenTask={onOpenTask} />);

    fireEvent.click((await column('In progress')).getByRole('button', { name: 'T-2' }));

    expect(onOpenTask).toHaveBeenCalledWith('T-2');
    expect((await column('Waiting')).getByText('ready').tagName).toBe('CODE');
  });

  test('a running plan can be paused or stopped — Stop asks first', async () => {
    serve(BOARD);
    controlPlanQueue.mockReturnValue(ok({ ...BOARD, status: 'paused' }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<PlanQueueView taskId="UNA-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    expect(confirm).toHaveBeenCalled();
    expect(controlPlanQueue).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(controlPlanQueue).toHaveBeenCalledWith('p1', 'pause'));
    expect(await screen.findByRole('button', { name: 'Run' })).toBeInTheDocument();
    confirm.mockRestore();
  });

  test('a finished plan can be split again', async () => {
    serve({ ...BOARD, status: 'finished' });
    proposePlanQueue.mockReturnValue(ok(plan({ plan_id: 'p2', status: 'proposing', steps: [] })));
    fetchPlanQueue.mockImplementation((id) => Promise.resolve(
      id === 'p2' ? plan({ plan_id: 'p2', status: 'proposing', steps: [] }) : { ...BOARD, status: 'finished' },
    ));
    render(<PlanQueueView taskId="UNA-1" />);

    expect(screen.queryByRole('button', { name: 'Run' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Split the plan again' }));
    // Back to the start: a split folder or plan.md, the operator's choice again.
    fireEvent.click(await screen.findByRole('button', { name: 'Propose a split of plan.md' }));

    expect(await screen.findByText(/splitting the plan in this task's chat/)).toBeInTheDocument();
  });
});

describe('PlanQueueView — a plan shared on the tracker', () => {
  test('creating sub-tasks is spelled out and always the operator\'s choice', async () => {
    serve(plan({ can_share: true, source_task_id: 'UNA-1' }));
    approvePlanQueue.mockReturnValue(ok(plan({ status: 'running' })));
    render(<PlanQueueView taskId="UNA-1" />);

    const create = await screen.findByRole('button', { name: 'Create 2 sub-tasks and start' });
    expect(create.getAttribute('data-tooltip')).toMatch(/Creates 2 sub-tasks of UNA-1 on the tracker, unassigned/);
    expect(screen.getByRole('button', { name: 'Keep them in kato only' }).getAttribute('data-tooltip'))
      .toMatch(/Nothing goes to the tracker/);
    expect(screen.queryByRole('button', { name: 'Approve and run' })).toBeNull();
    fireEvent.click(create);
    await waitFor(() => expect(approvePlanQueue).toHaveBeenCalledWith('p1', { run: true, shared: true }));
  });

  test('or kept in kato only, or created without running yet', async () => {
    serve(plan({ can_share: true }));
    approvePlanQueue.mockReturnValue(ok(plan({ status: 'approved' })));
    for (const [name, choice] of [['Keep them in kato only', { run: true, shared: false }],
                                  ['Create 2 sub-tasks', { run: false, shared: true }]]) {
      const { unmount } = render(<PlanQueueView taskId="UNA-1" />);
      fireEvent.click(await screen.findByRole('button', { name }));
      await waitFor(() => expect(approvePlanQueue).toHaveBeenLastCalledWith('p1', choice));
      unmount();
    }
  });

  test('a plan another kato drives is read-only here until taken over', async () => {
    const followed = {
      ...BOARD, mode: 'followed', driver_user: 'dana', source_task_id: 'UNA-1',
      steps: BOARD.steps.map((step) => (step.id === 'review' ? { ...step, worker: 'Dana' } : step)),
    };
    serve(followed);
    takeOverPlanQueue.mockReturnValue(ok({ ...followed, mode: 'tracker', status: 'paused' }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { container } = render(<PlanQueueView taskId="UNA-1" />);

    expect(await screen.findByText(/sub-tasks of UNA-1, driven by dana's kato/)).toBeInTheDocument();
    for (const name of ['Run', 'Pause', 'Stop']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    expect(container.querySelectorAll('.plan-card-action')).toHaveLength(0);  // even stuck cards
    expect(screen.getByText('with Dana')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(takeOverPlanQueue).toHaveBeenCalledWith('p1'));
    expect(await screen.findByText(/driven by this kato/)).toBeInTheDocument();
    confirm.mockRestore();
  });

  test('with no plan here, the tab finds the task\'s plan on the tracker', async () => {
    fetchSharedPlanQueue.mockResolvedValue({ ...BOARD, mode: 'followed', driver_user: 'dana' });
    fetchPlanQueue.mockResolvedValue({ ...BOARD, mode: 'followed', driver_user: 'dana' });
    render(<PlanQueueView taskId="UNA-7" />);
    expect(await screen.findByRole('region', { name: 'Plan board — UNA-7' })).toBeInTheDocument();
    expect(fetchSharedPlanQueue).toHaveBeenCalledWith('UNA-7');
  });
});

describe('the plan tab', () => {
  afterEach(() => { vi.useRealTimers(); });

  test('polls the plan only while its tab shows', async () => {
    vi.useFakeTimers();
    serve(BOARD);
    const { unmount } = render(<PlanQueueView taskId="UNA-1" />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const opened = fetchPlanQueue.mock.calls.length;
    expect(opened).toBeGreaterThan(0);

    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(fetchPlanQueue.mock.calls.length).toBeGreaterThan(opened);

    unmount();  // the tab was closed, or another tab shows
    const closed = fetchPlanQueue.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(fetchPlanQueue.mock.calls.length).toBe(closed);
  });

  test('a change to the plan refreshes the toolbar button at once, for that task only', async () => {
    render(<PlanQueueButton session={{ task_id: 'UNA-1' }} />);
    await waitFor(() => expect(fetchPlanQueues).toHaveBeenCalledTimes(1));
    act(() => planQueueChanges.notify('UNA-9'));
    expect(fetchPlanQueues).toHaveBeenCalledTimes(1);
    act(() => planQueueChanges.notify('UNA-1'));
    await waitFor(() => expect(fetchPlanQueues).toHaveBeenCalledTimes(2));
  });
});
