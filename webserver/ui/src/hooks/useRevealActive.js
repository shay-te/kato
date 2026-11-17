import { useEffect, useRef } from 'react';
import { revealInView } from '../utils/scrollUtils.js';

// Keep the item on show in view: the active tab of a strip, the active task
// of the tasks rail. Returns a ref for the container.
//
// It scrolls when the item on show becomes a DIFFERENT element — a tab clicked,
// opened, switched to by keyboard or picked in the palette, of any kind (a
// file, a review loop, the activity, a new task) — and when ``requestId``
// changes (asked again for the same one: a file opened from the tree that was
// already the active tab, scrolled away). Never otherwise: a re-render or a
// status poll with the same item on show leaves the list where the operator
// scrolled it. ``nearest`` moves nothing that is already visible.
//
// The item is found in the DOM by ``selector`` (``.file-tab.active``) after
// every render, so it needs no ref of its own and covers every kind of tab
// alike. It used to scroll only when a file was opened from the tree: clicking
// a review loop's tab left it off-screen (2026-11-17).
export function useRevealActive(selector, requestId = 0) {
  const containerRef = useRef(null);
  const shownRef = useRef({ node: null, requestId });
  useEffect(() => {
    const node = containerRef.current?.querySelector(selector) || null;
    const shown = shownRef.current;
    if (node && (node !== shown.node || requestId !== shown.requestId)) {
      revealInView(node, { smooth: true });
    }
    shownRef.current = { node, requestId };
  });
  return containerRef;
}
