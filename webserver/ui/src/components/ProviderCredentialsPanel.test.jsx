// Tests for the merged ProviderCredentialsPanel via its two thin config
// wrappers (Task provider + Git provider). Covers the shared machine —
// useSettingsResource (load → fields), providerFields (secret masking),
// settingsSource (badges), useRestartingSave (save → toast + restart
// banner) — plus the one real difference: Task writes the *active*
// provider, Git does not (includeActive).
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskProviders: vi.fn(),
  updateTaskProvider: vi.fn(),
  fetchGitProviders: vi.fn(),
  updateGitProvider: vi.fn(),
  fetchCredentialSources: vi.fn(),
  testTrackerConnection: vi.fn(),
  testGitHostConnection: vi.fn(),
  recheckConnections: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn(), errorFromResult: vi.fn() },
}));

import TaskProviderSettingsPanel from './TaskProviderSettingsPanel.jsx';
import GitProvidersSettingsPanel from './GitProvidersSettingsPanel.jsx';
import {
  fetchTaskProviders, updateTaskProvider,
  fetchGitProviders, updateGitProvider, fetchCredentialSources, testTrackerConnection, testGitHostConnection, recheckConnections,
} from '../api.js';
import { toast } from '../stores/toastStore.js';

const TASK_BODY = {
  active: 'youtrack',
  supported: ['youtrack', 'jira'],
  providers: {
    youtrack: {
      fields: {
        base_url: { value: 'https://yt.example', source: 'kato_settings' },
        token: { value: 'secret-tok', source: 'env' },
      },
    },
    jira: { fields: { base_url: { value: '', source: 'unset' } } },
  },
  settings_file_path: '~/.kato/settings.json',
};

const GIT_BODY = {
  supported: ['bitbucket', 'github'],
  providers: {
    bitbucket: {
      fields: {
        username: { value: 'me', source: 'kato_settings' },
        app_password: { value: 'pw', source: 'kato_settings' },
      },
    },
    github: { fields: { token: { value: '', source: 'unset' } } },
  },
  settings_file_path: '~/.kato/settings.json',
};

beforeEach(() => {
  fetchTaskProviders.mockReset();
  updateTaskProvider.mockReset();
  fetchGitProviders.mockReset();
  // Nothing discoverable by default — the panel keeps its paste form.
  fetchCredentialSources.mockReset();
  fetchCredentialSources.mockResolvedValue({ ok: true, body: { sources: [] } });
  updateGitProvider.mockReset();
  toast.show.mockReset();
  recheckConnections.mockReset();
  testTrackerConnection.mockReset();
});

describe('ProviderCredentialsPanel — Task provider (includeActive)', () => {
  test('loads, renders fields + source badges, masks secret fields', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: TASK_BODY });
    render(<TaskProviderSettingsPanel />);

    await waitFor(() => expect(screen.getByText('Base URL')).toBeInTheDocument());
    expect(screen.getByText('Token')).toBeInTheDocument();
    expect(screen.getByText('Active provider')).toBeInTheDocument();
    // sourceLabel: kato_settings -> "saved", env -> "live"
    expect(screen.getByText('saved')).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
    // isSecretKey('token') -> password input
    expect(screen.getByDisplayValue('secret-tok')).toHaveAttribute('type', 'password');
    expect(screen.getByDisplayValue('https://yt.example')).toHaveAttribute('type', 'text');
  });

  test('edit enables Save; Save sends {active, provider, fields} and puts it to work through the one apply', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: TASK_BODY });
    updateTaskProvider.mockResolvedValue({ ok: true, body: {} });
    recheckConnections.mockResolvedValue({ ok: true, body: { status: 'restarting', ok: true, working: [], error: '' } });
    render(<TaskProviderSettingsPanel />);
    await waitFor(() => expect(screen.getByText('Base URL')).toBeInTheDocument());

    const saveBtn = screen.getByRole('button', { name: /^Save$/i });
    expect(saveBtn).toBeDisabled();

    const baseUrlInput = screen.getByDisplayValue('https://yt.example');
    // The panel re-seeds ``draft`` from the server values in a
    // [selected, meta.providers] effect that can still be PENDING right
    // after the load settles; under parallel load it may run *after*
    // this edit and wipe it (isDirty flips back to false → Save stays
    // disabled). Re-fire the change inside waitFor so the edit re-applies
    // until the re-seed has settled and the dirty diff sticks.
    await waitFor(() => {
      fireEvent.change(baseUrlInput, { target: { value: 'https://new.example' } });
      expect(saveBtn).not.toBeDisabled();
    });

    fireEvent.click(saveBtn);
    await waitFor(() => expect(updateTaskProvider).toHaveBeenCalledTimes(1));
    const arg = updateTaskProvider.mock.calls[0][0];
    expect(arg).toMatchObject({ active: 'youtrack', provider: 'youtrack' });
    expect(arg.fields.base_url).toBe('https://new.example');
    await waitFor(() => expect(recheckConnections).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Kato is restarting to use it/)).toBeInTheDocument();
  });
});

