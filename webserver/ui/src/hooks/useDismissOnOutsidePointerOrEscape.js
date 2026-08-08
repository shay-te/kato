import { useEffect } from 'react';

// Dismiss a lightweight pop-over (e.g. a path context menu) on the next
// window pointerdown or on Escape. Only listens while ``active`` is
// truthy. Shared by the Files tab path menu and the diff-file header
// path menu, which previously duplicated this effect verbatim.
//
// ``containerRef`` is optional and marks the pop-over's own root: a
// pointerdown INSIDE it is not "outside", so it doesn't dismiss. Without it
// the listener fires for every pointerdown anywhere — including on the menu's
// own contents. That is fine for a menu whose items are all plain buttons
// (they act on click, and the menu was closing anyway), but it breaks any
// control that needs a second interaction to complete: opening the native
// model <select> inside the composer's actions menu tore the menu down before
// an option could be picked, so the model could not be changed at all.
//
// Callers that pass no ref keep the original dismiss-on-any-pointerdown
// behaviour exactly.
//
// Note: this intentionally does NOT reuse useEscapeKey — that hook calls
// event.preventDefault() and registers no pointerdown listener, so
// reusing it would change behavior.
export function useDismissOnOutsidePointerOrEscape(active, onDismiss, containerRef) {
  useEffect(() => {
    if (!active) { return undefined; }
    function onPointerDown(event) {
      const root = containerRef && containerRef.current;
      if (root && event.target instanceof Node && root.contains(event.target)) {
        return;
      }
      onDismiss();
    }
    function onKeyDown(event) {
      // Escape closes regardless of where focus sits — including from inside
      // the pop-over, which is the only way out once a control has focus.
      if (event.key === 'Escape') { onDismiss(); }
    }
    window.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [active]);
}
