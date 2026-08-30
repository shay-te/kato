// The single owner of GET /api/all-settings.
//
// Two readers share that payload — the Settings drawer (tab list + the
// cross-tab search index) and whichever schema panel is open. They fetched it
// independently, and the interesting half of the bug is not the duplication:
//
//   * the panel is mounted with key={sectionId}, so every schema-tab click
//     remounted it and re-fetched all ~121 settings (each resolved through an
//     uncached per-key read on the server) for one section's fields;
//   * the drawer's fetch is latched AND the drawer never unmounts — ``open``
//     only drives a CSS transform — so after a save its search index kept
//     serving pre-save values until a full page reload.
//
// A cache alone fixes the first and makes the second permanent. Hence the
// invalidation + subscription.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';

const { _calls } = vi.hoisted(() => ({ _calls: { n: 0, body: null, ok: true } }));
vi.mock('../api.js', () => ({
  fetchAllSettings: vi.fn(() => {
    _calls.n += 1;
    return Promise.resolve({ ok: _calls.ok, body: _calls.body });
  }),
}));

import {
  loadAllSettings,
  invalidateAllSettings,
  subscribeAllSettings,
  _resetAllSettingsStore,
} from './allSettingsStore.js';

beforeEach(() => {
  _resetAllSettingsStore();
  _calls.n = 0;
  _calls.ok = true;
  _calls.body = { sections: [{ id: 'general', fields: [] }] };
});
afterEach(() => { _resetAllSettingsStore(); });

describe('allSettingsStore', () => {
  test('fetches once and shares the answer', async () => {
    const [a, b] = await Promise.all([loadAllSettings(), loadAllSettings()]);
    expect(_calls.n).toBe(1);
    expect(a).toBe(b);
  });

  test('a later reader gets the cached payload with no request', async () => {
    await loadAllSettings();
    await loadAllSettings();
    expect(_calls.n).toBe(1);
  });

  test('force re-fetches', async () => {
    await loadAllSettings();
    await loadAllSettings(true);
    expect(_calls.n).toBe(2);
  });

  test('invalidate drops the cache so the next read is fresh', async () => {
    await loadAllSettings();
    invalidateAllSettings();
    await loadAllSettings();
    expect(_calls.n).toBe(2);
  });

  test('invalidate notifies every subscriber', () => {
    // This is what un-stales the drawer, which never remounts and would
    // otherwise never look again.
    const a = vi.fn();
    const b = vi.fn();
    subscribeAllSettings(a);
    subscribeAllSettings(b);

    invalidateAllSettings();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  test('unsubscribing stops the notifications', () => {
    const cb = vi.fn();
    const off = subscribeAllSettings(cb);
    off();
    invalidateAllSettings();
    expect(cb).not.toHaveBeenCalled();
  });

  test('one throwing subscriber does not stop the others', () => {
    const bad = vi.fn(() => { throw new Error('boom'); });
    const good = vi.fn();
    subscribeAllSettings(bad);
    subscribeAllSettings(good);

    expect(() => invalidateAllSettings()).not.toThrow();
    expect(good).toHaveBeenCalledTimes(1);
  });

  test('a REJECTED fetch is not cached — the next read retries', async () => {
    // Caching a failure would leave the drawer empty for the whole session.
    const { fetchAllSettings } = await import('../api.js');
    fetchAllSettings.mockImplementationOnce(() => {
      _calls.n += 1;
      return Promise.reject(new Error('offline'));
    });

    await expect(loadAllSettings()).rejects.toThrow('offline');
    await loadAllSettings();
    expect(_calls.n).toBe(2);
  });

  test('a NOT-OK envelope is not cached either', async () => {
    // requestEnvelope resolves {ok:false} rather than throwing, so the
    // rejection guard alone would happily cache a failed load.
    _calls.ok = false;
    const first = await loadAllSettings();
    expect(first.ok).toBe(false);

    _calls.ok = true;
    const second = await loadAllSettings();
    expect(second.ok).toBe(true);
    expect(_calls.n).toBe(2);
  });
});
