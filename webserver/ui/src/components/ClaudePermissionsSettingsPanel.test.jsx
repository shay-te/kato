// Tests for the Claude-permissions settings panel — lists remembered
// tool decisions and re-scopes / clears them through the backend
// (kato_core_lib/helpers/tool_decision_store.py is the sole source of
// truth; the panel holds no local copy other than a fetched cache).

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';

vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
}));

vi.mock('../api.js', () => ({
  fetchToolDecisions: vi.fn(),
  setToolDecision: vi.fn(),
  forgetToolDecision: vi.fn(),
  clearToolDecisions: vi.fn(),
}));

import {
  fetchToolDecisions, setToolDecision, forgetToolDecision, clearToolDecisions,
} from '../api.js';
import ClaudePermissionsSettingsPanel from './ClaudePermissionsSettingsPanel.jsx';

// A tiny in-memory fake backend: mutations update ``decisions`` in place
// so a follow-up fetch (the panel always re-fetches after a mutation)
// reflects the change, matching how the real server behaves.
let decisions;

function _key(entry) { return `${entry.tool_name}\u0000${entry.command_signature}`; }

function _sorted() {
  // Mirrors the real backend's list_tool_decisions() ordering.
  return [...decisions].sort(
    (a, b) => a.tool_name.localeCompare(b.tool_name)
      || a.command_signature.localeCompare(b.command_signature),
  );
}

beforeEach(() => {
  decisions = [];
  fetchToolDecisions.mockImplementation(async () => ({ ok: true, body: { decisions: _sorted() } }));
  setToolDecision.mockImplementation(async (toolName, commandSignature, allow) => {
    const entry = { tool_name: toolName, command_signature: commandSignature, allow };
    const idx = decisions.findIndex((d) => _key(d) === _key(entry));
    if (idx === -1) { decisions.push(entry); } else { decisions[idx] = entry; }
    return { ok: true };
  });
  forgetToolDecision.mockImplementation(async (toolName, commandSignature) => {
    decisions = decisions.filter(
      (d) => !(d.tool_name === toolName && d.command_signature === commandSignature),
    );
    return { ok: true };
  });
  clearToolDecisions.mockImplementation(async () => {
    decisions = [];
    return { ok: true };
  });
});

function remember(toolName, allow, commandSignature = '') {
  decisions.push({ tool_name: toolName, command_signature: commandSignature, allow });
}


describe('ClaudePermissionsSettingsPanel', () => {

  test('empty state when no decisions are remembered', async () => {
    render(<ClaudePermissionsSettingsPanel />);
    await waitFor(() => expect(fetchToolDecisions).toHaveBeenCalled());
    expect(screen.getByText(/no saved permissions yet/i)).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  test('lists remembered tools name-sorted with their scope', async () => {
    remember('Write', true);
    remember('Bash', false);
    render(<ClaudePermissionsSettingsPanel />);
    await screen.findByText('Bash');
    const rows = screen.getAllByRole('row').slice(1); // drop header
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('Bash')).toBeInTheDocument();
    expect(within(rows[0]).getByLabelText('Scope for Bash')).toHaveValue('deny');
    expect(within(rows[1]).getByText('Write')).toBeInTheDocument();
    expect(within(rows[1]).getByLabelText('Scope for Write')).toHaveValue('allow');
  });

  test('changing the scope select calls setToolDecision and refreshes', async () => {
    remember('Bash', true);
    render(<ClaudePermissionsSettingsPanel />);
    await screen.findByText('Bash');

    fireEvent.change(screen.getByLabelText('Scope for Bash'), {
      target: { value: 'deny' },
    });

    await waitFor(() => expect(setToolDecision).toHaveBeenCalledWith('Bash', '', false));
    await waitFor(() => expect(screen.getByLabelText('Scope for Bash')).toHaveValue('deny'));
  });

  test('Clear removes a single tool and the row disappears', async () => {
    remember('Bash', true);
    remember('Edit', true);
    render(<ClaudePermissionsSettingsPanel />);
    await screen.findByText('Bash');

    const bashRow = screen.getByText('Bash').closest('tr');
    fireEvent.click(within(bashRow).getByRole('button', { name: /clear/i }));

    await waitFor(() => expect(forgetToolDecision).toHaveBeenCalledWith('Bash', ''));
    await waitFor(() => expect(screen.queryByText('Bash')).toBeNull());
    expect(screen.getByText('Edit')).toBeInTheDocument();
  });

  test('Clear all wipes every decision and shows the empty state', async () => {
    remember('Bash', true);
    remember('Edit', false);
    render(<ClaudePermissionsSettingsPanel />);
    await screen.findByText('Bash');

    fireEvent.click(screen.getByRole('button', { name: /clear all/i }));

    await waitFor(() => expect(clearToolDecisions).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/no saved permissions yet/i)).toBeInTheDocument());
  });

  test('a command-keyed Bash entry shows its command + clears by command', async () => {
    remember('Bash', true, 'mvn -B verify');
    remember('Bash', true, 'docker run x');
    render(<ClaudePermissionsSettingsPanel />);
    await screen.findByText('mvn -B verify');
    expect(screen.getByText('docker run x')).toBeInTheDocument();

    // Clear ONLY the docker command — mvn stays.
    const dockerRow = screen.getByText('docker run x').closest('tr');
    fireEvent.click(within(dockerRow).getByRole('button', { name: /clear/i }));

    await waitFor(() => expect(forgetToolDecision).toHaveBeenCalledWith('Bash', 'docker run x'));
    await waitFor(() => expect(screen.queryByText('docker run x')).toBeNull());
    expect(screen.getByText('mvn -B verify')).toBeInTheDocument();
  });

  test('the filter narrows the listed entries', async () => {
    remember('Bash', true, 'mvn -B verify');
    remember('Bash', true, 'docker run x');
    remember('Edit', true);
    render(<ClaudePermissionsSettingsPanel />);
    await screen.findByText('mvn -B verify');

    fireEvent.change(screen.getByLabelText(/filter saved permissions/i), {
      target: { value: 'docker' },
    });

    expect(screen.getByText('docker run x')).toBeInTheDocument();
    expect(screen.queryByText('mvn -B verify')).toBeNull();
    expect(screen.queryByText('Edit')).toBeNull();
  });
});


// SettingsDrawer never unmounts — ``open`` only drives a CSS transform — and
// the selected tab survives closing. So a panel that polls unconditionally
// keeps polling for the rest of the page's life whenever it happened to be
// the last tab viewed, with nothing on screen to show for it.
describe('ClaudePermissionsSettingsPanel — polls only while the drawer is open', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  test('a closed drawer issues no request at all', async () => {
    render(<ClaudePermissionsSettingsPanel open={false} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchToolDecisions).not.toHaveBeenCalled();
  });

  test('an open drawer reads immediately', async () => {
    render(<ClaudePermissionsSettingsPanel open />);
    await waitFor(() => expect(fetchToolDecisions).toHaveBeenCalled());
  });

  test('mounted with no prop still reads', async () => {
    // The default keeps the component usable on its own; the drawer is what
    // supplies the real answer.
    render(<ClaudePermissionsSettingsPanel />);
    await waitFor(() => expect(fetchToolDecisions).toHaveBeenCalled());
  });

  test('closing the drawer stops the polling', async () => {
    const { rerender } = render(<ClaudePermissionsSettingsPanel open />);
    await waitFor(() => expect(fetchToolDecisions).toHaveBeenCalled());

    rerender(<ClaudePermissionsSettingsPanel open={false} />);
    fetchToolDecisions.mockClear();
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchToolDecisions).not.toHaveBeenCalled();
  });
});
