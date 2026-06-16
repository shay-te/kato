import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { _agentVer, _refresh, _upgrade } = vi.hoisted(() => ({
  _agentVer: { value: null },
  _refresh: { fn: null },
  _upgrade: { fn: null },
}));
vi.mock('../hooks/useAgentVersion.js', () => ({
  useAgentVersion: () => _agentVer.value,
  refreshAgentVersion: (...a) => _refresh.fn(...a),
}));
vi.mock('../api.js', () => ({
  upgradeAgentCli: (...a) => _upgrade.fn(...a),
}));

import AgentVersionBanner from './AgentVersionBanner.jsx';

beforeEach(() => {
  _refresh.fn = vi.fn().mockResolvedValue({});
  _upgrade.fn = vi.fn().mockResolvedValue({ ok: true, body: { ok: true, message: 'upgraded' } });
});

function renderWith(info) {
  _agentVer.value = info;
  return render(<AgentVersionBanner />);
}

const _UPGRADABLE = {
  backend: 'claude', binary: 'claude', found: true, up_to_date: false,
  version: '2.1.142', recommended_min: '2.1.160',
  download_url: 'https://code.claude.com/docs/en/setup',
  can_upgrade: true, upgrade_command: 'npm install -g @anthropic-ai/claude-code@latest',
};

describe('AgentVersionBanner', () => {
  test('renders nothing while loading (null)', () => {
    const { container } = renderWith(null);
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing for an up-to-date CLI', () => {
    const { container } = renderWith({
      backend: 'claude', found: true, up_to_date: true, version: '2.1.170',
    });
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing for OpenHands (no local CLI)', () => {
    const { container } = renderWith({ backend: 'openhands', up_to_date: true });
    expect(container.firstChild).toBeNull();
  });

  test('warns when the configured CLI is out of date', () => {
    renderWith({
      backend: 'claude', binary: 'claude', found: true, up_to_date: false,
      version: '2.1.142', recommended_min: '2.1.160',
      download_url: 'https://code.claude.com/docs/en/setup',
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/CLAUDE CLI 2\.1\.142 is out of date/i);
    expect(screen.getByRole('alert')).toHaveTextContent(/2\.1\.160/);
  });

  test('the out-of-date banner links to the download page (opens in a new tab)', () => {
    renderWith({
      backend: 'claude', binary: 'claude', found: true, up_to_date: false,
      version: '2.1.142', recommended_min: '2.1.160',
      download_url: 'https://code.claude.com/docs/en/setup',
    });
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', 'https://code.claude.com/docs/en/setup');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  test('warns when the configured CLI is missing from PATH', () => {
    renderWith({
      backend: 'codex', binary: 'codex', found: false, up_to_date: false,
      download_url: 'https://developers.openai.com/codex/',
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/CODEX CLI not found on PATH/i);
    expect(screen.getByRole('link')).toHaveAttribute('href', 'https://developers.openai.com/codex/');
  });

  test('claude out-of-date message mentions workflows/ultracode; codex does not', () => {
    const { unmount } = renderWith({
      backend: 'claude', binary: 'claude', found: true, up_to_date: false,
      version: '2.1.142', recommended_min: '2.1.160',
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/workflows\/ultracode/i);
    unmount();
    renderWith({
      backend: 'codex', binary: 'codex', found: true, up_to_date: false,
      version: '0.1.0', recommended_min: '0.5.0',
    });
    expect(screen.getByRole('alert')).not.toHaveTextContent(/workflows\/ultracode/i);
  });

  test('no upgrade button unless can_upgrade', () => {
    renderWith({ ..._UPGRADABLE, can_upgrade: false });
    expect(screen.queryByRole('button', { name: /upgrade now/i })).toBeNull();
  });

  test('upgrade requires explicit confirm (does not run on the first click)', () => {
    renderWith(_UPGRADABLE);
    fireEvent.click(screen.getByRole('button', { name: /upgrade now/i }));
    // The exact command is shown for approval; nothing has run yet.
    expect(screen.getByText('npm install -g @anthropic-ai/claude-code@latest')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm upgrade/i })).toBeInTheDocument();
    expect(_upgrade.fn).not.toHaveBeenCalled();
  });

  test('cancel backs out without running', () => {
    renderWith(_UPGRADABLE);
    fireEvent.click(screen.getByRole('button', { name: /upgrade now/i }));
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.getByRole('button', { name: /upgrade now/i })).toBeInTheDocument();
    expect(_upgrade.fn).not.toHaveBeenCalled();
  });

  test('confirming runs the upgrade and re-probes live (no reload)', async () => {
    renderWith(_UPGRADABLE);
    fireEvent.click(screen.getByRole('button', { name: /upgrade now/i }));
    fireEvent.click(screen.getByRole('button', { name: /confirm upgrade/i }));
    await waitFor(() => expect(_upgrade.fn).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(_refresh.fn).toHaveBeenCalledTimes(1));
  });

  test('a failed upgrade does not re-probe and returns to idle', async () => {
    _upgrade.fn = vi.fn().mockResolvedValue({ ok: true, body: { ok: false, message: 'npm exited 1' } });
    renderWith(_UPGRADABLE);
    fireEvent.click(screen.getByRole('button', { name: /upgrade now/i }));
    fireEvent.click(screen.getByRole('button', { name: /confirm upgrade/i }));
    await waitFor(() => expect(_upgrade.fn).toHaveBeenCalledTimes(1));
    expect(_refresh.fn).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole('button', { name: /upgrade now/i })).toBeInTheDocument());
  });
});
