// Tests for GlobalPermissionContainer — the SINGLE permission-approval
// modal for EVERY task, driven by the shared permissionStore. Contracts:
//   1. A pending ask (any task) surfaces, titled with the task code.
//   2. It surfaces the ask regardless of which task is focused (single
//      source of truth — no active-task exclusion any more).
//   3. A decision posts to the ASKING task and resolves the ask locally.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchPendingPermissions: vi.fn(),
  postSession: vi.fn(),
}));

import { fetchPendingPermissions, postSession } from '../api.js';
import { permissionStore } from '../stores/permissionStore.js';
import GlobalPermissionContainer from './GlobalPermissionContainer.jsx';

function _ask(taskId, requestId = 'r1', tool = 'Bash') {
  return {
    task_id: taskId,
    type: 'control_request',
    request_id: requestId,
    request: { request_id: requestId, tool_name: tool, input: { command: 'mvn' } },
  };
}

beforeEach(() => {
  permissionStore.__resetForTests();
  fetchPendingPermissions.mockReset();
  postSession.mockReset();
  postSession.mockResolvedValue({ ok: true });
});
afterEach(() => { vi.restoreAllMocks(); });


describe('GlobalPermissionContainer', () => {

  test('pops a titled modal for a pending ask', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-2')] });
    render(<GlobalPermissionContainer />);
    const heading = await screen.findByRole('heading');
    expect(heading).toHaveTextContent(/POJ-2.*wants permission/);
  });

  test('surfaces the ask for ANY task (single source — no active-task gap)', async () => {
    // Previously the focused task was excluded here; now this container is
    // the sole owner, so the ask must surface no matter which task it is.
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-1')] });
    render(<GlobalPermissionContainer />);
    const heading = await screen.findByRole('heading');
    expect(heading).toHaveTextContent(/POJ-1.*wants permission/);
  });

  test('a decision posts to the asking task and resolves it', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-2', 'req-9')] });
    render(<GlobalPermissionContainer />);
    fireEvent.click(await screen.findByRole('button', { name: /allow once/i }));
    await waitFor(() => expect(postSession).toHaveBeenCalled());
    expect(postSession).toHaveBeenCalledWith(
      'POJ-2', 'permission',
      expect.objectContaining({ request_id: 'req-9', allow: true, remember: false }),
    );
    // The modal closes once the decision resolves the ask.
    await waitFor(() => {
      expect(screen.queryByRole('heading')).toBeNull();
    });
  });

  test('"Allow always" forwards remember=true so the backend persists it', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-2', 'req-9')] });
    render(<GlobalPermissionContainer />);
    fireEvent.click(await screen.findByRole('button', { name: /allow always/i }));
    await waitFor(() => expect(postSession).toHaveBeenCalled());
    expect(postSession).toHaveBeenCalledWith(
      'POJ-2', 'permission',
      expect.objectContaining({ request_id: 'req-9', allow: true, remember: true }),
    );
  });
});

describe('GlobalPermissionContainer — the open ask is never swapped out', () => {
  // The dialog used to re-pick the list's head on every poll. The store
  // rebuilds its map from the SERVER's list, so a second ask (or a reordered
  // one) replaced the ask being answered — and an AskUserQuestion form the
  // operator had half filled in was torn down with everything typed in it.

  test('a second ask queues behind the one on screen', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-1', 'r1')] });
    render(<GlobalPermissionContainer />);
    expect(await screen.findByRole('heading')).toHaveTextContent(/POJ-1/);

    // The newcomer even arrives FIRST in the server's list.
    fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('POJ-2', 'r2'), _ask('POJ-1', 'r1')],
    });
    await act(async () => { await permissionStore.refresh(); });
    expect(screen.getByRole('heading')).toHaveTextContent(/POJ-1/);
    expect(screen.getByText(/1 more request waiting/i)).toBeInTheDocument();
  });

  test('the queued ask opens once the first is answered', async () => {
    fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('POJ-1', 'r1'), _ask('POJ-2', 'r2')],
    });
    render(<GlobalPermissionContainer />);
    await screen.findByRole('heading');
    fireEvent.click(screen.getByRole('button', { name: /allow once/i }));
    await waitFor(() => {
      expect(screen.getByRole('heading')).toHaveTextContent(/POJ-2/);
    });
  });
});

describe('GlobalPermissionContainer — the tab title says something is waiting', () => {
  beforeEach(() => {
    document.title = 'Kato — Planning UI';
    Object.defineProperty(document, 'hidden', {
      configurable: true, get: () => true,
    });
  });
  afterEach(() => { document.title = 'Kato — Planning UI'; });

  test('a pending ask flashes the title while the tab is in the background', async () => {
    // The desktop notification already fired, but notifications get
    // missed — and the agent stays blocked for as long as nobody notices.
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-2')] });
    render(<GlobalPermissionContainer />);
    await screen.findByRole('heading');
    expect(document.title).toMatch(/Approval needed/);
  });

  test('the count is shown when more than one task is waiting', async () => {
    fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('POJ-1', 'r1'), _ask('POJ-2', 'r2')],
    });
    render(<GlobalPermissionContainer />);
    await screen.findByRole('heading');
    expect(document.title).toMatch(/^\(2\) Approval needed/);
  });

  test('no pending asks leaves the title alone', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [] });
    render(<GlobalPermissionContainer />);
    await waitFor(() => expect(fetchPendingPermissions).toHaveBeenCalled());
    expect(document.title).toBe('Kato — Planning UI');
  });
});
