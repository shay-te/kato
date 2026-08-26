/**
 * The agent-tab strip must survive an OLD server answering a NEW bundle.
 *
 * ``/api/agent-backends`` changed from a list of ids to a list of objects
 * with readiness. The bundle and the Python process restart separately, so a
 * browser reload runs the new UI against the old route — and reading only
 * the new shape made the entire tab strip render blank until kato itself was
 * restarted. The operator's report was simply "where is the tabs?".
 */
import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import {
  normalizeBackendEntry, normalizeBackendEntries,
} from '../utils/agentBackendEntry.js';

vi.mock('../api.js', () => ({
  // Each tab shows its agent's status, polled from here.
  fetchTaskAgentStatus: vi.fn().mockResolvedValue({ backends: [] }),
  fetchAgentBackends: vi.fn(),
  switchTaskBackend: vi.fn(),
  // The chat-title bar under the tabs reads the same list the dropdown does.
  fetchTaskChats: vi.fn(async () => ({ chats: [] })),
  fetchClaudeSessions: vi.fn(async () => ({ ok: true, body: { sessions: [] } })),
  startNewChat: vi.fn(),
  switchChat: vi.fn(),
  renameChat: vi.fn(),
  pinChat: vi.fn(),
  deleteChat: vi.fn(),
}));

const { fetchAgentBackends } = await import('../api.js');
const AgentBackendTabs = (await import('../components/AgentBackendTabs.jsx')).default;

afterEach(() => { cleanup(); });

describe('normalizeBackendEntry', () => {
  test('accepts the OLD bare-id shape and assumes it is ready', () => {
    expect(normalizeBackendEntry('codex')).toEqual({
      id: 'codex', label: 'Codex',
      ready: true, wired: true, chat_available: true, error: '',
    });
  });

  test('accepts the NEW object shape verbatim', () => {
    expect(normalizeBackendEntry({
      id: 'codex', label: 'Codex', ready: false, wired: false,
      chat_available: false, error: 'not on PATH',
    })).toEqual({
      id: 'codex', label: 'Codex', ready: false, wired: false,
      chat_available: false, error: 'not on PATH',
    });
  });

  test('a partial object is read as ready, never as broken', () => {
    // A missing flag means "no probe ran". Defaulting it to false would hide
    // a working chat behind a setup panel.
    const entry = normalizeBackendEntry({ id: 'claude' });
    expect(entry.ready).toBe(true);
    expect(entry.chat_available).toBe(true);
  });

  test('drops entries with no id', () => {
    expect(normalizeBackendEntry('')).toBeNull();
    expect(normalizeBackendEntry('   ')).toBeNull();
    expect(normalizeBackendEntry({})).toBeNull();
    expect(normalizeBackendEntry(null)).toBeNull();
  });

  test('normalizeBackendEntries filters the junk out of a list', () => {
    expect(normalizeBackendEntries(['claude', '', null, { id: 'codex' }]))
      .toHaveLength(2);
    expect(normalizeBackendEntries(null)).toEqual([]);
    expect(normalizeBackendEntries('nope')).toEqual([]);
  });
});

describe('AgentBackendTabs against an old server', () => {
  test('renders the tabs from a bare-id payload', async () => {
    // Exactly what a not-yet-restarted kato returns.
    fetchAgentBackends.mockResolvedValue({ backends: ['claude', 'codex'] });
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);

    expect(await screen.findByRole('tab', { name: /Claude/ })).toBeTruthy();
    expect(screen.getByRole('tab', { name: /Codex/ })).toBeTruthy();
  });

  test('the strip is not blank — the reported symptom', async () => {
    fetchAgentBackends.mockResolvedValue({ backends: ['claude', 'codex'] });
    const { container } = render(
      <AgentBackendTabs taskId="T1" activeBackend="claude" />,
    );
    await screen.findByRole('tab', { name: /Claude/ });
    const labels = [...container.querySelectorAll('.agent-backend-tab-button')]
      .map((b) => b.textContent.trim());
    expect(labels).toEqual(['Claude', 'Codex']);
  });

  test('an old payload still offers the active tab its chat history', async () => {
    fetchAgentBackends.mockResolvedValue({ backends: ['claude', 'codex'] });
    render(<AgentBackendTabs taskId="T1" activeBackend="claude" />);
    await screen.findByRole('tab', { name: /Claude/ });
    expect(screen.getAllByRole('button', { name: /chats/i })).toHaveLength(1);
  });
});
