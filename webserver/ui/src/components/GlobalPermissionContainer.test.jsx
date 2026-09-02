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
import {
  APPROVAL_MODE_GLOBAL,
  APPROVAL_MODE_IN_CHAT,
  writeApprovalMode,
  _resetApprovalModePref,
} from '../utils/approvalModePref.js';

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
  // The ask renders INSIDE the chat now, portaled into the slot the chat
  // pane provides. A test that does not mount that slot is describing a
  // task whose chat is not on screen — which is a real state, but not the
  // one most of these are about.
  const slot = document.createElement('div');
  slot.id = 'chat-permission-slot';
  document.body.appendChild(slot);
});
afterEach(() => {
  vi.restoreAllMocks();
  document.getElementById('chat-permission-slot')?.remove();
});


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

// The operator chooses WHERE an ask appears — see utils/approvalModePref.js.
//
// Both modes watch every task; only the drawing differs. In-chat keeps a
// background task from covering the one you are working on; global restores
// kato's original interrupting dialog for operators who would rather be
// pulled away than have an agent sit blocked unnoticed.
describe('GlobalPermissionContainer — the approval-mode setting', () => {
  beforeEach(() => {
    globalThis.localStorage?.removeItem?.('kato.approvalMode.v1');
    _resetApprovalModePref();
  });
  afterEach(() => {
    globalThis.localStorage?.removeItem?.('kato.approvalMode.v1');
    _resetApprovalModePref();
  });

  test('in GLOBAL mode another task\u2019s ask opens over everything', () => {
    writeApprovalMode(APPROVAL_MODE_GLOBAL);
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    return screen.findByRole('heading').then((heading) => {
      expect(heading).toHaveTextContent(/POJ-9.*wants permission/);
    });
  });

  test('in GLOBAL mode it is a real modal, not a card in the chat', async () => {
    writeApprovalMode(APPROVAL_MODE_GLOBAL);
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
    const { container } = render(
      <GlobalPermissionContainer activeTaskId="POJ-9" />,
    );
    await screen.findByRole('heading');
    // The overlay carries aria-modal; the inline card deliberately does not.
    expect(container.querySelector('.modal.is-inline')).toBeNull();
    expect(document.querySelector('[aria-modal="true"]')).toBeTruthy();
  });

  test('in IN-CHAT mode another task\u2019s ask does NOT open', async () => {
    writeApprovalMode(APPROVAL_MODE_IN_CHAT);
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    await waitFor(() => expect(fetchPendingPermissions).toHaveBeenCalled());
    expect(screen.queryByRole('heading')).toBeNull();
  });

  test('in IN-CHAT mode the ask is an inline card, not a modal', async () => {
    writeApprovalMode(APPROVAL_MODE_IN_CHAT);
    fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
    render(<GlobalPermissionContainer activeTaskId="POJ-9" />);
    await screen.findByRole('heading');
    expect(document.querySelector('.modal.is-inline')).toBeTruthy();
    expect(document.querySelector('[aria-modal="true"]')).toBeNull();
  });

  test('the roster still lists waiting chats in BOTH modes', async () => {
    // It is the only signal in in-chat mode, and still useful in global mode
    // when several tasks are queued behind the open dialog.
    for (const mode of [APPROVAL_MODE_IN_CHAT, APPROVAL_MODE_GLOBAL]) {
      writeApprovalMode(mode);
      fetchPendingPermissions.mockResolvedValue({ pending: [_ask('POJ-9')] });
      const view = render(<GlobalPermissionContainer activeTaskId="POJ-1" />);
      await waitFor(() => {
        expect(screen.getAllByRole('status').length).toBeGreaterThan(0);
      });
      view.unmount();
    }
  });
});

// The header roster has to fit a bar that already carries the logo, the
// title and the scan status. The first version put an uppercase
// "WAITING FOR YOU" banner beside one pill per task; the flex row shrank the
// group, its nowrap children overflowed into each other, and the banner sat
// on top of the task id and clipped it.
describe('GlobalPermissionContainer — the header roster stays compact', () => {
  // Distinct request ids: the store keys asks by request id, so reusing the
  // default collapses several tasks into one.
  function askFor(taskId) {
    return _ask(taskId, `req-${taskId}`);
  }

  test('it shows a pill per waiting task, with no banner text', async () => {
    fetchPendingPermissions.mockResolvedValue({
      pending: [askFor('POJ-1'), askFor('POJ-2')],
    });
    render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    expect(await screen.findByRole('button', { name: /POJ-1/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /POJ-2/ })).toBeTruthy();
    // The count belongs to assistive tech, not to a banner taking header room.
    expect(screen.queryByText(/waiting for you/i)).toBeNull();
    expect(screen.getByRole('status'))
      .toHaveAttribute('aria-label', expect.stringMatching(/2 chats waiting/i));
  });

  test('beyond two, the rest collapse into a count', async () => {
    fetchPendingPermissions.mockResolvedValue({
      pending: [askFor('POJ-1'), askFor('POJ-2'), askFor('POJ-3'), askFor('POJ-4')],
    });
    render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    await screen.findByRole('button', { name: /POJ-1/ });
    expect(screen.queryByRole('button', { name: /POJ-3/ })).toBeNull();
    expect(screen.getByText('+2')).toBeTruthy();
  });

  test('the overflow names the tasks it could not show', async () => {
    fetchPendingPermissions.mockResolvedValue({
      pending: [askFor('POJ-1'), askFor('POJ-2'), askFor('POJ-3')],
    });
    render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    await screen.findByRole('button', { name: /POJ-1/ });
    expect(screen.getByText('+1')).toHaveAttribute('title', 'POJ-3');
  });

  test('clicking a pill opens that task', async () => {
    const onSelectTask = vi.fn();
    fetchPendingPermissions.mockResolvedValue({ pending: [askFor('POJ-9')] });
    render(
      <GlobalPermissionContainer
        activeTaskId="POJ-OTHER"
        onSelectTask={onSelectTask}
      />,
    );
    fireEvent.click(await screen.findByRole('button', { name: /POJ-9/ }));
    expect(onSelectTask).toHaveBeenCalledWith('POJ-9');
  });

  test('one ask per task, however many requests it has raised', async () => {
    // The roster lists CHATS, not requests — a task asking three times is
    // still one place to go.
    fetchPendingPermissions.mockResolvedValue({
      pending: [_ask('POJ-1', 'r1'), _ask('POJ-1', 'r2'), _ask('POJ-1', 'r3')],
    });
    render(<GlobalPermissionContainer activeTaskId="POJ-OTHER" />);
    await screen.findByRole('button', { name: /POJ-1/ });
    expect(screen.queryByText(/^\+/)).toBeNull();
    expect(screen.getByRole('status'))
      .toHaveAttribute('aria-label', expect.stringMatching(/1 chat waiting/i));
  });
});
