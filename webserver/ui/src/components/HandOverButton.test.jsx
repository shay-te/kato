import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

// The network edge only: the picker, the unpushed-work warning and the result
// formatter are the real ones.
vi.mock('../api.js', () => ({
  fetchHandoverAvailability: vi.fn(),
  fetchHandoverUsers: vi.fn(),
  handOverTask: vi.fn(),
  fetchTaskUnpushedWork: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => ({ toastResult: vi.fn() }));

import {
  fetchHandoverAvailability, fetchHandoverUsers, fetchTaskUnpushedWork, handOverTask,
} from '../api.js';
import { toastResult } from '../stores/toastStore.js';
import HandOverButton, {
  CHECKING_REMOVE_HERE, HANDOVER_TOOLTIP, START_BY_ITSELF_HINT, UNCHECKED_REMOVE_HERE, WAIT_FOR_THEM_HINT,
  forgetHandoverAvailability,
} from './HandOverButton.jsx';
import { KEPT_HERE_LINE } from './sessionHeaderFormatters.js';

const USERS = [{ handle: 'jane', name: 'Jane Doe' }, { handle: 'zed', name: 'zed' }];
const CLEAN = { repositories: [], unchecked: [] };
const UNPUSHED = { repositories: [{ repository: 'api', commits: 2, files: 0 }], unchecked: [] };

const headerButton = (container) => container.querySelector('#session-handover');

async function shown(taskId = 'UNA-1') {
  const view = render(<HandOverButton taskId={taskId} />);
  await waitFor(() => expect(headerButton(view.container)).not.toBeNull());
  return view;
}

async function pick(container, name = 'Jane Doe') {
  fireEvent.click(headerButton(container));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(await within(dialog).findByText(name));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Hand over' }));
}

describe('HandOverButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    forgetHandoverAvailability();
    fetchHandoverAvailability.mockResolvedValue({ available: true, reason: '' });
    fetchHandoverUsers.mockResolvedValue({ ok: true, body: { users: USERS } });
    fetchTaskUnpushedWork.mockResolvedValue(CLEAN);
    handOverTask.mockResolvedValue({
      ok: true,
      body: {
        status: 'handed_over',
        assignee: { handle: 'jane', name: 'Jane Doe' },
        steps: [{ step: 'assign', ok: true, detail: '' }, { step: 'open', ok: true, detail: '' }],
      },
    });
  });

  test('hidden for a task no one else\'s kato can see', async () => {
    fetchHandoverAvailability.mockResolvedValue({ available: false, reason: 'created in kato' });
    const { container } = render(<HandOverButton taskId="LOCAL-1" />);
    await waitFor(() => expect(fetchHandoverAvailability).toHaveBeenCalledWith('LOCAL-1'));
    expect(headerButton(container)).toBeNull();
  });

  test('hidden when the server cannot be asked', async () => {
    fetchHandoverAvailability.mockRejectedValue(new Error('offline'));
    const { container } = render(<HandOverButton taskId="UNA-1" />);
    await waitFor(() => expect(fetchHandoverAvailability).toHaveBeenCalled());
    expect(headerButton(container)).toBeNull();
  });

  test('says what it does: their kato picks it up, anyone assignable is listed', async () => {
    const { container } = await shown();
    expect(headerButton(container).getAttribute('data-tooltip')).toBe(HANDOVER_TOOLTIP);
    expect(HANDOVER_TOOLTIP).toMatch(/THEIR kato picks it up/);
  });

  test('picking someone with nothing unpushed hands it over and reports each step', async () => {
    const { container } = await shown();
    await pick(container);
    await waitFor(() => expect(handOverTask).toHaveBeenCalledWith('UNA-1', 'jane', 'Jane Doe', { waitForThem: true }));
    await waitFor(() => expect(toastResult).toHaveBeenCalled());
    expect(toastResult.mock.calls[0][0]).toMatchObject({ kind: 'success', title: 'Handed over to Jane Doe' });
  });

  test('work never pushed is warned about in hand-over words first', async () => {
    fetchTaskUnpushedWork.mockResolvedValue(UNPUSHED);
    const { container } = await shown();
    await pick(container);
    expect(await screen.findByText(/would not reach them/)).toBeTruthy();
    expect(handOverTask).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Hand over anyway' }));
    await waitFor(() => expect(handOverTask).toHaveBeenCalledWith('UNA-1', 'jane', 'Jane Doe', { waitForThem: true }));
  });

  test('cancelling the warning hands nothing over', async () => {
    fetchTaskUnpushedWork.mockResolvedValue(UNPUSHED);
    const { container } = await shown();
    await pick(container);
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText(/would not reach them/)).toBeNull());
    expect(handOverTask).not.toHaveBeenCalled();
  });

  test('the search narrows by name or login', async () => {
    const { container } = await shown();
    fireEvent.click(headerButton(container));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Jane Doe');
    fireEvent.change(within(dialog).getByPlaceholderText('Search by name or login…'), { target: { value: 'ZED' } });
    expect(within(dialog).queryByText('Jane Doe')).toBeNull();
    expect(within(dialog).getByText('zed')).toBeTruthy();
    fireEvent.change(within(dialog).getByPlaceholderText('Search by name or login…'), { target: { value: 'nobody' } });
    expect(within(dialog).getByText('No one matches.')).toBeTruthy();
  });

  test('a tracker that will not say who the ticket can go to says why', async () => {
    fetchHandoverUsers.mockResolvedValue({ ok: false, status: 502, body: { error: 'YouTrack is down' } });
    const { container } = await shown();
    fireEvent.click(headerButton(container));
    expect(await screen.findByText('YouTrack is down')).toBeTruthy();
  });

  test('no one listed and an odd answer both read as empty', async () => {
    fetchHandoverUsers.mockResolvedValue({ ok: true, body: { users: 'junk' } });
    const { container } = await shown();
    fireEvent.click(headerButton(container));
    expect(await screen.findByText('The tracker lists no one this ticket can go to.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  test('a refusal comes back red', async () => {
    handOverTask.mockResolvedValue({ ok: false, status: 409, body: { error: "UNA-1's agent is working" } });
    const { container } = await shown();
    await pick(container);
    await waitFor(() => expect(toastResult).toHaveBeenCalled());
    expect(toastResult.mock.calls[0][0]).toMatchObject({
      kind: 'error', title: 'Not handed over', message: "UNA-1's agent is working",
    });
  });

  test('a task seen before shows its button at once, an unknown one starts from the last answer', async () => {
    const first = await shown('UNA-1');
    first.unmount();
    let resolve;
    fetchHandoverAvailability.mockReturnValue(new Promise((done) => { resolve = done; }));
    const again = render(<HandOverButton taskId="UNA-1" />);
    expect(headerButton(again.container)).not.toBeNull();
    again.rerender(<HandOverButton taskId="UNA-2" />);
    expect(headerButton(again.container)).not.toBeNull();
    resolve({ available: false });
    await waitFor(() => expect(headerButton(again.container)).toBeNull());
  });

  describe('their kato waits for them', () => {
    const waits = (dialog) => within(dialog).getByRole('checkbox', { name: /Their kato waits for them/ });

    async function openPicker(container) {
      fireEvent.click(headerButton(container));
      const dialog = await screen.findByRole('dialog');
      await within(dialog).findByText('Jane Doe');
      return dialog;
    }

    test('on: the ticket is tagged kato:wait-editing, and the picker says so', async () => {
      const { container } = await shown();
      const dialog = await openPicker(container);
      expect(waits(dialog)).toBeChecked();
      expect(within(dialog).getByText(WAIT_FOR_THEM_HINT)).toBeTruthy();
    });

    test('off: their kato starts on it by itself, said before and sent', async () => {
      const { container } = await shown();
      const dialog = await openPicker(container);
      fireEvent.click(waits(dialog));
      expect(waits(dialog)).not.toBeChecked();
      expect(within(dialog).getByText(START_BY_ITSELF_HINT)).toBeTruthy();
      fireEvent.click(within(dialog).getByText('Jane Doe'));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Hand over' }));
      await waitFor(() => expect(handOverTask).toHaveBeenCalledWith('UNA-1', 'jane', 'Jane Doe', { waitForThem: false }));
    });

    test('off and warned about unpushed work, the choice survives the warning', async () => {
      fetchTaskUnpushedWork.mockResolvedValue(UNPUSHED);
      const { container } = await shown();
      const dialog = await openPicker(container);
      fireEvent.click(waits(dialog));
      fireEvent.click(within(dialog).getByText('Jane Doe'));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Hand over' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Hand over anyway' }));
      await waitFor(() => expect(handOverTask).toHaveBeenCalledWith('UNA-1', 'jane', 'Jane Doe', { waitForThem: false }));
    });

    test('a person reads as initials, name and login; the login only when it says more', async () => {
      const { container } = await shown();
      const dialog = await openPicker(container);
      expect(within(dialog).getByText('JD')).toBeTruthy();
      expect(within(dialog).getByText('jane')).toBeTruthy();
      expect(within(dialog).getAllByText('zed')).toHaveLength(1);  // name == login: shown once
      expect(within(dialog).getByText('ZE')).toBeTruthy();
    });

    test('on again every time the picker opens', async () => {
      const { container } = await shown();
      let dialog = await openPicker(container);
      fireEvent.click(waits(dialog));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      dialog = await openPicker(container);
      expect(waits(dialog)).toBeChecked();
    });
  });

  describe('also delete it from my tasks', () => {
    const PLAN_SOURCE = 'a plan was split from this task here: deleting it would delete the plan\'s sub-tasks too';
    const LANDED = {
      ok: true,
      body: {
        status: 'handed_over', landed: true, assignee: { handle: 'jane', name: 'Jane Doe' },
        steps: [{ step: 'assign', ok: true, detail: '' }, { step: 'open', ok: true, detail: '' }],
      },
    };

    beforeEach(() => {
      fetchHandoverAvailability.mockResolvedValue({ available: true, reason: '', remove_here_reason: '' });
      handOverTask.mockResolvedValue(LANDED);
    });

    async function openPicker(onRemoveHere) {
      const view = render(<HandOverButton taskId="UNA-1" onRemoveHere={onRemoveHere} />);
      await waitFor(() => expect(headerButton(view.container)).not.toBeNull());
      fireEvent.click(headerButton(view.container));
      const dialog = await screen.findByRole('dialog');
      await within(dialog).findByText('Jane Doe');
      return dialog;
    }

    const option = (dialog) => within(dialog).getByRole('checkbox', { name: /Also delete it from my tasks/ });

    async function confirm(dialog, label) {
      fireEvent.click(within(dialog).getByText('Jane Doe'));
      fireEvent.click(within(dialog).getByRole('button', { name: label }));
    }

    test('not offered when the header has no delete to run', async () => {
      const dialog = await openPicker(undefined);
      expect(within(dialog).queryByRole('checkbox', { name: /Also delete it from my tasks/ })).toBeNull();
      expect(fetchHandoverAvailability).toHaveBeenCalledTimes(1);  // only the button's own question
    });

    test('ticked, the task is deleted here once the ticket is theirs', async () => {
      const onRemoveHere = vi.fn();
      const dialog = await openPicker(onRemoveHere);
      await waitFor(() => expect(option(dialog)).not.toBeDisabled());
      expect(option(dialog)).not.toBeChecked();  // a fresh choice every time
      fireEvent.click(option(dialog));
      await confirm(dialog, 'Hand over and delete here');
      await waitFor(() => expect(onRemoveHere).toHaveBeenCalledWith('UNA-1'));
      expect(handOverTask).toHaveBeenCalledWith('UNA-1', 'jane', 'Jane Doe', { waitForThem: true });
      expect(toastResult.mock.calls[0][0]).toMatchObject({ kind: 'success', title: 'Handed over to Jane Doe' });
      // The hand-over's own toast came first, the delete's own after it.
      expect(toastResult.mock.invocationCallOrder[0]).toBeLessThan(onRemoveHere.mock.invocationCallOrder[0]);
    });

    test('ticked, a hand-over that stopped half way keeps the task here and says so', async () => {
      handOverTask.mockResolvedValue({
        ok: true,
        body: {
          status: 'handed_over_with_problems', landed: false, assignee: { handle: 'jane', name: 'Jane Doe' },
          steps: [{ step: 'assign', ok: true, detail: '' }, { step: 'tag', ok: false, detail: 'tag store down' }],
        },
      });
      const onRemoveHere = vi.fn();
      const dialog = await openPicker(onRemoveHere);
      await waitFor(() => expect(option(dialog)).not.toBeDisabled());
      fireEvent.click(option(dialog));
      await confirm(dialog, 'Hand over and delete here');
      await waitFor(() => expect(toastResult).toHaveBeenCalled());
      expect(onRemoveHere).not.toHaveBeenCalled();
      const toast = toastResult.mock.calls[0][0];
      expect(toast.kind).toBe('error');
      expect(toast.message.split('\n')).toContain(KEPT_HERE_LINE);
    });

    test('ticked, a refused hand-over deletes nothing', async () => {
      handOverTask.mockResolvedValue({ ok: false, status: 409, body: { error: "UNA-1's agent is working" } });
      const onRemoveHere = vi.fn();
      const dialog = await openPicker(onRemoveHere);
      await waitFor(() => expect(option(dialog)).not.toBeDisabled());
      fireEvent.click(option(dialog));
      await confirm(dialog, 'Hand over and delete here');
      await waitFor(() => expect(toastResult).toHaveBeenCalled());
      expect(onRemoveHere).not.toHaveBeenCalled();
    });

    test('left unticked, the task stays in my tasks', async () => {
      const onRemoveHere = vi.fn();
      const dialog = await openPicker(onRemoveHere);
      await waitFor(() => expect(option(dialog)).not.toBeDisabled());
      await confirm(dialog, 'Hand over');
      await waitFor(() => expect(toastResult).toHaveBeenCalled());
      expect(onRemoveHere).not.toHaveBeenCalled();
    });

    test('ticked with work never pushed, the warning says it is deleted for good too', async () => {
      fetchTaskUnpushedWork.mockResolvedValue(UNPUSHED);
      const onRemoveHere = vi.fn();
      const dialog = await openPicker(onRemoveHere);
      await waitFor(() => expect(option(dialog)).not.toBeDisabled());
      fireEvent.click(option(dialog));
      await confirm(dialog, 'Hand over and delete here');
      expect(await screen.findByText(/deleting the task here deletes it, with no backup/)).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Hand over and delete anyway' }));
      await waitFor(() => expect(onRemoveHere).toHaveBeenCalledWith('UNA-1'));
    });

    test('the task a plan was split from: not offered, asked again when the picker opens', async () => {
      // The header's own question said yes; the plan was split since.
      fetchHandoverAvailability
        .mockResolvedValueOnce({ available: true, reason: '', remove_here_reason: '' })
        .mockResolvedValueOnce({ available: true, reason: '', remove_here_reason: PLAN_SOURCE });
      const onRemoveHere = vi.fn();
      const dialog = await openPicker(onRemoveHere);
      await waitFor(() => expect(within(dialog).getByText(`Not offered — ${PLAN_SOURCE}.`)).toBeTruthy());
      expect(option(dialog)).toBeDisabled();
      fireEvent.click(option(dialog));
      await confirm(dialog, 'Hand over');
      await waitFor(() => expect(toastResult).toHaveBeenCalled());
      expect(onRemoveHere).not.toHaveBeenCalled();
    });

    test('off until the server answers, and off when it cannot say', async () => {
      let answer;
      fetchHandoverAvailability
        .mockResolvedValueOnce({ available: true, reason: '', remove_here_reason: '' })
        .mockReturnValueOnce(new Promise((done) => { answer = done; }));
      const dialog = await openPicker(vi.fn());
      expect(option(dialog)).toBeDisabled();
      expect(within(dialog).getByText(`Not offered — ${CHECKING_REMOVE_HERE}.`)).toBeTruthy();
      answer({ available: true, reason: '' });  // an older kato: says nothing about it
      await waitFor(() => expect(within(dialog).getByText(`Not offered — ${UNCHECKED_REMOVE_HERE}.`)).toBeTruthy());
      expect(option(dialog)).toBeDisabled();
    });

    test('a question that fails leaves it off', async () => {
      fetchHandoverAvailability
        .mockResolvedValueOnce({ available: true, reason: '', remove_here_reason: '' })
        .mockRejectedValueOnce(new Error('offline'));
      const dialog = await openPicker(vi.fn());
      await waitFor(() => expect(within(dialog).getByText(`Not offered — ${UNCHECKED_REMOVE_HERE}.`)).toBeTruthy());
      expect(option(dialog)).toBeDisabled();
    });

    test('an answer that arrives after the picker closed is dropped', async () => {
      let answer;
      fetchHandoverAvailability
        .mockResolvedValueOnce({ available: true, reason: '', remove_here_reason: '' })
        .mockReturnValueOnce(new Promise((done) => { answer = done; }));
      const dialog = await openPicker(vi.fn());
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      answer({ remove_here_reason: '' });
      await Promise.resolve();
      expect(screen.queryByRole('dialog')).toBeNull();
    });

    test('a failed question after the picker closed is dropped too', async () => {
      let refuse;
      fetchHandoverAvailability
        .mockResolvedValueOnce({ available: true, reason: '', remove_here_reason: '' })
        .mockReturnValueOnce(new Promise((_done, fail) => { refuse = fail; }));
      const dialog = await openPicker(vi.fn());
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      refuse(new Error('offline'));
      await Promise.resolve();
      expect(screen.queryByRole('dialog')).toBeNull();
    });
  });
});
