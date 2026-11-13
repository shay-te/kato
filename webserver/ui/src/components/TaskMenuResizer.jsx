import { useRef } from 'react';
import {
  TASK_MENU_WIDTH_MAX,
  TASK_MENU_WIDTH_MIN,
  resetTaskMenuWidth,
  setTaskMenuWidth,
  useTaskMenuWidth,
} from '../stores/taskMenuStore.js';

// How far one arrow-key press moves the edge.
export const RESIZE_KEY_STEP_PX = 16;

// The pinned tasks menu's right edge: drag it to change the menu's width
// (the menu is fixed at the screen's left edge, so the pointer's x IS the
// width), double-click it for the default width, or focus it and use the
// arrow keys. Kept in ``taskMenuStore`` and remembered per browser.
export default function TaskMenuResizer({ onResizingChange }) {
  const width = useTaskMenuWidth();
  const dragging = useRef(false);

  function handlePointerDown(event) {
    event.preventDefault();
    dragging.current = true;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    onResizingChange?.(true);
  }

  function handlePointerMove(event) {
    if (!dragging.current) { return; }
    setTaskMenuWidth(event.clientX);
  }

  function handlePointerEnd(event) {
    if (!dragging.current) { return; }
    dragging.current = false;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    onResizingChange?.(false);
  }

  function handleKeyDown(event) {
    const step = { ArrowLeft: -RESIZE_KEY_STEP_PX, ArrowRight: RESIZE_KEY_STEP_PX }[event.key];
    if (!step) { return; }
    event.preventDefault();
    setTaskMenuWidth(width + step);
  }

  return (
    <div
      className="tabs-pane-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the tasks menu"
      aria-valuenow={width}
      aria-valuemin={TASK_MENU_WIDTH_MIN}
      aria-valuemax={TASK_MENU_WIDTH_MAX}
      tabIndex={0}
      title="Drag to resize — double-click for the default width"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerEnd}
      onPointerCancel={handlePointerEnd}
      onDoubleClick={resetTaskMenuWidth}
      onKeyDown={handleKeyDown}
    />
  );
}
