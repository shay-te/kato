// Tests for RightPaneResizer. Tiny div with a mousedown handler that
// starts a drag-to-resize gesture. Only two real behaviors to pin:
// renders the id, fires onPointerDown when grabbed.

import { describe, test, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';

import RightPaneResizer from './RightPaneResizer.jsx';


describe('RightPaneResizer', () => {

  test('renders the right-pane-resizer element with the tooltip title', () => {
    const { container } = render(<RightPaneResizer onPointerDown={() => {}} />);
    const node = container.querySelector('#right-pane-resizer');
    expect(node).toBeInTheDocument();
    expect(node).toHaveAttribute('title', 'Drag to resize');
  });

  test('mousedown on the handle fires onPointerDown', () => {
    const onPointerDown = vi.fn();
    const { container } = render(<RightPaneResizer onPointerDown={onPointerDown} />);
    fireEvent.mouseDown(container.querySelector('#right-pane-resizer'));
    expect(onPointerDown).toHaveBeenCalledTimes(1);
  });

  test('renders without crashing when onPointerDown is undefined', () => {
    // Defensive: the prop can be omitted (parent might not wire the
    // handler yet). Mousedown should still not throw.
    const { container } = render(<RightPaneResizer />);
    expect(() => {
      fireEvent.mouseDown(container.querySelector('#right-pane-resizer'));
    }).not.toThrow();
  });
});
