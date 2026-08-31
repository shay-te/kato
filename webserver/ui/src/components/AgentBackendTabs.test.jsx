import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  // Each tab shows its agent's status, polled from here.
  fetchTaskAgentStatus: vi.fn().mockResolvedValue({ backends: [] }),
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
import {
  readChatMaximized,
  writeChatMaximized,
  _resetChatMaximizedPref,
} from '../utils/chatMaximizedPref.js';
import { fetchAgentBackends, fetchTaskChats, switchTaskBackend, fetchTaskAgentStatus} from '../api.js';
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


// Clicking a tab must SELECT it. Selection used to be derived from the
// session record alone, which this render tree never re-reads — so a
// successful switch changed nothing on screen and the Codex tab appeared to
// snap straight back to Claude. The operator's report: "when moving to codex
// tab he immediately goes back to claude tab".
describe('AgentBackendTabs — the picked tab stays picked', () => {
  function renderTabs(props = {}) {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), backendEntry('codex')],
    });
    return render(
      <AgentBackendTabs taskId="T1" activeBackend="claude" {...props} />,
    );
  }

  function selected() {
    return screen.getAllByRole('tab')
      .filter((t) => t.getAttribute('aria-selected') === 'true')
      .map((t) => t.textContent.trim());
  }

  test('a successful switch selects the clicked tab', async () => {
    switchTaskBackend.mockResolvedValue({
      ok: true, body: { agent_backend: 'codex' },
    });
    renderTabs();
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));
    await waitFor(() => expect(selected()).toEqual(['Codex']));
  });

  test('it stays selected even though the session prop never changes', async () => {
    // The exact bug: App keeps reporting activeBackend="claude" until its
    // next poll. The tab must not revert in the meantime.
    switchTaskBackend.mockResolvedValue({
      ok: true, body: { agent_backend: 'codex' },
    });
    const { rerender } = renderTabs();
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));
    await waitFor(() => expect(selected()).toEqual(['Codex']));

    rerender(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    expect(selected()).toEqual(['Codex']);
  });

  test('the server’s answer wins over the clicked id', async () => {
    switchTaskBackend.mockResolvedValue({
      ok: true, body: { agent_backend: 'claude' },
    });
    renderTabs();
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));
    await waitFor(() => expect(selected()).toEqual(['Claude']));
  });

  test('a REFUSED switch leaves the tab where it was', async () => {
    switchTaskBackend.mockResolvedValue({
      ok: false, error: 'the codex backend is not configured on this host',
    });
    renderTabs();
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));
    await waitFor(() => expect(switchTaskBackend).toHaveBeenCalled());
    expect(selected()).toEqual(['Claude']);
  });

  test('once the session agrees, the server is in charge again', async () => {
    switchTaskBackend.mockResolvedValue({
      ok: true, body: { agent_backend: 'codex' },
    });
    const { rerender } = renderTabs();
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));
    await waitFor(() => expect(selected()).toEqual(['Codex']));

    // App's poll catches up...
    rerender(<AgentBackendTabs taskId="T1" activeBackend="codex" />);
    await waitFor(() => expect(selected()).toEqual(['Codex']));
    // ...and a switch made ELSEWHERE is now reflected, not pinned by a
    // stale local pick.
    rerender(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    await waitFor(() => expect(selected()).toEqual(['Claude']));
  });

  test('the readiness report follows the picked tab', async () => {
    switchTaskBackend.mockResolvedValue({
      ok: true, body: { agent_backend: 'codex' },
    });
    const onReadinessChange = vi.fn();
    renderTabs({ onReadinessChange });
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));
    await waitFor(() => {
      expect(onReadinessChange.mock.calls.at(-1)[0].id).toBe('codex');
    });
  });
});


