import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { _agentVer, _refresh, _upgrade, _status, _catalogs } = vi.hoisted(() => ({
  _agentVer: { value: null },
  _refresh: { fn: null },
  _upgrade: { fn: null },
  _status: { fn: null },
  _catalogs: { fn: null },
}));
vi.mock('../hooks/useAgentVersion.js', () => ({
  useAgentVersion: () => _agentVer.value,
  refreshAgentVersion: (...a) => _refresh.fn(...a),
}));
vi.mock('../hooks/useCatalogRefresh.js', () => ({
  refreshCatalogs: (...a) => _catalogs.fn(...a),
}));
vi.mock('../api.js', () => ({
  upgradeAgentCli: (...a) => _upgrade.fn(...a),
  fetchAgentUpgradeStatus: (...a) => _status.fn(...a),
}));

import AgentVersionBanner from './AgentVersionBanner.jsx';

const IDLE = { state: 'idle', percent: 0, lines: [] };
const DONE = {
  state: 'done', ok: true, percent: 100, message: 'upgraded (2.1.142 → 2.1.222)',
  lines: ['added 1 package'],
};

beforeEach(() => {
  _refresh.fn = vi.fn().mockResolvedValue({});
  _catalogs.fn = vi.fn();
  _status.fn = vi.fn().mockResolvedValue({ ...IDLE });
  _upgrade.fn = vi.fn().mockResolvedValue({ ok: true, body: { ...DONE } });
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

  test('flags an available update calmly (status role, not a red alarm)', () => {
    const { container } = renderWith({
      backend: 'claude', binary: 'claude', found: true, up_to_date: false,
      version: '2.1.142', recommended_min: '2.1.160',
      download_url: 'https://code.claude.com/docs/en/setup',
    });
    const banner = screen.getByRole('status');
    expect(banner).toHaveTextContent(/CLAUDE CLI update available/i);
    expect(banner).toHaveTextContent(/2\.1\.142/);
    expect(banner).toHaveTextContent(/2\.1\.160/);
    // Calm advisory styling — NOT the red security-alert banner.
    expect(banner).toHaveClass('kato-version-banner', 'kato-version-banner--info');
    expect(container.querySelector('.kato-safety-banner')).toBeNull();
  });

  test('a CLI that clears the floor but trails the published release still shows', () => {
    // The reported bug: 2.1.179 vs a 2.1.160 floor read as "up to date" and the
    // banner stayed silent while 2.1.222 was out.
    renderWith({
      backend: 'claude', binary: 'claude', found: true, up_to_date: true,
      update_available: true, version: '2.1.179', recommended_min: '2.1.160',
      latest_version: '2.1.222',
    });
    const banner = screen.getByRole('status');
    expect(banner).toHaveTextContent(/update available/i);
    expect(banner).toHaveTextContent(/2\.1\.179/);
    expect(banner).toHaveTextContent(/latest is 2\.1\.222/i);
  });

  test('names the published version rather than the recommended floor', () => {
    renderWith({
      backend: 'claude', binary: 'claude', found: true, up_to_date: false,
      update_available: true, version: '2.1.142', recommended_min: '2.1.160',
      latest_version: '2.1.222',
    });
    const banner = screen.getByRole('status');
    expect(banner).toHaveTextContent(/latest is 2\.1\.222/i);
    expect(banner).not.toHaveTextContent(/recommended/i);
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

  test('renders exactly ONE link — only "open the download page", not the whole sentence', () => {
    renderWith({
      backend: 'claude', binary: 'claude', found: true, up_to_date: false,
      version: '2.1.142', recommended_min: '2.1.160',
      download_url: 'https://code.claude.com/docs/en/setup',
    });
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveTextContent(/open the download page/i);
    // The message itself is plain text, not wrapped in the anchor.
    expect(links[0]).not.toHaveTextContent(/update available/i);
  });

  test('explains why one-click upgrade is unavailable when there is no button', () => {
    renderWith({
      ..._UPGRADABLE, can_upgrade: false,
      upgrade_blocked_reason:
        'Docker sandbox mode — the CLI is in the image; rebuild with `kato sandbox build`',
    });
    expect(screen.queryByRole('button', { name: /upgrade now/i })).toBeNull();
    expect(screen.getByText(/Docker sandbox mode/i)).toBeInTheDocument();
  });

  test('CLI missing from PATH is a firmer warning (alert role)', () => {
    renderWith({
      backend: 'codex', binary: 'codex', found: false, up_to_date: false,
      download_url: 'https://developers.openai.com/codex/',
    });
    // "not found" genuinely blocks the backend → assertive alert + warn style.
    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/CODEX CLI not found on PATH/i);
    expect(banner).toHaveClass('kato-version-banner--warn');
    expect(screen.getByRole('link')).toHaveAttribute('href', 'https://developers.openai.com/codex/');
  });

  test('claude update message mentions workflows/ultracode; codex does not', () => {
    const { unmount } = renderWith({
      backend: 'claude', binary: 'claude', found: true, up_to_date: false,
      version: '2.1.142', recommended_min: '2.1.160',
    });
    expect(screen.getByRole('status')).toHaveTextContent(/workflows\/ultracode/i);
    unmount();
    renderWith({
      backend: 'codex', binary: 'codex', found: true, up_to_date: false,
      version: '0.1.0', recommended_min: '0.5.0',
    });
    expect(screen.getByRole('status')).not.toHaveTextContent(/workflows\/ultracode/i);
  });

  test('no upgrade button unless can_upgrade', () => {
    renderWith({ ..._UPGRADABLE, can_upgrade: false });
    expect(screen.queryByRole('button', { name: /upgrade now/i })).toBeNull();
  });

  test('the confirm is a popup dialog, not inline in the banner', () => {
    renderWith(_UPGRADABLE);
    // No dialog until the operator asks to upgrade.
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /upgrade now/i }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    // The exact command lives in the popup (the approval), not the banner row.
    expect(dialog).toHaveTextContent('npm install -g @anthropic-ai/claude-code@latest');
    expect(screen.getByRole('status')).not.toHaveTextContent(/npm install/i);
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

  test('a finished upgrade also re-fetches the model picker catalogue', async () => {
    // A new CLI can resolve its aliases to newer models. Re-probing only the
    // version would leave the picker showing the OLD CLI's model labels until
    // the operator hit the header Refresh.
    renderWith(_UPGRADABLE);
    fireEvent.click(screen.getByRole('button', { name: /upgrade now/i }));
    fireEvent.click(screen.getByRole('button', { name: /confirm upgrade/i }));
    await waitFor(() => expect(_catalogs.fn).toHaveBeenCalledTimes(1));
  });

  test('the outcome stays on screen after the run finishes', async () => {
    renderWith(_UPGRADABLE);
    fireEvent.click(screen.getByRole('button', { name: /upgrade now/i }));
    fireEvent.click(screen.getByRole('button', { name: /confirm upgrade/i }));
    await waitFor(() =>
      expect(screen.getByText(/2\.1\.142 → 2\.1\.222/)).toBeInTheDocument());
    // Closing it returns to the banner instead of leaving the dialog stuck.
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  test('a failed upgrade surfaces the error instead of silently closing', async () => {
    _upgrade.fn = vi.fn().mockResolvedValue({
      ok: true,
      body: { state: 'error', ok: false, percent: 60,
              message: 'npm exited with code 1', lines: ['npm ERR! EACCES'] },
    });
    renderWith(_UPGRADABLE);
    fireEvent.click(screen.getByRole('button', { name: /upgrade now/i }));
    fireEvent.click(screen.getByRole('button', { name: /confirm upgrade/i }));
    await waitFor(() =>
      expect(screen.getByText('npm exited with code 1')).toBeInTheDocument());
    expect(screen.getByLabelText('Upgrade output')).toHaveTextContent('EACCES');
  });

  test('re-attaches to an upgrade already running (survives a reload)', async () => {
    _status.fn = vi.fn().mockResolvedValue({
      state: 'running', percent: 55, step: 'Installing…', lines: ['reify'],
      command: 'npm install -g @anthropic-ai/claude-code@latest',
    });
    renderWith(_UPGRADABLE);
    // No click — the job was started before this mount.
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '55');
  });
});
