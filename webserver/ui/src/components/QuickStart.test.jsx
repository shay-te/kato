// Quick start, rendered for real: the API is the one stand-in, answering the
// way quick_start_routes.py does.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchQuickStart: vi.fn(),
  fetchQuickStartChecks: vi.fn(),
  saveQuickStart: vi.fn(),
  startClaudeSignIn: vi.fn(),
  fetchClaudeSignIn: vi.fn(),
  sendClaudeSignInCode: vi.fn(),
  cancelClaudeSignIn: vi.fn(),
  openGitInstaller: vi.fn(),
  upgradeAgentCli: vi.fn(),
  fetchAgentUpgradeStatus: vi.fn(),
  fetchDirectoryListing: vi.fn(),
}));
vi.mock('../hooks/useAgentVersion.js', () => ({ refreshAgentVersion: vi.fn() }));
vi.mock('../hooks/useCatalogRefresh.js', () => ({ refreshCatalogs: vi.fn() }));

import * as api from '../api.js';
import QuickStart from './QuickStart.jsx';

const READY_CLAUDE = { installed: true, version: '2.1.325', logged_in: true, install: null, binary: 'claude' };
const GIT = { installed: true, version: '2.43.0', install: null };

function serve({ claude = READY_CLAUDE, git = GIT, folders = [{ path: '/home/me/dev', repositories: 3 }] } = {}) {
  const body = { claude, git, folders, new_folder: '/home/me/code', home: '/home/me', folder: '' };
  api.fetchQuickStart.mockResolvedValue(body);
  api.fetchQuickStartChecks.mockResolvedValue({ claude, git });
  return body;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchAgentUpgradeStatus.mockResolvedValue({ state: 'idle' });
  api.saveQuickStart.mockResolvedValue({ ok: true, body: { ok: true } });
});
afterEach(() => { vi.useRealTimers(); });

async function startButton() {
  return screen.findByRole('button', { name: /Start kato/ });
}

