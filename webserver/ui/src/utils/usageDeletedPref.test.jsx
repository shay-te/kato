// The Usage dialog's "Include deleted tasks" switch — remembered per browser.

import { describe, test, expect, beforeEach, afterEach } from 'vitest';

import {
  readUsageShowDeleted,
  writeUsageShowDeleted,
  _resetUsageShowDeleted,
} from './usageDeletedPref.js';

beforeEach(() => {
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetUsageShowDeleted();
});
afterEach(() => { _resetUsageShowDeleted(); });

describe('usageDeletedPref', () => {
  test('is off until the operator asks for the history', () => {
    expect(readUsageShowDeleted()).toBe(false);
  });

  test('round-trips, and survives a reload (a stored `false` is not a missing value)', () => {
    writeUsageShowDeleted(true);
    _resetUsageShowDeleted();
    expect(readUsageShowDeleted()).toBe(true);
    writeUsageShowDeleted(false);
    _resetUsageShowDeleted();
    expect(readUsageShowDeleted()).toBe(false);
  });

  test('a damaged stored value reads as off', () => {
    localStorage.setItem('kato.usageShowDeleted.v1', '{nope');
    _resetUsageShowDeleted();
    expect(readUsageShowDeleted()).toBe(false);
  });
});
