// Tests for ChatsMenu — the session-header dropdown that starts a fresh
// chat or navigates back to one of the task's previous conversations.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskChats: vi.fn(),
  startTaskChat: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn(), errorFromResult: vi.fn() },
}));

import ChatsMenu from './ChatsMenu.jsx';
import { fetchTaskChats, startTaskChat } from '../api.js';
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
    expect(fetchTaskChats).toHaveBeenCalledWith('T1');
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
      expect(startTaskChat).toHaveBeenCalledWith('T1', '');
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
      expect(startTaskChat).toHaveBeenCalledWith('T1', 'older-session-id');
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
      expect(startTaskChat).toHaveBeenCalledWith('T1', '');
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
      expect(startTaskChat).toHaveBeenCalledWith('T1', '');
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
});
