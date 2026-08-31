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
    render(<GlobalPermissionContainer activeTaskId="POJ-2" />);
    const heading = await screen.findByRole('heading');
    expect(heading).toHaveTextContent(/POJ-2.*wants permission/);
  });

  test('it opens for the task on screen', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-1')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-1" />);
    const heading = await screen.findByRole('heading');
    expect(heading).toHaveTextContent(/POJ-1.*wants permission/);
  });

  // This container WATCHES every task and OPENS for one. It used to open for
  // any of them, which meant a background task could throw a modal over
  // whatever the operator was doing on a different task — reported as "it
  // blocks my flow while I am working on another task".
  //
  // The ask is not dropped: it stays in the store, keeps the asking task's
  // tab badge lit and the title flashing, and opens the moment the operator
  // switches to that task.
  test('it does NOT open for a task the operator is not on', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    await waitFor(() => expect(fetchPendingPermissions).toHaveBeenCalled());
    expect(screen.queryByRole('heading')).toBeNull();
  });

  test('with no task open, nothing is thrown on screen', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
    render(<GlobalPermissionContainer activeTaskId="" />);
    await waitFor(() => expect(fetchPendingPermissions).toHaveBeenCalled());
    expect(screen.queryByRole('heading')).toBeNull();
  });

  test('switching to the asking task opens it', async () => {
    // The whole point of keeping the store global: the ask was already
    // there, so arriving at the task shows it without another round trip.
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
    const { rerender } = render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    await waitFor(() => expect(fetchPendingPermissions).toHaveBeenCalled());
    expect(screen.queryByRole('heading')).toBeNull();

    rerender(<GlobalPermissionContainer activeTaskId="POJ-9" />);
    const heading = await screen.findByRole('heading');
    expect(heading).toHaveTextContent(/POJ-9.*wants permission/);
  });

  test('switching AWAY closes it rather than dragging it along', async () => {
    // The on-screen ask is deliberately sticky so a poll cannot tear down a
    // half-filled answer form. That stickiness must not outlive the task it
    // belongs to.
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
    const { rerender } = render(<GlobalPermissionContainer activeTaskId="POJ-9" />);
    await screen.findByRole('heading');

    rerender(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    await waitFor(() => expect(screen.queryByRole('heading')).toBeNull());
  });

  test('a decision posts to the asking task and resolves it', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-2', 'req-9')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-2" />);
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
    render(<GlobalPermissionContainer activeTaskId="POJ-2" />);
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
    // Both on the SAME task: queueing is what happens when one task asks
    // twice. An ask from a DIFFERENT task no longer queues here at all — it
    // waits on its own tab until the operator goes there.
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-1', 'r1')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-1" />);
    expect(await screen.findByRole('heading')).toHaveTextContent(/POJ-1/);

    // The newcomer even arrives FIRST in the server's list.
    fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('POJ-1', 'r2'), _ask('POJ-1', 'r1')],
    });
    await act(async () => { await permissionStore.refresh(); });
    expect(screen.getByRole('heading')).toHaveTextContent(/POJ-1/);
    expect(screen.getByText(/1 more request waiting/i)).toBeInTheDocument();
  });

  test('an ask from another task does not queue behind this one', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-1', 'r1')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-1" />);
    await screen.findByRole('heading');

    fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('POJ-1', 'r1'), _ask('POJ-9', 'r9')],
    });
    await act(async () => { await permissionStore.refresh(); });
    // "1 more waiting" would be a lie: the other one is not waiting on THIS
    // dialog, and answering this one must not open it.
    expect(screen.queryByText(/more request waiting/i)).toBeNull();
  });

  test('the queued ask opens once the first is answered', async () => {
    fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('POJ-1', 'r1'), _ask('POJ-1', 'r2')],
    });
    render(<GlobalPermissionContainer activeTaskId="POJ-1" />);
    await screen.findByRole('heading');
    fireEvent.click(screen.getByRole('button', { name: /allow once/i }));
    await waitFor(() => {
      expect(screen.getByRole('heading')).toHaveTextContent(/POJ-1/);
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
    render(<GlobalPermissionContainer activeTaskId="POJ-2" />);
    await screen.findByRole('heading');
    expect(document.title).toMatch(/Approval needed/);
  });

  test('the count covers EVERY task, not just the one on screen', async () => {
    // The title is the signal that survives the dialog being scoped: an ask
    // on a background task opens nothing, so this is one of the few things
    // telling the operator it exists. Counting only the visible task would
    // leave a blocked agent completely silent.
    fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('POJ-1', 'r1'), _ask('POJ-2', 'r2')],
    });
    render(<GlobalPermissionContainer activeTaskId="POJ-1" />);
    await waitFor(() => {
      expect(document.title).toMatch(/^\(2\) Approval needed/);
    });
  });

  test('it still flashes for a task that is NOT on screen', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9', 'r9')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    await waitFor(() => {
      expect(document.title).toMatch(/Approval needed/);
    });
    // ...without opening anything over the task being worked on.
    expect(screen.queryByRole('heading')).toBeNull();
  });

  test('no pending asks leaves the title alone', async () => {
    fetchPendingPermissions.mockResolvedValue({ pending: [] });
    render(<GlobalPermissionContainer />);
    await waitFor(() => expect(fetchPendingPermissions).toHaveBeenCalled());
    expect(document.title).toBe('Kato — Planning UI');
  });
});
