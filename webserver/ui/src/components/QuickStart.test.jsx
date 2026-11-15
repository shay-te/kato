// Quick start, rendered for real: the API is the one stand-in, answering the
// way quick_start_routes.py does. The operator picks the agent and the folder;
// the screen never picks either for them.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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

const READY_CLAUDE = { agent: 'claude', installed: true, version: '2.1.325', logged_in: true, install: null };
const MISSING = (agent, install, help = '') => ({ agent, installed: false, version: '', logged_in: null, install, help, download: `https://get.${agent}` });
const GIT = { installed: true, version: '2.43.0', install: null };

// What the server holds; ``checks`` answers for whichever agent is asked.
let statuses;
let git;
function serve({ agents = {}, gitStatus = GIT, folders = [{ path: '/home/me/dev', repositories: 3 }], agent = '', folder = '' } = {}) {
  statuses = {
    claude: READY_CLAUDE,
    codex: MISSING('codex', { available: false, command: '', reason: 'no npm' }, 'Install Codex CLI:\n  npm install -g @openai/codex'),
    grok: MISSING('grok', { available: false, command: '', reason: '' }),
    opencode: MISSING('opencode', { available: false, command: '', reason: '' }),
    ...agents,
  };
  git = gitStatus;
  api.fetchQuickStart.mockResolvedValue({
    agents: statuses, agent, git, folders, new_folder: '/home/me/code', home: '/home/me', folder,
  });
  api.fetchQuickStartChecks.mockImplementation(async (which) => ({ agent: statuses[which], git }));
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchAgentUpgradeStatus.mockResolvedValue({ state: 'idle' });
  api.saveQuickStart.mockResolvedValue({ ok: true, body: { ok: true } });
});
afterEach(() => { vi.useRealTimers(); });

const startButton = () => screen.findByRole('button', { name: /Start kato/ });
const chip = (name) => screen.getByRole('radio', { name: new RegExp(`^${name}`) });

describe('QuickStart — the operator picks', () => {
  test('nothing is picked for them: no agent, no folder, and Start says what is missing', async () => {
    serve();
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await startButton()).toBeDisabled();
    expect(screen.getByText('Pick the agent you use.')).toBeInTheDocument();
    for (const name of ['Claude Code', 'Codex', 'Grok', 'OpenCode']) {
      expect(chip(name)).toHaveAttribute('aria-checked', 'false');
    }
    expect(screen.getByRole('radio', { name: '~/dev — 3 repositories' })).not.toBeChecked();
    // Which agents are already on this computer, before picking.
    expect(within(chip('Claude Code')).getByText('installed')).toBeInTheDocument();
    expect(within(chip('Codex')).queryByText('installed')).toBeNull();
  });

  test('picking an agent and a folder, Start saves exactly those', async () => {
    serve({ folders: [{ path: '/home/me/dev', repositories: 3 }, { path: '/home/me/work', repositories: 1 }] });
    const onStarted = vi.fn();
    render(<QuickStart onStarted={onStarted} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Claude Code'));
    expect(chip('Claude Code')).toHaveAttribute('aria-checked', 'true');
    expect(await screen.findByText(/Installed and signed in — version 2.1.325/)).toBeInTheDocument();
    expect(screen.getByText('Pick the folder your code is in.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: '~/work — 1 repository' }));
    fireEvent.click(await startButton());
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(api.saveQuickStart).toHaveBeenCalledWith({ agent: 'claude', folder: '/home/me/work', create: false });
  });

  test('a computer with no repositories offers a folder to create — picked only by the operator', async () => {
    serve({ folders: [] });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Claude Code'));
    const offered = screen.getByRole('radio', { name: '~/code — new folder' });
    expect(offered).not.toBeChecked();
    fireEvent.click(offered);
    fireEvent.click(await startButton());
    await waitFor(() => expect(api.saveQuickStart).toHaveBeenCalledWith({ agent: 'claude', folder: '/home/me/code', create: true }));
  });

  test('what was picked on an earlier run is picked again', async () => {
    serve({ agents: { grok: { agent: 'grok', installed: true, version: '0.9.1', logged_in: null, login_command: 'grok login' } },
      agent: 'grok', folder: '/home/me/dev' });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await startButton()).toBeEnabled();
    expect(chip('Grok')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: '~/dev — 3 repositories' })).toBeChecked();
  });

  test('a save kato refuses says why and Start can be pressed again', async () => {
    serve({ agent: 'claude', folder: '/home/me/dev' });
    api.saveQuickStart.mockResolvedValue({ ok: false, body: { error: '/home/me/dev does not exist' } });
    const onStarted = vi.fn();
    render(<QuickStart onStarted={onStarted} onUseWizard={vi.fn()} />);
    fireEvent.click(await startButton());
    expect(await screen.findByRole('alert')).toHaveTextContent('/home/me/dev does not exist');
    expect(onStarted).not.toHaveBeenCalled();
    expect(await startButton()).toBeEnabled();
  });
});