describe('ProviderCredentialsPanel — order, defaults, workflow states', () => {
  const JIRA_BODY = {
    active: 'jira',
    supported: ['jira'],
    providers: {
      jira: {
        // JSON sorts keys: the fields arrive alphabetical, the order says how to show them.
        order: ['JIRA_API_BASE_URL', 'JIRA_EMAIL', 'JIRA_API_TOKEN', 'JIRA_DONE_STATE'],
        fields: {
          JIRA_API_BASE_URL: { value: 'https://x.atlassian.net', source: 'kato_settings', default: '' },
          JIRA_API_TOKEN: { value: '', source: 'unset', default: '' },
          JIRA_DONE_STATE: { value: '', source: 'unset', default: 'Done' },
          JIRA_EMAIL: { value: 'me@x.com', source: 'kato_settings', default: '' },
        },
      },
    },
    settings_file_path: '~/.kato/settings.json',
  };

  test('fields in the server order, the workflow states folded away showing their defaults', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: JIRA_BODY });
    render(<TaskProviderSettingsPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('me@x.com')).toBeInTheDocument());
    const main = document.querySelector('.settings-drawer-fields');
    const names = [...main.querySelectorAll('.settings-drawer-field-name')].map((node) => node.textContent);
    expect(names).toHaveLength(3);
    expect(names[1]).toMatch(/email/i);  // Email before the token, not alphabetical
    const workflow = document.querySelector('details.settings-drawer-advanced');
    expect(workflow).not.toHaveAttribute('open');
    expect(workflow.querySelector('summary')).toHaveTextContent('Workflow states');
    expect(workflow.querySelector('input')).toHaveAttribute('placeholder', 'Done');  // the real default
    // Test connection is at the top: before every field.
    const test = document.querySelector('.connection-test');
    expect(test.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('ProviderCredentialsPanel — Test connection (Task provider)', () => {
  const testButton = () => screen.getByRole('button', { name: 'Test connection' });

  test('tests what the form holds, before Save, and says it connected', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: TASK_BODY });
    testTrackerConnection.mockResolvedValue({ ok: true, body: { ok: true, platform: 'youtrack', error: '' } });
    render(<TaskProviderSettingsPanel />);
    await waitFor(() => expect(screen.getByText('Base URL')).toBeInTheDocument());
    const baseUrlInput = screen.getByDisplayValue('https://yt.example');
    await waitFor(() => {
      fireEvent.change(baseUrlInput, { target: { value: 'https://typed.example' } });
      expect(screen.getByRole('button', { name: /^Save$/i })).not.toBeDisabled();
    });
    fireEvent.click(testButton());
    expect(await screen.findByText('Connected — kato reached YouTrack.')).toBeInTheDocument();
    // Unsaved: it says to Save — kato is not asked to apply what is not saved.
    expect(screen.getByText('Connected — press Save to put it to work in kato.')).toBeInTheDocument();
    expect(recheckConnections).not.toHaveBeenCalled();
    expect(testTrackerConnection).toHaveBeenCalledWith({
      platform: 'youtrack', fields: { base_url: 'https://typed.example', token: 'secret-tok' },
    });
    expect(updateTaskProvider).not.toHaveBeenCalled();  // nothing saved by a test
  });

  test('a good test of the saved values: kato checks again at once, and says what it found', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: TASK_BODY });
    testTrackerConnection.mockResolvedValue({ ok: true, body: { ok: true } });
    const answers = [
      [{ ok: true, body: { status: 'checked', ok: true } }, 'Kato checked it too — it gets through.'],
      [{ ok: true, body: { status: 'checked', ok: false, error: '401' } }, "Kato still can't get through: 401"],
      [{ ok: true, body: { status: 'restart_needed', ok: false, working: ['UNA-1'] } }, 'agents are working now (UNA-1)'],
      [{ ok: false, error: 'offline' }, 'Kato could not be asked: offline'],
    ];
    render(<TaskProviderSettingsPanel />);
    await waitFor(() => expect(screen.getByText('Base URL')).toBeInTheDocument());
    for (const [answer, said] of answers) {
      recheckConnections.mockResolvedValueOnce(answer);
      fireEvent.click(testButton());
      expect(await screen.findByText(new RegExp(said.replace(/[()]/g, '\\$&')))).toBeInTheDocument();
    }
    expect(recheckConnections).toHaveBeenCalledTimes(answers.length);
  });

  test('a failure shows the provider\'s own words, whole; a request that throws says why', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: TASK_BODY });
    testTrackerConnection.mockResolvedValueOnce({ ok: true, body: {
      ok: false, platform: 'youtrack', error: 'HTTP 401 Unauthorized\nthe token was refused',
    } });
    render(<TaskProviderSettingsPanel />);
    await waitFor(() => expect(screen.getByText('Base URL')).toBeInTheDocument());
    fireEvent.click(testButton());
    const failed = await screen.findByRole('alert');
    expect(failed).toHaveTextContent('Could not connect to YouTrack:');
    expect(failed.querySelector('pre').textContent).toBe('HTTP 401 Unauthorized\nthe token was refused');

    testTrackerConnection.mockResolvedValueOnce({ ok: false, error: 'Failed to fetch' });
    fireEvent.click(testButton());
    await waitFor(() => expect(screen.getByRole('alert').querySelector('pre').textContent).toBe('Failed to fetch'));

    testTrackerConnection.mockRejectedValueOnce(new Error('network down'));
    fireEvent.click(testButton());
    await waitFor(() => expect(screen.getByRole('alert').querySelector('pre').textContent).toBe('network down'));

    testTrackerConnection.mockResolvedValueOnce({ ok: false });
    fireEvent.click(testButton());
    await waitFor(() => expect(screen.getByRole('alert').querySelector('pre').textContent).toBe('kato could not reach YouTrack.'));
  });

  test('a scoped Jira token: one click fills in the gateway address kato named', async () => {
    const jiraBody = {
      active: 'jira', supported: ['jira'],
      providers: { jira: { order: ['JIRA_API_BASE_URL'], fields: {
        JIRA_API_BASE_URL: { value: 'https://x.atlassian.net', source: 'kato_settings', default: '' },
      } } },
      settings_file_path: '~/.kato/settings.json',
    };
    const gateway = 'https://api.atlassian.com/ex/jira/07cc605d-3220-485b-8a81-3b3f4742f64d';
    fetchTaskProviders.mockResolvedValue({ ok: true, body: jiraBody });
    testTrackerConnection.mockResolvedValue({ ok: true, body: {
      ok: false, error: `Jira refused this token … Set the API base URL to ${gateway} — the same email and token then work.`,
    } });
    render(<TaskProviderSettingsPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('https://x.atlassian.net')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use this address' }));
    expect(screen.getByDisplayValue(gateway)).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('press Test connection again, then Save');
    expect(screen.getByRole('button', { name: /^Save$/i })).not.toBeDisabled();
  });

  test('while it runs the button says so', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: TASK_BODY });
    let answer;
    testTrackerConnection.mockImplementation(() => new Promise((done) => { answer = done; }));
    render(<TaskProviderSettingsPanel />);
    await waitFor(() => expect(screen.getByText('Base URL')).toBeInTheDocument());
    fireEvent.click(testButton());
    expect(screen.getByRole('button', { name: 'Testing YouTrack…' })).toBeDisabled();
    answer({ ok: true, body: { ok: true } });
    expect(await screen.findByRole('status')).toBeInTheDocument();
  });
});

