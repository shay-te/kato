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
const { _agentVer } = vi.hoisted(
  () => ({ _agentVer: { value: { supports_workflows: true } } }),
);
vi.mock('../hooks/useAgentVersion.js', () => ({
  useAgentVersion: () => _agentVer.value,
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
