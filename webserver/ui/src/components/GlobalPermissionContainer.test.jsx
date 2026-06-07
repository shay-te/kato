// Tests for GlobalPermissionContainer — the cross-task permission feed that
// pops the modal for asks on BACKGROUND tasks (the focused task is handled by
// its own SSE container). Contracts:
//   1. An ask on a task OTHER than the focused one surfaces, titled with the
//      task code.
//   2. The focused task's own ask is NOT duplicated here (SSE owns it).
//   3. A decision posts to the RIGHT task's permission endpoint.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchPendingPermissions: vi.fn(),
  postSession: vi.fn(),
}));

import { fetchPendingPermissions, postSession } from '../api.js';
import GlobalPermissionContainer from './GlobalPermissionContainer.jsx';

function _ask(taskId, requestId = 'r1', tool = 'Bash') {
  return {
    task_id: taskId,
    type: 'control_request',
    request_id: requestId,
    request: { request_id: requestId, tool_name: tool, input: { command: 'mvn' } },
  };
}

const _memory = { recall: () => null, remember: vi.fn() };

beforeEach(() => {
  fetchPendingPermissions.mockReset();
  postSession.mockReset();
  postSession.mockResolvedValue({ ok: true });
});
afterEach(() => { vi.restoreAllMocks(); });


describe('GlobalPermissionContainer', () => {

  test('pops a titled modal for an ask on a background task', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-2')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-1" toolMemory={_memory} />);
    const heading = await screen.findByRole('heading');
    expect(heading).toHaveTextContent('POJ-2 wants permission');
  });

  test('does NOT duplicate the focused task\'s own ask (SSE owns it)', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-1')] });
    const { container } = render(
      <GlobalPermissionContainer activeTaskId="POJ-1" toolMemory={_memory} />,
    );
    // Give the poll a tick; nothing should render for the focused task.
    await waitFor(() => expect(fetchPendingPermissions).toHaveBeenCalled());
    expect(container.querySelector('#permission-modal')).toBeNull();
  });

  test('a decision posts to the asking task, not the focused one', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-2', 'req-9')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-1" toolMemory={_memory} />);
    fireEvent.click(await screen.findByRole('button', { name: /allow once/i }));
    await waitFor(() => expect(postSession).toHaveBeenCalled());
    expect(postSession).toHaveBeenCalledWith(
      'POJ-2', 'permission',
      expect.objectContaining({ request_id: 'req-9', allow: true }),
    );
  });
});