describe('ProviderCredentialsPanel — Test connection (Git provider)', () => {
  test('asks the host with what the form holds and shows which repositories answered', async () => {
    fetchGitProviders.mockResolvedValue({ ok: true, body: GIT_BODY });
    testGitHostConnection.mockResolvedValue({ ok: true, body: {
      ok: true, host: 'bitbucket', error: '', message: 'Connected — Bitbucket answered for api, web.',
    } });
    render(<GitProvidersSettingsPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('me')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Connected — Bitbucket answered for api, web.');
    expect(testGitHostConnection).toHaveBeenCalledWith({
      provider: 'bitbucket', fields: { username: 'me', app_password: 'pw' },
    });
  });

  test('a refused repository is named with the host\'s own words', async () => {
    fetchGitProviders.mockResolvedValue({ ok: true, body: GIT_BODY });
    testGitHostConnection.mockResolvedValue({ ok: true, body: {
      ok: false, host: 'bitbucket', error: 'api: 401 Unauthorized', message: '',
    } });
    render(<GitProvidersSettingsPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('me')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    const failed = await screen.findByRole('alert');
    expect(failed).toHaveTextContent('Could not connect to Bitbucket:');
    expect(failed.querySelector('pre').textContent).toBe('api: 401 Unauthorized');
  });
});

describe('ProviderCredentialsPanel — Git provider (no active)', () => {
  test('uses "Host" label and Save sends {provider, fields} WITHOUT active', async () => {
    fetchGitProviders.mockResolvedValue({ ok: true, body: GIT_BODY });
    updateGitProvider.mockResolvedValue({ ok: true, body: {} });
    render(<GitProvidersSettingsPanel />);
    await waitFor(() => expect(screen.getByText('Username')).toBeInTheDocument());

    expect(screen.getByText('Host')).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('me'), { target: { value: 'newuser' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/i }));

    await waitFor(() => expect(updateGitProvider).toHaveBeenCalledTimes(1));
    const arg = updateGitProvider.mock.calls[0][0];
    expect(arg).not.toHaveProperty('active');
    expect(arg).toMatchObject({ provider: 'bitbucket' });
    expect(arg.fields.username).toBe('newuser');
  });

  // Report: "git repository always shows Bitbucket, though I set up Azure".
  test('opens on a host the operator set up, listed first and marked', async () => {
    fetchGitProviders.mockResolvedValue({
      ok: true,
      body: {
        supported: ['bitbucket', 'github', 'azure'],
        providers: {
          bitbucket: { set_up: false, fields: { BITBUCKET_USERNAME: { value: '', source: 'unset' } } },
          github: { set_up: false, fields: { GITHUB_OWNER: { value: '', source: 'unset' } } },
          azure: { set_up: true, fields: { AZURE_USERNAME: { value: 'bot@acme.com', source: 'kato_settings' } } },
        },
        settings_file_path: '~/.kato/settings.json',
      },
    });
    render(<GitProvidersSettingsPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('bot@acme.com')).toBeInTheDocument());
    const options = [...screen.getByRole('combobox').querySelectorAll('option')].map((o) => o.textContent);
    expect(options).toEqual(['Azure DevOps — set up', 'Bitbucket — not set up', 'GitHub — not set up']);
    expect(screen.getByRole('combobox')).toHaveValue('azure');
  });

  test('load error renders the error message', async () => {
    fetchGitProviders.mockResolvedValue({ ok: false, error: 'kaboom' });
    render(<GitProvidersSettingsPanel />);
    await waitFor(() => expect(screen.getByText('kaboom')).toBeInTheDocument());
  });
});
