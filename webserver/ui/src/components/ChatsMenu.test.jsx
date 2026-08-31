// Tests for ChatsMenu — the session-header dropdown that starts a fresh
// chat or navigates back to one of the task's previous conversations.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskChats: vi.fn(),
  startTaskChat: vi.fn(),
  renameTaskChat: vi.fn(),
  // Default: one backend wired, so the menu shows the plain "New chat"
  // button. Tests that exercise the picker override this.
  fetchAgentBackends: vi.fn().mockResolvedValue({ backends: ['claude'] }),
  // The adoption picker moved INTO this menu, so its api surface has to be
  // mocked here too.
  fetchAgentSessions: vi.fn().mockResolvedValue({ sessions: [] }),
  adoptAgentSession: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn(), errorFromResult: vi.fn() },
}));

import ChatsMenu from './ChatsMenu.jsx';
import {
  adoptAgentSession, fetchAgentBackends, fetchAgentSessions, fetchTaskChats,
  renameTaskChat,
  startTaskChat,
} from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { toast } from '../stores/toastStore.js';

const CHATS = [
  {
    [AGENT_SESSION_ID]: 'current-session-id',
    active: true,
    turn_count: 5,
    first_user_message: 'build the feature',
  },
  {
    [AGENT_SESSION_ID]: 'older-session-id',
    active: false,
    turn_count: 12,
    first_user_message: 'fix the bug',
  },
];

