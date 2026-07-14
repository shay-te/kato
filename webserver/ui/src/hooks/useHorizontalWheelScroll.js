import { useEffect } from 'react';

// Maps a "mostly vertical" wheel event to horizontal scroll on
// ``ref.current``. A plain (non-trackpad) mouse wheel emits deltaY
// only by default; inside a horizontally-scrolling strip that would
// scroll the PAGE instead of the strip's own content, leaving the
// operator no way to reach an off-screen tab except dragging the
// scrollbar thumb by hand. Trackpad gestures (which already carry
// deltaX) are passed through untouched — this only intervenes when
// the event is overwhelmingly vertical.
//
// Shared by every horizontally-scrolling tab strip (task tabs, file
// tabs, …) so the wheel-remap behavior — and its Windows/Firefox
// deltaMode normalisation — lives in exactly one place.
export function useHorizontalWheelScroll(ref) {
  useEffect(() => {
    const node = ref.current;
    if (!node) { return undefined; }
    const onWheel = (event) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) {
        return;
      }
      // Normalise deltaY to PIXELS. A physical mouse wheel on
      // Windows/Firefox reports deltaMode === 1 (LINES, deltaY ≈ 3)
      // — adding 3 to scrollLeft barely moves, so the wheel felt
      // dead. Lines → ~16px each; pages → a viewport width. Pixel
      // wheels (deltaMode 0) pass through as-is.
      const step = event.deltaMode === 1
        ? event.deltaY * 16
        : event.deltaMode === 2
          ? event.deltaY * node.clientWidth
          : event.deltaY;
      // Only consume the event when there's actually room to scroll
      // in that direction — otherwise the page should still scroll
      // normally (e.g. a fully-scrolled-right strip shouldn't eat a
      // wheel tick the operator meant for the page below it).
      const goingRight = step > 0;
      const atEnd = goingRight
        ? node.scrollLeft + node.clientWidth >= node.scrollWidth - 1
        : node.scrollLeft <= 0;
      if (atEnd) { return; }
      event.preventDefault();
      node.scrollLeft += step;
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, [ref]);
}
