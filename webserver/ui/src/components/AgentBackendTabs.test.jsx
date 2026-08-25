import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchAgentBackends: vi.fn(),
  switchTaskBackend: vi.fn(),
  // ChatsMenu renders inside the active tab.
  fetchTaskChats: vi.fn().mockResolvedValue({ chats: [] }),
  startTaskChat: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn(), errorFromResult: vi.fn() },
}));

import AgentBackendTabs from './AgentBackendTabs.jsx';
import { fetchAgentBackends, fetchTaskChats, switchTaskBackend } from '../api.js';
import { toast } from '../stores/toastStore.js';

beforeEach(() => {
  vi.clearAllMocks();
  fetchTaskChats.mockResolvedValue({ chats: [] });
});

// ``/api/agent-backends`` returns one entry PER BACKEND with its readiness,
// not a list of ids: both tabs always show, and ``ready`` decides whether the
// tab opens a chat or a setup panel.
function backendEntry(id, overrides = {}) {
  return {
    id,
    label: id === 'codex' ? 'Codex' : 'Claude',
    ready: true,
    wired: true,
    chat_available: true,
    error: '',
    ...overrides,
  };
}

describe('AgentBackendTabs', () => {
  it('shows one tab per agent the host can run', async () => {
    fetchAgentBackends.mockResolvedValue({ backends: [backendEntry('claude'), backendEntry('codex')] });

    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);

    expect(await screen.findByRole('tab', { name: 'Claude' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Codex' })).toBeTruthy();
  });

  it('marks the task\'s current agent as the selected tab', async () => {
    fetchAgentBackends.mockResolvedValue({ backends: [backendEntry('claude'), backendEntry('codex')] });

    render(<AgentBackendTabs taskId="T1" activeBackend="codex" />);

    const codex = await screen.findByRole('tab', { name: 'Codex' });
    expect(codex.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tab', { name: 'Claude' })
      .getAttribute('aria-selected')).toBe('false');
  });

  it('switching tabs asks the backend to swap the conversation', async () => {
    fetchAgentBackends.mockResolvedValue({ backends: [backendEntry('claude'), backendEntry('codex')] });
    switchTaskBackend.mockResolvedValue({ ok: true, body: {} });
    const onBackendChanged = vi.fn();

    render(<AgentBackendTabs taskId="T1" activeBackend="claude"
                             onBackendChanged={onBackendChanged} />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Codex' }));

    await waitFor(() => {
      expect(switchTaskBackend).toHaveBeenCalledWith('T1', 'codex');
      expect(onBackendChanged).toHaveBeenCalled();
    });
  });

  it('clicking the tab you are already on does nothing', async () => {
    // Re-switching would rewrite the record on every stray click.
    fetchAgentBackends.mockResolvedValue({ backends: [backendEntry('claude'), backendEntry('codex')] });

    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Claude' }));

    await waitFor(() => expect(switchTaskBackend).not.toHaveBeenCalled());
  });

  it('a failed switch is reported and does not change tab', async () => {
    fetchAgentBackends.mockResolvedValue({ backends: [backendEntry('claude'), backendEntry('codex')] });
    switchTaskBackend.mockResolvedValue({ ok: false, error: 'not configured' });
    const onBackendChanged = vi.fn();

    render(<AgentBackendTabs taskId="T1" activeBackend="claude"
                             onBackendChanged={onBackendChanged} />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Codex' }));

    await waitFor(() => expect(toast.errorFromResult).toHaveBeenCalled());
    expect(onBackendChanged).not.toHaveBeenCalled();
  });

  it('each tab carries its own chat history, scoped to that agent', async () => {
    // A Claude tab must never list a Codex thread it cannot resume.
    fetchAgentBackends.mockResolvedValue({ backends: [backendEntry('claude'), backendEntry('codex')] });

    render(<AgentBackendTabs taskId="T1" activeBackend="codex" />);
    fireEvent.click(await screen.findByRole('button', { name: /chats/i }));

    await waitFor(() => {
      expect(fetchTaskChats).toHaveBeenCalledWith('T1', 'codex');
    });
  });

  it('only the ACTIVE tab shows a history button', async () => {
    // An inactive tab's menu would switch conversations behind the operator.
    fetchAgentBackends.mockResolvedValue({ backends: [backendEntry('claude'), backendEntry('codex')] });

    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    await screen.findByRole('tab', { name: 'Codex' });

    expect(screen.getAllByRole('button', { name: /chats/i })).toHaveLength(1);
  });

  it('renders the current agent even before the list loads', async () => {
    // The history button must not vanish on every remount.
    let resolve;
    fetchAgentBackends.mockReturnValue(new Promise((r) => { resolve = r; }));

    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);

    expect(screen.getByRole('tab', { name: 'Claude' })).toBeTruthy();
    resolve({ backends: [backendEntry('claude')] });
  });

  it('a failed backend lookup still leaves the current agent usable', async () => {
    fetchAgentBackends.mockRejectedValue(new Error('offline'));

    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);

    expect(await screen.findByRole('tab', { name: 'Claude' })).toBeTruthy();
  });

  it('renders nothing when there is no agent at all', async () => {
    fetchAgentBackends.mockResolvedValue({ backends: [] });

    const { container } = render(<AgentBackendTabs taskId="T1" activeBackend="" />);

    await waitFor(() => expect(container.innerHTML).toBe(''));
  });
});


// A backend whose CLI is missing keeps its tab — hiding it is how the
// operator never learns kato can run that agent at all.
describe('AgentBackendTabs — unready backends', () => {
  const UNREADY = backendEntry('codex', {
    ready: false, chat_available: false,
    error: 'Codex CLI ("codex") was not found on PATH.',
  });

  test('an unready backend still gets a tab', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), UNREADY],
    });
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    expect(await screen.findByRole('tab', { name: /Codex/ })).toBeTruthy();
  });

  test('its tab is marked as needing attention', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), UNREADY],
    });
    const { container } = render(
      <AgentBackendTabs taskId="T1" activeBackend="claude" />,
    );
    await screen.findByRole('tab', { name: /Codex/ });
    expect(container.querySelector('.agent-backend-tab.is-unready')).toBeTruthy();
  });

  test('it offers no chat history — there is no chat to list', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), UNREADY],
    });
    render(<AgentBackendTabs taskId="T1" activeBackend="codex" />);
    await screen.findByRole('tab', { name: /Codex/ });
    expect(screen.queryByRole('button', { name: /chats/i })).toBeNull();
  });

  test('a ready active backend keeps its history button', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), UNREADY],
    });
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    await screen.findByRole('tab', { name: /Codex/ });
    expect(screen.getAllByRole('button', { name: /chats/i }).length).toBe(1);
  });

  test('readiness of the ACTIVE tab is reported upward', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), UNREADY],
    });
    const onReadinessChange = vi.fn();
    render(
      <AgentBackendTabs
        taskId="T1" activeBackend="codex"
        onReadinessChange={onReadinessChange}
      />,
    );
    await screen.findByRole('tab', { name: /Codex/ });
    const last = onReadinessChange.mock.calls.at(-1)[0];
    expect(last.id).toBe('codex');
    expect(last.ready).toBe(false);
    expect(last.error).toContain('not found on PATH');
  });

  test('the pre-load placeholder is treated as ready', async () => {
    // Otherwise a working chat flashes a setup panel while the probe is in
    // flight on every remount.
    let resolve;
    fetchAgentBackends.mockReturnValue(new Promise((r) => { resolve = r; }));
    const onReadinessChange = vi.fn();
    render(
      <AgentBackendTabs
        taskId="T1" activeBackend="claude"
        onReadinessChange={onReadinessChange}
      />,
    );
    expect(onReadinessChange.mock.calls.at(-1)[0].ready).toBe(true);
    resolve({ backends: [backendEntry('claude')] });
  });
});