describe('QuickStart — each agent', () => {
  test('Codex installed: it names its own sign-in, and kato does not block on it', async () => {
    serve({ agents: { codex: { agent: 'codex', installed: true, version: '0.166.0', logged_in: null, login_command: 'codex login' } },
      folder: '/home/me/dev' });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Codex'));
    expect(await screen.findByText(/Installed — version 0.166.0/)).toBeInTheDocument();
    expect(screen.getByText('codex login')).toBeInTheDocument();
    expect(await startButton()).toBeEnabled();
    fireEvent.click(await startButton());
    await waitFor(() => expect(api.saveQuickStart).toHaveBeenCalledWith({ agent: 'codex', folder: '/home/me/dev', create: false }));
  });

  test('an agent kato cannot install here shows its own install words and where to get it', async () => {
    serve();
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Codex'));
    expect(await screen.findByText('Codex is not on this computer yet.')).toBeInTheDocument();
    expect(screen.getByText(/npm install -g @openai\/codex/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://get.codex' })).toHaveAttribute('href', 'https://get.codex');
    expect(screen.queryByRole('button', { name: /^Install/ })).toBeNull();
    expect(screen.getByText('Install Codex first.')).toBeInTheDocument();
  });

  test('where kato can install it, it shows the command, installs, and checks again', async () => {
    serve({ agents: { claude: MISSING('claude', { available: true, command: 'curl -fsSL https://claude.ai/install.sh | bash', reason: '' }) } });
    api.upgradeAgentCli.mockResolvedValue({ ok: true, body: { state: 'done', ok: true, percent: 100 } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Claude Code'));
    expect(await screen.findByText('curl -fsSL https://claude.ai/install.sh | bash')).toBeInTheDocument();
    statuses.claude = READY_CLAUDE;  // what the install leaves behind
    fireEvent.click(screen.getByRole('button', { name: 'Install Claude Code' }));
    await waitFor(() => expect(api.upgradeAgentCli).toHaveBeenCalledWith('claude'));
    expect(await screen.findByText(/Installed and signed in/)).toBeInTheDocument();
  });

  test('an install under way shows its progress and locks the other chips; a failed one says why', async () => {
    serve({ agents: { codex: MISSING('codex', { available: true, command: 'npm install -g @openai/codex@latest', reason: '' }) } });
    api.upgradeAgentCli.mockResolvedValue({ ok: true, body: { state: 'running', percent: 40, step: 'Downloading…' } });
    let finish;
    api.fetchAgentUpgradeStatus.mockImplementation(() => new Promise((done) => { finish = done; }));
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Codex'));
    const install = await screen.findByRole('button', { name: 'Install Codex' });
    const askedBefore = api.fetchAgentUpgradeStatus.mock.calls.length;  // one per mount
    fireEvent.click(install);
    expect(await screen.findByRole('progressbar', { name: 'Installing Codex' })).toHaveAttribute('value', '40');
    expect(chip('Claude Code')).toBeDisabled();
    expect(chip('Codex')).toBeEnabled();
    // The answer that counts is to the poll that follows the install starting.
    await waitFor(() => expect(api.fetchAgentUpgradeStatus.mock.calls.length).toBeGreaterThan(askedBefore), { timeout: 3000 });
    await act(async () => { finish({ state: 'error', ok: false, message: 'npm ERR! EACCES' }); });
    expect(await screen.findByText(/The install did not finish: npm ERR! EACCES/)).toBeInTheDocument();
  });

  test('Claude Code signed out: kato runs the sign-in, offers the link, and hands the pasted code to the CLI', async () => {
    serve({ agents: { claude: { agent: 'claude', installed: true, version: '2.1.325', logged_in: false, install: null } } });
    api.startClaudeSignIn.mockResolvedValue({ ok: true, body: {
      state: 'waiting', link: 'https://claude.com/cai/oauth/authorize?x=1', asks_for_code: true, message: '',
    } });
    api.sendClaudeSignInCode.mockResolvedValue({ ok: true, body: { state: 'waiting', link: 'https://claude.com/cai/oauth/authorize?x=1', asks_for_code: true } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Claude Code'));
    expect(await screen.findByText('Sign in to Claude Code first.')).toBeInTheDocument();
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
    serve({ agents: { claude: { agent: 'claude', installed: true, version: '2.1.325', logged_in: false, install: null } } });
    api.startClaudeSignIn.mockResolvedValueOnce({ ok: false, body: { error: 'could not start claude' } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Claude Code'));
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

  test('while the picked agent is not ready it is asked about again, and no longer once it is', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve({ agents: { claude: { agent: 'claude', installed: true, version: '2.1.325', logged_in: false, install: null } } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Claude Code'));
    await screen.findByRole('button', { name: 'Sign in to Claude' });
    statuses.claude = READY_CLAUDE;  // signed in, in the browser
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(await screen.findByText(/Installed and signed in/)).toBeInTheDocument();
    expect(api.fetchQuickStartChecks).toHaveBeenCalledWith('claude');
    const asked = api.fetchQuickStartChecks.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(api.fetchQuickStartChecks.mock.calls.length).toBe(asked);
  });

  test('a sign-in finished in the browser is seen through its status too', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve({ agents: { claude: { agent: 'claude', installed: true, version: '2.1.325', logged_in: false, install: null } } });
    api.startClaudeSignIn.mockResolvedValue({ ok: true, body: { state: 'waiting', link: '', asks_for_code: false } });
    api.fetchClaudeSignIn.mockResolvedValue({ state: 'done', message: 'signed in' });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Claude Code'));
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in to Claude' }));
    await screen.findByText(/A browser window opened/);
    expect(screen.queryByRole('textbox')).toBeNull();  // no code asked for
    statuses.claude = READY_CLAUDE;
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(await screen.findByText(/Installed and signed in/)).toBeInTheDocument();
  });

  test('a check that comes back for an agent no longer picked is not shown for the new one', async () => {
    serve();
    let answerCodex;
    api.fetchQuickStartChecks.mockImplementation((which) => (which === 'codex'
      ? new Promise((done) => { answerCodex = done; })
      : Promise.resolve({ agent: statuses[which], git })));
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(chip('Codex'));
    fireEvent.click(chip('Claude Code'));
    await act(async () => { answerCodex({ agent: { agent: 'codex', installed: true, version: '9.9.9' }, git }); });
    expect(await screen.findByText(/Installed and signed in — version 2.1.325/)).toBeInTheDocument();
    expect(screen.queryByText(/9.9.9/)).toBeNull();
  });
});

describe('QuickStart — git, the folder browser, the wizard', () => {
  test('git missing on a Mac: kato opens Apple’s installer; elsewhere it shows the command', async () => {
    serve({ agent: 'claude', folder: '/home/me/dev', gitStatus: { installed: false, version: '', install: {
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

    serve({ gitStatus: { installed: false, version: '', install: { command: 'sudo apt install git', text: '', can_start: false } } });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await screen.findByText('sudo apt install git')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Install git' })).toBeNull();
  });

  test('another folder is picked from the browser and shown as the pick', async () => {
    serve();
    api.fetchDirectoryListing.mockImplementation(async (path) => (path === '/home/me/src'
      ? { path: '/home/me/src', parent: '/home/me', home: '/home/me', dirs: [] }
      : { path: '/home/me', parent: '/home', home: '/home/me', dirs: [{ name: 'src', path: '/home/me/src' }] }));
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Another folder…' }));
    fireEvent.click(await screen.findByRole('button', { name: /src/ }));
    await screen.findByText('(no subfolders)');
    fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));
    expect(await screen.findByRole('radio', { name: '~/src — your pick' })).toBeChecked();
    expect(screen.queryByRole('button', { name: 'Use this folder' })).toBeNull();  // the browser closed
  });

  // ~/code is offered before it exists: the browser must not open there.
  test('with the folder-to-create picked, the browser opens on the home folder', async () => {
    serve({ folders: [] });
    api.fetchDirectoryListing.mockResolvedValue({ path: '/home/me', parent: '/home', home: '/home/me', dirs: [] });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    await startButton();
    fireEvent.click(screen.getByRole('radio', { name: '~/code — new folder' }));
    fireEvent.click(screen.getByRole('button', { name: 'Another folder…' }));
    await waitFor(() => expect(api.fetchDirectoryListing).toHaveBeenCalledWith('/home/me'));
    expect(api.fetchDirectoryListing).not.toHaveBeenCalledWith('/home/me/code');
  });

  test('a folder picked on an earlier run that is not in the list is shown as the pick', async () => {
    serve({ folder: '/opt/src' });
    render(<QuickStart onStarted={vi.fn()} onUseWizard={vi.fn()} />);
    expect(await screen.findByRole('radio', { name: '/opt/src — your pick' })).toBeChecked();
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
