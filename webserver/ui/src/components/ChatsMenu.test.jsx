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
});