// Selecting a backend kato has no manager for must OPEN ITS SETUP PANEL,
// not ask the server to switch to it. The switch route refuses a backend it
// cannot run, so posting would answer the click with "Could not switch
// agent" — an error about the very thing the tab exists to explain.
describe('AgentBackendTabs — selecting an unwired backend', () => {
  const UNWIRED = backendEntry('codex', {
    ready: true, wired: false, chat_available: false, error: '',
  });

  function selected() {
    return screen.getAllByRole('tab')
      .filter((t) => t.getAttribute('aria-selected') === 'true')
      .map((t) => t.textContent.trim());
  }

  test('selects locally without posting a switch', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), UNWIRED],
    });
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));

    await waitFor(() => expect(selected()).toEqual(['Codex']));
    expect(switchTaskBackend).not.toHaveBeenCalled();
  });

  test('reports it upward so the chat area shows the setup panel', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), UNWIRED],
    });
    const onReadinessChange = vi.fn();
    render(
      <AgentBackendTabs
        taskId="T1" activeBackend="claude"
        onReadinessChange={onReadinessChange}
      />,
    );
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));
    await waitFor(() => {
      const last = onReadinessChange.mock.calls.at(-1)[0];
      expect(last.id).toBe('codex');
      expect(last.chat_available).toBe(false);
    });
  });

  test('a wired backend still goes through the server', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), backendEntry('codex')],
    });
    switchTaskBackend.mockResolvedValue({
      ok: true, body: { agent_backend: 'codex' },
    });
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    fireEvent.click(await screen.findByRole('tab', { name: /Codex/ }));
    await waitFor(() => expect(switchTaskBackend).toHaveBeenCalledWith('T1', 'codex'));
  });
});


// The chats control moved OUT of the active tab pill onto its own row,
// beside the name of the conversation you are in. Inside the pill it read as
// part of the agent's name, and the chat's own title appeared nowhere — you
// had to open the dropdown to learn which conversation you were looking at.
describe('AgentBackendTabs — the chat bar under the tabs', () => {
  function ready() {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), backendEntry('codex')],
    });
  }

  test('the chat name renders on its own row, below the tabs', async () => {
    ready();
    fetchTaskChats.mockResolvedValue({
      chats: [{ active: true, first_user_message: 'Fix the login redirect' }],
    });
    const { container } = render(
      <AgentBackendTabs taskId="T1" activeBackend="claude" />,
    );
    await screen.findByText('Fix the login redirect');

    const bar = container.querySelector('.agent-chat-bar');
    expect(bar).toBeTruthy();
    // The row is a SIBLING of the tab strip, not inside a tab.
    expect(container.querySelector('.agent-backend-tab .agent-chat-bar')).toBeNull();
  });

  test('the chats control sits on that row, not in the tab pill', async () => {
    ready();
    fetchTaskChats.mockResolvedValue({ chats: [] });
    const { container } = render(
      <AgentBackendTabs taskId="T1" activeBackend="claude" />,
    );
    await screen.findByRole('tab', { name: /Claude/ });

    const button = screen.getByRole('button', { name: /chats/i });
    expect(button.closest('.agent-chat-bar')).toBeTruthy();
    expect(button.closest('.agent-backend-tab')).toBeNull();
  });

  test('falls back to a neutral label when there is no chat yet', async () => {
    ready();
    fetchTaskChats.mockResolvedValue({ chats: [] });
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    expect(await screen.findByText('Chats')).toBeTruthy();
  });

  test('a failed lookup does not blank the row', async () => {
    ready();
    fetchTaskChats.mockRejectedValue(new Error('offline'));
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    expect(await screen.findByText('Chats')).toBeTruthy();
  });

  test('an unconfigured backend gets no chat bar', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [
        backendEntry('claude'),
        backendEntry('codex', { ready: false, chat_available: false }),
      ],
    });
    fetchTaskChats.mockResolvedValue({ chats: [] });
    const { container } = render(
      <AgentBackendTabs taskId="T1" activeBackend="codex" />,
    );
    await screen.findByRole('tab', { name: /Codex/ });
    expect(container.querySelector('.agent-chat-bar')).toBeNull();
  });

  test('the chats icon is a line drawing, not a filled glyph', async () => {
    ready();
    fetchTaskChats.mockResolvedValue({ chats: [] });
    const { container } = render(
      <AgentBackendTabs taskId="T1" activeBackend="claude" />,
    );
    await screen.findByRole('tab', { name: /Claude/ });
    const svg = screen.getByRole('button', { name: /chats/i }).querySelector('svg');
    expect(svg.getAttribute('fill')).toBe('none');
    expect(svg.getAttribute('stroke')).toBe('currentColor');
  });
});


