// Tests for SetupModeGate — the full-screen first-run gate. It renders ONLY
// when kato booted unconfigured (status.setup_mode) and hosts the wizard.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskProviders: vi.fn(),
  updateTaskProvider: vi.fn(),
  fetchSettings: vi.fn(),
  updateSettings: vi.fn(),
  fetchAllSettings: vi.fn(),
  updateAllSettings: vi.fn(),
  fetchDirectoryListing: vi.fn(),
}));

import { fetchTaskProviders, fetchSettings, fetchAllSettings } from '../api.js';
import SetupModeGate from './SetupModeGate.jsx';

beforeEach(() => {
  fetchTaskProviders.mockReset();
  fetchSettings.mockReset();
  fetchAllSettings.mockReset();
  fetchAllSettings.mockResolvedValue({ ok: true, body: { sections: [] } });
  fetchTaskProviders.mockResolvedValue({
    ok: true,
    body: { active: 'youtrack', providers: {}, supported: [] },
  });
  fetchSettings.mockResolvedValue({
    ok: true,
    body: { repository_root_path: { value: '' } },
  });
});

describe('SetupModeGate', () => {
  test('renders nothing while status has not loaded', () => {
    const { container } = render(
      <SetupModeGate status={null} onRefreshStatus={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing when kato booted configured', () => {
    const { container } = render(
      <SetupModeGate
        status={{ setup_mode: false, needs_config: false, missing: [] }}
        onRefreshStatus={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing after the live transition flips setup_mode off', () => {
    // needs_config may lag or differ — setup_mode alone decides the gate.
    const { container } = render(
      <SetupModeGate
        status={{ setup_mode: false, needs_config: true, missing: ['x'] }}
        onRefreshStatus={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  test('hidden prop keeps the gate mounted but invisible (settings drawer open)', () => {
    const { container } = render(
      <SetupModeGate
        status={{ setup_mode: true, needs_config: true, missing: [] }}
        hidden
        onRefreshStatus={vi.fn()}
      />,
    );
    // Still mounted (wizard state survives) but display:none via the class,
    // so the settings drawer (lower z-index) is reachable.
    const gate = container.querySelector('.setup-gate');
    expect(gate).toBeInTheDocument();
    expect(gate.className).toContain('is-hidden');
  });

  test('surfaces a failed start attempt on every step', () => {
    render(
      <SetupModeGate
        status={{
          setup_mode: true,
          needs_config: false,
          missing: [],
          setup_error: 'startup dependency validation failed: youtrack',
        }}
        onRefreshStatus={vi.fn()}
      />,
    );
    expect(screen.getByText('Start failed:')).toBeInTheDocument();
    expect(
      screen.getByText(/startup dependency validation failed: youtrack/),
    ).toBeInTheDocument();
    expect(screen.getByText(/retries automatically/)).toBeInTheDocument();
  });

  test('a kato that is set up but cannot connect is not greeted as new', () => {
    const error = 'startup dependency validation failed:\n- unable to validate youtrack: 401 Unauthorized';
    render(
      <SetupModeGate
        status={{
          setup_mode: true, needs_config: false, missing: [], setup_error: error,
          setup_kind: 'cannot_connect',
        }}
        onRefreshStatus={vi.fn()}
      />,
    );
    expect(screen.getByText('Kato can’t reach a service it needs')).toBeInTheDocument();
    expect(screen.queryByText('Welcome to Kato')).toBeNull();
    expect(screen.getByText('Could not connect:')).toBeInTheDocument();
    // The whole message, line breaks and all.
    expect(document.querySelector('.setup-gate-error pre').textContent).toBe(error);
  });

  test('shows the welcome gate with the wizard when booted unconfigured', () => {
    render(
      <SetupModeGate
        status={{ setup_mode: true, needs_config: true, missing: ['x'] }}
        onRefreshStatus={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Welcome to Kato')).toBeInTheDocument();
    expect(screen.getByText('Setup required')).toBeInTheDocument();
    // New users must see that configuration comes first — and that it's
    // all doable right here, no terminal.
    expect(screen.getByText(/isn't configured yet/)).toBeInTheDocument();
    expect(screen.getByText(/No terminal needed/)).toBeInTheDocument();
    // The wizard's first single-action step is live inside the gate.
    expect(screen.getByText('Where do your tickets live?')).toBeInTheDocument();
  });
});
