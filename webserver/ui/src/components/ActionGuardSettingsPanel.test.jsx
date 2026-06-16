// Tests for the dedicated Action Guard settings panel: concrete posture per
// category, locked floor categories shown read-only, and Save posting only the
// changed keys to /api/all-settings.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchAllSettings: vi.fn(),
  updateAllSettings: vi.fn(),
  fetchActionGuardAudit: vi.fn(),
}));

import { fetchAllSettings, updateAllSettings, fetchActionGuardAudit } from '../api.js';
import ActionGuardSettingsPanel from './ActionGuardSettingsPanel.jsx';

const OPTS = ['block', 'ask', 'allow'];

function _section() {
  return {
    id: 'action_guard',
    label: 'Action Guard',
    title: 'Action Guard',
    description: 'desc',
    fields: [
      { key: 'KATO_ACTION_GUARD_ENABLED', type: 'bool', label: 'Action Guard enabled', value: 'true' },
      { key: 'KATO_ACTION_GUARD_CREDENTIAL_READ', type: 'select', label: 'Credential / secret reads', value: 'block', options: OPTS, help: 'h' },
      { key: 'KATO_ACTION_GUARD_DESTRUCTIVE_FS', type: 'select', label: 'Destructive filesystem', value: 'ask', options: OPTS, help: 'h' },
      { key: 'KATO_ACTION_GUARD_NETWORK_TOOL', type: 'select', label: 'Network / connector tools', value: 'block', options: OPTS, help: 'h' },
      { key: 'KATO_ACTION_GUARD_EXTERNAL_CAPABILITY', type: 'select', label: 'New / unrecognized capabilities', value: 'ask', options: OPTS, help: 'h' },
      { key: 'KATO_ACTION_GUARD_REMOTE_EXEC', type: 'select', label: 'Remote code execution', value: 'block', options: OPTS, help: 'h' },
      { key: 'KATO_ACTION_GUARD_SANDBOX_ESCAPE', type: 'select', label: 'Sandbox escape', value: 'block', options: OPTS, help: 'h' },
    ],
  };
}

beforeEach(() => {
  fetchAllSettings.mockReset();
  updateAllSettings.mockReset();
  fetchAllSettings.mockResolvedValue({
    ok: true,
    body: { sections: [_section()], settings_file_path: '~/.kato/settings.json' },
  });
  updateAllSettings.mockResolvedValue({ ok: true, body: {} });
  fetchActionGuardAudit.mockResolvedValue({
    ok: true, body: { entries: [], ok: true, first_bad_index: -1 },
  });
});

describe('ActionGuardSettingsPanel', () => {
  test('renders a concrete posture select for each tunable category', async () => {
    const { container } = render(<ActionGuardSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByText('Credential / secret reads')).toBeInTheDocument();
    });
    const cred = container.querySelector(
      '[data-field-key="KATO_ACTION_GUARD_CREDENTIAL_READ"] select',
    );
    expect(cred).toBeInTheDocument();
    expect(cred.value).toBe('block');               // concrete, never blank
    const dest = container.querySelector(
      '[data-field-key="KATO_ACTION_GUARD_DESTRUCTIVE_FS"] select',
    );
    expect(dest.value).toBe('ask');
  });

  test('locked floor categories are read-only (Always block, no select)', async () => {
    const { container } = render(<ActionGuardSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByText('Remote code execution')).toBeInTheDocument();
    });
    const rce = container.querySelector('[data-field-key="KATO_ACTION_GUARD_REMOTE_EXEC"]');
    expect(rce.querySelector('select')).toBeNull();
    expect(rce).toHaveTextContent(/Always block/i);
    const esc = container.querySelector('[data-field-key="KATO_ACTION_GUARD_SANDBOX_ESCAPE"]');
    expect(esc.querySelector('select')).toBeNull();
  });

  test('changing a posture and saving posts only the changed key', async () => {
    const { container } = render(<ActionGuardSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByText('Credential / secret reads')).toBeInTheDocument();
    });
    const cred = container.querySelector(
      '[data-field-key="KATO_ACTION_GUARD_CREDENTIAL_READ"] select',
    );
    fireEvent.change(cred, { target: { value: 'ask' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(updateAllSettings).toHaveBeenCalled());
    expect(updateAllSettings).toHaveBeenCalledWith({
      KATO_ACTION_GUARD_CREDENTIAL_READ: 'ask',
    });
  });

  test('network_tool and external_capability are configurable here', async () => {
    const { container } = render(<ActionGuardSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByText('Network / connector tools')).toBeInTheDocument();
    });
    expect(
      container.querySelector('[data-field-key="KATO_ACTION_GUARD_NETWORK_TOOL"] select').value,
    ).toBe('block');
    expect(
      container.querySelector('[data-field-key="KATO_ACTION_GUARD_EXTERNAL_CAPABILITY"] select').value,
    ).toBe('ask');
  });

  test('renders recent decisions from the audit log', async () => {
    fetchActionGuardAudit.mockResolvedValue({
      ok: true,
      body: {
        ok: true,
        first_bad_index: -1,
        entries: [
          {
            timestamp: '2026-06-16T07:30:00+00:00', task_id: 'UNA-1',
            category: 'credential_read', decision: 'block',
            command_preview: 'cat ~/.ssh/id_***', answered_by: 'shay.te@gmail.com',
          },
        ],
      },
    });
    render(<ActionGuardSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByText('cat ~/.ssh/id_***')).toBeInTheDocument();
    });
    expect(screen.getByText('Recent decisions')).toBeInTheDocument();
  });

  test('flags a tampered audit log', async () => {
    fetchActionGuardAudit.mockResolvedValue({
      ok: true,
      body: { ok: false, first_bad_index: 3, entries: [] },
    });
    render(<ActionGuardSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByText(/integrity check FAILED/i)).toBeInTheDocument();
    });
  });

  test('disabling the master switch disables the posture selects', async () => {
    const { container } = render(<ActionGuardSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByLabelText('Action Guard enabled')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText('Action Guard enabled'));  // true → false
    const cred = container.querySelector(
      '[data-field-key="KATO_ACTION_GUARD_CREDENTIAL_READ"] select',
    );
    expect(cred).toBeDisabled();
  });
});
