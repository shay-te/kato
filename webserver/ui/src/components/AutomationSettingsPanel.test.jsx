// The "Automation" tab: every switch for what kato does on its own in a task,
// in the order a task meets them. Switches come from four schema sections; one
// section's switches can sit under several headings, and a save posts only the
// switch that changed.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const fetchAllSettings = vi.fn();
const updateAllSettings = vi.fn();
vi.mock('../api.js', () => ({
  fetchAllSettings: (...args) => fetchAllSettings(...args),
  updateAllSettings: (...args) => updateAllSettings(...args),
  fetchOpenRouterModels: vi.fn(() => Promise.resolve([])),
  fetchClaudeModels: vi.fn(() => Promise.resolve([])),
}));

import { _resetAllSettingsStore } from '../stores/allSettingsStore.js';
import AutomationSettingsPanel from './AutomationSettingsPanel.jsx';

const sw = (key, label, value = 'true') => ({
  key, type: 'bool', label, help: `${label} help`, panel: 'automation', value,
});

function settingsBody() {
  return {
    ok: true,
    body: {
      settings_file_path: '/home/me/.kato/settings.json',
      sections: [
        { id: 'general', label: 'General', title: 'General', description: '', fields: [
          sw('KATO_AUTO_PUSH_ENABLED', 'Autonomous push + PR', 'false'),
          sw('KATO_RUN_TESTS', 'Run tests'),
          sw('KATO_STOP_CONTAINERS_ON_DONE', 'Stop the task\'s containers on Done'),
          sw('KATO_PUBLISH_FOLLOWS_PROJECT_CONVENTIONS', 'Commits/PRs follow conventions', 'false'),
          sw('KATO_TASK_COMMENTS_ENABLED', 'Read ticket comments', 'false'),
          sw('KATO_TASK_COMMENTS_REQUIRE_MENTION', 'Only @-mentioned comments'),
          sw('KATO_REVIEW_COMMENTS_ENABLED', 'Pull PR review comments'),
          sw('KATO_REVIEW_COMMENTS_REQUIRE_MENTION', 'Only @-mentioned PR comments'),
          { key: 'KATO_LOG_LEVEL', type: 'select', label: 'Log level', options: ['info'], value: 'info' },
        ] },
        { id: 'security_scanner', label: 'Security', title: 'Security', description: '', fields: [
          sw('KATO_SECURITY_SCANNER_ENABLED', 'Security scan before the agent starts'),
          { key: 'KATO_SECURITY_RUNNER_BANDIT', type: 'bool', label: 'Bandit', value: 'true' },
        ] },
        { id: 'release_notes', label: 'Release notes', title: 'Release notes', description: '', fields: [
          sw('KATO_RELEASE_NOTES_ENABLED', 'Keep release notes'),
        ] },
        { id: 'email_slack', label: 'Email', title: 'Email', description: '', fields: [
          sw('KATO_FAILURE_EMAIL_ENABLED', 'Failure email enabled', 'false'),
          sw('KATO_COMPLETION_EMAIL_ENABLED', 'Completion email enabled', 'false'),
        ] },
      ],
    },
  };
}

beforeEach(() => {
  _resetAllSettingsStore();
  fetchAllSettings.mockReset();
  fetchAllSettings.mockResolvedValue(settingsBody());
  updateAllSettings.mockReset();
  updateAllSettings.mockResolvedValue({
    ok: true, body: { ok: true, restart_required: false, message: 'Saved.' },
  });
});

async function renderTab() {
  const view = render(<AutomationSettingsPanel />);
  await screen.findByText('Run tests');
  await screen.findByText('Completion email enabled');
  return view;
}

function namesIn(container) {
  return Array.from(container.querySelectorAll('.settings-drawer-field-name')).map((el) => el.textContent);
}

describe('AutomationSettingsPanel', () => {
  test('every switch, grouped by when kato does it, and nothing else', async () => {
    const { container } = await renderTab();
    for (const heading of [
      'Before the agent starts', 'While the agent works',
      'When the work is finished', 'Comments it answers by itself',
    ]) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
    }
    expect(namesIn(container)).toEqual([
      'Security scan before the agent starts',
      'Run tests',
      'Autonomous push + PR', 'Commits/PRs follow conventions', 'Stop the task\'s containers on Done',
      'Keep release notes',
      'Completion email enabled', 'Failure email enabled',
      'Read ticket comments', 'Only @-mentioned comments',
      'Pull PR review comments', 'Only @-mentioned PR comments',
    ]);
    expect(screen.queryByText('Bandit')).toBeNull();
    expect(container.querySelector('select')).toBeNull();
  });

  test('switching tests off saves only that switch', async () => {
    const { container } = await renderTab();
    const runTests = Array.from(container.querySelectorAll('label'))
      .find((label) => label.textContent.includes('Run tests'))
      .querySelector('input[type="checkbox"]');
    expect(runTests.checked).toBe(true);

    fireEvent.click(runTests);
    const saves = screen.getAllByRole('button', { name: /^save 1 change$/i });
    expect(saves).toHaveLength(1);
    fireEvent.click(saves[0]);

    await waitFor(() => expect(updateAllSettings).toHaveBeenCalledTimes(1));
    expect(updateAllSettings).toHaveBeenCalledWith({ KATO_RUN_TESTS: 'false' });
  });
});
