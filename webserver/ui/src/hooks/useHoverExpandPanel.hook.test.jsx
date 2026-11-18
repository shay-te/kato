// The ONE hover-to-expand panel behaviour — the tasks menu and the files rail
// beside a maximized pane both use it.

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

import { useHoverExpandPanel } from './useHoverExpandPanel.js';
import {
  HOVER_EXPAND_CLOSE_GRACE_MS,
  HOVER_EXPAND_OPEN_DELAY_MS,
  createHoverExpand,
} from '../stores/hoverExpand.js';

let panel;

function Panel({ enabled = true }) {
  const hover = useHoverExpandPanel(panel, { enabled });
  return (
    <>
      <aside
        data-testid="panel"
        ref={hover.ref}
        className={hover.open ? 'is-open' : ''}
        onMouseEnter={hover.onMouseEnter}
        onMouseLeave={hover.onMouseLeave}
      >
        <button type="button">inside</button>
      </aside>
      <main data-testid="outside" />
    </>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  panel = createHoverExpand();
});
afterEach(() => { vi.useRealTimers(); });

describe('useHoverExpandPanel', () => {
  test('pointed at, it opens after the delay; left, it closes after the grace', () => {
    render(<Panel />);
    fireEvent.mouseEnter(screen.getByTestId('panel'));
    act(() => { vi.advanceTimersByTime(HOVER_EXPAND_OPEN_DELAY_MS); });
    expect(screen.getByTestId('panel')).toHaveClass('is-open');
    fireEvent.mouseLeave(screen.getByTestId('panel'));
    act(() => { vi.advanceTimersByTime(HOVER_EXPAND_CLOSE_GRACE_MS); });
    expect(screen.getByTestId('panel')).not.toHaveClass('is-open');
  });

  test('a press inside keeps it open; a press outside, or Escape, closes it', () => {
    render(<Panel />);
    act(() => { panel.open(); });
    fireEvent.pointerDown(screen.getByRole('button', { name: 'inside' }));
    expect(screen.getByTestId('panel')).toHaveClass('is-open');
    fireEvent.pointerDown(screen.getByTestId('outside'));
    expect(screen.getByTestId('panel')).not.toHaveClass('is-open');
    act(() => { panel.open(); });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByTestId('panel')).not.toHaveClass('is-open');
  });

  test('switched off: no hover, and an open panel collapses', () => {
    const { rerender } = render(<Panel />);
    act(() => { panel.open(); });
    rerender(<Panel enabled={false} />);
    expect(panel.isOpen()).toBe(false);
    fireEvent.mouseEnter(screen.getByTestId('panel'));
    act(() => { vi.advanceTimersByTime(HOVER_EXPAND_OPEN_DELAY_MS * 2); });
    expect(screen.getByTestId('panel')).not.toHaveClass('is-open');
  });
});
