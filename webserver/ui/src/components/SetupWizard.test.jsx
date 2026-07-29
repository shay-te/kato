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
  fetchAllSettings: vi.fn(),
  updateAllSettings: vi.fn(),
  fetchDirectoryListing: vi.fn(),
  fetchCredentialSources: vi.fn(),
}));

import {
  fetchTaskProviders,
  updateTaskProvider,
  fetchSettings,
  updateSettings,
  fetchAllSettings,
  updateAllSettings,
  fetchDirectoryListing,
  fetchCredentialSources,
} from '../api.js';
import SetupWizard from './SetupWizard.jsx';
import { humanizeFieldKey } from '../utils/fieldHelp.js';

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
  fetchAllSettings.mockReset();
  updateAllSettings.mockReset();
  // Default: nothing discoverable, so the wizard shows the paste form —
  // the behavior every pre-existing test was written against.
  fetchCredentialSources.mockReset();
  fetchCredentialSources.mockResolvedValue({ ok: true, body: { sources: [] } });
  fetchAllSettings.mockResolvedValue({ ok: true, body: { sections: [] } });
  updateAllSettings.mockResolvedValue({ ok: true, body: {} });
});

// Ticket details saved → the AI-agent pick step; choose Claude (no required
// fields) and save through to the repositories step.
async function pickClaudeAndGoToRepoStep() {
  await screen.findByText('Which AI agent does the work?');
  fireEvent.click(screen.getByRole('radio', { name: /Claude agent/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('Connect Claude agent');
  fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
  await screen.findByText('Where are your repositories?');
}

async function pickSystemAndGoToDetails(choice, heading) {
  render(
    <SetupWizard status={UNCONFIGURED} onRefreshStatus={vi.fn()} />,
  );
  await waitFor(() => {
    expect(fetchTaskProviders).toHaveBeenCalled();
  });
  fireEvent.click(screen.getByRole('radio', { name: choice }));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText(heading);
}

async function pickJiraAndGoToDetails() {
  await pickSystemAndGoToDetails(/Jira/, 'Connect Jira');
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

// The install feedback that produced this block: "I don't know where I get
// the API key from — this isn't something I usually do, and there is no
// SECURITY menu." So every credential step must say WHY the key is needed
// and link out to the provider's own instructions.
describe('SetupWizard — credential guidance', () => {
  test('the ticket step says why the key is needed and how to get it', async () => {
    await pickJiraAndGoToDetails();
    expect(screen.getByText('Why kato needs this')).toBeInTheDocument();
    expect(
      screen.getByText(/Where do I get a Jira API token\?/),
    ).toBeInTheDocument();
    // Steps are visible on first run (details open by default) — the menu
    // path appears both as the summary line and inside the steps.
    expect(screen.getAllByText(/Create and manage API tokens/).length)
      .toBeGreaterThan(0);
    // And the provider's own documentation is one click away.
    const docs = screen.getByRole('link', { name: /Atlassian: manage API tokens/ });
    expect(docs).toHaveAttribute(
      'href',
      'https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/',
    );
    expect(docs).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link', { name: /Create an Atlassian API token/ }))
      .toHaveAttribute('href', 'https://id.atlassian.com/manage-profile/security/api-tokens');
  });

  test('GitHub sends the operator to Developer settings, not a Security menu', async () => {
    await pickSystemAndGoToDetails(/GitHub Issues/, 'Connect GitHub Issues');
    expect(screen.getAllByText(/Developer settings/).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: /Create a token on GitHub/ }))
      .toHaveAttribute('href', 'https://github.com/settings/personal-access-tokens/new');
  });

  test('the agent step explains the Claude CLI login (nothing to paste)', async () => {
    await pickJiraAndGoToDetails();
    fillAllJiraFields(document.body);
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await screen.findByText('Which AI agent does the work?');
    fireEvent.click(screen.getByRole('radio', { name: /Claude agent/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Connect Claude agent');
    expect(screen.getByText(/Nothing to paste here/)).toBeInTheDocument();
    // No "stored in settings.json" claim for a credential kato never holds.
    expect(screen.queryByText(/Stored on this machine/)).toBeNull();
    expect(screen.getByRole('link', { name: /Claude Code: install & sign in/ }))
      .toHaveAttribute('href', 'https://docs.claude.com/en/docs/claude-code/overview');
  });
});

// Follow-up feedback on the same screen: "api key is prehistoric." So when a
// login already exists on the machine, the wizard must OFFER it and drop the
// paste requirement entirely — while keeping paste as a first-class choice.
describe('SetupWizard — connect without pasting a token', () => {
  const CLI_SOURCE = {
    id: 'cli',
    label: 'gh CLI login',
    account: 'octocat',
    detail: 'Signed in as octocat',
  };

  async function goToGitHubWithDiscoveredLogin() {
    fetchCredentialSources.mockResolvedValue({
      ok: true, body: { provider: 'github', sources: [CLI_SOURCE] },
    });
    await pickSystemAndGoToDetails(/GitHub Issues/, 'Connect GitHub Issues');
    await screen.findByRole('radio', { name: /gh CLI login/ });
  }

  test('offers the existing login and hides the token field entirely', async () => {
    await goToGitHubWithDiscoveredLogin();
    expect(screen.getByText('octocat')).toBeInTheDocument();
    // No token input, and no "how to create a token" steps to wade through.
    expect(screen.queryByText('API token')).toBeNull();
    expect(screen.queryByText('Why kato needs this')).toBeNull();
  });

  test('saves the SOURCE, never a token, and needs no paste to continue', async () => {
    await goToGitHubWithDiscoveredLogin();
    fillAllJiraFields(document.body);   // the 4 remaining non-token fields
    const save = screen.getByRole('button', { name: 'Save & continue' });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    await screen.findByText('Which AI agent does the work?');
    const payload = updateTaskProvider.mock.calls[0][0];
    expect(payload.fields.GITHUB_API_TOKEN_SOURCE).toBe('cli');
    expect(payload.fields.GITHUB_API_TOKEN).toBeUndefined();
  });

  test('"paste a token instead" brings back the field and the steps', async () => {
    await goToGitHubWithDiscoveredLogin();
    fireEvent.click(screen.getByRole('radio', { name: /Paste a token instead/ }));
    expect(screen.getByText('API token')).toBeInTheDocument();
    expect(screen.getByText('Why kato needs this')).toBeInTheDocument();
    // ...and the token is required again before the step can be saved.
    expect(screen.getByRole('button', { name: 'Save & continue' })).toBeDisabled();
  });

  test('nothing discoverable → the form is exactly as it was', async () => {
    fetchCredentialSources.mockResolvedValue({ ok: true, body: { sources: [] } });
    await pickSystemAndGoToDetails(/GitHub Issues/, 'Connect GitHub Issues');
    expect(screen.queryByRole('radio', { name: /CLI login/ })).toBeNull();
    expect(screen.getByText('API token')).toBeInTheDocument();
    expect(screen.getByText('Why kato needs this')).toBeInTheDocument();
  });

  test('a failed probe never blocks setup', async () => {
    fetchCredentialSources.mockRejectedValue(new Error('gh exploded'));
    await pickSystemAndGoToDetails(/GitHub Issues/, 'Connect GitHub Issues');
    expect(screen.getByText('API token')).toBeInTheDocument();
  });
});

describe('SetupWizard step 2 — ticket system details', () => {
  test('shows the required fields with friendly labels, placeholders and info icons', async () => {
    await pickJiraAndGoToDetails();
    expect(screen.getByText('API base URL')).toBeInTheDocument();
    expect(screen.getByText('API token')).toBeInTheDocument();
    // The raw env key is NOT printed next to labels anymore...
    expect(screen.queryByText('JIRA_API_TOKEN')).toBeNull();
    // ...it lives inside each field's ⓘ info tooltip.
    const icons = screen.getAllByRole('img', { name: 'Field info' });
    expect(icons.length).toBe(5);
    fireEvent.mouseEnter(icons[1]); // API token
    expect(document.body.textContent).toContain('Environment variable: JIRA_API_TOKEN');
    fireEvent.mouseLeave(icons[1]);
    // Inputs carry example-value placeholders.
    const baseUrl = [...document.querySelectorAll('.setup-wizard-input')][0];
    expect(baseUrl.placeholder).toBe('https://your-domain.atlassian.net');
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

    await screen.findByText('Which AI agent does the work?');
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

  test('asks the workflow-state fields too, as optional, and saves them', async () => {
    // The server's field catalog for the platform (the backend whitelist)
    // drives step 2 — connection fields required, workflow fields optional.
    // Works for every platform since the catalog comes from the server.
    fetchTaskProviders.mockResolvedValue(providersBody({
      JIRA_API_BASE_URL: { value: '', source: 'unset' },
      JIRA_API_TOKEN: { value: '', source: 'unset' },
      JIRA_EMAIL: { value: '', source: 'unset' },
      JIRA_PROJECT: { value: '', source: 'unset' },
      JIRA_ASSIGNEE: { value: '', source: 'unset' },
      JIRA_PROGRESS_STATE_FIELD: { value: '', source: 'unset' },
      JIRA_PROGRESS_STATE: { value: '', source: 'unset' },
      JIRA_REVIEW_STATE_FIELD: { value: '', source: 'unset' },
      JIRA_REVIEW_STATE: { value: '', source: 'unset' },
      JIRA_ISSUE_STATES: { value: '', source: 'unset' },
    }));
    await pickJiraAndGoToDetails();

    // The workflow fields are rendered (friendly labels) and visibly
    // optional — with NO "(optional)" duplicated in the placeholder.
    expect(screen.getByText('Review state')).toBeInTheDocument();
    expect(screen.getByText('Issue states')).toBeInTheDocument();
    expect(screen.getAllByText('optional').length).toBe(5);
    const allInputs = [...document.querySelectorAll('.setup-wizard-input')];
    expect(allInputs[8].placeholder).toBe('To Verify');   // example, not "(optional…)"
    expect(allInputs[9].placeholder).toBe('Open,To Do');

    // Filling ONLY the 5 required fields enables save (workflow blank).
    const inputs = [...document.querySelectorAll('.setup-wizard-input')];
    expect(inputs.length).toBe(10);
    for (const input of inputs.slice(0, 5)) {
      fireEvent.change(input, { target: { value: 'req-value' } });
    }
    const save = screen.getByRole('button', { name: 'Save & continue' });
    expect(save).not.toBeDisabled();

    // A typed optional value is included in the payload...
    const reviewStateInput = inputs[8]; // JIRA_REVIEW_STATE
    fireEvent.change(reviewStateInput, { target: { value: 'To Verify' } });
    fireEvent.click(save);
    await screen.findByText('Which AI agent does the work?');
    const payload = updateTaskProvider.mock.calls[0][0];
    expect(payload.fields.JIRA_REVIEW_STATE).toBe('To Verify');
    // ...but untouched optional blanks are NOT sent (defaults apply).
    expect(payload.fields).not.toHaveProperty('JIRA_ISSUE_STATES');
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

describe('SetupWizard AI agent steps — every boot-mandatory key is asked here', () => {
  async function goToAgentStep() {
    await pickJiraAndGoToDetails();
    fillAllJiraFields(document.body);
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await screen.findByText('Which AI agent does the work?');
  }

  function agentInputs() {
    return [...document.querySelectorAll('.setup-wizard-input')];
  }

  test('offers Claude, OpenHands and OpenRouter; Codex is visibly not available', async () => {
    await goToAgentStep();
    expect(screen.getByRole('radio', { name: /Claude agent/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /^OpenHands/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /OpenRouter/ })).toBeInTheDocument();
    const codex = screen.getByRole('radio', { name: /Codex agent/ });
    expect(codex).toBeDisabled();
    // Nothing picked yet → Next is gated.
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  test('Claude needs no required fields and saves KATO_AGENT_BACKEND=claude', async () => {
    await goToAgentStep();
    fireEvent.click(screen.getByRole('radio', { name: /Claude agent/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Connect Claude agent');
    // The CLI note explains the one non-env prerequisite (the credential
    // guide below it repeats the command, hence getAllByText).
    expect(screen.getAllByText(/claude login/).length).toBeGreaterThan(0);
    const save = screen.getByRole('button', { name: 'Save & continue' });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    await screen.findByText('Where are your repositories?');
    // updateAllSettings receives the FLAT map (it wraps {updates: …} itself
    // — passing a pre-wrapped object was a real save-breaking bug).
    const updates = updateAllSettings.mock.calls[0][0];
    expect(updates.KATO_AGENT_BACKEND).toBe('claude');
    expect(updates).not.toHaveProperty('updates');
  });

  test('OpenHands gate mirrors the validator: a plain model requires the LLM API key', async () => {
    await goToAgentStep();
    fireEvent.click(screen.getByRole('radio', { name: /^OpenHands/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Connect OpenHands');

    // keys order: BASE_URL, API_KEY, OH_SECRET_KEY, LLM_MODEL, LLM_API_KEY, LLM_BASE_URL
    const inputs = agentInputs();
    fireEvent.change(inputs[0], { target: { value: 'http://localhost:3000' } });
    fireEvent.change(inputs[1], { target: { value: 'oh-key' } });
    fireEvent.change(inputs[2], { target: { value: 'oh-secret' } });
    fireEvent.change(inputs[3], { target: { value: 'gpt-4' } });
    // All 4 statically-required fields are filled, but a non-Bedrock model
    // ALSO requires the LLM API key — Finish must never be the place this
    // is discovered.
    expect(screen.getByRole('button', { name: 'Save & continue' })).toBeDisabled();
    fireEvent.change(inputs[4], { target: { value: 'llm-key' } });
    expect(screen.getByRole('button', { name: 'Save & continue' })).not.toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await screen.findByText('Where are your repositories?');
    const updates = updateAllSettings.mock.calls[0][0];
    expect(updates.KATO_AGENT_BACKEND).toBe('openhands');
    expect(updates.OPENHANDS_LLM_API_KEY).toBe('llm-key');
  });

  test('a bedrock model swaps the LLM key for AWS credentials', async () => {
    await goToAgentStep();
    fireEvent.click(screen.getByRole('radio', { name: /^OpenHands/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Connect OpenHands');

    const inputs = agentInputs();
    fireEvent.change(inputs[0], { target: { value: 'http://localhost:3000' } });
    fireEvent.change(inputs[1], { target: { value: 'oh-key' } });
    fireEvent.change(inputs[2], { target: { value: 'oh-secret' } });
    fireEvent.change(inputs[3], { target: { value: 'bedrock/qwen.qwen3' } });

    // The AWS fields appeared, and the gate wants bearer OR the trio.
    await screen.findByText('AWS bearer token bedrock');
    expect(screen.getByRole('button', { name: 'Save & continue' })).toBeDisabled();
    const bearer = agentInputs()[6]; // after the 6 openhands fields
    fireEvent.change(bearer, { target: { value: 'bedrock-bearer' } });
    expect(screen.getByRole('button', { name: 'Save & continue' })).not.toBeDisabled();
  });

  test('OpenRouter pre-fills the base URL and requires the LLM key', async () => {
    await goToAgentStep();
    fireEvent.click(screen.getByRole('radio', { name: /OpenRouter/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Connect OpenRouter (via OpenHands)');

    // required order: BASE_URL, API_KEY, OH_SECRET, MODEL, LLM_API_KEY, LLM_BASE_URL
    const inputs = agentInputs();
    expect(inputs[5].value).toBe('https://openrouter.ai/api/v1');
    fireEvent.change(inputs[0], { target: { value: 'http://localhost:3000' } });
    fireEvent.change(inputs[1], { target: { value: 'oh-key' } });
    fireEvent.change(inputs[2], { target: { value: 'oh-secret' } });
    fireEvent.change(inputs[3], { target: { value: 'openrouter/openai/gpt-4o' } });
    expect(screen.getByRole('button', { name: 'Save & continue' })).toBeDisabled();
    fireEvent.change(inputs[4], { target: { value: 'or-key' } });
    expect(screen.getByRole('button', { name: 'Save & continue' })).not.toBeDisabled();
  });
});

describe('SetupWizard step 5 — repositories folder', () => {
  async function goToRepoStep() {
    await pickJiraAndGoToDetails();
    fillAllJiraFields(document.body);
    fireEvent.click(screen.getByRole('button', { name: 'Save & continue' }));
    await pickClaudeAndGoToRepoStep();
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

  test('Browse… opens the folder picker and picking fills the input', async () => {
    fetchDirectoryListing.mockResolvedValue({
      path: '/Users/dev/Projects',
      parent: '/Users/dev',
      home: '/Users/dev',
      dirs: [{ name: 'kato', path: '/Users/dev/Projects/kato' }],
    });
    await goToRepoStep();
    fireEvent.click(screen.getByRole('button', { name: 'Browse…' }));

    await screen.findByText('📁 kato');
    fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));

    // The picked path landed in the input and the picker closed.
    const input = document.querySelector('.setup-wizard-input');
    expect(input.value).toBe('/Users/dev/Projects');
    expect(screen.queryByText('📁 kato')).toBeNull();
    expect(screen.getByRole('button', { name: 'Save & continue' })).not.toBeDisabled();
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
    await pickClaudeAndGoToRepoStep();
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
    await pickClaudeAndGoToRepoStep();
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
      JIRA_API_BASE_URL: { value: 'https://server.example', source: 'kato_settings' },
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
    await pickClaudeAndGoToRepoStep();
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
