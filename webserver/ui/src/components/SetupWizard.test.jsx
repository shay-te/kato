// Tests for SetupWizard — the first-comer flow, one action per step:
// 1) pick the ticket system  2) enter that system's details
// 3) point kato at the repositories folder  4) finish.
// Drives the real component through the whole flow; only the api module is
// stubbed (the same seam the app uses to reach the server).

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskProviders: vi.fn(),
  updateTaskProvider: vi.fn(),
  fetchSettings: vi.fn(),
  updateSettings: vi.fn(),
}));

import {
  fetchTaskProviders,
  updateTaskProvider,
  fetchSettings,
  updateSettings,
} from '../api.js';
import SetupWizard, { humanizeFieldKey } from './SetupWizard.jsx';

function providersBody(fields = {}) {
  return {
    ok: true,
    body: {
      active: 'youtrack',
      supported: ['youtrack', 'jira', 'github', 'gitlab', 'bitbucket'],
      providers: {
        jira: { fields },
        youtrack: { fields: {} },
      },
    },
  };
}

const UNCONFIGURED = { setup_mode: true, needs_config: true, missing: [] };

beforeEach(() => {
  fetchTaskProviders.mockReset();
  updateTaskProvider.mockReset();
  fetchSettings.mockReset();
  updateSettings.mockReset();
  fetchTaskProviders.mockResolvedValue(providersBody());
  updateTaskProvider.mockResolvedValue({ ok: true, body: {} });
  fetchSettings.mockResolvedValue({
    ok: true,
    body: { repository_root_path: { value: '' } },
  });
  updateSettings.mockResolvedValue({ ok: true, body: {} });
});

