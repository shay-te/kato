// Tests for the shared pop-over dismiss hook.
//
// The bug that added the containment check: the listener fired for EVERY
// pointerdown, including on the pop-over's own contents. Opening the native
// model <select> inside the composer's actions menu therefore tore the menu
// down before an option could be picked — the model could not be changed at
// all. Callers that pass no ref must keep the original behaviour, because
// three path context menus rely on it.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useDismissOnOutsidePointerOrEscape } from './useDismissOnOutsidePointerOrEscape.js';

function pointerDownOn(target) {
  target.dispatchEvent(new window.PointerEvent('pointerdown', { bubbles: true }));
}

function pressEscape(target = window) {
  target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
}

function mountWithContainer({ active = true } = {}) {
  const root = document.createElement('div');
  const inside = document.createElement('select');
  root.appendChild(inside);
  document.body.appendChild(root);
  const outside = document.createElement('button');
  document.body.appendChild(outside);

  const onDismiss = vi.fn();
  const ref = { current: root };
  const view = renderHook(
    () => useDismissOnOutsidePointerOrEscape(active, onDismiss, ref),
  );
  return { onDismiss, root, inside, outside, view };
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('useDismissOnOutsidePointerOrEscape', () => {
  test('a pointerdown INSIDE the container does not dismiss', () => {
    // The regression: this is opening the model <select>.
    const { onDismiss, inside } = mountWithContainer();
    pointerDownOn(inside);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  test('a pointerdown on the container itself does not dismiss', () => {
    const { onDismiss, root } = mountWithContainer();
    pointerDownOn(root);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  test('a pointerdown OUTSIDE still dismisses', () => {
    const { onDismiss, outside } = mountWithContainer();
    pointerDownOn(outside);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test('Escape dismisses even from inside — the way out once focus is in', () => {
    const { onDismiss, inside } = mountWithContainer();
    pressEscape(inside);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test('nothing is listened for while inactive', () => {
    const { onDismiss, outside } = mountWithContainer({ active: false });
    pointerDownOn(outside);
    pressEscape();
    expect(onDismiss).not.toHaveBeenCalled();
  });

  test('unmount removes the listeners', () => {
    const { onDismiss, outside, view } = mountWithContainer();
    view.unmount();
    pointerDownOn(outside);
    pressEscape();
    expect(onDismiss).not.toHaveBeenCalled();
  });

  // The three path context menus pass no ref and must be unaffected.
  test('with NO container ref, any pointerdown dismisses (unchanged)', () => {
    const onDismiss = vi.fn();
    renderHook(() => useDismissOnOutsidePointerOrEscape(true, onDismiss));
    const anywhere = document.createElement('div');
    document.body.appendChild(anywhere);
    pointerDownOn(anywhere);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test('a detached container ref falls back to dismissing', () => {
    // A ref that never attached must not silently disable dismissal.
    const onDismiss = vi.fn();
    renderHook(
      () => useDismissOnOutsidePointerOrEscape(true, onDismiss, { current: null }),
    );
    const anywhere = document.createElement('div');
    document.body.appendChild(anywhere);
    pointerDownOn(anywhere);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