describe('QuickStart', () => {
  test('everything found: the best folder is picked and Start saves it', async () => {
    serve({ folders: [{ path: '/home/me/dev', repositories: 3 }, { path: '/home/me/work', repositories: 1 }] });
    const onStarted = vi.fn();
    render(<QuickStart onStarted={onStarted} onUseWizard={vi.fn()} />);
    const start = await startButton();
    expect(screen.getByText(/Installed and signed in — version 2.1.325/)).toBeInTheDocument();
    expect(screen.getByText(/Installed — version 2.43.0/)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '~/dev — 3 repositories' })).toBeChecked();
    expect(screen.getByRole('radio', { name: '~/work — 1 repository' })).not.toBeChecked();

    fireEvent.click(screen.getByRole('radio', { name: /~\/work/ }));
    fireEvent.click(start);
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(api.saveQuickStart).toHaveBeenCalledWith({ folder: '/home/me/work', create: false });
  });

  test('a computer with no repositories gets a folder created for them', async () => {
    serve({ folders: [] });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    fireEvent.click(await startButton());
    expect(screen.getByText(/No repositories found on this computer yet/)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '~/code — new folder' })).toBeChecked();
    await waitFor(() => expect(api.saveQuickStart).toHaveBeenCalledWith({ folder: '/home/me/code', create: true }));
  });

  test('a save kato refuses says why and Start can be pressed again', async () => {
    serve();
    api.saveQuickStart.mockResolvedValue({ ok: false, body: { error: '/home/me/dev does not exist' } });
    const onStarted = vi.fn();
    render(<QuickStart onStarted={onStarted} onUseWizard={vi.fn()} />);
    fireEvent.click(await startButton());
    expect(await screen.findByRole('alert')).toHaveTextContent('/home/me/dev does not exist');
    expect(onStarted).not.toHaveBeenCalled();
    expect(await startButton()).toBeEnabled();
  });

  test('Claude Code missing: kato installs it, shows the command first, and checks again when done', async () => {
    serve({ claude: { installed: false, install: { available: true, command: 'curl -fsSL https://claude.ai/install.sh | bash', reason: '' } } });
    api.upgradeAgentCli.mockResolvedValue({ ok: true, body: { state: 'done', ok: true, percent: 100 } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await startButton()).toBeDisabled();
    expect(screen.getByText('Install Claude Code first.')).toBeInTheDocument();
    expect(screen.getByText('curl -fsSL https://claude.ai/install.sh | bash')).toBeInTheDocument();

    api.fetchQuickStartChecks.mockResolvedValue({ claude: READY_CLAUDE, git: GIT });
    fireEvent.click(screen.getByRole('button', { name: 'Install Claude Code' }));
    await waitFor(() => expect(api.upgradeAgentCli).toHaveBeenCalledWith('claude'));
    expect(await screen.findByText(/Installed and signed in/)).toBeInTheDocument();
    expect(await startButton()).toBeEnabled();
  });

  test('an install under way shows its progress; a failed one says why', async () => {
    serve({ claude: { installed: false, install: { available: true, command: 'x', reason: '' } } });
    api.upgradeAgentCli.mockResolvedValue({ ok: true, body: { state: 'running', percent: 40, step: 'Downloading…' } });
    api.fetchAgentUpgradeStatus.mockResolvedValue({ state: 'error', ok: false, message: 'curl: (6) Could not resolve host' });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Install Claude Code' }));
    expect(await screen.findByRole('progressbar', { name: 'Installing Claude Code' })).toHaveAttribute('value', '40');
    expect(screen.getByText('Downloading…')).toBeInTheDocument();
    expect(await screen.findByText(/The install did not finish: curl: \(6\) Could not resolve host/)).toBeInTheDocument();
  });

  test('where kato cannot install it, it says why and where to get it', async () => {
    serve({ claude: { installed: false, install: { available: false, command: '', reason: 'in-app upgrade is disabled' } } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await screen.findByText(/Kato cannot install it here: in-app upgrade is disabled/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'code.claude.com/docs/en/setup' })).toHaveAttribute('href', 'https://code.claude.com/docs/en/setup');
    expect(screen.queryByRole('button', { name: 'Install Claude Code' })).toBeNull();
  });

  test('signed out: kato runs the sign-in, offers the link, and hands the pasted code to the CLI', async () => {
    serve({ claude: { installed: true, version: '2.1.325', logged_in: false, install: null } });
    api.startClaudeSignIn.mockResolvedValue({ ok: true, body: {
      state: 'waiting', link: 'https://claude.com/cai/oauth/authorize?x=1', asks_for_code: true, message: '',
    } });
    api.sendClaudeSignInCode.mockResolvedValue({ ok: true, body: { state: 'waiting', link: 'https://claude.com/cai/oauth/authorize?x=1', asks_for_code: true } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await startButton()).toBeDisabled();
    expect(screen.getByText('Sign in to Claude first.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Sign in to Claude' }));
    expect(await screen.findByText(/A browser window opened to sign in/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the sign-in page' })).toHaveAttribute('href', 'https://claude.com/cai/oauth/authorize?x=1');
    const send = screen.getByRole('button', { name: 'Send code' });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Code from the sign-in page' }), { target: { value: '  abc#123 ' } });
    fireEvent.click(send);
    await waitFor(() => expect(api.sendClaudeSignInCode).toHaveBeenCalledWith('abc#123'));
  });

  test('a refused code or a sign-in that cannot start says why; cancel ends it', async () => {
    serve({ claude: { installed: true, version: '2.1.325', logged_in: false, install: null } });
    api.startClaudeSignIn.mockResolvedValueOnce({ ok: false, body: { error: 'could not start claude' } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in to Claude' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('could not start claude');

    api.startClaudeSignIn.mockResolvedValue({ ok: true, body: { state: 'waiting', link: '', asks_for_code: true } });
    api.sendClaudeSignInCode.mockResolvedValue({ ok: false, body: { error: 'paste the code exactly as the sign-in page shows it' } });
    api.cancelClaudeSignIn.mockResolvedValue({ ok: true, body: { state: 'cancelled', message: 'sign-in cancelled' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to Claude' }));
    fireEvent.change(await screen.findByRole('textbox', { name: 'Code from the sign-in page' }), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send code' }));
    expect(await screen.findByText('paste the code exactly as the sign-in page shows it')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open the sign-in page' })).toBeNull();  // no link printed
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('button', { name: 'Sign in to Claude' })).toBeInTheDocument();
  });

  test('while something is missing the checks are asked again, and stop once all pass', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const signedOut = { installed: true, version: '2.1.325', logged_in: false, install: null };
    serve({ claude: signedOut });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await screen.findByRole('button', { name: 'Sign in to Claude' });
    api.fetchQuickStartChecks.mockResolvedValue({ claude: READY_CLAUDE, git: GIT });  // signed in, in the browser
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(await screen.findByText(/Installed and signed in/)).toBeInTheDocument();
    const asked = api.fetchQuickStartChecks.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(api.fetchQuickStartChecks.mock.calls.length).toBe(asked);
  });

  test('a sign-in finished in the browser is seen through its status too', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve({ claude: { installed: true, version: '2.1.325', logged_in: false, install: null } });
    api.startClaudeSignIn.mockResolvedValue({ ok: true, body: { state: 'waiting', link: '', asks_for_code: false } });
    api.fetchClaudeSignIn.mockResolvedValue({ state: 'done', message: 'signed in' });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in to Claude' }));
    await screen.findByText(/A browser window opened/);
    expect(screen.queryByRole('textbox')).toBeNull();  // no code asked for
    api.fetchQuickStartChecks.mockResolvedValue({ claude: READY_CLAUDE, git: GIT });
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(await screen.findByText(/Installed and signed in/)).toBeInTheDocument();
  });

  test('git missing on a Mac: kato opens Apple’s installer; elsewhere it shows the command', async () => {
    serve({ git: { installed: false, version: '', install: {
      command: 'xcode-select --install', text: 'Apple’s command line tools include git.', can_start: true,
    } } });
    api.openGitInstaller.mockResolvedValue({ ok: true, body: { ok: true } });
    const { unmount } = render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await startButton()).toBeDisabled();
    expect(screen.getByText('Install git first.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Install git' }));
    expect(await screen.findByRole('button', { name: 'Installer opened — finish it there' })).toBeInTheDocument();
    expect(api.fetchQuickStartChecks).toHaveBeenCalled();
    unmount();

    serve({ git: { installed: false, version: '', install: { command: 'sudo apt install git', text: '', can_start: false } } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await screen.findByText('sudo apt install git')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Install git' })).toBeNull();
  });

  test('another folder is picked from the browser and shown as the pick', async () => {
    serve();
    api.fetchDirectoryListing.mockResolvedValue({ ok: true, body: { path: '/home/me', parent: '/home', entries: [{ name: 'src', path: '/home/me/src' }] } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Another folder…' }));
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.getByRole('button', { name: 'Another folder…' })).toBeInTheDocument();
  });

  test('the ticket-system wizard is one link away; a computer kato cannot read says so', async () => {
    serve();
    const onUseWizard = vi.fn();
    const { unmount } = render(<QuickStart onStarted={vi.fn()} onUseWizard={onUseWizard} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Connect a ticket system instead' }));
    expect(onUseWizard).toHaveBeenCalled();
    unmount();

    api.fetchQuickStart.mockRejectedValue(new Error('500 Internal Server Error'));
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not look at this computer: 500 Internal Server Error');
  });
});
