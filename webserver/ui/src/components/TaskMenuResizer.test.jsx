// The pinned tasks menu's right edge: drag, double-click, arrow keys.

import { describe, test, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import TaskMenuResizer, { RESIZE_KEY_STEP_PX } from './TaskMenuResizer.jsx';
import {
  TASK_MENU_WIDTH_DEFAULT,
  TASK_MENU_WIDTH_MIN,
  _resetTaskMenu,
  setTaskMenuWidth,
  taskMenuWidth,
} from '../stores/taskMenuStore.js';

function edge() {
  return screen.getByRole('separator', { name: 'Resize the tasks menu' });
}

describe('TaskMenuResizer', () => {
  beforeEach(() => { act(() => _resetTaskMenu()); });

  test('dragging the edge sets the width to where the pointer is, and says it is resizing', () => {
    const onResizingChange = vi.fn();
    render(<TaskMenuResizer onResizingChange={onResizingChange} />);
    fireEvent.pointerDown(edge(), { pointerId: 1, clientX: 360 });
    expect(onResizingChange).toHaveBeenLastCalledWith(true);
    fireEvent.pointerMove(edge(), { pointerId: 1, clientX: 452 });
    expect(taskMenuWidth()).toBe(452);
    expect(edge()).toHaveAttribute('aria-valuenow', '452');
    fireEvent.pointerUp(edge(), { pointerId: 1, clientX: 452 });
    expect(onResizingChange).toHaveBeenLastCalledWith(false);
    // After letting go, moving the pointer changes nothing.
    fireEvent.pointerMove(edge(), { pointerId: 1, clientX: 600 });
    expect(taskMenuWidth()).toBe(452);
  });

  test('a cancelled drag ends too, and an end with no drag does nothing', () => {
    const onResizingChange = vi.fn();
    render(<TaskMenuResizer onResizingChange={onResizingChange} />);
    fireEvent.pointerUp(edge(), { pointerId: 1 });
    expect(onResizingChange).not.toHaveBeenCalled();
    fireEvent.pointerDown(edge(), { pointerId: 1, clientX: 360 });
    fireEvent.pointerCancel(edge(), { pointerId: 1 });
    expect(onResizingChange).toHaveBeenLastCalledWith(false);
  });

  test('it works without anyone listening for the resizing state', () => {
    render(<TaskMenuResizer />);
    fireEvent.pointerDown(edge(), { pointerId: 1, clientX: 360 });
    fireEvent.pointerMove(edge(), { pointerId: 1, clientX: 100 });
    fireEvent.pointerUp(edge(), { pointerId: 1 });
    expect(taskMenuWidth()).toBe(TASK_MENU_WIDTH_MIN);
  });

  test('double-click is the default width', () => {
    act(() => setTaskMenuWidth(520));
    render(<TaskMenuResizer />);
    fireEvent.doubleClick(edge());
    expect(taskMenuWidth()).toBe(TASK_MENU_WIDTH_DEFAULT);
  });

  test('arrow keys move the edge; other keys are left alone', () => {
    render(<TaskMenuResizer />);
    fireEvent.keyDown(edge(), { key: 'ArrowRight' });
    expect(taskMenuWidth()).toBe(TASK_MENU_WIDTH_DEFAULT + RESIZE_KEY_STEP_PX);
    fireEvent.keyDown(edge(), { key: 'ArrowLeft' });
    fireEvent.keyDown(edge(), { key: 'ArrowLeft' });
    expect(taskMenuWidth()).toBe(TASK_MENU_WIDTH_DEFAULT - RESIZE_KEY_STEP_PX);
    fireEvent.keyDown(edge(), { key: 'Enter' });
    expect(taskMenuWidth()).toBe(TASK_MENU_WIDTH_DEFAULT - RESIZE_KEY_STEP_PX);
  });
});
