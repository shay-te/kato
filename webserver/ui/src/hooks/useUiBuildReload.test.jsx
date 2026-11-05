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

describe('useUiBuildReload — a rebuild of the styles alone', () => {
  beforeEach(() => {
    uiBuildStore.resetForTest('100-200-300');
    document.head.innerHTML = `
      <link rel="stylesheet" href="/static/build/app.css?v=200" />
      <link rel="stylesheet" href="/static/css/app.css?v=300" />
      <link rel="stylesheet" href="https://fonts.example/other.css" />`;
  });

  test('is swapped in place: no reload, no banner, the window current again', () => {
    const reloadPage = vi.fn();
    const { result } = renderHook(() => useUiBuildReload(reloadPage));
    becomeStale('100-201-301');

    expect(reloadPage).not.toHaveBeenCalled();
    expect(result.current.stale).toBe(false);
    expect(uiBuildStore.loadedBuild()).toBe('100-201-301');
    const hrefs = [...document.querySelectorAll('link[rel="stylesheet"]')].map((link) => link.getAttribute('href'));
    expect(hrefs).toContain('/static/build/app.css?v=100-201-301');
    expect(hrefs).toContain('/static/css/app.css?v=100-201-301');
    expect(hrefs).toContain('https://fonts.example/other.css');  // not ours: untouched
    // The old sheets leave once the new ones have loaded (never unstyled).
    for (const link of document.querySelectorAll('link[href*="?v=100-201-301"]')) {
      link.dispatchEvent(new Event('load'));
    }
    expect(document.querySelectorAll('link[href$="?v=200"], link[href$="?v=300"]')).toHaveLength(0);
  });

  test('a new script still reloads the way it always did', () => {
    setAttention({ hidden: true, focused: false });
    const reloadPage = vi.fn();
    const swap = vi.fn();
    renderHook(() => useUiBuildReload(reloadPage, swap));
    becomeStale('101-201-301');
    expect(swap).not.toHaveBeenCalled();
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  test('a sheet that fails to load is replaced all the same', () => {
    renderHook(() => useUiBuildReload(vi.fn()));
    becomeStale('100-201-300');
    const fresh = document.querySelector('link[href="/static/build/app.css?v=100-201-300"]');
    fresh.dispatchEvent(new Event('error'));
    expect(document.querySelector('link[href="/static/build/app.css?v=200"]')).toBeNull();
  });
});
