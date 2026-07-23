// Tests for the Chat settings tab — the "steer vs send immediately" choice,
// backed by the composerSteerPref localStorage store.

import { describe, test, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import {
  readSteerWhileWorking,
  writeSteerWhileWorking,
  _resetSteerWhileWorkingPref,
} from '../utils/composerSteerPref.js';
import ChatSettingsPanel from './ChatSettingsPanel.jsx';

beforeEach(() => {
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetSteerWhileWorkingPref();
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
