// Which pane has the whole window: the chat, the centre (file / diff view), or
// none. ONE value, so two panes can never both be maximized and one button
// restores whichever is.
//
// It is a store rather than App state because the TOGGLES live in the chat
// header (inside a component App remounts on every task switch), the tab strip
// and the plan view, while the pane GRID they control belongs to Layout.

import { describe, test, expect, beforeEach, afterEach } from 'vitest';

import {
  MAXIMIZED_PANE,
  readMaximizedPane,
  writeMaximizedPane,
  toggleMaximizedPane,
  subscribeMaximizedPane,
  _resetMaximizedPanePref,
  filesRailShown,
} from './maximizedPanePref.js';

const STORAGE_KEY = 'kato.chatMaximized.v1';

beforeEach(() => {
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetMaximizedPanePref();
});
afterEach(() => { _resetMaximizedPanePref(); });

describe('maximizedPanePref', () => {
  test('defaults to nothing maximized', () => {
    // Three panes is what kato has always opened on; a view preference must
    // not surprise someone who never asked for it.
    expect(readMaximizedPane()).toBe(MAXIMIZED_PANE.NONE);
  });

  test('round-trips each pane', () => {
    for (const pane of [MAXIMIZED_PANE.CHAT, MAXIMIZED_PANE.CENTER, MAXIMIZED_PANE.NONE]) {
      expect(writeMaximizedPane(pane)).toBe(pane);
      expect(readMaximizedPane()).toBe(pane);
    }
  });

  test('toggle maximizes a pane, and the same toggle restores', () => {
    expect(toggleMaximizedPane(MAXIMIZED_PANE.CENTER)).toBe(MAXIMIZED_PANE.CENTER);
    expect(toggleMaximizedPane(MAXIMIZED_PANE.CENTER)).toBe(MAXIMIZED_PANE.NONE);
  });

  test('one pane at a time: maximizing the other takes over', () => {
    toggleMaximizedPane(MAXIMIZED_PANE.CHAT);
    expect(toggleMaximizedPane(MAXIMIZED_PANE.CENTER)).toBe(MAXIMIZED_PANE.CENTER);
    expect(readMaximizedPane()).toBe(MAXIMIZED_PANE.CENTER);
  });

  test('an unknown pane reads as none', () => {
    expect(writeMaximizedPane('sidebar')).toBe(MAXIMIZED_PANE.NONE);
  });

  test('survives a reload', () => {
    // A reading posture: someone who maximized to read has not finished
    // reading because they refreshed.
    writeMaximizedPane(MAXIMIZED_PANE.CENTER);
    _resetMaximizedPanePref(); // drops the in-memory cache, keeps localStorage
    expect(readMaximizedPane()).toBe(MAXIMIZED_PANE.CENTER);
  });

  test('a chat maximized before the centre could be still opens maximized', () => {
    // The chat-only preference stored ``{chatMaximized: true}`` under this key.
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ chatMaximized: true }));
    expect(readMaximizedPane()).toBe(MAXIMIZED_PANE.CHAT);
    _resetMaximizedPanePref();
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ chatMaximized: false }));
    expect(readMaximizedPane()).toBe(MAXIMIZED_PANE.NONE);
  });

  test('notifies subscribers on change, and unsubscribing stops it', () => {
    const seen = [];
    const off = subscribeMaximizedPane((pane) => seen.push(pane));
    writeMaximizedPane(MAXIMIZED_PANE.CHAT);
    writeMaximizedPane(MAXIMIZED_PANE.NONE);
    off();
    writeMaximizedPane(MAXIMIZED_PANE.CENTER);
    expect(seen).toEqual([MAXIMIZED_PANE.CHAT, MAXIMIZED_PANE.NONE]);
  });

  test('unreadable storage reads as the default rather than throwing', () => {
    // Private windows and blocked site data throw on access; a view
    // preference must never be the thing that breaks the page.
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() { throw new Error('blocked'); },
    });
    try {
      expect(readMaximizedPane()).toBe(MAXIMIZED_PANE.NONE);
    } finally {
      if (original) { Object.defineProperty(window, 'localStorage', original); }
    }
  });
});

describe('filesRailShown', () => {
  test('the files tree is a rail whichever pane is maximized, a column otherwise', () => {
    expect(filesRailShown(MAXIMIZED_PANE.CHAT)).toBe(true);
    expect(filesRailShown(MAXIMIZED_PANE.CENTER)).toBe(true);
    expect(filesRailShown(MAXIMIZED_PANE.NONE)).toBe(false);
    expect(filesRailShown('sidebar')).toBe(false);
  });
});
