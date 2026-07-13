// Tests for GlobalPermissionContainer — the SINGLE permission-approval
// modal for EVERY task, driven by the shared permissionStore. Contracts:
//   1. A pending ask (any task) surfaces, titled with the task code.
//   2. It surfaces the ask regardless of which task is focused (single
//      source of truth — no active-task exclusion any more).
//   3. A decision posts to the ASKING task and resolves the ask locally.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

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
