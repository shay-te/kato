// The setup wizard's agent step says whether the agent's CLI is installed and
// signed in — before the first task finds out.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({ fetchAgentBackends: vi.fn(), refreshAgentBackends: vi.fn() }));
vi.mock('../api.js', () => api);

const { default: AgentCliStatus } = await import('./AgentCliStatus.jsx');

function backends(claude) {
  return { backends: [{ id: 'codex', ready: true, error: '', logged_in: null }, { id: 'claude', ...claude }] };
}

function renderIt() {
  render(<AgentCliStatus backend="claude" label="Claude Code" loginCommand="claude auth login" />);
}

beforeEach(() => {
  api.fetchAgentBackends.mockReset();
  api.refreshAgentBackends.mockReset();
});

describe('AgentCliStatus', () => {
  test('installed and signed in', async () => {
    api.fetchAgentBackends.mockResolvedValue(backends({ ready: true, error: '', logged_in: true }));
    renderIt();
    expect(await screen.findByText('✓ Claude Code is installed and signed in.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull();
  });

  test('not installed: the transport\'s own install words, and check again', async () => {
    api.fetchAgentBackends.mockResolvedValue(backends({
      ready: false, error: 'Claude CLI was not found on PATH.\n\n    npm install -g @anthropic-ai/claude-code', logged_in: null,
    }));
    api.refreshAgentBackends.mockResolvedValue(backends({ ready: true, error: '', logged_in: true }));
    renderIt();
    expect(await screen.findByText('Claude Code is not installed on this computer yet.')).toBeInTheDocument();
    expect(screen.getByText(/npm install -g @anthropic-ai\/claude-code/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findByText('✓ Claude Code is installed and signed in.')).toBeInTheDocument();
    expect(api.refreshAgentBackends).toHaveBeenCalledTimes(1);  // past the server's cache
  });

  test('installed but signed out names the command', async () => {
    api.fetchAgentBackends.mockResolvedValue(backends({ ready: true, error: '', logged_in: false }));
    renderIt();
    expect(await screen.findByText(/is installed but not signed in/)).toBeInTheDocument();
    expect(screen.getByText('claude auth login')).toBeInTheDocument();
  });

  test('cannot tell is never "signed out"', async () => {
    api.fetchAgentBackends.mockResolvedValue(backends({ ready: true, error: '', logged_in: null }));
    renderIt();
    expect(await screen.findByText(/Whether it is signed in shows at your first task/)).toBeInTheDocument();
    expect(screen.queryByText(/not signed in/)).toBeNull();
  });

  test('a check that fails, or a list without it, says it could not check', async () => {
    api.fetchAgentBackends.mockRejectedValue(new Error('offline'));
    renderIt();
    expect(await screen.findByText(/could not check Claude Code right now: offline/)).toBeInTheDocument();
    api.fetchAgentBackends.mockReset();
    api.fetchAgentBackends.mockResolvedValue({ backends: [] });
    renderIt();
    await waitFor(() => expect(screen.getAllByText(/could not check Claude Code right now/)).toHaveLength(2));
  });
});