// An infinite render loop, found when a test suite hung for 300s.
//
// The pre-load placeholder tab was rebuilt on every render, so the readiness
// entry changed IDENTITY every time; the effect reporting it upward fired
// every time; the parent re-rendered; repeat. It affected every real session
// — they all carry a backend — for the whole window before the backends
// lookup returned, which in production is a spinning CPU, not a hung test.
describe('AgentBackendTabs — readiness is reported by value, not identity', () => {
  test('a pending lookup does not re-notify on every render', async () => {
    let resolve;
    fetchAgentBackends.mockReturnValue(new Promise((r) => { resolve = r; }));
    fetchTaskChats.mockResolvedValue({ chats: [] });
    const onReadinessChange = vi.fn();

    const { rerender } = render(
      <AgentBackendTabs
        taskId="T1" activeBackend="claude"
        onReadinessChange={onReadinessChange}
      />,
    );
    const afterFirst = onReadinessChange.mock.calls.length;

    // Re-render with identical props, the way a parent state update would.
    for (let i = 0; i < 5; i += 1) {
      rerender(
        <AgentBackendTabs
          taskId="T1" activeBackend="claude"
          onReadinessChange={onReadinessChange}
        />,
      );
    }
    expect(onReadinessChange.mock.calls.length).toBe(afterFirst);
    resolve({ backends: [backendEntry('claude')] });
  });

  test('a genuine readiness change IS reported', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), backendEntry('codex', {
        ready: false, chat_available: false, error: 'not installed',
      })],
    });
    fetchTaskChats.mockResolvedValue({ chats: [] });
    const onReadinessChange = vi.fn();
    render(
      <AgentBackendTabs
        taskId="T1" activeBackend="codex"
        onReadinessChange={onReadinessChange}
      />,
    );
    await waitFor(() => {
      const last = onReadinessChange.mock.calls.at(-1)[0];
      expect(last?.ready).toBe(false);
      expect(last?.error).toBe('not installed');
    });
  });
});


// Each agent's status sits ON its own tab — "Claude (working)". It used to
// live in the header: first one chip for the focused agent (silent about the
// other), then two chips detached from the tabs they referred to.
describe('AgentBackendTabs — status on the tab', () => {
  function withStatuses(rows) {
    fetchAgentBackends.mockResolvedValue({
      backends: [backendEntry('claude'), backendEntry('codex')],
    });
    fetchTaskChats.mockResolvedValue({ chats: [] });
    fetchTaskAgentStatus.mockResolvedValue({ backends: rows });
    return render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
  }

  test('a working agent says so on its own tab', async () => {
    withStatuses([
      { id: 'claude', label: 'Claude', active: true, live: true, working: true },
      { id: 'codex', label: 'Codex', active: false, live: false, working: false },
    ]);
    const claudeTab = await screen.findByRole('tab', { name: /Claude/ });
    await waitFor(() => expect(claudeTab.textContent).toContain('working'));
  });

  test('the OTHER agent reports its own state, not the active one’s', async () => {
    withStatuses([
      { id: 'claude', label: 'Claude', active: true, live: true, working: false },
      { id: 'codex', label: 'Codex', active: false, live: true, working: true },
    ]);
    const codexTab = await screen.findByRole('tab', { name: /Codex/ });
    await waitFor(() => expect(codexTab.textContent).toContain('working'));
    const claudeTab = screen.getByRole('tab', { name: /Claude/ });
    expect(claudeTab.textContent).not.toContain('working');
  });

  test('an unconfigured agent shows the setup marker, not a status', async () => {
    fetchAgentBackends.mockResolvedValue({
      backends: [
        backendEntry('claude'),
        backendEntry('codex', { ready: false, chat_available: false }),
      ],
    });
    fetchTaskChats.mockResolvedValue({ chats: [] });
    fetchTaskAgentStatus.mockResolvedValue({ backends: [] });
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);

    const codexTab = await screen.findByRole('tab', { name: /Codex/ });
    expect(codexTab.textContent).toContain('!');
    expect(codexTab.textContent).not.toContain('idle');
  });

  test('no status yet leaves the tab as just its name', async () => {
    withStatuses([]);
    const claudeTab = await screen.findByRole('tab', { name: /Claude/ });
    expect(claudeTab.textContent.trim()).toBe('Claude');
  });
});


