// Tests for useHorizontalWheelScroll — the shared vertical-wheel-to-
// horizontal-scroll remap behind every horizontally-scrolling tab
// strip (task tabs, file tabs). Attaches a real 'wheel' listener to a
// plain DOM node (no component needed) and asserts scrollLeft moves,
// deltaMode normalisation applies, and trackpad/edge events are left
// alone.

import { describe, test, expect } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useHorizontalWheelScroll } from './useHorizontalWheelScroll.js';

function scrollableNode({ scrollLeft = 0, clientWidth = 200, scrollWidth = 1000 } = {}) {
  const el = document.createElement('div');
  let _scrollLeft = scrollLeft;
  Object.defineProperty(el, 'scrollLeft', {
    get: () => _scrollLeft,
    set: (v) => { _scrollLeft = v; },
  });
  Object.defineProperty(el, 'clientWidth', { value: clientWidth });
  Object.defineProperty(el, 'scrollWidth', { value: scrollWidth });
  return el;
}

function wheel(el, { deltaY = 0, deltaX = 0, deltaMode = 0 } = {}) {
  const event = new Event('wheel', { cancelable: true });
  Object.defineProperty(event, 'deltaY', { value: deltaY });
  Object.defineProperty(event, 'deltaX', { value: deltaX });
  Object.defineProperty(event, 'deltaMode', { value: deltaMode });
  el.dispatchEvent(event);
  return event;
}

describe('useHorizontalWheelScroll', () => {
  test('a pixel-mode vertical wheel scrolls the node horizontally', () => {
    const el = scrollableNode({ scrollLeft: 100 });
    const ref = { current: el };
    renderHook(() => useHorizontalWheelScroll(ref));

    wheel(el, { deltaY: 50, deltaMode: 0 });
    expect(el.scrollLeft).toBe(150);
  });

  test('a line-mode (deltaMode 1) wheel — the Windows/Firefox dead-scroll bug — is normalised to ~16px per line', () => {
    const el = scrollableNode({ scrollLeft: 0 });
    const ref = { current: el };
    renderHook(() => useHorizontalWheelScroll(ref));

    wheel(el, { deltaY: 3, deltaMode: 1 });
    expect(el.scrollLeft).toBe(48); // 3 lines * 16px
  });

  test('a page-mode (deltaMode 2) wheel scrolls by a full clientWidth per page', () => {
    const el = scrollableNode({ scrollLeft: 0, clientWidth: 200 });
    const ref = { current: el };
    renderHook(() => useHorizontalWheelScroll(ref));

    wheel(el, { deltaY: 1, deltaMode: 2 });
    expect(el.scrollLeft).toBe(200);
  });

  test('a mostly-horizontal event (trackpad, deltaX >= deltaY) is left alone', () => {
    const el = scrollableNode({ scrollLeft: 100 });
    const ref = { current: el };
    renderHook(() => useHorizontalWheelScroll(ref));

    const event = wheel(el, { deltaY: 5, deltaX: 20 });
    expect(el.scrollLeft).toBe(100);
    expect(event.defaultPrevented).toBe(false);
  });

  test('scrolling right past the end does not consume the event (page can still scroll)', () => {
    const el = scrollableNode({ scrollLeft: 800, clientWidth: 200, scrollWidth: 1000 });
    const ref = { current: el };
    renderHook(() => useHorizontalWheelScroll(ref));

    const event = wheel(el, { deltaY: 50 });
    expect(el.scrollLeft).toBe(800);
    expect(event.defaultPrevented).toBe(false);
  });

  test('scrolling left past the start does not consume the event', () => {
    const el = scrollableNode({ scrollLeft: 0 });
    const ref = { current: el };
    renderHook(() => useHorizontalWheelScroll(ref));

    const event = wheel(el, { deltaY: -50 });
    expect(el.scrollLeft).toBe(0);
    expect(event.defaultPrevented).toBe(false);
  });

  test('a real scroll (not at the edge) DOES preventDefault, so the page does not also scroll', () => {
    const el = scrollableNode({ scrollLeft: 100 });
    const ref = { current: el };
    renderHook(() => useHorizontalWheelScroll(ref));

    const event = wheel(el, { deltaY: 50 });
    expect(event.defaultPrevented).toBe(true);
  });

  test('null ref is a no-op (does not throw)', () => {
    const ref = { current: null };
    expect(() => renderHook(() => useHorizontalWheelScroll(ref))).not.toThrow();
  });

  test('cleans up the wheel listener on unmount', () => {
    const el = scrollableNode({ scrollLeft: 100 });
    const ref = { current: el };
    const { unmount } = renderHook(() => useHorizontalWheelScroll(ref));
    unmount();

    wheel(el, { deltaY: 50 });
    expect(el.scrollLeft).toBe(100); // listener removed — no scroll happened
  });
});
