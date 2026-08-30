// Tests for the Chat settings tab — the "steer vs send immediately" choice
// and the "ultracode for new tasks" default, both backed by localStorage
// preference stores.

import { describe, test, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import {
  readSteerWhileWorking,
  writeSteerWhileWorking,
  _resetSteerWhileWorkingPref,
} from '../utils/composerSteerPref.js';
import {
  readUltracodeByDefault,
  writeUltracodeByDefault,
  _resetUltracodeDefaultPref,
} from '../utils/ultracodeDefaultPref.js';
import ChatSettingsPanel from './ChatSettingsPanel.jsx';

// The installed agent CLI's capability gates the ultracode setting — offering
// it against a CLI with no workflow support would promise something the
// keyword cannot deliver. Default: supported.
const { _agentVer, _versionCalls } = vi.hoisted(
  () => ({
    _agentVer: { value: { supports_workflows: true } },
    _versionCalls: { args: [] },
  }),
);
vi.mock('../hooks/useAgentVersion.js', () => ({
  useAgentVersion: (backend) => {
    _versionCalls.args.push(backend);
    return _agentVer.value;
  },
  resetAgentVersionCacheForTests: () => {},
}));

beforeEach(() => {
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetSteerWhileWorkingPref();
  _resetUltracodeDefaultPref();
  _agentVer.value = { supports_workflows: true };
});

describe('ChatSettingsPanel', () => {
  const steerRadio = () =>
    screen.getByRole('radio', { name: /Steer/i });
  const immediateRadio = () =>
    screen.getByRole('radio', { name: /Send immediately/i });

  test('defaults to "steer" selected', () => {
    render(<ChatSettingsPanel />);
    expect(steerRadio().checked).toBe(true);
    expect(immediateRadio().checked).toBe(false);
  });

  test('choosing "send immediately" persists false to the pref', () => {
    render(<ChatSettingsPanel />);
    fireEvent.click(immediateRadio());
    expect(immediateRadio().checked).toBe(true);
    expect(steerRadio().checked).toBe(false);
    expect(readSteerWhileWorking()).toBe(false);
  });

  test('switching back to "steer" persists true', () => {
    render(<ChatSettingsPanel />);
    fireEvent.click(immediateRadio());
    fireEvent.click(steerRadio());
    expect(steerRadio().checked).toBe(true);
    expect(readSteerWhileWorking()).toBe(true);
  });

  test('reflects an externally-stored preference on mount', () => {
    writeSteerWhileWorking(false);
    render(<ChatSettingsPanel />);
    expect(immediateRadio().checked).toBe(true);
  });
});

describe('ChatSettingsPanel — ultracode default', () => {
  const ultracodeCheckbox = () =>
    screen.getByRole('checkbox', { name: /ultracode for new tasks/i });

  test('is off by default — workflow mode is opt-in, not a surprise on the bill', () => {
    render(<ChatSettingsPanel />);
    expect(ultracodeCheckbox().checked).toBe(false);
  });

  test('checking it persists to the pref', () => {
    render(<ChatSettingsPanel />);
    fireEvent.click(ultracodeCheckbox());
    expect(ultracodeCheckbox().checked).toBe(true);
    expect(readUltracodeByDefault()).toBe(true);
  });

  test('reflects an already-stored preference on mount', () => {
    writeUltracodeByDefault(true);
    render(<ChatSettingsPanel />);
    expect(ultracodeCheckbox().checked).toBe(true);
  });

  test('is hidden when the installed CLI has no workflow support', () => {
    _agentVer.value = { supports_workflows: false };
    render(<ChatSettingsPanel />);
    expect(
      screen.queryByRole('checkbox', { name: /ultracode for new tasks/i }),
    ).toBeNull();
  });

  test('is hidden while the agent version is still unknown', () => {
    _agentVer.value = null;
    render(<ChatSettingsPanel />);
    expect(
      screen.queryByRole('checkbox', { name: /ultracode for new tasks/i }),
    ).toBeNull();
  });
});


// The ultracode toggle is gated on whether THIS HOST can run workflows —
// deliberately NOT on the task tab the operator happens to be looking at.
//
// The setting is host-global: one localStorage key, read by every Claude
// task's composer. Two wrong gates preceded this one. A bare
// ``useAgentVersion()`` asks about the CONFIGURED backend, so a
// codex-configured host hid the toggle from a Claude user. Keying it to the
// ACTIVE TASK's backend fixed that and broke the mirror case: a global control
// vanished from Settings whenever a Codex task was in front, so the operator
// could not switch off an expensive default from where they were standing.
//
// ``supports_workflows`` is only ever true for claude (the server sets it for
// no other backend), so asking about claude IS asking "can this host run
// workflows at all".
describe('ChatSettingsPanel — the workflow gate is host-scoped', () => {
  beforeEach(() => { _versionCalls.args.length = 0; });

  test('asks about claude, whatever tab is in front', () => {
    render(<ChatSettingsPanel />);
    expect(_versionCalls.args).toEqual(['claude']);
  });

  test('never asks about the configured-backend key', () => {
    // '' is "the configured backend" — the original bug, which answered a
    // different question on a codex-configured host.
    render(<ChatSettingsPanel />);
    expect(_versionCalls.args).not.toContain('');
  });

  test('the toggle is offered when the host supports workflows', () => {
    _agentVer.value = { supports_workflows: true };
    render(<ChatSettingsPanel />);
    expect(
      screen.getByRole('checkbox', { name: /ultracode for new tasks/i }),
    ).toBeInTheDocument();
  });

  test('and hidden when it does not', () => {
    _agentVer.value = { supports_workflows: false };
    render(<ChatSettingsPanel />);
    expect(
      screen.queryByRole('checkbox', { name: /ultracode for new tasks/i }),
    ).toBeNull();
  });
});
