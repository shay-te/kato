// Tests for ``useClampedPointMenu`` — the shared measure-then-place
// hook behind every right-click "path" context menu (Files tree,
// diff file header, editor header). Regression coverage for the
// production bug: right-clicking near the bottom/right of the
// viewport opened the menu off-screen with no bounds check.

import { describe, test, expect, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useClampedPointMenu } from './useClampedPointMenu.js';

const MENU_WIDTH = 180;
const MENU_HEIGHT = 100;

function _mockMenuRect() {
  return { width: MENU_WIDTH, height: MENU_HEIGHT };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useClampedPointMenu', () => {
  test('null anchor renders off-screen and sets no ref measurement', () => {
    const { result } = renderHook(() => useClampedPointMenu(null));
    expect(result.current.style).toEqual({ left: '-9999px', top: '-9999px' });
  });

  test('anchor with no rendered element yet stays off-screen (avoids flash)', () => {
    const { result } = renderHook(() => useClampedPointMenu({ x: 50, y: 50 }));
    // menuRef.current is null until the caller attaches it to a DOM
    // node — before that, style must never resolve to the raw anchor
    // coordinates (that's the pre-fix flash-at-wrong-position bug).
    expect(result.current.style).toEqual({ left: '-9999px', top: '-9999px' });
  });

  test('anchor with plenty of room below/right places at the raw point', () => {
    Object.defineProperty(window, 'innerWidth', { value: 1200, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(_mockMenuRect());

    const el = document.createElement('div');
    const { result, rerender } = renderHook(
      ({ anchor }) => {
        const menu = useClampedPointMenu(anchor);
        menu.menuRef.current = el;
        return menu;
      },
      { initialProps: { anchor: { x: 100, y: 100 } } },
    );
    act(() => { rerender({ anchor: { x: 100, y: 100 } }); });

    expect(result.current.style).toEqual({ left: '100px', top: '100px' });
  });

  test('anchor near the bottom edge flips the menu upward from the click point', () => {
    Object.defineProperty(window, 'innerWidth', { value: 1200, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(_mockMenuRect());

    const el = document.createElement('div');
    // y=850: 850 + MENU_HEIGHT(100) + pad(8) > 900 → must flip upward.
    const anchor = { x: 100, y: 850 };
    const { result, rerender } = renderHook(
      ({ a }) => {
        const menu = useClampedPointMenu(a);
        menu.menuRef.current = el;
        return menu;
      },
      { initialProps: { a: anchor } },
    );
    act(() => { rerender({ a: anchor }); });

    // Flipped: top = anchor.y - height = 850 - 100 = 750.
    expect(result.current.style.top).toBe('750px');
    expect(result.current.style.left).toBe('100px');
  });

  test('anchor near the right edge shifts the menu left to stay on-screen', () => {
    Object.defineProperty(window, 'innerWidth', { value: 1200, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(_mockMenuRect());

    const el = document.createElement('div');
    // x=1150: 1150 + MENU_WIDTH(180) + pad(8) > 1200 → must shift left.
    const anchor = { x: 1150, y: 100 };
    const { result, rerender } = renderHook(
      ({ a }) => {
        const menu = useClampedPointMenu(a);
        menu.menuRef.current = el;
        return menu;
      },
      { initialProps: { a: anchor } },
    );
    act(() => { rerender({ a: anchor }); });

    // left = viewportWidth - width - pad = 1200 - 180 - 8 = 1012.
    expect(result.current.style.left).toBe('1012px');
    expect(result.current.style.top).toBe('100px');
  });

  test('extreme top-left anchor never goes negative past the pad', () => {
    Object.defineProperty(window, 'innerWidth', { value: 1200, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(_mockMenuRect());

    const el = document.createElement('div');
    const anchor = { x: 2, y: 2 };
    const { result, rerender } = renderHook(
      ({ a }) => {
        const menu = useClampedPointMenu(a);
        menu.menuRef.current = el;
        return menu;
      },
      { initialProps: { a: anchor } },
    );
    act(() => { rerender({ a: anchor }); });

    expect(result.current.style).toEqual({ left: '8px', top: '8px' });
  });

  test('closing the menu (anchor → null) resets to off-screen', () => {
    Object.defineProperty(window, 'innerWidth', { value: 1200, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(_mockMenuRect());

    const el = document.createElement('div');
    const { result, rerender } = renderHook(
      ({ a }) => {
        const menu = useClampedPointMenu(a);
        if (a) { menu.menuRef.current = el; }
        return menu;
      },
      { initialProps: { a: { x: 100, y: 100 } } },
    );
    act(() => { rerender({ a: { x: 100, y: 100 } }); });
    expect(result.current.style).toEqual({ left: '100px', top: '100px' });

    act(() => { rerender({ a: null }); });
    expect(result.current.style).toEqual({ left: '-9999px', top: '-9999px' });
  });
});