describe('ChatsMenu', () => {
  beforeEach(() => {
    fetchTaskChats.mockReset();
    startTaskChat.mockReset();
    toast.show.mockReset();
    toast.errorFromResult.mockReset();
    fetchTaskChats.mockResolvedValue({ chats: CHATS });
    startTaskChat.mockResolvedValue({ ok: true, body: {} });
  });

  test('opening the menu lists the task\'s chats, active first', async () => {
    render(<ChatsMenu taskId="T1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    expect(fetchTaskChats).toHaveBeenCalledWith('T1', '');
    await waitFor(() => {
      expect(screen.getByText('build the feature')).toBeInTheDocument();
    });
    expect(screen.getByText('fix the bug')).toBeInTheDocument();
    expect(screen.getByText('current')).toBeInTheDocument();   // active marker
    expect(screen.getByText('12 turns')).toBeInTheDocument();  // previous chat meta
    expect(screen.queryByText(/current-session-id/)).not.toBeInTheDocument();
    expect(screen.queryByText(/older-session-id/)).not.toBeInTheDocument();
  });

  test('"New chat" detaches with an empty id and notifies the parent', async () => {
    const onChatChanged = vi.fn();
    startTaskChat.mockResolvedValue({
      ok: true, body: { [AGENT_SESSION_ID]: '' },
    });
    render(<ChatsMenu taskId="T1" onChatChanged={onChatChanged} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    fireEvent.click(await screen.findByRole('button', { name: /new chat/i }));
    await waitFor(() => {
      expect(startTaskChat).toHaveBeenCalledWith('T1', '', '');
    });
    expect(onChatChanged).toHaveBeenCalledWith({ [AGENT_SESSION_ID]: '' });
    expect(toast.show).toHaveBeenCalled();
    // Menu closed after the action.
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  test('picking a previous chat switches to its session id', async () => {
    const onChatChanged = vi.fn();
    startTaskChat.mockResolvedValue({
      ok: true, body: { [AGENT_SESSION_ID]: 'older-session-id' },
    });
    render(<ChatsMenu taskId="T1" onChatChanged={onChatChanged} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    fireEvent.click((await screen.findByText('fix the bug')).closest('button'));
    await waitFor(() => {
      expect(startTaskChat).toHaveBeenCalledWith('T1', 'older-session-id', '');
    });
    expect(onChatChanged).toHaveBeenCalledWith({
      [AGENT_SESSION_ID]: 'older-session-id',
    });
  });

  test('picking the active chat just closes the menu — no API call', async () => {
    render(<ChatsMenu taskId="T1" onChatChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    fireEvent.click((await screen.findByText('build the feature')).closest('button'));
    expect(startTaskChat).not.toHaveBeenCalled();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  test('a failed switch surfaces a toast and keeps the menu open', async () => {
    const onChatChanged = vi.fn();
    startTaskChat.mockResolvedValue({ ok: false, status: 400, body: { error: 'nope' } });
    render(<ChatsMenu taskId="T1" onChatChanged={onChatChanged} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    fireEvent.click((await screen.findByText('fix the bug')).closest('button'));
    await waitFor(() => {
      expect(toast.errorFromResult).toHaveBeenCalled();
    });
    expect(onChatChanged).not.toHaveBeenCalled();
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  test('chat-list fetch failure shows the error in the menu', async () => {
    fetchTaskChats.mockRejectedValue(new Error('boom'));
    render(<ChatsMenu taskId="T1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });

  test('no chats yet → hint plus the New chat action', async () => {
    fetchTaskChats.mockResolvedValue({ chats: [] });
    render(<ChatsMenu taskId="T1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    await waitFor(() => {
      expect(screen.getByText(/no chats yet/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /new chat/i })).toBeInTheDocument();
  });

  test('mid-turn: first click arms a warning, second click confirms the switch', async () => {
    // Switching kills the live subprocess — while Claude is mid-turn the
    // operator must explicitly confirm.
    render(<ChatsMenu taskId="T1" turnInFlight />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    const newChat = await screen.findByRole('button', { name: /new chat/i });

    fireEvent.click(newChat);
    expect(startTaskChat).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/mid-turn/i);

    fireEvent.click(newChat);
    await waitFor(() => {
      expect(startTaskChat).toHaveBeenCalledWith('T1', '', '');
    });
  });

  test('the armed mid-turn warning clears when the turn ends', async () => {
    // Once Claude is no longer mid-turn the warning's premise is gone —
    // keeping it armed would show a false "Claude is mid-turn" state.
    const { rerender } = render(<ChatsMenu taskId="T1" turnInFlight />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    fireEvent.click(await screen.findByRole('button', { name: /new chat/i }));
    expect(screen.getByRole('alert')).toBeInTheDocument();

    rerender(<ChatsMenu taskId="T1" turnInFlight={false} />);
    await waitFor(() => {
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });

  test('a failed action disarms the warning too', async () => {
    startTaskChat.mockResolvedValue({ ok: false, status: 409, body: { error: 'busy' } });
    render(<ChatsMenu taskId="T1" turnInFlight />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    const newChat = await screen.findByRole('button', { name: /new chat/i });
    fireEvent.click(newChat);                 // arm
    fireEvent.click(newChat);                 // confirm → POST fails
    await waitFor(() => {
      expect(toast.errorFromResult).toHaveBeenCalled();
    });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  test('idle: no confirmation step — first click acts immediately', async () => {
    render(<ChatsMenu taskId="T1" turnInFlight={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    fireEvent.click(await screen.findByRole('button', { name: /new chat/i }));
    await waitFor(() => {
      expect(startTaskChat).toHaveBeenCalledWith('T1', '', '');
    });
  });

  test('arms the switch-pending flag BEFORE the request and clears it on failure', async () => {
    // The backend kill can flip the stream's turn state before the POST
    // resolves — the parent must already be suppressing the queued-message
    // flush by then. On failure the old chat is untouched, so the flag is
    // released (normal flushing resumes).
    const onChatSwitchPending = vi.fn();
    let resolvePost;
    startTaskChat.mockReturnValue(new Promise((resolve) => { resolvePost = resolve; }));
    render(
      <ChatsMenu taskId="T1" onChatSwitchPending={onChatSwitchPending} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    fireEvent.click(await screen.findByRole('button', { name: /new chat/i }));
    expect(onChatSwitchPending).toHaveBeenCalledWith(true);  // armed pre-flight

    resolvePost({ ok: false, status: 409, body: { error: 'busy' } });
    await waitFor(() => {
      expect(onChatSwitchPending).toHaveBeenLastCalledWith(false);
    });
  });

  test('on success the pending flag is NOT cleared here — onChatChanged owns it', async () => {
    // Clearing in the menu would race onChatChanged's queue discard; the
    // parent clears the flag as part of the same handler.
    const onChatSwitchPending = vi.fn();
    const onChatChanged = vi.fn();
    startTaskChat.mockResolvedValue({ ok: true, body: {} });
    render(
      <ChatsMenu
        taskId="T1"
        onChatChanged={onChatChanged}
        onChatSwitchPending={onChatSwitchPending}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    fireEvent.click(await screen.findByRole('button', { name: /new chat/i }));
    await waitFor(() => {
      expect(onChatChanged).toHaveBeenCalled();
    });
    expect(onChatSwitchPending).toHaveBeenCalledWith(true);
    expect(onChatSwitchPending).not.toHaveBeenCalledWith(false);
  });

  test('Escape closes the menu', async () => {
    render(<ChatsMenu taskId="T1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Chats' }));
    await screen.findByRole('menu');
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => {
      expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    });
  });

  it('shows which CLI produced each chat', async () => {
    // The operator can switch backends between chats; the menu must say
    // which one each conversation belongs to, not which one is configured.
    fetchTaskChats.mockResolvedValue({
      chats: [
        { agent_session_id: 'a1', active: true, agent_backend: 'claude',
          first_user_message: 'fix the tabs', turn_count: 3 },
        { agent_session_id: 'b2', active: false, agent_backend: 'codex',
          first_user_message: 'try codex', turn_count: 1 },
      ],
    });
    render(<ChatsMenu taskId="PROJ-1" />);
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));

    expect(await screen.findByText('Claude')).toBeTruthy();
    expect(screen.getByText('Codex')).toBeTruthy();
  });

  it('shows no chip for a chat recorded before kato tracked the backend', async () => {
    fetchTaskChats.mockResolvedValue({
      chats: [{ agent_session_id: 'old', active: true, first_user_message: 'old chat' }],
    });
    render(<ChatsMenu taskId="PROJ-1" />);
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));

    await screen.findByText('old chat');
    expect(screen.queryByText('Claude')).toBeNull();
    expect(screen.queryByText('Codex')).toBeNull();
  });





  it('switching back to an existing chat sends NO backend override', async () => {
    // That chat resumes through the CLI that created it; overriding here
    // would try to resume one CLI's conversation with another.
    fetchAgentBackends.mockResolvedValue({ backends: ['claude', 'codex'] });
    fetchTaskChats.mockResolvedValue({
      chats: [{ agent_session_id: 'old-1', active: false, first_user_message: 'earlier' }],
    });
    startTaskChat.mockResolvedValue({ ok: true, body: {} });
    render(<ChatsMenu taskId="T1" />);
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));
    fireEvent.click(await screen.findByText('earlier'));

    await waitFor(() => {
      expect(startTaskChat).toHaveBeenCalledWith('T1', 'old-1', '');
    });
  });
});


// Naming a chat. The list labels each conversation with its first user
// message otherwise — a reasonable guess and a poor name: two chats that
// began "fix the failing test" are indistinguishable a week later.
describe('ChatsMenu — rename', () => {
  const CHATS = [
    {
      agent_session_id: 'chat-1', active: true, turn_count: 3,
      first_user_message: 'fix the failing test', name: '',
    },
    {
      agent_session_id: 'chat-2', active: false, turn_count: 9,
      first_user_message: 'fix the failing test', name: 'The flaky hunt',
    },
  ];

  beforeEach(() => {
    fetchTaskChats.mockResolvedValue({ chats: CHATS });
    renameTaskChat.mockClear();
    renameTaskChat.mockResolvedValue({ ok: true, body: { name: 'Renamed' } });
    toast.errorFromResult.mockClear();
  });

  async function openMenu() {
    render(<ChatsMenu taskId="T1" />);
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));
    await screen.findByText('The flaky hunt');
  }

  test('a stored name is shown instead of the message preview', async () => {
    await openMenu();
    // Both chats opened with the same sentence; only the named one is
    // distinguishable.
    expect(screen.getByText('The flaky hunt')).toBeInTheDocument();
    expect(screen.getByText('fix the failing test')).toBeInTheDocument();
  });

  test('every row offers a rename control', async () => {
    await openMenu();
    expect(screen.getAllByRole('button', { name: /^Rename / })).toHaveLength(2);
  });

  test('renaming posts the new name', async () => {
    await openMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Rename fix the failing test' }));

    const input = screen.getByRole('textbox', { name: /chat name/i });
    fireEvent.change(input, { target: { value: 'Login bug' } });
    fireEvent.submit(input.closest('form'));

    await waitFor(() => {
      expect(renameTaskChat).toHaveBeenCalledWith('T1', 'chat-1', 'Login bug');
    });
  });

  test('the box opens EMPTY on a never-renamed chat', async () => {
    // Seeded from the stored name, not the displayed label — otherwise the
    // operator has to delete the message preview before typing.
    await openMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Rename fix the failing test' }));
    expect(screen.getByRole('textbox', { name: /chat name/i })).toHaveValue('');
  });

  test('the box opens with the CURRENT name on a renamed chat', async () => {
    await openMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Rename The flaky hunt' }));
    expect(screen.getByRole('textbox', { name: /chat name/i }))
      .toHaveValue('The flaky hunt');
  });

  test('an empty name is allowed — it clears the stored one', async () => {
    await openMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Rename The flaky hunt' }));
    const input = screen.getByRole('textbox', { name: /chat name/i });
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.submit(input.closest('form'));

    await waitFor(() => {
      expect(renameTaskChat).toHaveBeenCalledWith('T1', 'chat-2', '');
    });
  });

  test('Escape cancels without posting and without closing the menu', async () => {
    // The menu's own Escape handler would take the whole dropdown down and
    // lose the operator's place in the list.
    await openMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Rename The flaky hunt' }));
    const input = screen.getByRole('textbox', { name: /chat name/i });
    fireEvent.change(input, { target: { value: 'discarded' } });
    fireEvent.keyDown(input, { key: 'Escape' });

    expect(renameTaskChat).not.toHaveBeenCalled();
    expect(screen.getByText('The flaky hunt')).toBeInTheDocument();
  });

  test('a failed rename is reported', async () => {
    renameTaskChat.mockResolvedValue({ ok: false, error: 'nope' });
    await openMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Rename The flaky hunt' }));
    const input = screen.getByRole('textbox', { name: /chat name/i });
    fireEvent.submit(input.closest('form'));

    await waitFor(() => expect(toast.errorFromResult).toHaveBeenCalled());
  });
});

// Adoption used to be a button in the session-header toolbar. That toolbar
// has no backend in scope, so the one button could only ever mean Claude —
// a Codex operator had no way to hand over a conversation at all. It lives
// here now, where the menu already knows which agent's chats it is showing.
describe('ChatsMenu — adopting an existing session', () => {
  beforeEach(() => {
    fetchTaskChats.mockResolvedValue({ chats: CHATS });
  });

  test('the menu offers adoption, named for the backend it is showing', async () => {
    render(<ChatsMenu taskId="T1" agentBackend="codex" onChatChanged={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));
    expect(
      await screen.findByRole('button', { name: /Adopt existing Codex session/i }),
    ).toBeTruthy();
  });

  test('it names Claude in a Claude tab', async () => {
    render(<ChatsMenu taskId="T1" agentBackend="claude" onChatChanged={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));
    expect(
      await screen.findByRole('button', { name: /Adopt existing Claude session/i }),
    ).toBeTruthy();
  });

  // Adoption must NOT be reported as a fresh chat: SessionDetail reads a
  // missing session id as "new chat" and posts a bubble saying the next
  // message starts a fresh session — directly contradicting the adoption.
  test('it reports the adopted chat, not an empty one', async () => {
    const onChatChanged = vi.fn();
    fetchAgentSessions.mockResolvedValue({
      sessions: [{
        [AGENT_SESSION_ID]: 'adopted-9', cwd: '/w', turn_count: 1,
        last_modified_epoch: 1, first_user_message: 'hi',
        last_user_message: 'hi', adopted_by_task_id: '',
      }],
    });
    adoptAgentSession.mockResolvedValue({ ok: true });
    render(
      <ChatsMenu taskId="T1" agentBackend="codex" onChatChanged={onChatChanged} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));
    fireEvent.click(
      await screen.findByRole('button', { name: /Adopt existing Codex session/i }),
    );
    fireEvent.click(await screen.findByText('/w'));
    fireEvent.click(screen.getByRole('button', { name: /Adopt selected/i }));
    await waitFor(() => {
      expect(onChatChanged).toHaveBeenCalled();
      const arg = onChatChanged.mock.calls.at(-1)[0];
      expect(arg && arg[AGENT_SESSION_ID]).toBe('adopted-9');
    });
  });

  // A backend whose conversations do not live on this machine has nothing to
  // adopt. OpenHands runs its sessions server-side, so the control there
  // opened a picker that could only ever come back empty.
  test('the control is hidden for a backend with no local sessions', async () => {
    render(
      <ChatsMenu
        taskId="T1" agentBackend="openhands" supportsAdoption={false}
        onChatChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));
    await screen.findByRole('button', { name: /New chat/i });
    expect(screen.queryByRole('button', { name: /Adopt existing/i })).toBeNull();
  });

  test('an unknown answer still shows it — hiding a working feature is worse', async () => {
    render(<ChatsMenu taskId="T1" agentBackend="claude" onChatChanged={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));
    expect(
      await screen.findByRole('button', { name: /Adopt existing/i }),
    ).toBeTruthy();
  });

  test('choosing it opens the picker scoped to that backend', async () => {
    render(<ChatsMenu taskId="T1" agentBackend="codex" onChatChanged={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /chats/i }));
    fireEvent.click(
      await screen.findByRole('button', { name: /Adopt existing Codex session/i }),
    );
    await waitFor(
      () => expect(fetchAgentSessions).toHaveBeenCalledWith('codex', ''),
    );
  });
});
