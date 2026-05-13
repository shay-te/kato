// Tests for the useNotifications hook. The underlying storage
// helpers (notificationsStorage.js) are unit-tested already; this
// file proves the hook wiring:
//
//   - ``supported`` reflects whether `Notification` exists.
//   - ``enabled`` requires permission=granted AND a remembered "on".
//   - toggle() walks the permission flow and persists the decision.
//   - notify() respects all the gates: enabled, granted, kind, active
//     tab visibility, document.hidden.
//   - setKindEnabled() persists per-kind prefs.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

import { useNotifications } from './useNotifications.js';
import {
  ENABLED_STORAGE_KEY,
  KIND_STORAGE_KEY,
} from '../utils/notificationsStorage.js';


// jsdom doesn't have ``Notification`` by default — install a
// controllable fake before each test.
class FakeNotification {
  static permission = 'default';
  static requests = [];
  static instances = [];

  constructor(title, options = {}) {
    this.title = title;
    this.options = options;
    this.body = options.body;
    this.icon = options.icon;
    this.tag = options.tag;
    this.onclick = null;
    this.closed = false;
    FakeNotification.instances.push(this);
  }

  close() { this.closed = true; }

  static async requestPermission() {
    FakeNotification.requests.push(true);
    return FakeNotification.permission;
  }
}


beforeEach(() => {
  FakeNotification.permission = 'default';
  FakeNotification.requests = [];
  FakeNotification.instances = [];
  globalThis.Notification = FakeNotification;
});

afterEach(() => {
  delete globalThis.Notification;
});


describe('useNotifications — supported + initial state', () => {

  test('supported=true when Notification exists in window', () => {
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );
    expect(result.current.supported).toBe(true);
  });

  test('supported=false + permission=denied when Notification is missing', () => {
    delete globalThis.Notification;
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );
    expect(result.current.supported).toBe(false);
    expect(result.current.permission).toBe('denied');
  });

  test('starts disabled when no localStorage record exists', () => {
    FakeNotification.permission = 'granted';
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );
    expect(result.current.enabled).toBe(false);
  });

  test('starts enabled when permission=granted + storage="on"', () => {
    // The combination required for "ready to notify".
    FakeNotification.permission = 'granted';
    window.localStorage.setItem(ENABLED_STORAGE_KEY, 'on');
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );
    expect(result.current.enabled).toBe(true);
  });

  test('storage="on" but permission denied → enabled=false', () => {
    // Operator revoked permission in browser settings. The remembered
    // "on" gets ignored until permission is granted again.
    FakeNotification.permission = 'denied';
    window.localStorage.setItem(ENABLED_STORAGE_KEY, 'on');
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );
    expect(result.current.enabled).toBe(false);
  });
});


describe('useNotifications — toggle flow', () => {

  test('toggle on first time: requests permission, then persists', async () => {
    FakeNotification.permission = 'default';
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    // Simulate the browser prompt resolving with "Allow":
    // requestPermission resolves AS 'granted' (returns the value).
    FakeNotification.requestPermission = async () => {
      FakeNotification.requests.push(true);
      FakeNotification.permission = 'granted';
      return 'granted';
    };

    await act(async () => { await result.current.toggle(); });

    expect(FakeNotification.requests.length).toBe(1);
    expect(result.current.enabled).toBe(true);
    expect(window.localStorage.getItem(ENABLED_STORAGE_KEY)).toBe('on');
  });

  test('toggle when permission denied: stays disabled (no popup)', async () => {
    // Operator already denied at the OS / browser level. Don't ask
    // again — would just produce a denied result instantly.
    FakeNotification.permission = 'denied';
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    await act(async () => { await result.current.toggle(); });

    expect(FakeNotification.requests.length).toBe(0);
    expect(result.current.enabled).toBe(false);
  });

  test('toggle when permission default but user denies the prompt', async () => {
    FakeNotification.permission = 'default';
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    // The popup appears but user clicks "Block".
    FakeNotification.requestPermission = async () => {
      FakeNotification.requests.push(true);
      FakeNotification.permission = 'denied';
      return 'denied';
    };
    await act(async () => { await result.current.toggle(); });

    expect(result.current.enabled).toBe(false);
    expect(window.localStorage.getItem(ENABLED_STORAGE_KEY)).not.toBe('on');
  });

  test('toggle off when currently enabled persists "off"', async () => {
    FakeNotification.permission = 'granted';
    window.localStorage.setItem(ENABLED_STORAGE_KEY, 'on');
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );
    expect(result.current.enabled).toBe(true);

    await act(async () => { await result.current.toggle(); });

    expect(result.current.enabled).toBe(false);
    expect(window.localStorage.getItem(ENABLED_STORAGE_KEY)).toBe('off');
  });

  test('toggle is a no-op when Notification is unsupported', async () => {
    delete globalThis.Notification;
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );
    await act(async () => { await result.current.toggle(); });
    expect(result.current.enabled).toBe(false);
  });
});


