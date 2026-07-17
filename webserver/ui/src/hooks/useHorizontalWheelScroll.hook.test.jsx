// Tests for useHorizontalWheelScroll — the shared vertical-wheel-to-
// horizontal-scroll remap behind every horizontally-scrolling tab
// strip (task tabs, file tabs). The hook returns a CALLBACK ref
// (invoked by React exactly when a DOM node attaches/detaches) rather
// than accepting a plain useRef object — a regression test below
// pins the exact real-world scenario that broke with the old
// useRef + useEffect(..., [ref]) design: a strip that renders with NO
// scrollable element on its first render (an empty-state placeholder,
// or nothing at all) and only mounts the real element once content
// shows up later. useEffect's dependency array tracks the ref OBJECT's
// identity, not ref.current's value, so it never re-ran once content
// appeared — the wheel silently did nothing forever, while
// click-and-drag kept working (native browser behavior, no JS
// required) making the bug easy to miss.

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
  test('THE REGRESSION: attaching the callback ref to a node LATER (not on first render) still wires up the listener', () => {
    // Simulates: strip first renders with nothing scrollable (empty
    // state / no tabs open yet), then later mounts the real element
    // once content appears — e.g. React calling the ref callback with
    // null first (or never, if nothing rendered a ref at all), then
    // calling it with the real node on a subsequent render.
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const refCallback = result.current;

    // Nothing attaches yet (equivalent to the empty-state render).
    refCallback(null);

    // Now the real element mounts (a tab opened / a session loaded).
    const el = scrollableNode({ scrollLeft: 100 });
    refCallback(el);

    const event = wheel(el, { deltaY: 50 });
    expect(el.scrollLeft).toBe(150);
    expect(event.defaultPrevented).toBe(true);
  });

  test('a pixel-mode vertical wheel scrolls the node horizontally', () => {
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const el = scrollableNode({ scrollLeft: 100 });
    result.current(el);

    wheel(el, { deltaY: 50, deltaMode: 0 });
    expect(el.scrollLeft).toBe(150);
  });

  test('a line-mode (deltaMode 1) wheel — the Windows/Firefox dead-scroll bug — is normalised to ~16px per line', () => {
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const el = scrollableNode({ scrollLeft: 0 });
    result.current(el);

    wheel(el, { deltaY: 3, deltaMode: 1 });
    expect(el.scrollLeft).toBe(48); // 3 lines * 16px
  });

  test('a page-mode (deltaMode 2) wheel scrolls by a full clientWidth per page', () => {
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const el = scrollableNode({ scrollLeft: 0, clientWidth: 200 });
    result.current(el);

    wheel(el, { deltaY: 1, deltaMode: 2 });
    expect(el.scrollLeft).toBe(200);
  });

  test('a mostly-horizontal event (trackpad, deltaX >= deltaY) is left alone', () => {
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const el = scrollableNode({ scrollLeft: 100 });
    result.current(el);

    const event = wheel(el, { deltaY: 5, deltaX: 20 });
    expect(el.scrollLeft).toBe(100);
    expect(event.defaultPrevented).toBe(false);
  });

  test('scrolling right past the end does not consume the event (page can still scroll)', () => {
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const el = scrollableNode({ scrollLeft: 800, clientWidth: 200, scrollWidth: 1000 });
    result.current(el);

    const event = wheel(el, { deltaY: 50 });
    expect(el.scrollLeft).toBe(800);
    expect(event.defaultPrevented).toBe(false);
  });

  test('scrolling left past the start does not consume the event', () => {
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const el = scrollableNode({ scrollLeft: 0 });
    result.current(el);

    const event = wheel(el, { deltaY: -50 });
    expect(el.scrollLeft).toBe(0);
    expect(event.defaultPrevented).toBe(false);
  });

  test('re-attaching to a DIFFERENT node removes the old listener and attaches to the new one', () => {
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const first = scrollableNode({ scrollLeft: 100 });
    const second = scrollableNode({ scrollLeft: 100 });
    result.current(first);
    result.current(second);

    wheel(first, { deltaY: 50 });
    expect(first.scrollLeft).toBe(100); // old node: listener removed, no scroll

    wheel(second, { deltaY: 50 });
    expect(second.scrollLeft).toBe(150); // new node: listener active
  });

  test('detaching (callback called with null) removes the listener', () => {
    const { result } = renderHook(() => useHorizontalWheelScroll());
    const el = scrollableNode({ scrollLeft: 100 });
    result.current(el);
    result.current(null);

    wheel(el, { deltaY: 50 });
    expect(el.scrollLeft).toBe(100);
  });

  test('an optional externalRef is kept in sync with the attached node', () => {
    const externalRef = { current: null };
    const { result } = renderHook(() => useHorizontalWheelScroll(externalRef));
    const el = scrollableNode();
    result.current(el);
    expect(externalRef.current).toBe(el);

    result.current(null);
    expect(externalRef.current).toBe(null);
  });

  test('onAttach runs when the node attaches LATER, so TabList scroll/resize listeners survive the empty→populated remount', () => {
    // #11: the chevron scroll/resize listeners used to be a one-shot
    // useEffect that fired before the strip existed and never re-ran. Riding
    // onAttach means they wire up exactly when the node attaches.
    const attaches = [];
    const cleanups = [];
    const onAttach = (node) => {
      attaches.push(node);
      const cleanup = () => cleanups.push(node);
      return cleanup;
    };
    const { result } = renderHook(() => useHorizontalWheelScroll(null, onAttach));
    const refCallback = result.current;

    refCallback(null);  // empty-state render: nothing attaches
    expect(attaches).toEqual([]);

    const el = scrollableNode();
    refCallback(el);  // strip mounts once sessions load
    expect(attaches).toEqual([el]);

    refCallback(null);  // unmount → the caller's cleanup runs
    expect(cleanups).toEqual([el]);
  });

  test('onAttach cleanup runs when re-attaching to a different node (no leak)', () => {
    const cleanups = [];
    const onAttach = (node) => () => cleanups.push(node);
    const { result } = renderHook(() => useHorizontalWheelScroll(null, onAttach));
    const first = scrollableNode();
    const second = scrollableNode();
    result.current(first);
    result.current(second);  // swapping nodes must clean up the first
    expect(cleanups).toEqual([first]);
  });
});
