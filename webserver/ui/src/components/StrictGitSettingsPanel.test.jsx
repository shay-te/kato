// The "Strict git" tab: one setting, explained. The switch is the schema field
// lifted out of General (``panel: 'strict-git'``), so turning it off asks in a
// popup first, and saving posts only that setting.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const KEY = 'KATO_AGENT_GIT_STRICT';
const fetchAllSettings = vi.fn();
const updateAllSettings = vi.fn();
vi.mock('../api.js', () => ({
  fetchAllSettings: (...args) => fetchAllSettings(...args),
  updateAllSettings: (...args) => updateAllSettings(...args),
  fetchOpenRouterModels: vi.fn(() => Promise.resolve([])),
  fetchClaudeModels: vi.fn(() => Promise.resolve([])),
}));

import { _resetAllSettingsStore } from '../stores/allSettingsStore.js';
import StrictGitSettingsPanel from './StrictGitSettingsPanel.jsx';

function settingsBody(value = 'true') {
  return {
    ok: true,
    body: {
      settings_file_path: '/home/me/.kato/settings.json',
      sections: [{
        id: 'general', label: 'General', title: 'General', description: '',
        fields: [
          {
            key: KEY, type: 'bool', label: 'Strict git', help: 'On by default.',
            panel: 'strict-git', value,
            confirm: {
              when: 'false', title: 'Turn strict git off?', lead: 'Read this.',
              effects: ['Only kato pushes.'], confirm_label: 'Turn strict git off',
            },
          },
          { key: 'KATO_LOG_LEVEL', type: 'select', label: 'Log level', options: ['info'], value: 'info' },
        ],
      }],
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
  const view = render(<StrictGitSettingsPanel />);
  await waitFor(() => expect(view.container.querySelector('input[type="checkbox"]')).not.toBeNull());
  return { ...view, checkbox: view.container.querySelector('input[type="checkbox"]') };
}

describe('StrictGitSettingsPanel', () => {
  test('it explains what strict git is and why it is recommended', async () => {
    await renderTab();
    for (const heading of [
      'What strict git is', 'Why it is recommended', 'When to turn it off',
      'What changes when it is off', 'What the agent never does, in either mode',
      'Claude, Codex, Grok and OpenCode',
    ]) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText(/On by default, and recommended/)).toBeInTheDocument();
    expect(screen.getByText(/Only kato\s+pushes, and only when you press Push or Done/)).toBeInTheDocument();
  });

  test('it shows only its own switch, on when unset', async () => {
    const { container, checkbox } = await renderTab();
    expect(screen.getByText('Strict git', { selector: '.settings-drawer-field-name' })).toBeInTheDocument();
    expect(checkbox.checked).toBe(true);
    expect(container.querySelector('select')).toBeNull();
  });

  test('turning it off asks first; confirming then saving posts only this setting', async () => {
    const { checkbox } = await renderTab();

    fireEvent.click(checkbox);
    expect(screen.getByText('Turn strict git off?')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^save/i })).toBeNull();

    fireEvent.click(screen.getByText('Turn strict git off'));
    fireEvent.click(screen.getByRole('button', { name: /^save 1 change$/i }));

    await waitFor(() => expect(updateAllSettings).toHaveBeenCalledTimes(1));
    expect(updateAllSettings).toHaveBeenCalledWith({ [KEY]: 'false' });
  });

  test('cancelling the popup leaves nothing to save', async () => {
    const { checkbox } = await renderTab();
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByText('Cancel'));
    expect(checkbox.checked).toBe(true);
    expect(screen.queryByRole('button', { name: /^save/i })).toBeNull();
  });
});