describe('useNotifications — notify() gates', () => {

  function setupEnabled() {
    FakeNotification.permission = 'granted';
    window.localStorage.setItem(ENABLED_STORAGE_KEY, 'on');
  }

  test('notify is silently dropped when disabled', () => {
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );
    expect(result.current.enabled).toBe(false);

    act(() => {
      result.current.notify({
        title: 'hi', body: '...', taskId: 'T1', kind: 'attention',
      });
    });
    expect(FakeNotification.instances).toHaveLength(0);
  });

  test('notify fires when enabled + permission granted + tab hidden', () => {
    setupEnabled();
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    act(() => {
      result.current.notify({
        title: 'Approval needed', body: 'T1', taskId: 'T1', kind: 'attention',
      });
    });

    expect(FakeNotification.instances).toHaveLength(1);
    expect(FakeNotification.instances[0].title).toBe('Approval needed');
  });

  test('notify is suppressed for the active tab when document is visible', () => {
    // Operator is already looking at this tab — no need to notify.
    setupEnabled();
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: 'T1' }),
    );

    act(() => {
      result.current.notify({
        title: 'Reply', body: 'T1', taskId: 'T1', kind: 'reply',
      });
    });

    expect(FakeNotification.instances).toHaveLength(0);
  });

  test('notify still fires for non-active tabs even when visible', () => {
    setupEnabled();
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: 'T-OTHER' }),
    );

    act(() => {
      result.current.notify({
        title: 'Reply', body: 'T1', taskId: 'T1', kind: 'started',
      });
    });

    expect(FakeNotification.instances).toHaveLength(1);
  });

  test('notify respects per-kind opt-out (kindPrefs[kind]===false)', () => {
    // Set "reply" to OFF before mount.
    setupEnabled();
    window.localStorage.setItem(
      KIND_STORAGE_KEY,
      JSON.stringify({ reply: false, attention: true }),
    );
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    act(() => {
      result.current.notify({
        title: 'Reply', body: 'T1', taskId: 'T1', kind: 'reply',
      });
    });

    expect(FakeNotification.instances).toHaveLength(0);

    // Attention kind passes (opted in).
    act(() => {
      result.current.notify({
        title: 'Attn', body: 'T1', taskId: 'T1', kind: 'attention',
      });
    });
    expect(FakeNotification.instances).toHaveLength(1);
  });

  test('notify with no kind uses "info" key (which is allowed by default)', () => {
    setupEnabled();
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    act(() => {
      result.current.notify({ title: 'hi', body: 't', taskId: 't1' });
    });

    expect(FakeNotification.instances).toHaveLength(1);
  });

  test('notify uses a stable tag for deduplication', () => {
    // Browser dedupes notifications with the same tag — we want
    // sequential ones for the SAME (kind, task) to replace, not
    // stack up.
    setupEnabled();
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    act(() => {
      result.current.notify({
        title: 'Attn', body: 'b', taskId: 'T1', kind: 'attention',
      });
    });

    expect(FakeNotification.instances[0].tag).toBe('kato-attention-T1');
  });
});


describe('useNotifications — setKindEnabled persists per-kind prefs', () => {

  test('setKindEnabled writes the new pref to localStorage', () => {
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    act(() => { result.current.setKindEnabled('reply', false); });

    const stored = JSON.parse(window.localStorage.getItem(KIND_STORAGE_KEY));
    expect(stored.reply).toBe(false);
  });

  test('setKindEnabled flips back to true cleanly', () => {
    window.localStorage.setItem(
      KIND_STORAGE_KEY, JSON.stringify({ reply: false }),
    );
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    act(() => { result.current.setKindEnabled('reply', true); });

    const stored = JSON.parse(window.localStorage.getItem(KIND_STORAGE_KEY));
    expect(stored.reply).toBe(true);
  });

  test('exposed kindPrefs reflects current preferences', () => {
    window.localStorage.setItem(
      KIND_STORAGE_KEY,
      JSON.stringify({ reply: false, attention: true }),
    );
    const { result } = renderHook(
      () => useNotifications({ activeTaskId: null }),
    );

    expect(result.current.kindPrefs.reply).toBe(false);
    expect(result.current.kindPrefs.attention).toBe(true);
  });
});