async function pickJiraAndGoToDetails() {
  render(
    <SetupWizard status={UNCONFIGURED} onRefreshStatus={vi.fn()} />,
  );
  await waitFor(() => {
    expect(fetchTaskProviders).toHaveBeenCalled();
  });
  fireEvent.click(screen.getByRole('radio', { name: /Jira/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('Connect Jira');
}

function fillAllJiraFields(container) {
  for (const input of container.querySelectorAll('.setup-wizard-input')) {
    fireEvent.change(input, { target: { value: 'some-value' } });
  }
}

describe('SetupWizard step 1 — pick the ticket system', () => {
  test('lists every supported system as a single-choice option', async () => {
    render(
      <SetupWizard status={UNCONFIGURED} onRefreshStatus={vi.fn()} />,
    );
    for (const label of ['YouTrack', 'Jira', 'GitHub Issues', 'GitLab Issues', 'Bitbucket Issues']) {
      expect(screen.getByRole('radio', { name: new RegExp(label) })).toBeInTheDocument();
    }
    // Single action: the only way forward is Next.
    expect(screen.getByRole('button', { name: 'Next' })).toBeInTheDocument();
    expect(screen.queryByText('Connect Jira')).toBeNull();
  });

  test('picking a system marks it selected and Next advances', async () => {
    await pickJiraAndGoToDetails();
    // Step 2 heading is on screen; step 1's choices are gone.
    expect(screen.queryByRole('radio', { name: /YouTrack/ })).toBeNull();
  });
});

describe('SetupWizard step 2 — ticket system details', () => {
  test('shows the required fields for the chosen system with friendly labels', async () => {
    await pickJiraAndGoToDetails();
    expect(screen.getByText('API base URL')).toBeInTheDocument();
    expect(screen.getByText('API token')).toBeInTheDocument();
    // The raw env key stays visible so operators can map docs ↔ fields.
    expect(screen.getByText('JIRA_API_TOKEN')).toBeInTheDocument();
  });

  test('save stays disabled until every required field has a value', async () => {
    await pickJiraAndGoToDetails();
    const save = screen.getByRole('button', { name: 'Save & continue' });
    expect(save).toBeDisabled();
  });

  test('saving posts the platform + only the typed fields, then advances', async () => {
    await pickJiraAndGoToDetails();
    const { container } = { container: document.body };
    fillAllJiraFields(container);
    const save = screen.getByRole('button', { name: 'Save & continue' });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    await screen.findByText('Where are your repositories?');
    expect(updateTaskProvider).toHaveBeenCalledTimes(1);
    const payload = updateTaskProvider.mock.calls[0][0];
    expect(payload.active).toBe('jira');
    expect(payload.provider).toBe('jira');
    // Every posted field carries the operator's value — blanks are never
    // sent, so they can't clobber values already in settings.json.
    for (const value of Object.values(payload.fields)) {
      expect(value).toBe('some-value');
    }
    expect(Object.keys(payload.fields)).toContain('JIRA_API_TOKEN');
  });

  test('a failed save keeps the operator on the details step', async () => {
    updateTaskProvider.mockResolvedValue({ ok: false, body: { error: 'nope' } });
    await pickJiraAndGoToDetails();
    fillAllJiraFields(document.body);
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await waitFor(() => {
      expect(updateTaskProvider).toHaveBeenCalled();
    });
    expect(screen.getByText('Connect Jira')).toBeInTheDocument();
    expect(screen.queryByText('Where are your repositories?')).toBeNull();
  });

  test('secrets already on the server count as filled and render masked', async () => {
    fetchTaskProviders.mockResolvedValue(providersBody({
      JIRA_API_TOKEN: { value: 'srv-secret', source: 'kato_settings' },
    }));
    await pickJiraAndGoToDetails();
    // The token input is a password field, NOT pre-filled with the secret.
    const tokenInput = document.querySelector('input[type="password"]');
    expect(tokenInput).toBeInTheDocument();
    expect(tokenInput.value).toBe('');
    expect(tokenInput.placeholder).toMatch(/already set/);
    // With the token known server-side, filling just the others enables save.
    for (const input of document.querySelectorAll('.setup-wizard-input')) {
      if (input !== tokenInput) {
        fireEvent.change(input, { target: { value: 'v' } });
      }
    }
    expect(screen.getByRole('button', { name: 'Save & continue' })).not.toBeDisabled();
  });
});

describe('SetupWizard step 3 — repositories folder', () => {
  async function goToRepoStep() {
    await pickJiraAndGoToDetails();
    fillAllJiraFields(document.body);
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await screen.findByText('Where are your repositories?');
  }

  test('single input; save posts the path and advances to Finish', async () => {
    await goToRepoStep();
    const input = document.querySelector('.setup-wizard-input');
    fireEvent.change(input, { target: { value: '/Users/dev/Projects' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));

    await screen.findByText('Almost there');
    expect(updateSettings).toHaveBeenCalledWith(
      { repository_root_path: '/Users/dev/Projects' },
    );
  });

  test('save is disabled while the path is blank', async () => {
    await goToRepoStep();
    expect(screen.getByRole('button', { name: 'Save & continue' })).toBeDisabled();
  });
});

describe('SetupWizard step 4 — finish', () => {
  test('lists what is still missing and offers the full settings panel', async () => {
    const onOpenFullSettings = vi.fn();
    const status = {
      setup_mode: true,
      needs_config: true,
      missing: ['missing required OpenHands env var: OH_SECRET_KEY'],
    };
    render(
      <SetupWizard
        status={status}
        onRefreshStatus={vi.fn()}
        onOpenFullSettings={onOpenFullSettings}
      />,
    );
    await waitFor(() => {
      expect(fetchTaskProviders).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByRole('radio', { name: /YouTrack/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    // YouTrack fields (none required on the stub? — required list is
    // static) — walk via the header buttons instead: jump straight by
    // completing the flow isn't needed to probe the finish step; drive
    // the steps with real saves.
    // Fill + save details:
    for (const input of document.querySelectorAll('.setup-wizard-input')) {
      fireEvent.change(input, { target: { value: 'v' } });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await screen.findByText('Where are your repositories?');
    fireEvent.change(document.querySelector('.setup-wizard-input'), {
      target: { value: '/p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));

    await screen.findByText('Almost there');
    expect(
      screen.getByText('missing required OpenHands env var: OH_SECRET_KEY'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open full settings' }));
    expect(onOpenFullSettings).toHaveBeenCalled();
  });

  test('when the config is complete it says kato is starting itself', async () => {
    const status = { setup_mode: true, needs_config: false, missing: [] };
    render(
      <SetupWizard status={status} onRefreshStatus={vi.fn()} />,
    );
    await waitFor(() => {
      expect(fetchTaskProviders).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByRole('radio', { name: /YouTrack/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    for (const input of document.querySelectorAll('.setup-wizard-input')) {
      fireEvent.change(input, { target: { value: 'v' } });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await screen.findByText('Where are your repositories?');
    fireEvent.change(document.querySelector('.setup-wizard-input'), {
      target: { value: '/p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));

    await screen.findByText(/You're all set/);
    // Terminal-free apply: no restart instructions, kato starts in-process.
    expect(screen.getByText(/starting now/)).toBeInTheDocument();
    expect(screen.getByText(/No restart needed/)).toBeInTheDocument();
  });
});

describe('SetupWizard resilience', () => {
  test('a late providers response never wipes values the operator typed', async () => {
    // The mount-time fetch resolves AFTER the operator reached step 2 and
    // typed — the reseed must keep their work.
    let resolveProviders;
    fetchTaskProviders.mockReturnValue(new Promise((resolve) => {
      resolveProviders = resolve;
    }));
    render(
      <SetupWizard status={UNCONFIGURED} onRefreshStatus={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole('radio', { name: /Jira/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Connect Jira');
    const firstInput = document.querySelector('.setup-wizard-input');
    fireEvent.change(firstInput, { target: { value: 'typed-by-operator' } });

    // Now the slow fetch lands, carrying a server value for the same field.
    resolveProviders(providersBody({
      JIRA_API_BASE_URL: { value: 'https://server.example', source: 'env_file' },
    }));
    await waitFor(() => {
      expect(document.querySelector('.setup-wizard-input').value).toBe('typed-by-operator');
    });
  });

  test('finish step reports a failed start instead of claiming all set', async () => {
    const status = {
      setup_mode: true,
      needs_config: false,
      missing: [],
      setup_error: 'startup dependency validation failed: youtrack',
    };
    render(<SetupWizard status={status} onRefreshStatus={vi.fn()} />);
    await waitFor(() => {
      expect(fetchTaskProviders).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByRole('radio', { name: /YouTrack/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    for (const input of document.querySelectorAll('.setup-wizard-input')) {
      fireEvent.change(input, { target: { value: 'v' } });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await screen.findByText('Where are your repositories?');
    fireEvent.change(document.querySelector('.setup-wizard-input'), {
      target: { value: '/p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));

    await screen.findByText('Start failed');
    expect(screen.queryByText(/You're all set/)).toBeNull();
    expect(screen.getByText(/kato retries automatically/)).toBeInTheDocument();
  });
});

describe('humanizeFieldKey', () => {
  test('drops the platform prefix and prettifies acronyms', () => {
    expect(humanizeFieldKey('JIRA_API_BASE_URL', 'jira')).toBe('API base URL');
    expect(humanizeFieldKey('YOUTRACK_ASSIGNEE', 'youtrack')).toBe('Assignee');
    expect(humanizeFieldKey('BITBUCKET_REPO_SLUG', 'bitbucket')).toBe('Repo slug');
  });

  test('keys without the prefix still render readably', () => {
    expect(humanizeFieldKey('REPOSITORY_ROOT_PATH', 'jira')).toBe('Repository root path');
  });
});
