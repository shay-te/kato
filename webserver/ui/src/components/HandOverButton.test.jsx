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
import HandOverButton, { HANDOVER_TOOLTIP, forgetHandoverAvailability } from './HandOverButton.jsx';

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
    await waitFor(() => expect(handOverTask).toHaveBeenCalledWith('UNA-1', 'jane', 'Jane Doe'));
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
    await waitFor(() => expect(handOverTask).toHaveBeenCalledWith('UNA-1', 'jane', 'Jane Doe'));
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
});
