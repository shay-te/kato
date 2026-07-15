import { useCallback, useRef } from 'react';

// Maps a "mostly vertical" wheel event to horizontal scroll on the
// element this hook's returned ref is attached to. A plain
// (non-trackpad) mouse wheel emits deltaY only by default; inside a
// horizontally-scrolling strip that would scroll the PAGE instead of
// the strip's own content, leaving the operator no way to reach an
// off-screen tab except dragging the scrollbar thumb by hand.
// Trackpad gestures (which already carry deltaX) are passed through
// untouched — this only intervenes when the event is overwhelmingly
// vertical.
//
// Returns a CALLBACK ref, not a plain useRef — deliberately. A plain
// ``useRef`` + ``useEffect(() => { attach to ref.current }, [ref])``
// only runs that effect ONCE, at mount, because ``ref`` (the object
// useRef returns) never changes identity across renders. If the
// scrollable element isn't mounted yet on that very first render
// (task tabs render an empty-state placeholder before sessions load;
// file tabs render nothing before any file is open), ref.current is
// null when the effect fires, and the listener never attaches even
// after the real element shows up moments later — click-and-drag
// still works (native browser behavior needs no JS) but the wheel
// silently does nothing forever. A callback ref fires exactly when
// the DOM node attaches or detaches, so there is no timing gap to
// miss.
//
// ``externalRef`` is optional — pass an existing ``useRef()`` object
// when the caller ALSO needs ``.current`` for its own purposes (e.g.
// TabList's chevron-scroll / hold-to-scroll logic); this hook keeps
// it in sync as a side effect of the same callback.
//
// Shared by every horizontally-scrolling tab strip (task tabs, file
// tabs, …) so the wheel-remap behavior — and its Windows/Firefox
// deltaMode normalisation — lives in exactly one place.
export function useHorizontalWheelScroll(externalRef = null) {
  const cleanupRef = useRef(null);
  return useCallback((node) => {
    if (externalRef) { externalRef.current = node; }
    if (cleanupRef.current) {
      cleanupRef.current();
      cleanupRef.current = null;
    }
    if (!node) { return; }
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
    cleanupRef.current = () => node.removeEventListener('wheel', onWheel);
  }, [externalRef]);
}
