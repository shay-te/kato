// The chat-maximize preference — collapse the files and preview panes so the
// conversation gets the whole window.
//
// It is a store rather than App state because the TOGGLE lives in the chat
// header (inside a component App remounts on every task switch) while the pane
// GRID it controls belongs to Layout, a sibling several levels up.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  readChatMaximized,
  writeChatMaximized,
  toggleChatMaximized,
  subscribeChatMaximized,
  _resetChatMaximizedPref,
} from './chatMaximizedPref.js';

beforeEach(() => {
  try { localStorage.clear(); } catch (_) { /* jsdom */ }
  _resetChatMaximizedPref();
});
afterEach(() => { _resetChatMaximizedPref(); });

describe('chatMaximizedPref', () => {
  test('defaults to NOT maximized', () => {
    // Three panes is what kato has always opened on; a view preference must
    // not surprise someone who never asked for it.
    expect(readChatMaximized()).toBe(false);
  });

  test('round-trips', () => {
    writeChatMaximized(true);
    expect(readChatMaximized()).toBe(true);
    writeChatMaximized(false);
    expect(readChatMaximized()).toBe(false);
  });

  test('toggle flips and returns the new value', () => {
    expect(toggleChatMaximized()).toBe(true);
    expect(readChatMaximized()).toBe(true);
    expect(toggleChatMaximized()).toBe(false);
  });

  test('coerces truthy junk to a boolean', () => {
    expect(writeChatMaximized('yes')).toBe(true);
    expect(readChatMaximized()).toBe(true);
  });

  test('survives a reload', () => {
    // It is a reading posture: someone who maximized to read a long
    // transcript has not finished reading it because they refreshed.
    writeChatMaximized(true);
    _resetChatMaximizedPref(); // drops the in-memory cache, keeps localStorage
    expect(readChatMaximized()).toBe(true);
  });

  test('notifies subscribers on change', () => {
    const seen = [];
    subscribeChatMaximized((v) => seen.push(v));
    writeChatMaximized(true);
    writeChatMaximized(false);
    expect(seen).toEqual([true, false]);
  });

  test('unsubscribing stops the notifications', () => {
    const seen = [];
    const off = subscribeChatMaximized((v) => seen.push(v));
    off();
    writeChatMaximized(true);
    expect(seen).toEqual([]);
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
      expect(() => readChatMaximized()).not.toThrow();
    } finally {
      if (original) { Object.defineProperty(window, 'localStorage', original); }
    }
  });
});