// The maximize toggle — it sits on this row (rather than the task header)
// because it acts on the CHAT, and it drives a shared preference rather than
// local state, because the pane grid it collapses belongs to Layout.
describe('AgentBackendTabs — maximize chat', () => {
  beforeEach(() => {
    try { localStorage.clear(); } catch (_) { /* jsdom */ }
    _resetChatMaximizedPref();
  });

  async function renderTabs() {
    render(<AgentBackendTabs taskId="T1" />);
    return screen.findByRole('button', { name: /maximize chat|restore panes/i });
  }

  it('offers a maximize control', async () => {
    const button = await renderTabs();
    expect(button).toHaveAttribute('aria-pressed', 'false');
  });

  it('clicking it maximizes, and the label becomes the way back', async () => {
    // The same control restores — an operator who hid two panes must not have
    // to guess where the way back is.
    const button = await renderTabs();
    fireEvent.click(button);

    expect(readChatMaximized()).toBe(true);
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /restore panes/i }),
      ).toHaveAttribute('aria-pressed', 'true');
    });
  });

  it('clicking again restores', async () => {
    const button = await renderTabs();
    fireEvent.click(button);
    await waitFor(() => expect(readChatMaximized()).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: /restore panes/i }));
    expect(readChatMaximized()).toBe(false);
  });

  it('opens in the stored state after a reload', async () => {
    writeChatMaximized(true);
    const button = await renderTabs();
    expect(button).toHaveAttribute('aria-pressed', 'true');
  });

  it('follows a change made elsewhere', async () => {
    // Two chat panes can be open across windows; the control must not drift
    // from the layout it describes.
    const button = await renderTabs();
    expect(button).toHaveAttribute('aria-pressed', 'false');

    await waitFor(() => {
      writeChatMaximized(true);
      expect(
        screen.getByRole('button', { name: /restore panes/i }),
      ).toHaveAttribute('aria-pressed', 'true');
    });
  });
});


// The agent session id sits HERE, beside the chats control — not in the global
// task header it used to live in. It is a per-backend fact, and one chip in a
// header shared by every agent tab could only ever name one backend's session:
// on a task with both a Claude and a Codex chat it showed a single id and
// silently implied it belonged to whichever tab was in front.
describe('AgentBackendTabs — the session id chip', () => {
  it('shows the ACTIVE chat\'s id, truncated', async () => {
    fetchTaskChats.mockResolvedValue({
      chats: [
        { agent_session_id: 'aaaa1111-2222-3333-4444-555566667777', active: true },
        { agent_session_id: 'bbbb9999-0000-1111-2222-333344445555', active: false },
      ],
    });
    render(<AgentBackendTabs taskId="T1" />);

    const chip = await screen.findByText(/^sid:aaaa1111…$/);
    expect(chip).toBeInTheDocument();
  });

  it('names the backend the id belongs to', async () => {
    // The whole reason it moved: the tooltip must say WHOSE session this is.
    fetchTaskChats.mockResolvedValue({
      chats: [{ agent_session_id: 'aaaa1111-2222', active: true }],
    });
    render(<AgentBackendTabs taskId="T1" />);

    const chip = await screen.findByText(/^sid:aaaa1111…$/);
    expect(chip.getAttribute('title')).toMatch(/session id: aaaa1111-2222/);
  });

  it('shows nothing when the chat has no session id yet', async () => {
    // A brand-new chat has no id until the first message spawns it. An empty
    // chip would read as a broken one.
    fetchTaskChats.mockResolvedValue({ chats: [] });
    render(<AgentBackendTabs taskId="T1" />);
    await screen.findByRole('tablist');
    expect(screen.queryByText(/^sid:/)).toBeNull();
  });
});

// Removing the "session started" bubble from the log (it reprinted on every
// reconnect) left the id with only one surface: this chip. But the chat bar's
// lookup is a one-shot fetch, and a brand-new chat has no id when it runs —
// so for the whole first turn the id appeared nowhere at all.
describe('AgentBackendTabs — the sid chip during a brand-new chat', () => {
  test('it falls back to the id the live stream knows', async () => {
    fetchTaskChats.mockResolvedValue({ chats: [] });   // fetch has no id yet
    render(
      <AgentBackendTabs
        taskId="T1"
        activeBackend="claude"
        liveAgentSessionId="abc12345-live"
        onChatChanged={() => {}}
      />,
    );
    expect(await screen.findByText(/sid:abc12345/)).toBeTruthy();
  });

  test('another tab never borrows the active backend\u2019s id', async () => {
    fetchTaskChats.mockResolvedValue({ chats: [] });
    render(
      <AgentBackendTabs
        taskId="T1"
        activeBackend="claude"
        liveAgentSessionId="abc12345-live"
        onChatChanged={() => {}}
      />,
    );
    const codexTab = screen.queryByRole('tab', { name: 'Codex' });
    if (!codexTab) { return; }          // single-backend host: nothing to check
    fireEvent.click(codexTab);
    await waitFor(
      () => expect(screen.queryByText(/sid:abc12345/)).toBeNull(),
    );
  });
});
