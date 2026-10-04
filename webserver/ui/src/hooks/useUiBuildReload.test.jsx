// When does a window on an old bundle reload itself?
//
// Never under someone's hands; always the moment they are not looking.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useUiBuildReload } from './useUiBuildReload.js';
import { uiBuildStore, STABLE_MS } from '../stores/uiBuildStore.js';

function setAttention({ hidden = false, focused = true } = {}) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  document.hasFocus = () => focused;
}

function becomeStale(build = '200') {
  act(() => {
    uiBuildStore.observe(build, 0);
    uiBuildStore.observe(build, STABLE_MS);
  });
}

beforeEach(() => {
  uiBuildStore.resetForTest('100');
  window.sessionStorage.clear();
  setAttention({ hidden: false, focused: true });
});

afterEach(() => {
  delete document.hidden;
});

describe('useUiBuildReload', () => {
  test('a current window does nothing', () => {
    const reloadPage = vi.fn();
    const { result } = renderHook(() => useUiBuildReload(reloadPage));
    expect(result.current.stale).toBe(false);
    expect(reloadPage).not.toHaveBeenCalled();
  });

  test('a stale window that nobody is looking at reloads at once', () => {
    setAttention({ hidden: true, focused: false });
    const reloadPage = vi.fn();
    renderHook(() => useUiBuildReload(reloadPage));
    becomeStale();
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  test('visible but unfocused counts as unattended', () => {
    // The operator is in their editor with this window beside it — the
    // ordinary state while a bundle is being rebuilt.
    setAttention({ hidden: false, focused: false });
    const reloadPage = vi.fn();
    renderHook(() => useUiBuildReload(reloadPage));
    becomeStale();
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  test('a window in use is NOT reloaded — it only reports stale', () => {
    const reloadPage = vi.fn();
    const { result } = renderHook(() => useUiBuildReload(reloadPage));
    becomeStale();
    expect(reloadPage).not.toHaveBeenCalled();
    expect(result.current.stale).toBe(true);
  });

  test('it reloads the moment the operator switches away', () => {
    const reloadPage = vi.fn();
    renderHook(() => useUiBuildReload(reloadPage));
    becomeStale();

    setAttention({ hidden: false, focused: false });
    act(() => { window.dispatchEvent(new Event('blur')); });

    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  test('or when the window is hidden', () => {
    const reloadPage = vi.fn();
    renderHook(() => useUiBuildReload(reloadPage));
    becomeStale();

    setAttention({ hidden: true, focused: true });
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });

    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  test('the button reloads even while the window is in use', () => {
    const reloadPage = vi.fn();
    const { result } = renderHook(() => useUiBuildReload(reloadPage));
    becomeStale();
    act(() => { result.current.reload(); });
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  test('one automatic reload per build — never a loop', () => {
    // If the page and the server kept disagreeing after a reload, reloading
    // again would never fix it. The banner stays; the loop does not start.
    setAttention({ hidden: true, focused: false });
    const first = vi.fn();
    const mounted = renderHook(() => useUiBuildReload(first));
    becomeStale('200');
    expect(first).toHaveBeenCalledTimes(1);
    mounted.unmount();

    // The page "came back" still on the old bundle and saw 200 again.
    uiBuildStore.resetForTest('100');
    const second = vi.fn();
    const { result } = renderHook(() => useUiBuildReload(second));
    becomeStale('200');
    expect(second).not.toHaveBeenCalled();
    expect(result.current.stale).toBe(true);

    // A genuinely newer build is a new reason to reload.
    becomeStale('300');
    expect(second).toHaveBeenCalledTimes(1);
  });

  test('listeners are removed with the hook', () => {
    const reloadPage = vi.fn();
    const { unmount } = renderHook(() => useUiBuildReload(reloadPage));
    becomeStale();
    unmount();
    setAttention({ hidden: true, focused: false });
    window.dispatchEvent(new Event('blur'));
    document.dispatchEvent(new Event('visibilitychange'));
    expect(reloadPage).not.toHaveBeenCalled();
  });
});
